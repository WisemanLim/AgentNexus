import { initialRun, progress, reduceRun, replay } from '@/pages/chat/runState'

const ev = (id: number, type: string, data: object = {}) => ({ id, type, data })

describe('runState', () => {
  const events = [
    ev(1, 'run.started', { trace_id: 't1', data_class: 'internal' }),
    ev(2, 'plan', { steps: [{ id: 's1', agent: 'retriever', tool: 'jira.search_issues' }, { id: 's2', agent: 'executor', tool: 'confluence.create_page', depends_on: ['s1'] }], source: 'template', fallback_reason: '빈 계획' }),
    ev(3, 'step.started', { step: 's1', node: 'retriever', tool: 'jira.search_issues' }),
    ev(4, 'step.completed', { step: 's1', node: 'retriever', preview: '14건' }),
    ev(5, 'approval.required', { step: 's2', approval_id: 'apr_1', tool: 'confluence.create_page', args: { title: 'x' }, risk: 'high', approver: 'requester' }),
    ev(6, 'run.waiting', { approvals: ['apr_1'] }),
  ]
  it('계획·단계·승인 대기를 재생한다', () => {
    const st = replay(events)
    expect(st.status).toBe('waiting_approval')
    expect(st.traceId).toBe('t1')
    expect(st.order).toEqual(['s1', 's2'])
    expect(st.steps.s1.status).toBe('succeeded')
    expect(st.steps.s2).toMatchObject({ status: 'waiting_approval', approvalId: 'apr_1', dependsOn: ['s1'] })
    expect(st.approvals.apr_1).toMatchObject({ risk: 'high', status: 'pending' })
    expect(st.lastEventId).toBe(6)
    expect(progress(st)).toEqual({ all: 2, done: 1, failed: 0 })
  })
  it('승인 후 재개 — 델타 누적, 완료 시 최종 텍스트·인용으로 교체', () => {
    let st = replay(events)
    st = replay([ev(7, 'approval.resolved', { approval_id: 'apr_1', status: 'approved' }), ev(8, 'step.completed', { step: 's2', node: 'executor' }),
      ev(9, 'delta', { text: '요약 ' }), ev(10, 'delta', { text: '[1]' })], st)
    expect(st.text).toBe('요약 [1]')
    expect(st.approvals.apr_1.status).toBe('approved')
    st = reduceRun(st, { id: '11', event: 'run.completed', data: { status: 'succeeded', text: '최종 [1]', citations: [{ n: 1, title: 'ORI-1', ref: 'ORI-1' }] } })
    expect(st).toMatchObject({ status: 'succeeded', text: '최종 [1]' })
    expect(st.citations).toHaveLength(1)
  })
  it('부분 실패 → 재시도 시 텍스트 초기화, 실패 단계 재실행', () => {
    let st = replay([ev(1, 'plan', { steps: [{ id: 's1', agent: 'remote', remote_agent: 'sre' }] }), ev(2, 'step.failed', { step: 's1', node: 'remote', error: 'timeout' }),
      ev(3, 'run.completed', { status: 'partial', text: '부분' })])
    expect(st.status).toBe('partial')
    expect(progress(st).failed).toBe(1)
    st = reduceRun(st, { event: 'run.retry', data: {} })
    expect(st).toMatchObject({ status: 'running', text: '' })
    st = reduceRun(st, { event: 'step.retry', data: { step: 's1', attempt: 2 } })
    expect(st.steps.s1).toMatchObject({ status: 'running', attempt: 2 })
  })
  it('스트림 첫 이벤트(run)로 run_id 를 얻는다', () => {
    expect(reduceRun(initialRun(), { event: 'run', data: { run_id: 'run_x' } })).toMatchObject({ runId: 'run_x', status: 'running' })
  })
})

describe('MCP 동의 (SCR-C08)', () => {
  it('sampling 요청 → 검토 → 완료, elicitation 요청 → 응답', () => {
    let st = replay([
      ev(1, 'sampling.required', { approval_id: 'apr_s', server: 'internal-orders', step_id: 'stp_1', params: { maxTokens: 256, messages: [] }, routing: { model: 'domain-local', data_class: 'internal' }, expires_at: '2026-10-06T00:01:00Z' }),
    ])
    expect(st.consents.apr_s).toMatchObject({ kind: 'sampling', status: 'pending', server: 'internal-orders', routing: { model: 'domain-local' } })
    st = replay([ev(2, 'sampling.review', { approval_id: 'apr_s', draft: '[domain-7b] 분류', model: 'domain-local', expires_at: 'x' }),
      ev(3, 'sampling.resolved', { approval_id: 'apr_s', status: 'completed', model: 'domain-local' }),
      ev(4, 'elicitation.required', { approval_id: 'apr_e', server: 'internal-orders', params: { message: 'O-2404 반품', requestedSchema: { properties: {} } } }),
      ev(5, 'elicitation.resolved', { approval_id: 'apr_e', status: 'approved', action: 'accept' })], st)
    expect(st.consents.apr_s).toMatchObject({ status: 'completed', draft: '[domain-7b] 분류' })
    expect(st.consents.apr_e).toMatchObject({ kind: 'elicitation', status: 'approved', action: 'accept' })
    expect(st.lastEventId).toBe(5)
  })
  it('시간 연장 이벤트 → 카드 기한·횟수 갱신 (WCAG 2.2.1, D14-③)', () => {
    const st = replay([
      ev(1, 'sampling.required', { approval_id: 'apr_x', server: 'internal-orders', params: {}, expires_at: '2026-10-08T00:01:00Z', extend_step_s: 60, max_extensions: 10 }),
      ev(2, 'sampling.extended', { approval_id: 'apr_x', expires_at: '2026-10-08T00:02:00Z', extensions: 1 }),
    ])
    expect(st.consents.apr_x).toMatchObject({ expiresAt: '2026-10-08T00:02:00Z', extensions: 1, extendStep: 60, maxExtensions: 10 })
  })
})

describe('재계획·조건·추가 입력 (SCR-C02·C05, FR-W02)', () => {
  it('plan.revised → 이전 단계 replanned, 새 단계 round 표시 · 조건 건너뜀 사유', () => {
    const st = replay([
      ev(1, 'plan', { steps: [{ id: 's1', agent: 'retriever', tool: 'jira.no_such_tool' }, { id: 's2', agent: 'summarize', depends_on: ['s1'], when: 's1.count > 0' }, { id: 's3', agent: 'retriever', kb: 'ops-runbooks' }], source: 'template' }),
      ev(2, 'step.failed', { step: 's1', node: 'retriever', error: '도구 없음' }),
      ev(3, 'step.skipped', { step: 's2', reason: 'condition', when: 's1.count > 0' }),
      ev(4, 'plan.revised', { round: 1, source: 'fallback', reason: 's1: 도구 없음', previous: ['s1'], steps: [{ id: 'r1_alt', agent: 'retriever', tool: 'confluence.search_pages' }] }),
      ev(5, 'step.completed', { step: 'r1_alt', node: 'retriever', preview: 'ok' }),
    ])
    expect(st.steps.s1.status).toBe('replanned')
    expect(st.steps.s2).toMatchObject({ status: 'skipped', error: '조건 불충족: s1.count > 0', when: 's1.count > 0' })
    expect(st.steps.s3.tool).toBe('rag:ops-runbooks')
    expect(st.steps.r1_alt).toMatchObject({ round: 1, status: 'succeeded' })
    expect(st.revisions).toEqual([{ round: 1, source: 'fallback', reason: 's1: 도구 없음', previous: ['s1'], steps: ['r1_alt'] }])
    expect(progress(st)).toMatchObject({ all: 4, done: 3 })
  })

  it('input.required → waiting_input, input.resolved → 다시 실행', () => {
    let st = replay([
      ev(1, 'plan', { steps: [{ id: 's2', agent: 'remote', remote_agent: 'observability-analyst' }], source: 'template' }),
      ev(2, 'input.required', { step: 's2', agent: 'observability-analyst', task_id: 't1', context_id: 'c1', question: '어떤 서비스?' }),
      ev(3, 'run.waiting', { approvals: [], inputs: ['s2'] }),
    ])
    expect(st.status).toBe('waiting_input')
    expect(st.inputs.s2).toMatchObject({ taskId: 't1', contextId: 'c1', question: '어떤 서비스?', status: 'pending' })
    expect(st.steps.s2.status).toBe('waiting_input')
    st = replay([ev(4, 'input.resolved', { step: 's2' })], st)
    expect(st.inputs.s2.status).toBe('answered')
    expect(st).toMatchObject({ status: 'running' })
    expect(st.steps.s2.status).toBe('pending')
  })
})
