// SCR-D02 오케스트레이션 그래프 편집기 · D03 Planner · D04 Retriever/Summarize · D05 Executor·승인 · D06 팬아웃·합류·실패 정책 · D10 A2A 노출(Agent Card 편집·게시 요청)
import '@xyflow/react/dist/style.css'
import { Background, Controls, Handle, Position, ReactFlow, type Connection, type Node, type NodeProps } from '@xyflow/react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { Badge, Button, Card, CodeBlock, Drawer, ErrorBox, Field, Input, Kv, Loading, PageHeader, RiskChip, Select, StatusBadge, Textarea, cx, useToast } from '@/components/ui'
import type { Json } from '@/lib/api'
import { fmtTime } from '@/lib/format'
import { send, useGet } from '@/lib/query'
import { addStep, clone, connect, edgesOf, layout, removeStep, setStep, type Graph, type PlanStep } from './graphModel'

const COLORS: Record<string, string> = { planner: '#1F3864', retriever: '#3F51E5', summarize: '#0C8599', executor: '#C2410C', remote: '#7048E8' }
const LABEL: Record<string, string> = { planner: 'Planner', retriever: 'Retriever', summarize: 'Summarize', executor: 'Executor', remote: '원격 A2A' }

function StepNode({ data, selected }: NodeProps<Node<{ kind: string; title: string; sub?: string; risk?: string; err?: boolean; retry?: number; timeout?: number; join?: string; required?: boolean; when?: string; dry?: boolean }>>) {
  return (
    <div className={cx('w-[190px] rounded-lg border-2 bg-surface px-3 py-2 text-xs shadow-sm', selected && 'ring-2 ring-primary', data.err && 'border-danger')} style={{ borderColor: data.err ? undefined : COLORS[data.kind] }}>
      {data.kind !== 'planner' && <Handle type="target" position={Position.Left} />}
      <div className="flex items-center gap-1 font-semibold" style={{ color: COLORS[data.kind] }}>{LABEL[data.kind] ?? data.kind}{data.risk === 'high' && <RiskChip risk="high" />}</div>
      <div className="mono mt-0.5 truncate text-fg" title={data.title}>{data.title}</div>
      {data.sub && <div className="truncate text-fg-muted">{data.sub}</div>}
      {(data.retry || data.timeout || data.join || data.required || data.when) && <div className="mt-1 flex flex-wrap gap-1">
        {data.join && <Badge title="합류 조건">⋈ {data.join}</Badge>}{data.required && <Badge tone="warn" title="필수 노드">★ 필수</Badge>}
        {data.retry && <Badge title="노드 재시도">↻ ×{data.retry}</Badge>}{data.timeout && <Badge title="노드 타임아웃">⏱ {data.timeout}s</Badge>}{data.when && <Badge tone="warn" title={data.when}>조건</Badge>}</div>}
      <Handle type="source" position={Position.Right} />
    </div>
  )
}
const nodeTypes = { step: StepNode }

export function ProfileTabs({ id, active }: { id: string; active: 'graph' | 'code' | 'test' }) {
  return (
    <div className="mb-4 flex gap-1 border-b border-line text-sm">
      {([['graph', '그래프'], ['code', 'YAML/코드'], ['test', '테스트 실행']] as const).map(([k, l]) => (
        <Link key={k} to={`/console/agents/${id}/${k}`} className={cx('-mb-px border-b-2 px-3 py-2', active === k ? 'border-primary font-semibold text-primary' : 'border-transparent text-fg-muted hover:text-fg')}>{l}</Link>
      ))}
    </div>
  )
}

export function AgentGraph() {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const toast = useToast()
  const prof = useGet(`/api/v1/agents/${id}`)
  const blocks = useGet('/api/v1/agents/tools')
  const [g, setG] = useState<Graph | null>(null)
  const [dirty, setDirty] = useState(false)
  const [sel, setSel] = useState<string | null>(null)
  const [errors, setErrors] = useState<Json[]>([])
  const [busy, setBusy] = useState('')
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({})
  const [versions, setVersions] = useState(false)
  useEffect(() => {
    if (prof.data && !dirty) {
      setG(clone(prof.data.graph))
      setPositions(layout(prof.data.graph.default_plan))
    }
  }, [prof.data, dirty])
  const tools: Json[] = blocks.data?.tools ?? []
  const toolRisk = useMemo(() => Object.fromEntries(tools.map((t) => [t.qualified, t.risk])), [tools])
  const readOnly = !!prof.data?.template

  const update = (next: Graph | null) => {
    if (!next) return toast('error', '연결할 수 없습니다 — 앞 단계로만 의존할 수 있습니다(순환 금지).')
    setG(next)
    setDirty(true)
    setPositions((p) => ({ ...layout(next.default_plan), ...Object.fromEntries(Object.entries(p).filter(([k]) => next.default_plan.some((s) => s.id === k) || k === 'planner')) }))
  }
  const errIds = new Set(errors.map((e) => /default_plan\[(\d+)\]/.exec(e.path)?.[1]).filter(Boolean).map((i) => g?.default_plan[Number(i)]?.id))
  const nodes: Node[] = g ? [
    { id: 'planner', type: 'step', position: positions.planner ?? { x: 0, y: 0 }, data: { kind: 'planner', title: `mode ${g.planner.mode ?? 'llm'}`, sub: `model ${g.planner.model ?? 'auto'} · max ${g.planner.max_steps ?? 4}` }, deletable: false },
    ...g.default_plan.map((s) => ({
      id: s.id, type: 'step', position: positions[s.id] ?? { x: 0, y: 0 },
      data: { kind: s.agent, title: s.tool || s.remote_agent || (s.agent === 'summarize' ? (g.workers.summarize?.model ?? 'auto') : s.id), sub: s.id, risk: s.tool ? toolRisk[s.tool] : undefined, err: errIds.has(s.id),
        retry: s.retry?.max_attempts, timeout: s.timeout_s, when: s.when, required: (g.fanout?.required ?? []).includes(s.id),
        join: (s.depends_on ?? []).length > 1 ? (g.fanout?.join ?? 'all') : undefined },
    })),
  ] : []
  const edges = g ? edgesOf(g.default_plan).map((e) => ({ ...e, animated: g.fanout?.parallel !== false })) : []

  const save = async () => {
    setBusy('save')
    try {
      await send('PUT', `/api/v1/agents/${id}/graph`, { graph: g })
      setErrors([])
      setDirty(false)
      await prof.refetch()
      toast('ok', '저장했습니다 (초안 — 게시 필요).')
    } catch (e: any) {
      setErrors(e.data?.errors ?? [])
      toast('error', e.message)
    } finally {
      setBusy('')
    }
  }
  const validate = async () => {
    const r = await send<Json>('POST', `/api/v1/agents/${id}/validate`, { graph: g })
    setErrors(r.errors)
    toast(r.valid ? 'ok' : 'error', r.valid ? '검증 통과' : `오류 ${r.errors.length}건`)
  }
  const act = async (k: string, fn: () => Promise<unknown>, okMsg: string) => {
    setBusy(k)
    try {
      await fn()
      await prof.refetch()
      toast('ok', okMsg)
    } catch (e: any) {
      setErrors(e.data?.errors ?? [])
      toast('error', e.message)
    } finally {
      setBusy('')
    }
  }
  const duplicate = async () => {
    const name = prompt('새 프로파일 이름', `${prof.data?.name} 사본`)
    if (!name) return
    try {
      const r = await send<Json>('POST', '/api/v1/agents', { name, from_template: id })
      nav(`/console/agents/${r.id}/graph`)
    } catch (e: any) {
      toast('error', e.message)
    }
  }

  if (prof.error) return <ErrorBox error={prof.error} />
  if (!prof.data || !g) return <Loading rows={6} />
  const p = prof.data
  const step = g.default_plan.find((s) => s.id === sel)
  return (
    <>
      <PageHeader title={p.name} crumbs={['에이전트', p.id]} desc={p.description}
        actions={<>
          <StatusBadge status={p.status} /><Badge>v{p.version}</Badge>{p.template && <Badge tone="profile">템플릿</Badge>}
          <Button size="sm" onClick={() => setVersions(true)}>버전</Button>
          {readOnly ? <Button size="sm" variant="primary" onClick={duplicate}>복제해서 편집</Button> : <>
            <Button size="sm" onClick={validate}>검증</Button>
            <Button size="sm" onClick={save} disabled={!dirty} loading={busy === 'save'}>저장</Button>
            <Button size="sm" variant="primary" disabled={dirty || errors.length > 0 || p.sync === false} title={dirty ? '먼저 저장하세요' : errors.length ? `검증 오류 ${errors.length}건 — 고친 뒤 게시` : p.sync === false ? '코드만 저장된 변경이 있습니다 (MSG-001)' : undefined}
              loading={busy === 'pub'} onClick={() => act('pub', () => send('POST', `/api/v1/agents/${id}/publish`, { note: '콘솔에서 게시' }), '게시했습니다 — 채팅에 노출됩니다.')}>게시</Button>
          </>}
        </>} />
      <ProfileTabs id={id} active="graph" />
      {p.sync === false && <div role="status" className="mb-3 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-sm">YAML/코드 탭에 그래프로 반영되지 않은 코드만 저장분이 있습니다 — 오류를 고쳐 저장하거나 되돌리세요 (MSG-001).</div>}
      {(p.checks?.warnings ?? []).length > 0 && <div className="mb-3 rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-xs">{p.checks.warnings.map((w: Json, i: number) => <div key={i}>! {w.message}</div>)}</div>}
      {errors.length > 0 && (
        <div role="alert" className="mb-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
          {errors.map((e, i) => <div key={i}><span className="mono">{e.path}</span>{e.line ? ` (줄 ${e.line})` : ''}: {e.message}</div>)}
        </div>
      )}
      <div className="grid grid-cols-[1fr_300px] gap-4">
        <div className="h-[560px] rounded-lg border border-line bg-surface" aria-label="오케스트레이션 그래프 캔버스">
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView nodesConnectable={!readOnly} nodesDraggable deleteKeyCode={readOnly ? null : ['Delete', 'Backspace']}
            onNodeClick={(_, n) => setSel(n.id)} onPaneClick={() => setSel(null)}
            onNodesChange={(ch) => ch.forEach((c) => { if (c.type === 'position' && c.position) setPositions((pp) => ({ ...pp, [c.id]: c.position! })) })}
            onNodesDelete={(ns) => ns.forEach((n) => n.id !== 'planner' && update(removeStep(g, n.id)))}
            onConnect={(c: Connection) => c.source && c.target && update(connect(g, c.source, c.target))}>
            <Background /><Controls />
          </ReactFlow>
        </div>
        <div className="space-y-3">
          {!readOnly && (
            <Card title="노드 추가">
              <div className="grid grid-cols-2 gap-2">
                {(['retriever', 'summarize', 'executor', 'remote'] as const).map((k) => (
                  <Button key={k} size="sm" onClick={() => update(addStep(g, k, sel && sel !== 'planner' ? sel : undefined))}>+ {LABEL[k]}</Button>
                ))}
              </div>
              <p className="mt-2 text-xs text-fg-muted">선택한 노드 뒤에 붙습니다. 핸들을 끌어 의존을 연결하고 Delete 로 지웁니다.</p>
            </Card>
          )}
          <FanoutSettings id={id} g={g} dirty={dirty} readOnly={readOnly} onChange={update} />
          <A2AExpose p={p} onChange={() => prof.refetch()} />
        </div>
      </div>
      <Drawer open={!!sel} onClose={() => setSel(null)} title={sel === 'planner' ? 'Planner 노드 설정' : `${LABEL[step?.agent ?? ''] ?? ''} 노드 설정 — ${sel}`}>
        {sel === 'planner' && <PlannerSettings g={g} agents={blocks.data?.remote_agents ?? []} models={blocks.data?.models ?? [{ id: 'auto' }]} readOnly={readOnly} onChange={update} />}
        {step && <StepSettings g={g} step={step} tools={tools} agents={blocks.data?.remote_agents ?? []} models={blocks.data?.models ?? []} readOnly={readOnly} onChange={update} onDelete={() => { update(removeStep(g, step.id)); setSel(null) }} />}
      </Drawer>
      <Versions open={versions} onClose={() => setVersions(false)} id={id} readOnly={readOnly} onRollback={() => prof.refetch()} />
    </>
  )
}

// SCR-D03 Planner — 계획 모델·폴백·분해 프롬프트·규칙·배정 대상·재계획 조건
function PlannerSettings({ g, agents, models, readOnly, onChange }: { g: Graph; agents: Json[]; models: Json[]; readOnly: boolean; onChange: (g: Graph) => void }) {
  const pl = g.planner as Json
  const set = (patch: Json) => onChange({ ...g, planner: { ...g.planner, ...patch } })
  const rp: Json = pl.replan ?? {}
  const triggers: string[] = rp.triggers ?? rp.on ?? ['failed']
  const targets = [...Object.keys(g.workers ?? {}).filter((w) => w !== 'remote'), ...((g.workers.remote?.agents ?? []) as string[])]
  const assign: string[] | undefined = pl.assign
  const trust = Object.fromEntries(agents.map((a) => [a.id, a.trust_tier]))
  const toggleAssign = (t: string, on: boolean) => { const cur = assign ?? targets; set({ assign: on ? [...new Set([...cur, t])] : cur.filter((x) => x !== t) }) }
  const toggleTrig = (t: string, on: boolean) => set({ replan: { ...rp, triggers: on ? [...new Set([...triggers, t])] : triggers.filter((x) => x !== t), on: undefined } })
  return (
    <div className="space-y-3">
      <Field label="모드" hint="llm: 플래너 모델이 계획 · template: 기본 계획 그대로"><Select disabled={readOnly} value={pl.mode ?? 'llm'} onChange={(e) => set({ mode: e.target.value })}><option value="llm">llm</option><option value="template">template</option></Select></Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="계획 모델(게이트웨이 별칭)"><Select disabled={readOnly} value={pl.model ?? 'auto'} onChange={(e) => set({ model: e.target.value })}>{models.map((m) => <option key={m.id}>{m.id}</option>)}</Select></Field>
        <Field label="폴백 (MSG-015)"><Select disabled={readOnly} value={pl.fallback_model ?? ''} onChange={(e) => set({ fallback_model: e.target.value || undefined })}><option value="">없음 — 게이트웨이 라우팅</option>{models.map((m) => <option key={m.id}>{m.id}</option>)}</Select></Field>
      </div>
      <Field label="분해 프롬프트" hint="{request}·{tools}·{agents} 자동 주입 — 비우면 기본 지시문">
        <textarea disabled={readOnly} aria-label="분해 프롬프트" className="mono h-24 w-full rounded-md border border-line bg-surface p-2 text-xs" value={pl.prompt ?? ''} onChange={(e) => set({ prompt: e.target.value || undefined })} /></Field>
      <div className="grid grid-cols-3 gap-2">
        <Field label="최대 하위 작업"><Input type="number" min={1} max={20} disabled={readOnly} value={pl.max_steps ?? 6} onChange={(e) => set({ max_steps: Number(e.target.value) })} /></Field>
        <Field label="최대 깊이"><Input type="number" min={1} max={10} disabled={readOnly} value={pl.max_depth ?? ''} placeholder="—" onChange={(e) => set({ max_depth: e.target.value ? Number(e.target.value) : undefined })} /></Field>
        <Field label="출력 스키마"><Input disabled value="plan.v1" /></Field>
      </div>
      <fieldset><legend className="mb-1 text-xs font-medium text-fg-muted">배정 대상</legend>
        {targets.map((t) => { const low = ['untrusted', 'blocked'].includes(trust[t]); return (
          <label key={t} className={cx('mr-3 inline-flex items-center gap-1 text-sm', low && 'text-fg-muted')} title={low ? `신뢰 등급 미달(${trust[t]}) — 위임 불가 (ST-04)` : undefined}>
            <input type="checkbox" disabled={readOnly || low} checked={!low && (assign ?? targets).includes(t)} onChange={(e) => toggleAssign(t, e.target.checked)} />{LABEL[t] ?? `A2A ${t}`}{low && ' (신뢰 미달)'}</label>) })}
      </fieldset>
      <fieldset><legend className="mb-1 text-xs font-medium text-fg-muted">재계획 조건 (최대 {rp.max_rounds ?? 0}회)</legend>
        {([['failed', '하위 작업 실패'], ['empty', '검색 결과 0건'], ['rejected', '사용자 거부']] as const).map(([k, l]) => <label key={k} className="mr-3 inline-flex items-center gap-1 text-sm"><input type="checkbox" disabled={readOnly} checked={triggers.includes(k)} onChange={(e) => toggleTrig(k, e.target.checked)} />{l}</label>)}
        <Input type="number" min={0} max={3} aria-label="재계획 최대 횟수" className="mt-1 w-24" disabled={readOnly} value={rp.max_rounds ?? 0} onChange={(e) => set({ replan: { ...rp, max_rounds: Number(e.target.value) } })} />
      </fieldset>
      {g.data_class !== 'public' && <p className="rounded-md border border-model/40 bg-model/5 px-3 py-2 text-xs">🔒 내부 데이터면 계획 모델도 internal-data(로컬)로 전환됩니다 (MSG-011)</p>}
    </div>
  )
}

const TTLS = [[300, '5분'], [600, '10분'], [1800, '30분'], [3600, '1시간']] as const

// SCR-D06 팬아웃·합류·실패 처리 — 실행 방식·최대 동시·합류 조건·타임아웃·재시도·백오프·429·부분 결과·시뮬레이션
function FanoutSettings({ id, g, dirty, readOnly, onChange }: { id: string; g: Graph; dirty: boolean; readOnly: boolean; onChange: (g: Graph) => void }) {
  const fo = (g.fanout ?? {}) as Json
  const rt = (g.retry ?? {}) as Json
  const fb = ((g as Json).fallbacks ?? {}) as Json
  const setF = (p: Json) => onChange({ ...g, fanout: { ...g.fanout, ...p } })
  const setR = (p: Json) => onChange({ ...g, retry: { ...g.retry, ...p } })
  const [fail, setFail] = useState<string[]>([])
  const [sim, setSim] = useState<Json | null>(null)
  const ids = g.default_plan.map((s) => s.id)
  const simulate = async () => setSim(await send<Json>('POST', `/api/v1/agents/${id}/simulate`, { failed: fail }))
  return (
    <Card title="팬아웃·합류·실패 정책">
      <div className="space-y-2">
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1"><input type="radio" name="par" disabled={readOnly} checked={fo.parallel !== false} onChange={() => setF({ parallel: true })} />병렬</label>
          <label className="flex items-center gap-1"><input type="radio" name="par" disabled={readOnly} checked={fo.parallel === false} onChange={() => setF({ parallel: false })} />순차</label>
          <Input type="number" min={1} max={20} aria-label="최대 동시" className="w-20" disabled={readOnly || fo.parallel === false} value={fo.max_concurrency ?? ''} placeholder="동시" onChange={(e) => setF({ max_concurrency: e.target.value ? Number(e.target.value) : undefined })} />
        </div>
        <Field label="합류 조건"><Select disabled={readOnly} value={fo.join ?? 'all'} onChange={(e) => setF({ join: e.target.value })}>
          <option value="all">모두 완료</option><option value="any">하나라도</option><option value="required">필수 노드 완료</option><option value="n_of_m">N 중 M 완료</option></Select></Field>
        {fo.join === 'required' && <div className="flex flex-wrap gap-2 text-xs">{ids.map((s) => <label key={s} className="flex items-center gap-1"><input type="checkbox" disabled={readOnly} checked={(fo.required ?? []).includes(s)} onChange={(e) => setF({ required: e.target.checked ? [...(fo.required ?? []), s] : (fo.required ?? []).filter((x: string) => x !== s) })} />★ {s}</label>)}</div>}
        {fo.join === 'n_of_m' && <Field label="N (필요 완료 수)"><Input type="number" min={1} max={20} disabled={readOnly} value={fo.n ?? 1} onChange={(e) => setF({ n: Number(e.target.value) })} /></Field>}
        <div className="grid grid-cols-2 gap-2">
          <Field label="노드 타임아웃(초)"><Input type="number" min={1} max={600} disabled={readOnly} value={fo.node_timeout_s ?? ''} placeholder="—" onChange={(e) => setF({ node_timeout_s: e.target.value ? Number(e.target.value) : undefined })} /></Field>
          <Field label="전체 타임아웃(초)"><Input type="number" min={1} max={3600} disabled={readOnly} value={fo.total_timeout_s ?? ''} placeholder="—" onChange={(e) => setF({ total_timeout_s: e.target.value ? Number(e.target.value) : undefined })} /></Field>
          <Field label="재시도(최대 시도)"><Input type="number" min={1} max={5} disabled={readOnly} value={rt.max_attempts ?? 2} onChange={(e) => setR({ max_attempts: Number(e.target.value) })} /></Field>
          <Field label="백오프"><Select disabled={readOnly} value={rt.backoff ?? 'fixed'} onChange={(e) => setR({ backoff: e.target.value })}><option value="fixed">고정</option><option value="exponential">지수</option></Select></Field>
        </div>
        <Field label="도구 429 (MSG-012)"><Select disabled={readOnly} value={fb.tool_429 ?? 'wait'} onChange={(e) => onChange({ ...g, fallbacks: { ...fb, tool_429: e.target.value } } as Graph)}><option value="wait">재시도 가능 시각까지 대기(≤10초) 후 재시도</option><option value="skip">바로 제외</option></Select></Field>
        <p className="text-xs text-fg-muted">모델 오류 → 게이트웨이 폴백 별칭 · 원격 A2A 실패 → Planner 재계획(대체 계획)</p>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={readOnly} checked={g.on_failure !== 'fail'} onChange={(e) => onChange({ ...g, on_failure: e.target.checked ? 'partial' : 'fail' })} />부분 결과 반환 (MSG-014)</label>
        <Field label="데이터 등급(기본)"><Select disabled={readOnly} value={g.data_class ?? 'internal'} onChange={(e) => onChange({ ...g, data_class: e.target.value })}><option>public</option><option>internal</option><option>confidential</option></Select></Field>
        <div className="rounded-md border border-line p-2">
          <div className="mb-1 text-xs font-medium text-fg-muted">실패 시뮬레이션 — 실패를 가정할 단계</div>
          <div className="flex flex-wrap gap-2 text-xs">{ids.map((s) => <label key={s} className="flex items-center gap-1"><input type="checkbox" checked={fail.includes(s)} onChange={(e) => setFail(e.target.checked ? [...fail, s] : fail.filter((x) => x !== s))} />{s}</label>)}</div>
          <Button size="sm" className="mt-1" disabled={dirty} title={dirty ? '저장한 그래프로 시뮬레이션합니다 — 먼저 저장하세요' : undefined} onClick={simulate}>시뮬레이션</Button>
          {sim && <p className={cx('mt-1 text-xs', sim.status === 'failed' ? 'text-danger' : sim.status === 'partial' ? 'text-warn' : 'text-ok')}>{sim.steps.map((x: Json) => `${x.step} ${x.status === 'succeeded' ? '✓' : '✗'}`).join(' · ')} → {sim.message}</p>}
        </div>
      </div>
    </Card>
  )
}

// SCR-D05 Executor 승인 정책 — high 는 필수 고정, medium 은 선택 · 승인자 · 만료 · 거부 처리
function ExecutorApproval({ g, tool, readOnly, onChange }: { g: Graph; tool?: Json; readOnly: boolean; onChange: (g: Graph) => void }) {
  const ex = (g.workers.executor ?? {}) as Json
  const ap = (ex.approval ?? {}) as Json
  const set = (p: Json) => onChange({ ...g, workers: { ...g.workers, executor: { ...ex, approval: { ...ap, ...p } } } })
  const medium: string[] = ap.medium ?? []
  return (
    <div className="space-y-2 rounded-md border border-line p-2">
      <div className="text-xs font-medium text-fg-muted">승인 정책 (interrupt) — 게이트웨이 정책보다 완화할 수 없습니다</div>
      {tool && <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" disabled={readOnly || tool.risk === 'high'} checked={tool.risk === 'high' || tool.approval_required || medium.includes(tool.qualified)}
          onChange={(e) => set({ medium: e.target.checked ? [...new Set([...medium, tool.qualified])] : medium.filter((x) => x !== tool.qualified) })} />
        사람 승인 {tool.risk === 'high' ? '필수 (고위험 — 해제 불가)' : tool.approval_required ? '필수 (게이트웨이 규칙)' : '— 중위험은 선택'}</label>}
      <fieldset className="text-sm"><legend className="text-xs text-fg-muted">승인자</legend>
        <label className="mr-3 inline-flex items-center gap-1"><input type="radio" name="approver" disabled={readOnly} checked={(ap.approver ?? 'requester') === 'requester'} onChange={() => set({ approver: 'requester' })} />요청 사용자 본인</label>
        <label className="inline-flex items-center gap-1"><input type="radio" name="approver" disabled={readOnly} checked={ap.approver === 'approvers'} onChange={() => set({ approver: 'approvers' })} />별도 승인자 (H12)</label></fieldset>
      <div className="grid grid-cols-2 gap-2">
        <Field label="승인 만료"><Select disabled={readOnly} value={ap.ttl_s ?? 1800} onChange={(e) => set({ ttl_s: Number(e.target.value) })}>{TTLS.map(([v, l]) => <option key={v} value={v}>{l} → 자동 거부</option>)}</Select></Field>
        <Field label="거부·만료 시"><Select disabled={readOnly} value={ap.on_deny ?? 'partial'} onChange={(e) => set({ on_deny: e.target.value })}><option value="partial">부분 결과</option><option value="replan">Planner 재계획</option><option value="fail">전체 실패</option></Select></Field>
      </div>
      <p className="text-xs text-fg-muted">✓ 인자 스키마 검증 실패면 승인 요청 전에 차단 · ✓ 승인 화면에 영향 요약·정책 근거 표시 · 허용목록 밖 도구는 선택 불가 (MSG-006)</p>
    </div>
  )
}

function StepSettings({ g, step, tools, agents, models, readOnly, onChange, onDelete }: { g: Graph; step: PlanStep; tools: Json[]; agents: Json[]; models: Json[]; readOnly: boolean; onChange: (g: Graph) => void; onDelete: () => void }) {
  const [args, setArgs] = useState(JSON.stringify(step.args ?? {}, null, 2))
  const [argErr, setArgErr] = useState('')
  useEffect(() => setArgs(JSON.stringify(step.args ?? {}, null, 2)), [step.id, step.args])
  const pool = tools.filter((t) => (step.agent === 'executor' ? true : t.suggested_node !== 'executor' || t.qualified === step.tool))
  const tool = tools.find((t) => t.qualified === step.tool)
  return (
    <div className="space-y-3">
      {(step.agent === 'retriever' || step.agent === 'executor') && (
        <>
          <Field label="MCP 도구" hint={step.agent === 'executor' ? '쓰기 도구는 실행 전 사람 승인(정책) — SCR-C04' : '읽기 도구 권장 (병렬 실행)'}>
            <Select disabled={readOnly} value={step.tool ?? ''} onChange={(e) => onChange(setStep(g, step.id, { tool: e.target.value }))}>
              <option value="">선택…</option>
              {pool.map((t) => <option key={t.qualified} value={t.qualified}>{t.qualified} ({t.risk})</option>)}
            </Select>
          </Field>
          {tool && <div className="flex gap-1"><RiskChip risk={tool.risk} />{tool.approval_required && <Badge tone="warn">승인 필요</Badge>}</div>}
          {step.agent === 'retriever' && tool && tool.risk !== 'low' && <p role="alert" className="text-xs text-danger">Retriever 에는 읽기 도구만 — 쓰기 도구는 Executor 로 옮기세요 (검증 오류).</p>}
          {step.agent === 'retriever' && <div className="grid grid-cols-2 gap-2">
            <Field label="top-k (RAG·결과 수)"><Input type="number" min={1} max={50} disabled={readOnly} value={g.workers.retriever?.top_k ?? ''} placeholder="5" onChange={(e) => onChange({ ...g, workers: { ...g.workers, retriever: { ...g.workers.retriever, top_k: e.target.value ? Number(e.target.value) : undefined } } })} /></Field>
            <Field label="타임아웃(초)"><Input type="number" min={1} max={300} disabled={readOnly} value={g.workers.retriever?.timeout_s ?? ''} placeholder="—" onChange={(e) => onChange({ ...g, workers: { ...g.workers, retriever: { ...g.workers.retriever, timeout_s: e.target.value ? Number(e.target.value) : undefined } } })} /></Field>
          </div>}
          {step.agent === 'retriever' && <p className="text-xs text-fg-muted">원격 MCP 는 게이트웨이 경유가 강제되고, 호출 전 Tool Governance 정책을 통과해야 합니다 (MSG-005). 인덱스 권한은 사용자 신원으로 거릅니다.</p>}
          {step.agent === 'executor' && <ExecutorApproval g={g} tool={tool} readOnly={readOnly} onChange={onChange} />}
          <Field label="인자(JSON) · {{request}} 치환 가능" error={argErr}>
            <textarea disabled={readOnly} className="mono h-28 w-full rounded-md border border-line bg-surface p-2 text-xs" value={args}
              onChange={(e) => setArgs(e.target.value)} onBlur={() => { try { onChange(setStep(g, step.id, { args: JSON.parse(args || '{}') })); setArgErr('') } catch { setArgErr('JSON 형식 오류') } }} />
          </Field>
        </>
      )}
      {step.agent === 'summarize' && (
        <>
          <Field label="요약 모델" hint="internal/confidential 데이터는 로컬 모델로 재라우팅"><Select disabled={readOnly} value={g.workers.summarize?.model ?? 'auto'} onChange={(e) => onChange({ ...g, workers: { ...g.workers, summarize: { ...g.workers.summarize, model: e.target.value } } })}>{models.map((m) => <option key={m.id}>{m.id}</option>)}</Select></Field>
          <Field label="최대 길이(토큰)"><Input type="number" min={64} max={8000} disabled={readOnly} value={g.workers.summarize?.max_tokens ?? ''} placeholder="제한 없음" onChange={(e) => onChange({ ...g, workers: { ...g.workers, summarize: { ...g.workers.summarize, max_tokens: e.target.value ? Number(e.target.value) : undefined } } })} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={readOnly} checked={g.workers.summarize?.cite !== false} onChange={(e) => onChange({ ...g, workers: { ...g.workers, summarize: { ...g.workers.summarize, cite: e.target.checked } } })} /> 근거 인용 필수 — 인용 없으면 1회 재생성, 실패 시 ‘근거 없음’</label>
          <Field label="인용 형식"><Select disabled={readOnly} value={g.workers.summarize?.citation_format ?? 'n'} onChange={(e) => onChange({ ...g, workers: { ...g.workers, summarize: { ...g.workers.summarize, citation_format: e.target.value } } })}><option value="n">[n]</option><option value="n_link">[n] + 원문 링크</option></Select></Field>
          {g.data_class !== 'public' && <Badge tone="model">로컬 전용 — 내부 데이터 요약은 로컬 모델만 (MSG-011)</Badge>}
        </>
      )}
      {step.agent === 'remote' && (
        <Field label="원격 A2A 에이전트"><Select disabled={readOnly} value={step.remote_agent ?? ''} onChange={(e) => onChange(setStep(g, step.id, { remote_agent: e.target.value, args: step.args ?? { message: '{{request}}' } }))}>
          <option value="">선택…</option>{agents.map((a) => <option key={a.id} value={a.id} disabled={['untrusted', 'blocked'].includes(a.trust_tier)}>{a.name} ({a.trust_tier}{['untrusted', 'blocked'].includes(a.trust_tier) ? ' — 신뢰 미달, 위임 불가' : ''})</option>)}
        </Select></Field>
      )}
      <div className="grid grid-cols-2 gap-2">
        <Field label="노드 재시도(최대 시도)"><Input type="number" min={1} max={5} disabled={readOnly} value={step.retry?.max_attempts ?? ''} placeholder="그래프 기본" onChange={(e) => onChange(setStep(g, step.id, { retry: e.target.value ? { max_attempts: Number(e.target.value) } : undefined }))} /></Field>
        <Field label="노드 타임아웃(초)"><Input type="number" min={1} max={600} disabled={readOnly} value={step.timeout_s ?? ''} placeholder="그래프 기본" onChange={(e) => onChange(setStep(g, step.id, { timeout_s: e.target.value ? Number(e.target.value) : undefined }))} /></Field>
      </div>
      <Kv rows={[['의존', step.depends_on?.join(', ') || 'Planner'], ...(step.when ? [['조건', <span className="mono text-xs">{step.when}</span>] as [string, ReactNode]] : [])]} />
      {!readOnly && <Button variant="danger" size="sm" onClick={onDelete}>노드 삭제</Button>}
    </div>
  )
}

// SCR-D10 A2A 서버 노출 — Agent Card 편집 → 검증 → 게시 요청(E05 승인 후 공개) · 중지는 즉시
function A2AExpose({ p, onChange }: { p: Json; onChange: () => void }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const stop = async () => {
    if (!confirm('A2A 노출을 중지합니다 — 외부 에이전트의 위임 요청이 바로 거부됩니다. 계속할까요?')) return
    try {
      await send('POST', `/api/v1/agents/${p.id}/a2a`, { exposed: false })
      onChange()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  return (
    <Card title="A2A 서버 노출 (플랫폼 Agent Card)" actions={<StatusBadge status={p.a2a_status} />}>
      {p.a2a_card ? <a className="mono block truncate text-xs text-primary" href={p.a2a_card} target="_blank" rel="noreferrer">{p.a2a_card}</a>
        : <p className="text-xs text-fg-muted">{p.a2a_status === 'pending' ? '게시 요청됨 — 승인 대기열(E05)에서 승인되면 공개됩니다 (MSG-008).' : '카드를 편집·검증한 뒤 게시를 요청하세요.'}</p>}
      {p.status !== 'published' && <p className="mt-1 text-xs text-fg-muted">게시된 프로파일만 노출할 수 있습니다.</p>}
      <div className="mt-2 flex gap-2">
        <Button size="sm" onClick={() => setOpen(true)}>Agent Card 편집</Button>
        {p.a2a_exposed && <Button size="sm" variant="danger" onClick={stop}>노출 중지</Button>}
      </div>
      {open && <CardEditor pid={p.id} onClose={() => { setOpen(false); onChange() }} />}
    </Card>
  )
}

type Skill = { id: string; name: string; description: string; tags: string; examples: string }
const toSkill = (s: Json): Skill => ({ id: s.id ?? '', name: s.name ?? '', description: s.description ?? '', tags: (s.tags ?? []).join(', '), examples: (s.examples ?? []).join('\n') })

function CardEditor({ pid, onClose }: { pid: string; onClose: () => void }) {
  const toast = useToast()
  const q = useGet(`/api/v1/agents/${pid}/a2a-card`)
  const [d, setD] = useState<Json | null>(null)
  const [skills, setSkills] = useState<Skill[]>([])
  const [scopes, setScopes] = useState('a2a:invoke')
  const [view, setView] = useState<Json | null>(null)
  const [busy, setBusy] = useState('')
  useEffect(() => {
    if (q.data && !d) {
      setD(q.data.draft)
      setSkills((q.data.draft.skills ?? []).map(toSkill))
      setScopes((q.data.draft.security?.scopes ?? ['a2a:invoke']).join(', '))
      setView(q.data)
    }
  }, [q.data, d])
  if (q.error) return <Drawer open onClose={onClose} title="Agent Card 편집"><ErrorBox error={q.error} /></Drawer>
  if (!d || !view) return <Drawer open onClose={onClose} title="Agent Card 편집"><Loading /></Drawer>
  const body = () => ({ ...d, capabilities: d.capabilities ?? {}, security: { scheme: 'oauth2', scopes: scopes.split(',').map((x) => x.trim()).filter(Boolean) },
    skills: skills.map((s) => ({ id: s.id.trim(), name: s.name || s.id, description: s.description, tags: s.tags.split(',').map((x) => x.trim()).filter(Boolean), examples: s.examples.split('\n').map((x) => x.trim()).filter(Boolean) })) })
  const save = async () => {
    setBusy('save')
    try {
      setView(await send<Json>('PUT', `/api/v1/agents/${pid}/a2a-card`, body()))
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy('')
    }
  }
  const publish = async () => {
    setBusy('pub')
    try {
      const r = await send<Json>('PUT', `/api/v1/agents/${pid}/a2a-card`, body())
      setView(r)
      if (r.errors?.length) return
      const out = await send<Json>('POST', `/api/v1/agents/${pid}/a2a-card/publish`)
      toast('ok', out.requested ? '게시 요청 — 승인 대기열(E05)에서 승인되면 공개됩니다 (MSG-008)' : '게시했습니다 — 외부 에이전트가 위임할 수 있습니다')
      onClose()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy('')
    }
  }
  const setSkill = (n: number, k: keyof Skill, v: string) => setSkills(skills.map((s, i) => i === n ? { ...s, [k]: v } : s))
  const caps = d.capabilities ?? {}
  return (
    <Drawer open onClose={onClose} title={`Agent Card 편집 — ${pid}`} width="w-[920px]">
      <div className="grid grid-cols-2 gap-4 text-sm">
        <div className="space-y-3">
          <div className="grid grid-cols-[1fr_120px] gap-2">
            <Field label="이름"><Input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} /></Field>
            <Field label="버전 (SemVer)"><Input className="mono" value={d.version} onChange={(e) => setD({ ...d, version: e.target.value })} /></Field>
          </div>
          <Field label="설명"><Textarea className="h-16" value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} /></Field>
          <div className="flex gap-4">
            <label className="flex items-center gap-1"><input type="checkbox" checked={!!caps.streaming} onChange={(e) => setD({ ...d, capabilities: { ...caps, streaming: e.target.checked } })} />streaming</label>
            <label className="flex items-center gap-1"><input type="checkbox" checked={!!caps.pushNotifications} onChange={(e) => setD({ ...d, capabilities: { ...caps, pushNotifications: e.target.checked } })} />push 알림</label>
          </div>
          <Field label="보안 스킴 스코프 (OAuth2 client credentials · AgentNexus IdP)"><Input className="mono" value={scopes} onChange={(e) => setScopes(e.target.value)} /></Field>
          <div>
            <div className="mb-1 flex items-center justify-between"><span className="text-xs font-semibold text-fg-muted">스킬 {skills.length}</span>
              <Button size="sm" onClick={() => setSkills([...skills, { id: '', name: '', description: '', tags: '', examples: '' }])}>+ 스킬</Button></div>
            {skills.map((s, n) => (
              <div key={n} className="mb-2 space-y-1 rounded-md border border-line p-2">
                <div className="grid grid-cols-[1fr_1fr_auto] gap-1">
                  <Input aria-label="스킬 id" className="mono" placeholder="id" value={s.id} onChange={(e) => setSkill(n, 'id', e.target.value)} />
                  <Input aria-label="스킬 이름" placeholder="이름" value={s.name} onChange={(e) => setSkill(n, 'name', e.target.value)} />
                  <Button size="sm" variant="ghost" aria-label="스킬 삭제" onClick={() => setSkills(skills.filter((_, i) => i !== n))}>✕</Button>
                </div>
                <Input aria-label="스킬 설명" placeholder="설명" value={s.description} onChange={(e) => setSkill(n, 'description', e.target.value)} />
                <Input aria-label="스킬 태그" placeholder="태그 (쉼표)" value={s.tags} onChange={(e) => setSkill(n, 'tags', e.target.value)} />
                <Textarea aria-label="스킬 예시" className="h-12 text-xs" placeholder="예시 요청 (줄마다 하나)" value={s.examples} onChange={(e) => setSkill(n, 'examples', e.target.value)} />
              </div>
            ))}
          </div>
        </div>
        <div className="space-y-2">
          <div className="flex items-center gap-2"><span className="text-xs font-semibold text-fg-muted">게시될 agent-card.json</span><StatusBadge status={view.status} /><span className="mono truncate text-[11px] text-fg-muted">{view.url}</span></div>
          {view.errors?.length ? <div role="alert" className="rounded-md border border-danger/30 bg-danger/5 p-2 text-xs text-danger">검증 오류 {view.errors.length}<ul className="list-disc pl-4">{view.errors.map((e: string) => <li key={e}>{e}</li>)}</ul></div>
            : <p className="text-xs text-ok">✓ A2A Agent Card 검증 통과 — url 은 게이트웨이로 고정됩니다</p>}
          <CodeBlock value={view.preview} maxH="max-h-[520px]" />
          <div className="flex justify-end gap-2">
            <Button loading={busy === 'save'} onClick={save}>저장·검증</Button>
            <Button variant="primary" loading={busy === 'pub'} onClick={publish}>게시 요청</Button>
          </div>
        </div>
      </div>
    </Drawer>
  )
}

function Versions({ open, onClose, id, readOnly, onRollback }: { open: boolean; onClose: () => void; id: string; readOnly: boolean; onRollback: () => void }) {
  const q = useGet(open ? `/api/v1/agents/${id}/versions` : null)
  const toast = useToast()
  const [show, setShow] = useState<Json | null>(null)
  return (
    <Drawer open={open} onClose={onClose} title="게시 버전 이력">
      {(q.data?.items ?? []).map((v: Json) => (
        <div key={v.version} className="mb-2 rounded-md border border-line p-2 text-sm">
          <div className="flex items-center gap-2"><Badge tone={v.version === q.data?.current ? 'ok' : 'muted'}>v{v.version}</Badge><span className="flex-1 truncate">{v.note || '—'}</span></div>
          <div className="mt-1 text-xs text-fg-muted">{v.created_by} · {fmtTime(v.created_at, true)}</div>
          <div className="mt-1 flex gap-2">
            <Button size="sm" onClick={() => setShow(v)}>정의 보기</Button>
            {!readOnly && v.version !== q.data?.current && <Button size="sm" onClick={() => send('POST', `/api/v1/agents/${id}/rollback`, { version: v.version }).then(() => { toast('ok', `v${v.version} 로 롤백(새 버전 게시)`); q.refetch(); onRollback() }).catch((e) => toast('error', e.message))}>롤백</Button>}
          </div>
        </div>
      ))}
      {!q.data?.items?.length && <p className="text-sm text-fg-muted">게시 이력이 없습니다.</p>}
      {show && <CodeBlock className="mt-3" value={show.graph} />}
    </Drawer>
  )
}
