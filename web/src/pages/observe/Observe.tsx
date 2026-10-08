// SCR-K02 트레이스 탐색 · K03 트레이스 상세(워터폴) · K04 로그 검색 · K06 Usage & Cost · K08 세션 리플레이 링크 · K09 감사 로그·해시 체인 · K10 규제 리포트
import { createColumnHelper } from '@tanstack/react-table'
import { useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, CodeBlock, Drawer, ErrorBox, Gate, Input, Kpi, Kv, Loading, PageHeader, Select, StatusBadge, Tabs, TraceLink, cx, useToast } from '@/components/ui'
import { buildUrl, get, request, type Json } from '@/lib/api'
import { can, useAuth } from '@/lib/auth'
import { fmtMs, fmtNum, fmtTime, fmtUsd } from '@/lib/format'
import { send, useGet } from '@/lib/query'

const col = createColumnHelper<Json>()
const SVC_COLOR = ['#3F51E5', '#7048E8', '#0C8599', '#C2410C', '#2F9E44', '#E8A317']

export type BarDatum = { key: string; value: number; tip: string; alert?: boolean }
/** CMP-15 간이 막대 차트 (인라인 CSS) — 막대마다 툴팁, onPick 이 있으면 클릭으로 구간 선택 */
export function MiniBars({ data, label, height = 72, onPick, selected }: { data: BarDatum[]; label: string; height?: number; onPick?: (key: string) => void; selected?: string | null }) {
  const max = Math.max(1, ...data.map((d) => d.value))
  return (
    <div>
      {/* 막대는 포인터 단축(작은 목표) — 키보드·보조기기는 아래 구간 선택으로 같은 기능 (WCAG 2.5.8 동등 컨트롤 예외) */}
      <div role="img" aria-label={label} className="flex items-end gap-0.5 border-b border-line" style={{ height }}>
        {data.map((d) => (
          <span key={d.key} title={d.tip} aria-hidden onClick={() => onPick?.(d.key)}
            className={cx('flex h-full min-w-0 flex-1 items-end', onPick ? 'cursor-pointer' : 'cursor-default', selected && selected !== d.key && 'opacity-40')}>
            <span className={cx('block w-full rounded-t', d.alert ? 'bg-danger' : 'bg-primary/70')} style={{ height: `${d.value ? Math.max((d.value / max) * 100, 3) : 0}%` }} />
          </span>
        ))}
      </div>
      {onPick && (
        <select aria-label={`${label} 구간`} className="mt-1 h-7 rounded-md border border-line bg-surface px-2 text-xs" value={selected ?? ''}
          onChange={(e) => onPick(e.target.value || (selected ?? ''))}>
          <option value="">구간 선택…</option>{data.map((d) => <option key={d.key} value={d.key}>{d.tip}</option>)}
        </select>
      )}
    </div>
  )
}

/** CMP-15 선 차트 (인라인 SVG) — 점선 목표선, 구간별 툴팁·클릭 */
export function LineChart({ points, label, target, height = 160, fmt = (v: number) => String(v), onPick }: {
  points: { t: string; v: number | null }[]; label: string; target?: number | null; height?: number; fmt?: (v: number) => string; onPick?: (i: number) => void
}) {
  const W = 600, H = height, P = 8
  const max = Math.max(1, target ?? 0, ...points.map((p) => p.v ?? 0)) * 1.1
  const x = (i: number) => P + (i * (W - 2 * P)) / Math.max(points.length - 1, 1)
  const y = (v: number) => H - P - (v / max) * (H - 2 * P)
  const line = points.flatMap((p, i) => (p.v == null ? [] : [`${x(i)},${y(p.v)}`])).join(' ')
  const slot = W / Math.max(points.length, 1)
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
      <line x1={0} x2={W} y1={H - P} y2={H - P} className="stroke-line" vectorEffect="non-scaling-stroke" />
      {target != null && <line x1={0} x2={W} y1={y(target)} y2={y(target)} className="stroke-danger" strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />}
      <polyline points={line} fill="none" className="stroke-primary" strokeWidth={2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      {points.map((p, i) => (
        <rect key={i} x={x(i) - slot / 2} y={0} width={slot} height={H} fill="transparent" className={onPick ? 'cursor-pointer hover:fill-primary/10' : undefined} onClick={() => onPick?.(i)}>
          <title>{`${fmtTime(p.t, true)} · ${p.v == null ? '데이터 없음' : fmt(p.v)}`}</title>
        </rect>
      ))}
    </svg>
  )
}

export async function download(path: string, name: string) {
  const res = await request(path)
  const url = URL.createObjectURL(await res.blob())
  const a = Object.assign(document.createElement('a'), { href: url, download: name })
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const HEALTH_RE = /^GET \/(health|healthz|metrics)/

/** 지연 분포 (SCR-K02 ③) — 점 = trace(적색 = 오류), 드래그로 구간 선택 → 표 필터 */
function LatencyScatter({ items, range, onRange, onOpen }: { items: Json[]; range: [number, number] | null; onRange: (r: [number, number]) => void; onOpen: (id: string) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<[number, number] | null>(null)
  const ts = items.map((t) => new Date(t.start).getTime())
  const t0 = Math.min(...ts), span = Math.max(Math.max(...ts) - t0, 1)
  const maxD = Math.max(1, ...items.map((t) => t.duration_ms ?? 0))
  const frac = (e: MouseEvent) => {
    const r = ref.current!.getBoundingClientRect()
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))
  }
  const box = drag ? [Math.min(...drag), Math.max(...drag)] : range ? [(range[0] - t0) / span, (range[1] - t0) / span] : null
  if (!items.length) return <div className="py-6 text-center text-xs text-fg-muted">표시할 trace 가 없습니다.</div>
  return (
    <div ref={ref} role="group" aria-label="지연 분포" className="relative h-40 select-none border-b border-l border-line"
      onMouseDown={(e) => { if (!(e.target as HTMLElement).closest('[data-trace-dot]')) { const f = frac(e); setDrag([f, f]) } }}
      onMouseMove={(e) => drag && setDrag([drag[0], frac(e)])}
      onMouseUp={() => {
        if (drag && Math.abs(drag[1] - drag[0]) > 0.01) onRange([t0 + Math.min(...drag) * span, t0 + Math.max(...drag) * span])
        setDrag(null)
      }}
      onMouseLeave={() => setDrag(null)}>
      <span className="pointer-events-none absolute top-0 left-1 text-[10px] text-fg-muted">{fmtMs(maxD)}</span>
      {box && <div className="pointer-events-none absolute inset-y-0 border border-dashed border-primary bg-primary/10" style={{ left: `${Math.max(box[0], 0) * 100}%`, width: `${Math.max(Math.min(box[1], 1) - Math.max(box[0], 0), 0) * 100}%` }} />}
      {items.map((t, i) => (
        // 점은 포인터 단축 — 키보드·보조기기는 아래 trace 표의 행이 같은 기능 (WCAG 2.5.8 동등 컨트롤 예외)
        <span key={t.trace_id} aria-hidden data-trace-dot title={`${fmtTime(t.start, true)} · ${fmtMs(t.duration_ms)} · ${t.root ?? ''}${t.errors ? ` · 오류 ${t.errors}` : ''}`}
          onClick={() => onOpen(t.trace_id)}
          className="absolute grid size-3 -translate-x-1/2 translate-y-1/2 cursor-pointer place-items-center"
          style={{ left: `${((ts[i] - t0) / span) * 100}%`, bottom: `${((t.duration_ms ?? 0) / maxD) * 100}%` }}>
          <span className={cx('size-2 rounded-full ring-2 ring-surface', t.errors ? 'bg-danger' : 'bg-primary')} />
        </span>
      ))}
    </div>
  )
}

export function Traces() {
  const nav = useNavigate()
  const [sp, setSp] = useSearchParams()
  const since = Number(sp.get('since') || 60)
  const errorsOnly = sp.get('err') === '1'
  const minMs = Number(sp.get('min') || 0)
  const user = sp.get('user') || ''
  const tool = sp.get('tool') || ''
  const range: [number, number] | null = sp.get('from') && sp.get('to') ? [Number(sp.get('from')), Number(sp.get('to'))] : null
  // 조건은 URL 에 저장 (공유 가능, SCR-K02 ②)
  const upd = (kv: Record<string, string | undefined>) => {
    const n = new URLSearchParams(sp)
    for (const [k, v] of Object.entries(kv)) if (v) n.set(k, v); else n.delete(k)
    setSp(n)
  }
  const commit = (k: string, cur: string) => ({
    defaultValue: cur,
    onKeyDown: (e: KeyboardEvent<HTMLInputElement>) => e.key === 'Enter' && upd({ [k]: e.currentTarget.value.trim() || undefined }),
    onBlur: (e: { target: HTMLInputElement }) => e.target.value.trim() !== cur && upd({ [k]: e.target.value.trim() || undefined }),
  })
  const [hideHealth, setHideHealth] = useState(true)
  const q = useGet('/api/v1/traces', { since_minutes: since, errors_only: errorsOnly || undefined, limit: 500, q: sp.get('q') || undefined, user: user || undefined, tool: tool || undefined }, { refetchInterval: 20_000 })
  const shown = (q.data?.items ?? []).filter((t: Json) => (!hideHealth || !HEALTH_RE.test(t.root ?? '')) && (t.duration_ms ?? 0) >= minMs)
  const items = range ? shown.filter((t: Json) => { const s = new Date(t.start).getTime(); return s >= range[0] && s <= range[1] }) : shown
  const columns = [
    col.accessor('start', { header: '시작', cell: (c) => fmtTime(c.getValue(), true) }),
    col.accessor('root', { header: '루트 스팬', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
    col.accessor('services', { header: '서비스', enableSorting: false, cell: (c) => (c.getValue() ?? []).map((s: string) => <Badge key={s} className="mr-1">{s.replace('agentnexus-', '')}</Badge>) }),
    col.accessor('spans', { header: '스팬' }),
    col.accessor('duration_ms', { header: '지연', cell: (c) => fmtMs(c.getValue()) }),
    col.accessor('errors', { header: '오류', cell: (c) => c.getValue() ? <Badge tone="danger">{c.getValue()}</Badge> : '—' }),
    col.accessor('user', { header: '사용자', cell: (c) => c.getValue() || '—' }),
    col.accessor('trace_id', { header: 'trace_id', cell: (c) => <span className="mono text-[11px] text-fg-muted">{c.getValue()}</span> }),
  ]
  return (
    <>
      <PageHeader title="트레이스 탐색" crumbs={['관측·감사', '트레이스']} desc="W3C trace context — 게이트웨이·MCP 서버·원격 에이전트가 하나의 trace_id 로 묶입니다 (OTel GenAI 규약)." />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input aria-label="trace_id 또는 스팬 이름" placeholder="trace_id 또는 스팬 이름" defaultValue={sp.get('q') ?? ''} onKeyDown={(e) => {
          if (e.key !== 'Enter') return
          const v = (e.target as HTMLInputElement).value.trim()
          if (/^[0-9a-f]{32}$/.test(v)) nav(`/console/observe/traces/${v}`)
          else upd({ q: v || undefined })
        }} className="w-72" />
        <Input aria-label="사용자" placeholder="사용자 (usr_…)" {...commit('user', user)} className="w-36" />
        <Input aria-label="도구" placeholder="도구 (jira.search…)" {...commit('tool', tool)} className="w-40" />
        <Select aria-label="기간" value={since} onChange={(e) => upd({ since: e.target.value, from: undefined, to: undefined })} className="w-28"><option value={15}>15분</option><option value={60}>1시간</option><option value={1440}>24시간</option><option value={10080}>7일</option></Select>
        <Select aria-label="지연 하한" value={minMs} onChange={(e) => upd({ min: e.target.value === '0' ? undefined : e.target.value })} className="w-28"><option value={0}>지연 전체</option><option value={1000}>지연 ≥ 1s</option><option value={3000}>지연 ≥ 3s</option><option value={10000}>지연 ≥ 10s</option></Select>
        <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={errorsOnly} onChange={(e) => upd({ err: e.target.checked ? '1' : undefined })} />오류만</label>
        <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={hideHealth} onChange={(e) => setHideHealth(e.target.checked)} />헬스·메트릭 숨김</label>
        <span className="text-xs text-fg-muted">조건은 URL 에 저장 · 공유 가능</span>
      </div>
      {q.error && <ErrorBox error={q.error} />}
      <Card title="지연 분포 (점 = trace · 적색 = 오류)" className="mb-3"
        actions={range ? <><span className="text-xs text-fg-muted">선택 구간 {fmtTime(new Date(range[0]).toISOString())}–{fmtTime(new Date(range[1]).toISOString())} · {items.length}건</span><Button size="sm" onClick={() => upd({ from: undefined, to: undefined })}>선택 해제</Button></> : <span className="text-xs text-fg-muted">드래그로 구간 선택 → 표 필터</span>}>
        <LatencyScatter items={shown} range={range} onRange={([a, b]) => upd({ from: String(Math.floor(a)), to: String(Math.ceil(b)) })} onOpen={(id) => nav(`/console/observe/traces/${id}`)} />
      </Card>
      {q.isLoading ? <Loading /> : <DataTable data={items} columns={columns} filter={false} onRowClick={(r) => nav(`/console/observe/traces/${r.trace_id}`)} empty="해당 기간에 트레이스가 없습니다." />}
      <p className="mt-2 text-xs text-fg-muted">사용자·도구 필터는 게이트웨이 사용 기록(UsageRecord)의 trace_id 기준 · 사용자 이름·프롬프트 일부는 역할에 따라 마스킹됩니다 (MSG-021).</p>
    </>
  )
}

export function Trace() {
  const { id = '' } = useParams()
  const q = useGet(`/api/v1/traces/${id}`)
  const [sel, setSel] = useState<Json | null>(null)
  const t = q.data
  const svcColor = useMemo(() => Object.fromEntries((t?.services ?? []).map((s: string, i: number) => [s, SVC_COLOR[i % SVC_COLOR.length]])), [t])
  const rows = useMemo(() => {
    if (!t) return []
    const kids: Record<string, Json[]> = {}
    const ids = new Set(t.spans.map((s: Json) => s.span_id))
    for (const s of t.spans) (kids[s.parent_id && ids.has(s.parent_id) ? s.parent_id : 'root'] ??= []).push(s)
    const out: (Json & { depth: number })[] = []
    const walk = (pid: string, depth: number) => (kids[pid] ?? []).sort((a, b) => a.start_offset_ms - b.start_offset_ms).forEach((s) => { out.push({ ...s, depth }); walk(s.span_id, depth + 1) })
    walk('root', 0)
    return out
  }, [t])
  // 세션 리플레이(K08) 연결 — 스팬의 anx.run_id → 실행의 session_id
  const runId: string | undefined = t?.spans.find((s: Json) => s.attributes?.['anx.run_id'])?.attributes['anx.run_id']
  const run = useGet(runId ? `/api/v1/runs/${runId}` : null, undefined, { retry: false })
  if (q.error) return <ErrorBox error={q.error} />
  if (!t) return <Loading rows={8} />
  const total = Math.max(t.duration_ms, 1)
  return (
    <>
      <PageHeader title="트레이스 상세 (워터폴)" crumbs={['관측·감사', '트레이스', id.slice(0, 8)]} desc={<span className="mono text-xs">{id}</span>}
        actions={run.data?.session_id && <Link to={`/console/observe/replay/${run.data.session_id}`} className="text-sm text-primary hover:underline">세션 리플레이 (K08) →</Link>} />
      <div className="mb-4 grid grid-cols-5 gap-3">
        <Kpi label="전체 지연" value={fmtMs(t.duration_ms)} /><Kpi label="스팬" value={t.spans.length} /><Kpi label="GenAI 스팬" value={t.gen_ai_spans} />
        <Kpi label="토큰" value={fmtNum(t.tokens)} /><Kpi label="비용" value={fmtUsd(t.cost_usd)} />
      </div>
      <div className="mb-2 flex flex-wrap gap-2 text-xs">{(t.services ?? []).map((s: string) => <span key={s} className="flex items-center gap-1"><span className="size-2.5 rounded-sm" style={{ background: svcColor[s] }} />{s}</span>)}</div>
      <Card bodyClass="p-0">
        <div className="max-h-[600px] overflow-auto">
          {rows.map((s) => (
            <button key={s.span_id} type="button" onClick={() => setSel(s)} className={cx('grid w-full grid-cols-[minmax(280px,40%)_1fr_70px] items-center gap-2 border-b border-line px-3 py-1 text-left text-xs hover:bg-surface-2', sel?.span_id === s.span_id && 'bg-primary-soft')}>
              <span className="truncate" style={{ paddingLeft: s.depth * 14 }} title={s.name}>
                {s.status === 'error' && <span className="mr-1 text-danger">●</span>}<span className="mono">{s.name}</span>
                {s.attributes?.['gen_ai.request.model'] && <Badge tone="model" className="ml-1">{s.attributes['gen_ai.request.model']}</Badge>}
              </span>
              <span className="relative h-3.5 rounded bg-surface-2">
                <span className="absolute top-0 h-3.5 rounded" style={{ left: `${(s.start_offset_ms / total) * 100}%`, width: `${Math.max((s.duration_ms / total) * 100, 0.4)}%`, background: s.status === 'error' ? '#C92A2A' : svcColor[s.service] }} />
              </span>
              <span className="text-right tabular-nums">{fmtMs(s.duration_ms)}</span>
            </button>
          ))}
        </div>
      </Card>
      <Drawer open={!!sel} onClose={() => setSel(null)} title={sel?.name ?? ''} width="w-[520px]">
        {sel && <div className="space-y-3 text-sm">
          <Kv rows={[['서비스', sel.service], ['종류', sel.kind], ['상태', <StatusBadge status={sel.status} />], ['시작 오프셋', fmtMs(sel.start_offset_ms)], ['지연', fmtMs(sel.duration_ms)], ['span_id', <span className="mono text-xs">{sel.span_id}</span>]]} />
          <div className="text-xs font-medium text-fg-muted">속성 (PII 마스킹 적용)</div><CodeBlock value={sel.attributes} maxH="max-h-80" />
          {sel.events?.length > 0 && <><div className="text-xs font-medium text-fg-muted">이벤트</div><CodeBlock value={sel.events} maxH="max-h-48" /></>}
        </div>}
      </Drawer>
    </>
  )
}

const LEVEL: Record<string, 'danger' | 'warn' | 'muted' | 'primary'> = { ERROR: 'danger', CRITICAL: 'danger', WARNING: 'warn', INFO: 'primary', DEBUG: 'muted' }

// 저장 단계 마스킹 표지 (guards.mask_pii) — [MASKED:email] · [MASKED] (사용자 규칙)
const MASK_RE = /\[MASKED(?::([a-z_]+))?\]/g
const MASK_LABEL: Record<string, string> = { email: '이메일', phone: '전화번호', rrn: '주민번호', card: '카드번호', custom: '사용자 규칙', jwt: '시크릿·JWT' }
const masksOf = (e: Json) => [...new Set([...`${e.message ?? ''} ${JSON.stringify(e.data ?? {})}`.matchAll(MASK_RE)].map((m) => m[1] || 'custom'))]
// 빈도 막대 구간 — 1시간: 1분 · 24시간: 30분 · 7일: 4시간
const LOG_BUCKET_S: Record<number, number> = { 60: 60, 1440: 1800, 10080: 14400 }

function Retention() {
  const { me } = useAuth()
  const s = useGet(can(me, 'admin:read') ? '/api/v1/admin/settings' : null, undefined, { retry: false })
  const r = s.data?.retention
  return (
    <Card title="보존 정책" className="mt-4">
      {r ? (
        <Kv rows={[['로그', `${r.logs_days}일`], ['스팬(트레이스)', `${r.spans_days}일`], ['사용 기록', `${r.usage_days}일`], ['원문 PII', '암호문으로만 보관 — 사유 입력 후 30분 열람'], ['감사 로그', `${r.audit_days}일 · 별도 해시 체인 (K09)`]]} />
      ) : <p className="text-xs text-fg-muted">로그 유형별 보존 기간은 일반 설정(L06)에서 관리하며, 만료되면 자동 삭제됩니다. 감사 로그는 별도로 보존됩니다 (K09).</p>}
      {can(me, 'admin:write') && <Link to="/console/admin" className="mt-2 inline-block text-xs text-primary hover:underline">변경 → 일반 설정 (L06)</Link>}
    </Card>
  )
}

export function Logs() {
  const toast = useToast()
  const [level, setLevel] = useState('')
  const [component, setComponent] = useState('')
  const [qq, setQ] = useState('')
  const [since, setSince] = useState(1440)
  const q = useGet('/api/v1/logs', { level: level || undefined, component: component || undefined, q: qq || undefined, since_minutes: since, limit: 2000 }, { refetchInterval: 20_000 })
  const [sel, setSel] = useState<Json | null>(null)
  const [maskF, setMaskF] = useState('')
  const [bucket, setBucket] = useState<string | null>(null)
  const [grant, setGrant] = useState<string | null>(null)
  const [raw, setRaw] = useState<Record<number, Json | null>>({})
  const all: Json[] = q.data?.items ?? []
  const maskCounts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const e of all) for (const m of masksOf(e)) c[m] = (c[m] ?? 0) + 1
    return c
  }, [all])
  const size = (LOG_BUCKET_S[since] ?? 1800) * 1000
  const bars = useMemo(() => {
    const end = q.dataUpdatedAt || Date.now()
    const n = Math.ceil((since * 60_000) / size)
    const start = end - n * size
    const b = Array.from({ length: n }, (_, i) => ({ t: start + i * size, n: 0, err: 0 }))
    for (const e of all) {
      const i = Math.floor((new Date(e.ts).getTime() - start) / size)
      if (i >= 0 && i < n) { b[i].n++; if (LEVEL[e.level] === 'danger') b[i].err++ }
    }
    return b.map((x) => ({ key: String(x.t), value: x.n, alert: x.err > 0, tip: `${fmtTime(new Date(x.t).toISOString(), since > 1440)} · ${x.n}건${x.err ? ` · 오류 ${x.err}` : ''}` }))
  }, [all, since, size, q.dataUpdatedAt])
  const items = all.filter((e) => (!maskF || masksOf(e).includes(maskF)) && (!bucket || (() => { const t = new Date(e.ts).getTime(); return t >= Number(bucket) && t < Number(bucket) + size })()))
  const grantActive = !!grant && new Date(grant).getTime() > Date.now()
  // 마스킹 해제 (SCR-K04 ⑦) — 사유 필수(MSG-003) · 30분 · 시크릿은 계속 가림 · 감사 기록
  const unmask = async (e: Json) => {
    let reason = ''
    if (!grantActive) {
      for (;;) {
        const v = window.prompt('마스킹 해제 사유를 입력하세요 (필수 4자 이상, MSG-003) — 30분 동안 원문(PII)을 보며, 시크릿은 계속 가려집니다. 해제는 감사에 기록됩니다.')
        if (v === null) return
        if (v.trim().length >= 4) { reason = v.trim(); break }
      }
    }
    try {
      const r = await send<Json>('POST', '/api/v1/logs/unmask', { ids: [e.id], reason })
      setGrant(r.until)
      setRaw((x) => ({ ...x, [e.id]: r.items?.[0]?.raw ?? null }))
      toast('info', `마스킹 해제 — ${fmtTime(r.until)} 까지 열람 (감사 기록)`)
    } catch (err: any) {
      toast('error', err.message)
    }
  }
  const columns = [
    col.accessor('ts', { header: '시각', cell: (c) => <span className="tabular-nums">{fmtTime(c.getValue(), true)}</span> }),
    col.accessor('level', { header: '레벨', cell: (c) => <Badge tone={LEVEL[c.getValue()] ?? 'muted'}>{c.getValue()}</Badge> }),
    col.accessor('component', { header: '구성요소' }),
    col.accessor('message', { header: '메시지', cell: (c) => <span className="line-clamp-1 text-xs">{c.getValue()}</span> }),
    col.accessor('trace_id', { header: 'trace', cell: (c) => <span onClick={(e) => e.stopPropagation()}><TraceLink id={c.getValue()} /></span> }),
  ]
  return (
    <>
      <PageHeader title="로그 검색" crumbs={['관측·감사', '로그']} desc="구조화 JSON 로그 — PII·시크릿은 저장 전에 마스킹되고, 보존 기간이 지나면 삭제됩니다 (FR-O02)." />
      <div className="mb-3 flex flex-wrap gap-2">
        <Select aria-label="레벨" value={level} onChange={(e) => setLevel(e.target.value)} className="w-32"><option value="">모든 레벨</option>{['ERROR', 'WARNING', 'INFO', 'DEBUG'].map((l) => <option key={l}>{l}</option>)}</Select>
        <Input aria-label="구성요소" placeholder="구성요소 (gateway·host…)" value={component} onChange={(e) => setComponent(e.target.value)} className="w-48" />
        <Input aria-label="본문 검색" placeholder="본문 검색" value={qq} onChange={(e) => setQ(e.target.value)} className="w-64" />
        <Select aria-label="기간" value={since} onChange={(e) => { setSince(Number(e.target.value)); setBucket(null) }} className="w-28"><option value={60}>1시간</option><option value={1440}>24시간</option><option value={10080}>7일</option></Select>
      </div>
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs" role="group" aria-label="마스킹 유형">
        <Badge tone="ok">PII 마스킹 적용 중</Badge>
        {Object.entries(maskCounts).map(([k, n]) => (
          <button key={k} type="button" aria-pressed={maskF === k} onClick={() => setMaskF(maskF === k ? '' : k)}
            className={cx('rounded-full border px-2 py-0.5', maskF === k ? 'border-primary bg-primary-soft text-primary' : 'border-line text-fg-muted hover:bg-surface-2')}>{MASK_LABEL[k] ?? k} {n}</button>
        ))}
        {!Object.keys(maskCounts).length && <span className="text-fg-muted">불러온 로그에 마스킹된 값이 없습니다</span>}
        <span className="ml-auto text-fg-muted">규칙(이메일·전화·주민번호 등)은 일반 설정(L06) · MSG-021</span>
      </div>
      {q.error && <ErrorBox error={q.error} />}
      <Card title={`빈도 (불러온 ${fmtNum(all.length)}건 · 적색 = 오류 포함)`} className="mb-3" bodyClass="px-4 pt-3 pb-2"
        actions={bucket && <Button size="sm" onClick={() => setBucket(null)}>구간 해제</Button>}>
        <MiniBars data={bars} label="로그 빈도" height={56} selected={bucket} onPick={(k) => setBucket(bucket === k ? null : k)} />
      </Card>
      <DataTable data={items} columns={columns} filter={false} onRowClick={setSel} height={620} />
      <Retention />
      <Drawer open={!!sel} onClose={() => setSel(null)} title="로그 상세" width="w-[560px]">
        {sel && <div className="space-y-3">
          <CodeBlock value={sel} maxH="max-h-[50vh]" />
          <Gate perm="audit:read">{(ok, why) => {
            const has = masksOf(sel).length > 0
            return (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <Button size="sm" disabled={!ok || !has} title={why ?? (has ? undefined : '마스킹된 필드가 없습니다')} onClick={() => unmask(sel)}>마스킹 해제 요청</Button>
                <span className="text-fg-muted">보안·IAM 만 · 사유 입력 · 30분 한정 · 감사 기록</span>
                {grantActive && <Badge tone="warn">해제 유효 ~{fmtTime(grant)}</Badge>}
              </div>
            )
          }}</Gate>
          {sel.id in raw && (raw[sel.id] ? (
            <><div className="text-xs font-medium text-fg-muted">원문 (PII 해제 · 시크릿은 계속 마스킹)</div><CodeBlock value={raw[sel.id]} maxH="max-h-80" /></>
          ) : <p className="text-xs text-fg-muted">보관된 원문이 없습니다 — 원문 없는 필드는 해제할 수 없습니다.</p>)}
        </div>}
      </Drawer>
    </>
  )
}

/** 드릴다운 (SCR-K06 ⑤) — 팀 → 사용자 → 모델·도구 (cost:read) */
function Drilldown({ days }: { days: number }) {
  const [team, setTeam] = useState<string | null>(null)
  const [user, setUser] = useState<string | null>(null)
  const q = useGet('/api/v1/usage/drilldown', { days, team: team ?? undefined, user: user ?? undefined })
  const level: string = q.data?.level ?? 'team'
  const items: Json[] = q.data?.items ?? []
  const max = Math.max(0, ...items.map((i) => i.cost_usd)) || 1
  const pick = (k: string) => (level === 'team' ? setTeam(k) : level === 'user' ? setUser(k) : undefined)
  return (
    <Card title="드릴다운 — 팀 → 사용자 → 모델·도구" bodyClass="p-0">
      <nav aria-label="드릴다운 경로" className="flex flex-wrap items-center gap-1 border-b border-line px-4 py-2 text-xs">
        <button type="button" className="text-primary hover:underline" onClick={() => { setTeam(null); setUser(null) }}>전체 팀</button>
        {team && <>›<button type="button" className="text-primary hover:underline" onClick={() => setUser(null)}>{team}</button></>}
        {user && <>›<span>{user}</span></>}
      </nav>
      {q.error ? <div className="p-3"><ErrorBox error={q.error} /></div> : (
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-4 py-2">{{ team: '팀', user: '사용자', target: '모델·도구' }[level]}</th><th>호출</th><th>토큰</th><th className="w-48 pr-4">비용</th></tr></thead>
          <tbody>{items.map((r) => {
            const deeper = level !== 'target' && r.key !== '-'
            return (
              <tr key={r.key} className="border-t border-line">
                <td className="px-4 py-1.5">{deeper ? <button type="button" className="mono text-xs text-primary hover:underline" onClick={() => pick(r.key)}>{r.key} ›</button> : <span className="mono text-xs" title={r.key === '-' ? '미지정' : undefined}>{r.key}</span>}</td>
                <td className="tabular-nums">{fmtNum(r.calls)}</td><td className="tabular-nums">{fmtNum(r.tokens)}</td>
                <td className="pr-4"><div className="flex items-center gap-2"><span className="h-1.5 flex-1 rounded bg-surface-2"><span className="block h-1.5 rounded bg-primary" style={{ width: `${(r.cost_usd / max) * 100}%` }} /></span><span className="w-20 text-right text-xs tabular-nums">{fmtUsd(r.cost_usd)}</span></div></td>
              </tr>
            )
          })}</tbody>
        </table>
      )}
      {!q.error && !items.length && !q.isLoading && <p className="px-4 py-3 text-xs text-fg-muted">해당 기간 사용 기록이 없습니다.</p>}
    </Card>
  )
}

/** 공급자 단가표 (SCR-K06 ⑧) — USD / 1M 토큰. 편집은 cost:write(FinOps)·models:write · 이후 호출부터 새 단가로 집계, 변경은 감사 기록 */
function PriceTable() {
  const toast = useToast()
  const { me } = useAuth()
  const canModels = can(me, 'models:write')
  const q = useGet('/api/v1/models')
  const [edit, setEdit] = useState<Record<string, { price_in: string; price_out: string }> | null>(null)
  const [saving, setSaving] = useState(false)
  const models: Json[] = q.data?.items ?? []
  const start = () => setEdit(Object.fromEntries(models.map((m) => [m.id, { price_in: String(m.price_in), price_out: String(m.price_out) }])))
  const save = async () => {
    const changed = models.filter((m) => edit![m.id] && (Number(edit![m.id].price_in) !== m.price_in || Number(edit![m.id].price_out) !== m.price_out))
    if (!changed.length) return setEdit(null)
    if (changed.some((m) => [edit![m.id].price_in, edit![m.id].price_out].some((v) => v.trim() === '' || !(Number(v) >= 0)))) return toast('error', '단가는 0 이상의 숫자여야 합니다 (MSG-004)')
    if (!window.confirm(`단가 ${changed.length}건을 변경합니다 — 이후 호출부터 새 단가로 집계되며 감사에 기록됩니다. 계속할까요? (MSG-002)`)) return
    setSaving(true)
    try {
      for (const m of changed) await send('PATCH', `/api/v1/models/${m.id.split('/').map(encodeURIComponent).join('/')}`, { price_in: Number(edit![m.id].price_in), price_out: Number(edit![m.id].price_out) })
      toast('ok', `단가 ${changed.length}건 저장되었습니다 (MSG-001)`)
      setEdit(null)
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setSaving(false)
    }
  }
  const cell = (id: string, k: 'price_in' | 'price_out', v: number) => edit
    ? <Input aria-label={`${id} ${k === 'price_in' ? '입력' : '출력'} 단가`} type="number" min={0} step="any" value={edit[id]?.[k] ?? ''} onChange={(e) => setEdit({ ...edit, [id]: { ...edit[id], [k]: e.target.value } })} className="w-24 text-right" />
    : <span className="tabular-nums">{fmtUsd(v)}</span>
  return (
    <Card title="공급자 단가표 (USD / 1M 토큰)" bodyClass="p-0"
      actions={edit ? <><Button size="sm" onClick={() => setEdit(null)}>취소</Button><Button size="sm" variant="primary" loading={saving} onClick={save}>저장</Button></>
        : <Gate perm="cost:write">{(ok, why) => <Button size="sm" disabled={!(ok || canModels) || !models.length} title={ok || canModels ? undefined : why} onClick={start}>단가 편집</Button>}</Gate>}>
      {q.error ? <div className="p-3"><ErrorBox error={q.error} /></div> : (
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-4 py-2">모델</th><th>구분</th><th className="text-right">입력/1M</th><th className="pr-4 text-right">출력/1M</th></tr></thead>
          <tbody>{models.map((m) => (
            <tr key={m.id} className={cx('border-t border-line', !m.enabled && 'text-fg-muted')}>
              <td className="mono px-4 py-1.5 text-xs">{m.id}</td>
              <td><Badge tone={m.category === 'local' ? 'model' : 'muted'}>{m.category === 'local' ? '로컬 · GPU 원가' : m.category}</Badge></td>
              <td className="text-right">{cell(m.id, 'price_in', m.price_in)}</td><td className="pr-4 text-right">{cell(m.id, 'price_out', m.price_out)}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </Card>
  )
}

export function Cost() {
  const toast = useToast()
  const { me } = useAuth()
  const [group, setGroup] = useState('model')
  const [days, setDays] = useState(30)
  const sinceIso = useMemo(() => new Date(Date.now() - days * 86400_000).toISOString(), [days])
  const q = useGet('/api/v1/usage', { group_by: group, since: sinceIso })
  const daily = useGet('/api/v1/usage', { group_by: 'day', since: sinceIso })
  const status = useGet('/api/v1/gateway/limits/status')
  const items: Json[] = q.data?.items ?? []
  const series = [...(daily.data?.items ?? [])].sort((a: Json, b: Json) => a.key.localeCompare(b.key))
  const exportCsv = () => download(buildUrl('/api/v1/usage/export', { days }), `agentnexus-usage-${days}d.csv`)
    .then(() => toast('ok', 'CSV 내보내기 완료 — 감사에 기록됩니다'), (e) => toast('error', e.message))
  return (
    <>
      <PageHeader title="Usage & Cost" crumbs={['관측·감사', '비용']} desc={<>단가표 기준 집계(오차 ±2%) · 범위 <Badge>{q.data?.scope === 'all' ? '전체(cost:read)' : '본인 사용분'}</Badge></>}
        actions={<><Select aria-label="그룹" value={group} onChange={(e) => setGroup(e.target.value)} className="w-32">{['model', 'team', 'user', 'tool', 'agent', 'kind', 'provider'].map((g) => <option key={g}>{g}</option>)}</Select>
          <Select aria-label="기간" value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-28"><option value={1}>1일</option><option value={7}>7일</option><option value={30}>30일</option></Select>
          <Gate perm="cost:read">{(ok, why) => <Button disabled={!ok} title={why} onClick={exportCsv}>CSV 내보내기</Button>}</Gate></>} />
      {q.error && <ErrorBox error={q.error} />}
      <div className="mb-4 grid grid-cols-4 gap-3">
        <Kpi label="총 비용" value={fmtUsd(q.data?.total_cost_usd)} />
        <Kpi label="호출" value={fmtNum(items.reduce((s, r) => s + r.calls, 0))} />
        <Kpi label="토큰" value={fmtNum(items.reduce((s, r) => s + r.tokens_in + r.tokens_out, 0))} />
        <Kpi label="오류" value={fmtNum(items.reduce((s, r) => s + r.errors, 0))} />
      </div>
      <div className="mb-4 grid grid-cols-3 gap-4">
        <Card title="일별 비용" className="col-span-2">
          <div className="h-56"><ResponsiveContainer><BarChart data={series}><CartesianGrid strokeDasharray="3 3" stroke="var(--line)" /><XAxis dataKey="key" fontSize={11} stroke="var(--fg-muted)" /><YAxis fontSize={11} stroke="var(--fg-muted)" tickFormatter={(v) => `$${v}`} /><Tooltip formatter={(v) => fmtUsd(Number(v))} contentStyle={{ background: 'var(--surface)', border: '1px solid var(--line)', fontSize: 12 }} /><Bar dataKey="cost_usd" name="비용" fill="#3F51E5" isAnimationActive={false} /></BarChart></ResponsiveContainer></div>
        </Card>
        <Card title={`${group} 별 비중`}>
          <div className="h-56"><ResponsiveContainer><PieChart><Pie data={items.filter((r) => r.cost_usd > 0)} dataKey="cost_usd" nameKey="key" innerRadius={45} outerRadius={80}>{items.map((_, i) => <Cell key={i} fill={SVC_COLOR[i % SVC_COLOR.length]} />)}</Pie><Tooltip formatter={(v) => fmtUsd(Number(v))} /></PieChart></ResponsiveContainer></div>
        </Card>
      </div>
      <div className="mb-4 grid grid-cols-2 gap-4">
        {can(me, 'cost:read') ? <Drilldown days={days} /> : <Card title="드릴다운 — 팀 → 사용자 → 모델·도구"><p className="text-xs text-fg-muted">팀·사용자 드릴다운은 cost:read 권한이 필요합니다.</p></Card>}
        {can(me, 'models:read') && <PriceTable />}
      </div>
      {(status.data?.items?.length ?? 0) > 0 && (
        <Card title="예산 사용률 (SCR-K07)" className="mb-4" actions={<Link to="/console/observe/budgets" className="text-xs text-primary hover:underline">예산·경보 관리 →</Link>}>
          <ul className="space-y-2 text-sm">{status.data!.items.map((s: Json) => (
            <li key={s.limit_id + s.scope} className="grid grid-cols-[200px_1fr_140px] items-center gap-3"><span>{s.name} <span className="text-xs text-fg-muted">{s.scope}</span></span>
              <span className="h-2 rounded bg-surface-2"><span className={`block h-2 rounded ${s.pct >= 100 ? 'bg-danger' : s.pct >= 80 ? 'bg-warn' : 'bg-ok'}`} style={{ width: `${Math.min(s.pct ?? 0, 100)}%` }} /></span>
              <span className="text-right text-xs tabular-nums">{fmtUsd(s.used_usd)} / {fmtUsd(s.budget_usd)} ({s.pct}%)</span></li>))}</ul>
        </Card>
      )}
      <DataTable data={items} filter={false} columns={[
        col.accessor('key', { header: group, cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
        col.accessor('calls', { header: '호출' }), col.accessor('tokens_in', { header: '입력 토큰', cell: (c) => fmtNum(c.getValue()) }),
        col.accessor('tokens_out', { header: '출력 토큰', cell: (c) => fmtNum(c.getValue()) }), col.accessor('avg_latency_ms', { header: '평균 지연', cell: (c) => fmtMs(c.getValue()) }),
        col.accessor('errors', { header: '오류' }), col.accessor('cost_usd', { header: '비용', cell: (c) => <span className="tabular-nums">{fmtUsd(c.getValue())}</span> }),
      ]} />
    </>
  )
}

const sidOf = (e: Json): string | undefined => [e.target, e.data?.session_id, e.data?.session].find((x) => typeof x === 'string' && x.startsWith('ses_'))

function CopyHash({ value, toast }: { value?: string | null; toast: (k: 'ok', t: string) => void }) {
  if (!value) return <span className="text-fg-muted">—</span>
  return (
    <button type="button" title={value} aria-label={`hash ${value.slice(0, 12)} 복사`} className="mono rounded px-1 text-[11px] text-fg-muted hover:bg-surface-2 hover:text-fg"
      onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(value).then(() => toast('ok', '해시를 복사했습니다'), () => {}) }}>{value.slice(0, 12)} ⧉</button>
  )
}

/** 구간 검증 (SCR-K09 ⑥) — 지정 seq 구간의 해시를 다시 계산 (전역 배너 상태는 바꾸지 않음) */
function RangeVerify({ lastSeq }: { lastSeq?: number }) {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [res, setRes] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const dFrom = lastSeq ? String(Math.max(1, lastSeq - 999)) : '1'
  const dTo = lastSeq ? String(lastSeq) : ''
  const run = async () => {
    setBusy(true)
    setErr(null)
    try {
      setRes(await get<Json>('/api/v1/audit/verify', { from_seq: Number(from || dFrom), to_seq: Number(to || dTo) || undefined }))
    } catch (e) {
      setErr(e)
      setRes(null)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card title="구간 검증" className="mb-3" bodyClass="px-4 py-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-xs text-fg-muted">seq</span>
        <Input aria-label="시작 seq" type="number" min={1} placeholder={dFrom} value={from} onChange={(e) => setFrom(e.target.value)} className="w-28" />
        <span>~</span>
        <Input aria-label="끝 seq" type="number" min={1} placeholder={dTo || '끝'} value={to} onChange={(e) => setTo(e.target.value)} className="w-28" />
        <Button size="sm" variant="primary" loading={busy} onClick={run}>구간 검증 실행</Button>
        {res && (res.valid ? (
          <span role="status" className="text-xs text-ok">✔ seq {res.range?.[0]}→{res.last_seq} · {fmtNum(res.checked)}건 해시 재계산 일치{res.last_hash && <> · 끝 해시 <span className="mono">{res.last_hash.slice(0, 12)}</span></>}</span>
        ) : (
          <span role="alert" className="text-xs text-danger">⚠ seq {res.broken_at} 에서 불일치 ({res.reason}) — 해당 구간 리포트 내보내기 차단 · 보안 담당 알림 (MSG-022)</span>
        ))}
      </div>
      {!!err && <div className="mt-2"><ErrorBox error={err} /></div>}
    </Card>
  )
}

export function Audit() {
  const toast = useToast()
  const nav = useNavigate()
  const [tab, setTab] = useState<'events' | 'reports' | 'archives'>('events')
  const [action, setAction] = useState('')
  const verify = useGet('/api/v1/audit/verify')
  const events = useGet('/api/v1/audit/events', { action: action || undefined, limit: 1000 })
  const reports = useGet(tab === 'reports' ? '/api/v1/audit/reports' : null)
  const archives = useGet(tab === 'archives' ? '/api/v1/audit/archives' : null)
  const [sel, setSel] = useState<Json | null>(null)
  const [sid, setSid] = useState('')
  const replay = useGet(sid ? `/api/v1/audit/replay/${sid}` : null)
  const v = verify.data
  const makeReport = async () => {
    try {
      const r = await send<Json>('POST', '/api/v1/audit/reports', { actions: action ? [action] : [] })
      toast('ok', `리포트 ${r.id} — ${r.count}건 · 서명 생성`)
      reports.refetch()
      setTab('reports')
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const dl = (id: string, f: 'jsonl' | 'csv' | 'md') => download(`/api/v1/audit/reports/${id}/download?format=${f}`, `${id}.${f}`).catch((e) => toast('error', e.message))
  const selSid = sel && sidOf(sel)
  return (
    <>
      <PageHeader title="감사 로그·해시 체인" crumbs={['관측·감사', '감사']} desc="모든 결정·변경은 append-only 해시 체인에 기록됩니다 — 변조 시 끊긴 위치가 표시됩니다 (FR-O05)."
        actions={<><Button onClick={() => verify.refetch()} loading={verify.isFetching}>체인 검증</Button><Button variant="primary" onClick={makeReport}>서명 리포트 생성</Button>
          <Button onClick={() => nav('/console/audit/reports/new')}>규제 리포트(K10) →</Button></>} />
      {v && (v.valid ? (
        <div role="status" className="mb-4 rounded-md border border-ok/40 bg-ok/5 px-3 py-2 text-sm">✔ 해시 체인 유효 — 검사 {fmtNum(v.checked)}건 · seq {v.anchor_seq}→{v.last_seq}</div>
      ) : (
        <div role="alert" className="mb-4 rounded-md border border-danger bg-danger/10 px-3 py-2 text-sm text-danger">⚠ 감사 체인 손상 — seq {v.broken_at}: {v.reason} (MSG-022 · 보안 알림 발송)</div>
      ))}
      <Tabs tabs={[{ id: 'events', label: '이벤트' }, { id: 'reports', label: '규제 리포트' }, { id: 'archives', label: '아카이브·파기 증명' }]} value={tab} onChange={setTab} />
      {tab === 'events' && (
        <>
          <RangeVerify lastSeq={v?.last_seq} />
          <div className="mb-3 flex gap-2">
            <Input aria-label="action 접두사" placeholder="action 접두사 (token. · gateway. · catalog.)" value={action} onChange={(e) => setAction(e.target.value)} className="w-80" />
            <Input aria-label="세션 리플레이" placeholder="세션 ID 로 리플레이 (ses_…)" onKeyDown={(e) => e.key === 'Enter' && setSid((e.target as HTMLInputElement).value.trim())} className="w-72" />
          </div>
          {events.error && <ErrorBox error={events.error} />}
          <DataTable data={events.data?.items ?? []} filter={false} onRowClick={setSel} columns={[
            col.accessor('seq', { header: 'seq' }), col.accessor('ts', { header: '시각', cell: (c) => fmtTime(c.getValue(), true) }),
            col.accessor('action', { header: 'action', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }), col.accessor('actor', { header: '행위자' }),
            col.accessor('target', { header: '대상', cell: (c) => <span className="mono text-[11px]">{c.getValue() || '—'}</span> }),
            col.accessor('decision', { header: '결정', cell: (c) => c.getValue() ? <StatusBadge status={c.getValue()} /> : '—' }),
            col.accessor('hash', { header: 'hash', cell: (c) => <CopyHash value={c.getValue()} toast={toast} /> }),
            col.accessor('trace_id', { header: 'trace', cell: (c) => <span onClick={(e) => e.stopPropagation()}><TraceLink id={c.getValue()} /></span> }),
          ]} />
        </>
      )}
      {tab === 'reports' && (
        <Card bodyClass="p-0" title="서명 리포트" actions={<Button size="sm" variant="primary" onClick={() => nav('/console/audit/reports/new')}>+ 새 규제 리포트 (K10)</Button>}>
          <table className="w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">ID</th><th>생성</th><th>건수</th><th>체인</th><th>SHA-256</th><th /></tr></thead>
            <tbody>{(reports.data?.items ?? []).map((r: Json) => (
              <tr key={r.id} className="border-t border-line"><td className="mono px-3 py-2 text-xs">{r.id}</td><td>{r.created_by} · {fmtTime(r.created_at, true)}</td><td>{r.event_count}</td>
                <td>{r.chain_valid ? <Badge tone="ok">유효</Badge> : <Badge tone="danger">손상</Badge>}</td><td className="mono text-[11px]">{r.sha256?.slice(0, 16)}…</td>
                <td className="pr-3 text-right"><div className="inline-flex gap-1"><Button size="sm" onClick={() => dl(r.id, 'jsonl')}>다운로드</Button>
                  <Button size="sm" variant="ghost" onClick={() => dl(r.id, 'csv')}>CSV</Button><Button size="sm" variant="ghost" onClick={() => dl(r.id, 'md')}>MD</Button></div></td></tr>))}</tbody></table>
          <p className="border-t border-line px-3 py-2 text-xs text-fg-muted">리포트는 RS256 JWS 로 서명됩니다 — POST /api/v1/audit/reports/verify 로 원본·서명을 검증하세요. 서명·해시는 JSONL 원본 기준입니다.</p>
        </Card>
      )}
      {tab === 'archives' && <CodeBlock value={archives.data ?? {}} maxH="max-h-[600px]" />}
      <Drawer open={!!sel} onClose={() => setSel(null)} title={`감사 이벤트 #${sel?.seq}`} width="w-[520px]">
        {sel && <div className="space-y-3 text-sm">
          <div aria-label="체인 도식" className="flex items-center gap-2 text-xs">
            <div className="rounded-md border border-line px-2 py-1"><div className="text-fg-muted">#{sel.seq - 1}</div><CopyHash value={sel.prev_hash} toast={toast} /></div>
            <span aria-hidden>→</span>
            <div className="rounded-md border border-primary px-2 py-1"><div className="font-medium">#{sel.seq}</div><span className="text-fg-muted">prev={sel.prev_hash?.slice(0, 6)}</span></div>
            <span aria-hidden>·</span>
            <div><div className="text-fg-muted">hash</div><CopyHash value={sel.hash} toast={toast} /></div>
          </div>
          <div className="flex flex-wrap gap-2">
            {selSid && <Link to={`/console/observe/replay/${selSid}`} className="text-xs text-primary hover:underline">세션 리플레이 (K08) →</Link>}
            {sel.trace_id && <TraceLink id={sel.trace_id} />}
          </div>
          <CodeBlock value={sel} maxH="max-h-[60vh]" />
        </div>}
      </Drawer>
      <Drawer open={!!sid} onClose={() => setSid('')} title={`세션 리플레이 — ${sid}`} width="w-[640px]">
        <Link to={`/console/observe/replay/${sid}`} className="mb-3 inline-block text-sm text-primary hover:underline">단계별 재생 화면 열기 (K08) →</Link>
        {replay.error ? <ErrorBox error={replay.error} /> : !replay.data ? <Loading /> : (
          <ol className="space-y-2 text-xs">{(replay.data.timeline ?? []).map((e: Json, i: number) => (
            <li key={i} className="rounded-md border border-line p-2"><div className="flex items-center gap-2"><Badge>{e.kind}</Badge><span className="text-fg-muted">{fmtTime(e.ts, true)}</span></div>
              <div className="mt-1 line-clamp-4 whitespace-pre-wrap">{e.content ?? e.summary ?? e.name ?? JSON.stringify(e.data ?? e).slice(0, 300)}</div></li>))}</ol>
        )}
      </Drawer>
    </>
  )
}
