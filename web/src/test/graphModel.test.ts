import { addStep, connect, edgesOf, layout, removeStep, setStep, type Graph } from '@/pages/agents/graphModel'

const g: Graph = {
  planner: { mode: 'llm' }, workers: { retriever: { tools: ['jira.search_issues'] }, summarize: { model: 'auto' } },
  default_plan: [{ id: 's1', agent: 'retriever', tool: 'jira.search_issues' }, { id: 's2', agent: 'retriever' }, { id: 's3', agent: 'summarize', depends_on: ['s1', 's2'] }],
}

describe('graphModel', () => {
  it('의존 깊이로 열을 배치하고 Planner 를 세로 가운데에 둔다', () => {
    const p = layout(g.default_plan)
    expect(p.s1.x).toBe(p.s2.x)
    expect(p.s3.x).toBeGreaterThan(p.s1.x)
    expect(p.planner.y).toBe((p.s1.y + p.s2.y) / 2)
  })
  it('간선: 의존 없는 단계는 Planner 에서 출발', () => {
    expect(edgesOf(g.default_plan).map((e) => `${e.source}>${e.target}`)).toEqual(['planner>s1', 'planner>s2', 's1>s3', 's2>s3'])
  })
  it('뒤 단계 → 앞 단계 연결(순환)은 거부', () => {
    expect(connect(g, 's3', 's1')).toBeNull()
    expect(connect(g, 's1', 's1')).toBeNull()
    expect(connect(g, 's1', 's2')!.default_plan[1].depends_on).toEqual(['s1'])
  })
  it('노드 삭제 시 다른 단계의 의존도 정리', () => {
    expect(removeStep(g, 's1').default_plan.find((s) => s.id === 's3')!.depends_on).toEqual(['s2'])
  })
  it('도구 지정 시 workers 허용 목록에 추가, 새 노드는 고유 id', () => {
    const n = setStep(addStep(g, 'executor', 's3'), 's4', { tool: 'confluence.create_page' })
    expect(n.default_plan.at(-1)).toMatchObject({ id: 's4', agent: 'executor', depends_on: ['s3'], tool: 'confluence.create_page' })
    expect(n.workers.executor.tools).toEqual(['confluence.create_page'])
    expect(g.default_plan).toHaveLength(3) // 원본 불변
  })
})
