// 실행 이벤트(SSE·재생) → 화면 상태. 같은 리듀서로 실시간 스트림과 GET /runs/{id}/events 재생을 처리한다.
import type { SseEvent } from '@/lib/sse'

export type StepStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'waiting_approval' | 'waiting_input' | 'skipped' | 'replanned'
export type Step = { name: string; node: string; tool?: string; agent?: string; status: StepStatus; preview?: string; error?: string; attempt?: number; approvalId?: string; taskId?: string; dependsOn?: string[]; when?: string; round?: number; dryRun?: boolean }
// 도구 429 (SCR-C06 ⑦, MSG-012) — 게이트웨이 재시도 가능 시각까지 기다렸다 재시도(wait) 또는 제외(skip)
export type RateLimited = { step: string; tool?: string; retry_after: number; action: 'wait' | 'skip' }
// 원격 에이전트 추가 입력 요청 (SCR-C05 input-required) — 같은 Task·contextId 로 답한다
export type InputRequest = { step: string; agent?: string; taskId?: string; contextId?: string; question: string; status: 'pending' | 'answered' }
// 재계획 이력 (SCR-C02) — round 1, 2 …
export type Revision = { round: number; source: string; reason: string; previous: string[]; steps: string[] }
export type Approval = { id: string; step: string; tool: string; args: unknown; risk?: string; approver?: string; reason?: string; status: string }
export type Citation = { n: number; title: string; tool?: string; agent?: string; ref?: string }
// MCP 서버→클라이언트 요청에 대한 Host 동의 (SCR-C08) — pending → (sampling: review) → completed|rejected|expired
export type Consent = {
  id: string; kind: 'sampling' | 'elicitation'; server: string; stepId?: string; params: any; routing?: any
  expiresAt?: string; status: string; draft?: string; model?: string; action?: string
  extensions?: number; maxExtensions?: number; extendStep?: number
}
export type RunState = {
  runId?: string; traceId?: string; status: string; dataClass?: string; planSource?: string; planNote?: string
  order: string[]; steps: Record<string, Step>; text: string; citations: Citation[]; approvals: Record<string, Approval>
  consents: Record<string, Consent>; inputs: Record<string, InputRequest>; revisions: Revision[]
  lastEventId: number; error?: string; startedAt?: number; finishedAt?: number
  rateLimited: RateLimited[]; planModel?: string
}

export const initialRun = (): RunState => ({ status: 'idle', order: [], steps: {}, text: '', citations: [], approvals: {}, consents: {}, inputs: {}, revisions: [], lastEventId: 0, rateLimited: [] })

export const ACTIVE = new Set(['queued', 'running', 'starting'])
export const isEnd = (type: string) => ['run.completed', 'run.failed', 'run.waiting', 'run.canceled'].includes(type)

const nodeOf = (s: any) => (s.agent === 'remote' ? 'remote' : s.agent || s.node || 'step')

export function reduceRun(st: RunState, ev: SseEvent): RunState {
  const d = ev.data || {}
  const s: RunState = { ...st, steps: { ...st.steps }, approvals: { ...st.approvals }, consents: { ...st.consents }, inputs: { ...st.inputs }, revisions: [...st.revisions], rateLimited: [...(st.rateLimited ?? [])] }
  if (ev.id && Number(ev.id) > s.lastEventId) s.lastEventId = Number(ev.id)
  const step = (name: string, patch: Partial<Step>) => {
    const prev = s.steps[name] ?? { name, node: patch.node ?? 'step', status: 'pending' as StepStatus }
    s.steps[name] = { ...prev, ...patch }
    if (!s.order.includes(name)) s.order = [...s.order, name]
  }
  switch (ev.event) {
    case 'run':
      s.runId = d.run_id
      s.status = 'running'
      s.startedAt ??= Date.now()
      break
    case 'run.started':
      s.status = 'running'
      s.traceId = d.trace_id
      s.dataClass = d.data_class
      break
    case 'plan':
      s.planSource = d.source
      s.planNote = d.fallback_reason
      s.planModel = d.fallback_model
      s.order = []
      for (const p of d.steps ?? []) step(p.id, { node: nodeOf(p), tool: p.tool ?? (p.kb ? `rag:${p.kb}` : undefined), agent: p.remote_agent, dependsOn: p.depends_on, when: p.when, status: s.steps[p.id]?.status ?? 'pending' })
      break
    case 'plan.revised':
      for (const n of d.previous ?? []) if (s.steps[n]) step(n, { status: 'replanned' })
      for (const p of d.steps ?? []) step(p.id, { node: nodeOf(p), tool: p.tool ?? (p.kb ? `rag:${p.kb}` : undefined), agent: p.remote_agent, dependsOn: p.depends_on, when: p.when, round: d.round, status: 'pending' })
      s.revisions = [...s.revisions, { round: d.round, source: d.source, reason: d.reason, previous: d.previous ?? [], steps: (d.steps ?? []).map((p: any) => p.id) }]
      break
    case 'step.started':
      step(d.step, { node: d.node, tool: d.tool ?? undefined, agent: d.agent ?? undefined, status: 'running' })
      break
    case 'step.completed':
      step(d.step, { node: d.node, status: 'succeeded', preview: d.preview, taskId: d.task_id, error: undefined })
      break
    case 'step.failed':
      step(d.step, { node: d.node, status: 'failed', error: d.error })
      break
    case 'step.retry':
      step(d.step, { attempt: d.attempt, error: d.error, status: 'running' })
      break
    case 'step.rate_limited':
      s.rateLimited = [...s.rateLimited, { step: d.step, tool: d.tool, retry_after: d.retry_after, action: d.action }]
      break
    case 'step.dry_run':
      step(d.step, { dryRun: true })
      break
    case 'step.skipped':
      step(d.step, { status: 'skipped', error: d.reason === 'condition' ? `조건 불충족: ${d.when}` : s.steps[d.step]?.error })
      break
    case 'input.required':
      step(d.step, { status: 'waiting_input', taskId: d.task_id, error: d.question })
      s.inputs[d.step] = { step: d.step, agent: d.agent, taskId: d.task_id, contextId: d.context_id, question: d.question, status: 'pending' }
      break
    case 'input.resolved':
      if (s.inputs[d.step]) s.inputs[d.step] = { ...s.inputs[d.step], status: 'answered' }
      step(d.step, { status: 'pending', error: undefined })
      s.status = 'running'
      break
    case 'approval.required':
      step(d.step, { status: 'waiting_approval', approvalId: d.approval_id })
      s.approvals[d.approval_id] = { id: d.approval_id, step: d.step, tool: d.tool, args: d.args, risk: d.risk, approver: d.approver, reason: d.reason, status: 'pending' }
      break
    case 'approval.resolved':
      if (s.approvals[d.approval_id]) s.approvals[d.approval_id] = { ...s.approvals[d.approval_id], status: d.status }
      break
    case 'sampling.required':
    case 'elicitation.required':
      s.consents[d.approval_id] = {
        id: d.approval_id, kind: ev.event.startsWith('sampling') ? 'sampling' : 'elicitation', server: d.server,
        stepId: d.step_id, params: d.params ?? {}, routing: d.routing, expiresAt: d.expires_at, status: 'pending',
        extensions: 0, maxExtensions: d.max_extensions, extendStep: d.extend_step_s,
      }
      break
    case 'sampling.extended':
    case 'elicitation.extended':  // 시간 연장 (WCAG 2.2.1) — 다른 탭·워커에서 늘려도 카드가 따라간다
      if (s.consents[d.approval_id]) s.consents[d.approval_id] = { ...s.consents[d.approval_id], expiresAt: d.expires_at, extensions: d.extensions }
      break
    case 'sampling.review':
      if (s.consents[d.approval_id]) s.consents[d.approval_id] = { ...s.consents[d.approval_id], status: 'review', draft: d.draft, model: d.model, expiresAt: d.expires_at }
      break
    case 'sampling.resolved':
    case 'elicitation.resolved':
      if (s.consents[d.approval_id]) s.consents[d.approval_id] = { ...s.consents[d.approval_id], status: d.status, model: d.model ?? s.consents[d.approval_id].model, action: d.action }
      break
    case 'delta':
      s.text += d.text ?? ''
      break
    case 'run.waiting':
      s.status = (d.approvals ?? []).length ? 'waiting_approval' : (d.inputs ?? []).length ? 'waiting_input' : 'waiting_approval'
      break
    case 'run.retry':
      s.status = 'running'
      s.text = ''
      break
    case 'run.completed':
      s.status = d.status || 'succeeded'
      if (typeof d.text === 'string') s.text = d.text
      s.citations = d.citations ?? []
      s.finishedAt = Date.now()
      break
    case 'run.failed':
      s.status = 'failed'
      s.error = d.error
      s.finishedAt = Date.now()
      break
    case 'run.canceled':
      s.status = 'canceled'
      s.finishedAt = Date.now()
      break
  }
  return s
}

export const replay = (events: { id: number; type: string; data: any }[], base = initialRun()) =>
  events.reduce((acc, e) => reduceRun(acc, { id: String(e.id), event: e.type, data: e.data }), base)

export const progress = (st: RunState) => {
  const all = st.order.length
  const done = st.order.filter((n) => ['succeeded', 'failed', 'skipped', 'replanned'].includes(st.steps[n]?.status)).length
  return { all, done, failed: st.order.filter((n) => st.steps[n]?.status === 'failed').length }
}
