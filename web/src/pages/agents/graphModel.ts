// 오케스트레이션 그래프(YAML 정의) ↔ 캔버스 변환 — 노드 = Planner + default_plan 단계, 간선 = depends_on
export type PlanStep = { id: string; agent: string; tool?: string; remote_agent?: string; args?: Record<string, unknown>; depends_on?: string[]
  when?: string; kb?: string; top_k?: number; retry?: { max_attempts?: number }; timeout_s?: number }
export type Graph = {
  version?: number; planner: { model?: string; mode?: string; max_steps?: number; [k: string]: any }
  workers: Record<string, any>; fanout?: { parallel?: boolean; join?: string; required?: string[]; [k: string]: any }
  retry?: { max_attempts?: number; backoff_ms?: number; backoff?: string }; fallbacks?: { tool_429?: string }
  on_failure?: string; data_class?: string; default_plan: PlanStep[]
}

export const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v))

export function depthOf(plan: PlanStep[]) {
  const d: Record<string, number> = {}
  for (const s of plan) d[s.id] = 1 + Math.max(0, ...(s.depends_on ?? []).map((x) => d[x] ?? 0))
  return d
}

export function layout(plan: PlanStep[]) {
  const depth = depthOf(plan)
  const rows: Record<number, number> = {}
  const pos: Record<string, { x: number; y: number }> = { planner: { x: 0, y: 0 } }
  for (const s of plan) {
    const col = depth[s.id]
    const row = (rows[col] = (rows[col] ?? -1) + 1)
    pos[s.id] = { x: col * 240, y: row * 110 }
  }
  const maxRows = Math.max(1, ...Object.values(rows).map((r) => r + 1))
  pos.planner.y = ((maxRows - 1) * 110) / 2
  return pos
}

export function edgesOf(plan: PlanStep[]) {
  const e: { id: string; source: string; target: string }[] = []
  for (const s of plan) {
    const deps = s.depends_on ?? []
    if (!deps.length) e.push({ id: `planner-${s.id}`, source: 'planner', target: s.id })
    for (const d of deps) e.push({ id: `${d}-${s.id}`, source: d, target: s.id })
  }
  return e
}

export function nextId(plan: PlanStep[]) {
  let i = plan.length + 1
  while (plan.some((s) => s.id === `s${i}`)) i++
  return `s${i}`
}

export function addStep(g: Graph, agent: string, after?: string): Graph {
  const n = clone(g)
  const id = nextId(n.default_plan)
  n.default_plan.push({ id, agent, ...(after ? { depends_on: [after] } : {}) })
  n.workers[agent] ??= agent === 'remote' ? { agents: [] } : agent === 'summarize' ? { model: 'auto', cite: true } : { tools: [] }
  return n
}

export function removeStep(g: Graph, id: string): Graph {
  const n = clone(g)
  n.default_plan = n.default_plan.filter((s) => s.id !== id).map((s) => ({ ...s, depends_on: s.depends_on?.filter((d) => d !== id) }))
  return n
}

// 순환 방지: target 이 source 의 앞 단계(조상)면 연결 거부
export function connect(g: Graph, source: string, target: string): Graph | null {
  if (source === target || target === 'planner') return null
  const n = clone(g)
  const ids = n.default_plan.map((s) => s.id)
  const t = n.default_plan.find((s) => s.id === target)
  if (!t) return null
  if (source === 'planner') {
    t.depends_on = []
    return n
  }
  if (ids.indexOf(source) > ids.indexOf(target)) return null
  t.depends_on = Array.from(new Set([...(t.depends_on ?? []), source]))
  return n
}

export function setStep(g: Graph, id: string, patch: Partial<PlanStep>): Graph {
  const n = clone(g)
  const s = n.default_plan.find((x) => x.id === id)
  if (!s) return n
  Object.assign(s, patch)
  if (patch.tool) {
    const w = (n.workers[s.agent] ??= { tools: [] })
    w.tools = Array.from(new Set([...(w.tools ?? []), patch.tool]))
  }
  if (patch.remote_agent) {
    const w = (n.workers.remote ??= { agents: [] })
    w.agents = Array.from(new Set([...(w.agents ?? []), patch.remote_agent]))
  }
  return n
}
