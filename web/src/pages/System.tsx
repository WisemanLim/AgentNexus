// SCR-B05 구성요소 상태·헬스 · SCR-B07 API(OpenAPI) 탐색기
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { Badge, Button, Card, CodeBlock, ErrorBox, Gate, Input, Kv, Loading, PageHeader, Select, StatusBadge, Textarea, TraceLink, cx, type Tone } from '@/components/ui'
import { ApiError, buildUrl, request, type Json } from '@/lib/api'
import { can, useAuth } from '@/lib/auth'
import { fmtAgo, fmtMs, fmtTime, json } from '@/lib/format'
import { send, useGet, useSend } from '@/lib/query'

// ───────────────────────── SCR-B05 구성요소 상태·헬스 ─────────────────────────
type RowStatus = 'fail' | 'degraded' | 'ok' | 'off'
type Row = { key: string; name: string; kind: 'component' | 'dependency' | 'mcp' | 'item'; status: RowStatus; version?: string; replicas?: string; note: string; data?: Json }
const RANK: Record<RowStatus, number> = { fail: 0, degraded: 1, ok: 2, off: 3 }
const ROW_TONE: Record<RowStatus, [Tone, string]> = { fail: ['danger', '✗ 실패'], degraded: ['warn', '저하'], ok: ['ok', '✓ 정상'], off: ['muted', '꺼짐'] }
const ITEM_OK = (v: unknown) => v === true || v === 'ok' || v === 'healthy'
const SHOWN = 8

function buildRows(h?: Json, comps?: Json): Row[] {
  const rows: Row[] = []
  const items: Json[] = comps?.items ?? Object.entries(h?.components ?? {}).map(([component, v]: [string, any]) => ({ component, enabled: v.enabled }))
  for (const c of items) {
    const st: RowStatus = !c.enabled ? 'off' : c.health === 'degraded' ? 'degraded' : 'ok'
    const extra = [c.errors_1h ? `오류 ${c.errors_1h}(1h)` : '', c.restarts_24h ? `재시작 ${c.restarts_24h}(24h)` : '', ...(c.detail ?? [])].filter(Boolean).join(' · ')
    rows.push({ key: `c:${c.component}`, name: c.component, kind: 'component', status: st, version: c.version ?? h?.version,
      replicas: c.replicas == null ? undefined : `${c.replicas}`, note: !c.enabled ? '꺼짐 (배포 프로파일)' : extra || '기동', data: c })
  }
  for (const [k, v] of Object.entries(h?.dependencies ?? {}) as [string, any][]) {
    const sub = Object.entries(v.items ?? {})
    const bad = sub.filter(([, s]) => !ITEM_OK(s))
    const note = sub.length ? `${sub.length - bad.length}/${sub.length} 정상` : [v.engine, v.backend, v.error, v.exported != null && `내보냄 ${v.exported}`].filter(Boolean).join(' · ')
    // 항목이 있는 의존성은 묶음 행(저하)과 실패 항목 행으로 나눈다
    rows.push({ key: `d:${k}`, name: k, kind: 'dependency', status: v.ok ? 'ok' : sub.length && bad.length < sub.length ? 'degraded' : 'fail', note, data: v })
    for (const [id, s] of bad) {
      const prefix = k === 'mcp_servers' ? 'mcp' : k === 'a2a_agents' ? 'a2a' : k === 'providers' ? 'provider' : k
      rows.push({ key: `${prefix}:${id}`, name: `${prefix}/${id}`, kind: k === 'mcp_servers' ? 'mcp' : 'item', status: 'fail', note: s === false ? '카드 없음' : String(s), data: { dependency: k, id, status: s } })
    }
  }
  return rows.sort((a, b) => RANK[a.status] - RANK[b.status])
}

export function System() {
  const q = useGet('/api/v1/system/health', undefined, { refetchInterval: 20_000 })
  const comps = useGet('/api/v1/system/components', undefined, { refetchInterval: 30_000 })
  const profiles = useGet('/api/v1/admin/profiles')
  const h = q.data
  const rows = useMemo(() => buildRows(h, comps.data), [h, comps.data])
  const [sel, setSel] = useState<string | null>(null)
  const [all, setAll] = useState(false)
  const live = rows.filter((r) => r.status !== 'off')
  const fails = rows.filter((r) => r.status === 'fail').length
  const degraded = rows.filter((r) => r.status === 'degraded').length
  const profile: string = profiles.data?.current?.profile ?? comps.data?.profile?.profile ?? 'full'
  const current = rows.find((r) => r.key === sel) ?? rows.find((r) => r.status === 'fail') ?? rows[0]
  const shown = all ? rows : rows.slice(0, SHOWN)
  return (
    <>
      <PageHeader title="구성요소 상태·헬스" crumbs={['개요', '구성요소 상태']} desc={h && <>버전 {h.version} · 확인 {fmtTime(h.checked_at, true)}</>}
        actions={h && <StatusBadge status={h.status} />} />
      {q.error && <ErrorBox error={q.error} onRetry={() => q.refetch()} />}
      {!h && !q.error && <Loading />}
      {h && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2" aria-label="상태 요약">
            <Badge tone="primary">프로파일: {profile}</Badge>
            <Badge tone={fails ? 'warn' : 'ok'}>{live.filter((r) => r.status === 'ok').length}/{live.length} 정상</Badge>
            {fails > 0 && <Badge tone="danger">실패 {fails}</Badge>}
            {degraded > 0 && <Badge tone="warn">degraded {degraded}</Badge>}
            {comps.data?.workers?.length > 0 && <span className="text-xs text-fg-muted">워커 {comps.data?.workers.join(', ')}</span>}
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div className="col-span-2 space-y-4">
              <Card title="구성요소" bodyClass="p-0">
                <table className="w-full text-sm" aria-label="구성요소 표">
                  <thead className="text-left text-xs text-fg-muted"><tr className="border-b border-line">
                    <th className="px-3 py-2">구성요소</th><th className="px-3">버전</th><th className="px-3">레플리카</th><th className="px-3">헬스</th><th className="px-3">비고</th>
                  </tr></thead>
                  <tbody>
                    {shown.map((r) => (
                      <tr key={r.key} onClick={() => setSel(r.key)} aria-selected={current?.key === r.key}
                        className={cx('cursor-pointer border-b border-line last:border-0 hover:bg-surface-2', current?.key === r.key && 'bg-primary-soft/40')}>
                        <td className="mono px-3 py-1.5">{r.name}</td><td className="px-3 text-xs">{r.version ?? '—'}</td><td className="px-3 text-xs">{r.replicas ?? '—'}</td>
                        <td className="px-3"><Badge tone={ROW_TONE[r.status][0]}>{ROW_TONE[r.status][1]}</Badge></td><td className="px-3 text-xs text-fg-muted">{r.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {rows.length > SHOWN && (
                  <div className="flex items-center gap-2 border-t border-line px-3 py-2 text-xs text-fg-muted">
                    {!all && <span className="truncate">— 외 {rows.length - SHOWN}: {rows.slice(SHOWN).map((r) => r.name).join('·')}</span>}
                    <button type="button" className="ml-auto shrink-0 text-primary hover:underline" onClick={() => setAll((v) => !v)}>{all ? '접기' : '전체 보기'}</button>
                  </div>
                )}
              </Card>
              {current && <RowDetail row={current} />}
            </div>
            <div className="space-y-4">
              <BypassCheck />
              <Card title="독립 실행 프로파일" actions={<Link to="/console/admin" className="text-xs text-primary">변경 → SCR-L01</Link>}>
                {profiles.error ? <p className="text-sm text-fg-muted">현재 프로파일: <span className="mono">{profile}</span></p> : (
                  <ul className="space-y-1.5 text-sm">
                    {(profiles.data?.items ?? []).map((p: any) => (
                      <li key={p.id} className="flex items-center gap-2" title={p.desc}>
                        <span className={cx('size-2 rounded-full', p.id === profile ? 'bg-ok' : 'border border-line')} aria-hidden />
                        <span className="mono">{p.id}</span><span className="truncate text-xs text-fg-muted">{p.name}</span>
                        {p.id === profile && <Badge tone="ok" className="ml-auto">적용 중</Badge>}
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          </div>
        </>
      )}
    </>
  )
}

function CopyCmd({ label, cmd }: { label: string; cmd: string }) {
  const [done, setDone] = useState(false)
  return (
    <div className="flex items-center gap-2">
      <span className="w-16 shrink-0 text-xs text-fg-muted">{label}</span>
      <code className="mono min-w-0 flex-1 truncate rounded bg-surface-2 px-2 py-1 text-xs" title={cmd}>{cmd}</code>
      <Button size="sm" aria-label={`${label} 재시작 명령 복사`} onClick={() => navigator.clipboard?.writeText(cmd).then(() => { setDone(true); setTimeout(() => setDone(false), 1200) })}>{done ? '복사됨' : '복사'}</Button>
    </div>
  )
}

/** 실패 상세·조치 (SCR-B05 ④⑤) — 재시작은 배포 도구 명령만 제공(콘솔에서 실행하지 않음) */
function RowDetail({ row }: { row: Row }) {
  const { me } = useAuth()
  const sid = row.kind === 'mcp' ? String(row.data?.id) : null
  const srv = useGet(sid && can(me, 'mcp:read') ? `/api/v1/mcp-servers/${sid}` : null)
  const logs = useGet(sid && can(me, 'mcp:read') ? `/api/v1/mcp-servers/${sid}/logs` : null, { limit: 5 })
  const s = srv.data
  const detail = sid
    ? [`health: ${s?.health ?? row.note}`, s?.health_detail && `detail: ${s.health_detail}`, s?.health_checked_at && `checked_at: ${fmtTime(s.health_checked_at, true)} (${fmtAgo(s.health_checked_at)})`,
      s?.endpoint && `endpoint: ${s.endpoint} (${s.transport})`, ...(logs.data?.logs ?? []).map((l: any) => `${fmtTime(l.ts, true)} ${l.level} ${l.message}`)].filter(Boolean).join('\n')
    : row.kind === 'component'
      ? [`health: ${row.data?.health ?? '—'} · 오류 ${row.data?.errors_1h ?? 0}(1h) · 재시작 ${row.data?.restarts_24h ?? 0}(24h) · 복제 ${row.data?.replicas ?? '—'}`,
        ...(row.data?.detail ?? []), row.data?.last_error ? `마지막 오류 ${fmtTime(row.data.last_error.ts, true)}: ${row.data.last_error.message}${row.data.last_error.trace_id ? ` (trace ${row.data.last_error.trace_id})` : ''}` : '마지막 오류 없음'].join('\n')
      : json(row.data ?? {})
  const restart = row.data?.restart as { compose?: string; helm?: string } | undefined
  return (
    <Card title={<span className={row.status === 'fail' ? 'text-danger' : undefined}>{row.name} — {row.status === 'fail' ? '실패 상세' : '상세'}</span>}>
      <CodeBlock value={detail} maxH="max-h-48" />
      {restart && (
        <div className="mt-3 space-y-1.5">
          <CopyCmd label="compose" cmd={restart.compose ?? ''} />
          <CopyCmd label="helm" cmd={restart.helm ?? ''} />
          <p className="text-xs text-fg-muted">재시작은 배포 도구로 실행합니다 — 콘솔은 명령만 제공하고 실행하지 않습니다.</p>
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {sid ? <>
          <Link to={`/console/mcp-servers/${sid}`} className="rounded-md border border-line px-3 py-1 text-sm hover:bg-surface-2">로그·재시작 (F02)</Link>
          <Link to={`/console/mcp-servers/${sid}/config`} className="rounded-md bg-primary px-3 py-1 text-sm text-white hover:bg-primary/90">설정 열기 (F03)</Link>
        </> : <Link to={`/console/observe/logs?component=${encodeURIComponent(row.name)}`} className="rounded-md border border-line px-3 py-1 text-sm hover:bg-surface-2">로그 보기</Link>}
      </div>
      {sid && <p className="mt-2 text-xs text-fg-muted">3회 실패 시 알림 + 도구를 카탈로그에서 일시 비활성 (ST-08)</p>}
    </Card>
  )
}

const BYPASS: Record<string, string> = {
  provider_keys: '공급자 키는 게이트웨이에만', mcp_via_gateway: 'MCP 호출은 게이트웨이 경유', mcp_identity: '원격 MCP 인증 경유', stdio: 'stdio 허용 명령만',
  a2a_via_gateway: 'A2A 위임은 게이트웨이 경유', network: 'egress 기본 거부', otel: '관측 파이프라인',
}

/** 우회 차단 점검 (SCR-B05 ⑥⑦ · H01) — 게이트웨이를 거치지 않는 호출이 막혀 있는지 */
function BypassCheck() {
  const run = useSend(() => send('POST', '/api/v1/gateway/bypass-check'))
  const r = run.data
  return (
    <Card title="우회 차단 점검" actions={r && <Badge tone={r.ok ? 'ok' : 'danger'}>{r.ok ? '통과' : '점검 필요'}</Badge>}>
      {r ? (
        <>
          <ul className="space-y-1.5 text-sm" aria-label="우회 차단 점검 결과">
            {r.items.map((i: any) => (
              <li key={i.key} title={i.detail}>
                <div className="flex items-center gap-2"><span className={cx('size-2 shrink-0 rounded-full', i.ok ? 'bg-ok' : 'bg-danger')} aria-hidden /><span>{BYPASS[i.key] ?? i.key}</span><span className="sr-only">{i.ok ? '정상' : '실패'}</span></div>
                {!i.ok && <div className="ml-4 text-xs text-danger">{i.detail}</div>}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-fg-muted">차단 {r.counts.blocked_24h} (24h) · 거부 {r.counts.denied_1h} · 429 {r.counts.rate_limited_1h} · 오류 {r.counts.errors_1h} (1h) · {fmtTime(r.checked_at, true)}</p>
        </>
      ) : <p className="text-sm text-fg-muted">단일 진입(게이트웨이) 보장 항목과 최근 차단·429·오류를 점검합니다. 점검은 감사 로그에 남습니다.</p>}
      {run.error && <div className="mt-2"><ErrorBox error={run.error} /></div>}
      <Gate perm="gateway:read">{(ok, why) => (
        <Button className="mt-3 w-full" disabled={!ok} title={why} loading={run.isPending} onClick={() => run.mutate(undefined)}>지금 점검</Button>
      )}</Gate>
    </Card>
  )
}

// ───────────────────────── SCR-B07 API·OpenAPI 탐색기 ─────────────────────────
type Op = { p: string; method: string; tag: string; summary: string; o: Json }
type Param = { name: string; in: string; required?: boolean; schema?: Json; description?: string }
type Result = { status: number; statusText: string; ms: number; trace?: string | null; body: string }

const resolve = (spec: Json, s?: Json): Json | undefined => {
  let cur = s
  for (let i = 0; cur?.$ref && i < 10; i++) cur = String(cur.$ref).split('/').slice(1).reduce((o: any, k) => o?.[k], spec)
  return cur
}
const baseOf = (spec: Json, s?: Json) => { const r = resolve(spec, s); return resolve(spec, r?.anyOf?.find((x: Json) => x.type !== 'null')) ?? r ?? {} }

/** 요청 본문 골격 — 필수·기본값 필드만 (깊이 4) */
function skeleton(spec: Json, s: Json | undefined, depth = 0): unknown {
  const b = baseOf(spec, s)
  if (b.default !== undefined) return b.default
  if (b.example !== undefined) return b.example
  if (b.enum) return b.enum[0]
  if (depth > 4) return null
  if (b.type === 'object' || b.properties) {
    const props: Record<string, Json> = b.properties ?? {}
    const req = new Set<string>(b.required ?? [])
    const keys = Object.keys(props).filter((k) => req.has(k) || resolve(spec, props[k])?.default !== undefined)
    return Object.fromEntries((keys.length ? keys : Object.keys(props)).map((k) => [k, skeleton(spec, props[k], depth + 1)]))
  }
  return b.type === 'array' ? [] : b.type === 'string' ? '' : b.type === 'integer' || b.type === 'number' ? 0 : b.type === 'boolean' ? false : null
}

function validate(spec: Json, p: Param, v: string): string | undefined {
  if (!v) return p.required ? '필수 값입니다 (MSG-004)' : undefined
  const b = baseOf(spec, p.schema)
  if (b.enum && !b.enum.map(String).includes(v)) return `허용 값: ${b.enum.join(', ')} (MSG-004)`
  if (b.type === 'integer' && !/^-?\d+$/.test(v)) return '정수를 입력하세요 (MSG-004)'
  if (b.type === 'number' && Number.isNaN(Number(v))) return '숫자를 입력하세요 (MSG-004)'
  if ((b.type === 'integer' || b.type === 'number') && ((b.maximum != null && Number(v) > b.maximum) || (b.minimum != null && Number(v) < b.minimum))) return `범위 ${b.minimum ?? '−∞'}~${b.maximum ?? '∞'} (MSG-004)`
  try {
    if (b.pattern && !new RegExp(b.pattern).test(v)) return `형식: ${b.pattern} (MSG-004)`
  } catch { /* JS 로 해석할 수 없는 패턴 — 서버 검증에 맡긴다 */ }
  return undefined
}

const SECRET_KEY = /(secret|password|passwd|token|api[_-]?key|private[_-]?key|credential)s?$/i
const mask = (v: unknown, key = ''): unknown => typeof v === 'string' && SECRET_KEY.test(key) && v.length > 0 ? (v.length > 8 ? `${v.slice(0, 4)}••••${v.slice(-2)}` : '••••')
  : Array.isArray(v) ? v.map((x) => mask(x)) : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, mask(x, k)])) : v

const shq = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`
const sh = (v?: string) => (!v ? '' : /^[\w.:/@=-]+$/.test(v) ? v : shq(v))
type CliFn = (path: string[], q: Record<string, string>, b: any) => string
// nexus CLI 대응 (app/cli/nexus.py) — 경로 변수는 {} 로 정규화한 키
const CLI: Record<string, CliFn> = {
  'GET /api/v1/me': () => 'nexus whoami --json',
  'GET /v1/catalog': (_, q) => ['nexus catalog list', q.type && `--type ${sh(q.type)}`, q.q && `--q ${sh(q.q)}`, '--json'].filter(Boolean).join(' '),
  'POST /v1/catalog/{}/register': ([id]) => `nexus catalog register ${sh(id) || '<item_id>'} --json`,
  'POST /v0/publish': () => 'nexus registry publish server.json --json',
  'GET /api/v1/mcp-servers/templates/custom': (_, q) => ['nexus mcp scaffold', sh(q.name) || '<name>', q.title && `--title ${sh(q.title)}`].filter(Boolean).join(' '),
  'GET /api/v1/mcp-servers': () => 'nexus mcp list --json',
  'POST /api/v1/mcp-servers/{}/inspect': ([id], _, b) => b?.method === 'tools/call'
    ? `nexus mcp inspect ${sh(id) || '<server>'} ${sh(b.params?.name) || '<tool>'} --args ${shq(JSON.stringify(b.params?.arguments ?? {}))}` : `nexus mcp inspect ${sh(id) || '<server>'}`,
  'GET /api/v1/agents': () => 'nexus agents list --json',
  'POST /api/v1/agents/{}/test': ([id], _, b) => `nexus agents test ${sh(id) || '<profile>'} ${shq(b?.request || '<요청>')} --json`,
  'GET /api/v1/knowledge-bases': () => 'nexus kb list --json',
  'POST /api/v1/knowledge-bases/{}/search': ([id], _, b) => `nexus kb search ${sh(id) || '<kb_id>'} ${shq(b?.query || '<질의>')}${b?.top_k ? ` --top-k ${b.top_k}` : ''} --json`,
  'GET /api/v1/approvals': (_, q) => `nexus approvals list${q.status ? ` --status ${sh(q.status)}` : ''} --json`,
  'POST /api/v1/approvals/{}/approve': ([id], _, b) => `nexus approvals approve ${sh(id) || '<approval_id>'}${b?.note ? ` --note ${shq(b.note)}` : ''}`,
  'POST /api/v1/approvals/{}/reject': ([id], _, b) => `nexus approvals approve ${sh(id) || '<approval_id>'} --reject${b?.note ? ` --note ${shq(b.note)}` : ''}`,
  'GET /api/v1/usage': (_, q) => `nexus usage${q.group_by ? ` --group-by ${sh(q.group_by)}` : ''} --json`,
  'GET /api/v1/audit/events': (_, q) => `nexus audit list${q.action ? ` --action ${sh(q.action)}` : ''} --json`,
  'GET /api/v1/audit/verify': () => 'nexus audit-verify',
  'POST /api/v1/admin/backup': () => 'nexus bundle export --out ./agentnexus-airgap',
}

const methodTone = (m: string): Tone => (m === 'GET' ? 'ok' : m === 'DELETE' ? 'danger' : 'primary')

export function ApiExplorer() {
  const q = useGet<any>('/openapi.json')
  const [sp, setSp] = useSearchParams()
  const [f, setF] = useState('')
  const [openTags, setOpenTags] = useState<Set<string>>(new Set())
  const ops: Op[] = useMemo(() => Object.entries(q.data?.paths ?? {}).flatMap(([p, m]: [string, any]) =>
    Object.entries(m).map(([method, o]: [string, any]) => ({ p, method: method.toUpperCase(), tag: o.tags?.[0] ?? '-', summary: o.summary || '', o }))), [q.data])
  const shown = ops.filter((o) => !f || `${o.method} ${o.p} ${o.tag} ${o.summary}`.toLowerCase().includes(f.toLowerCase()))
  const tags = [...new Set(shown.map((o) => o.tag))].sort()
  const selKey = sp.get('op') || ''
  const op = ops.find((o) => `${o.method} ${o.p}` === selKey)
  const toggle = (t: string) => setOpenTags((s) => { const n = new Set(s); if (n.has(t)) n.delete(t); else n.add(t); return n })
  return (
    <>
      <PageHeader title="API·OpenAPI 탐색기" crumbs={['개요', 'API']} desc={<>모든 화면 기능은 REST 로 1:1 제공 · CLI <code>nexus</code> · 스펙 <a className="text-primary" href="/openapi.json">/openapi.json</a></>} />
      {q.error && <ErrorBox error={q.error} />}
      <div className="grid grid-cols-[280px_1fr] gap-4">
        <Card bodyClass="p-2">
          <Input placeholder="경로·태그 검색" aria-label="엔드포인트 검색" value={f} onChange={(e) => setF(e.target.value)} className="mb-2" />
          <nav aria-label="엔드포인트 목록" className="max-h-[70vh] overflow-auto text-sm">
            {tags.map((t) => {
              const list = shown.filter((o) => o.tag === t)
              const expanded = !!f || openTags.has(t) || op?.tag === t
              return (
                <div key={t} className="mb-1">
                  <button type="button" aria-expanded={expanded} onClick={() => toggle(t)} className="flex w-full items-center gap-1 rounded px-1 py-1 text-left font-semibold text-navy hover:bg-surface-2">
                    <span aria-hidden className="w-3 text-xs">{expanded ? '▾' : '▸'}</span>{t}<span className="ml-auto text-xs font-normal text-fg-muted">{list.length}</span>
                  </button>
                  {expanded && list.map((o) => (
                    <button key={o.method + o.p} type="button" aria-current={op === o || undefined} onClick={() => setSp({ op: `${o.method} ${o.p}` }, { replace: true })} title={o.summary}
                      className={cx('flex w-full items-center gap-1.5 rounded px-1 py-0.5 pl-4 text-left text-xs', op === o ? 'bg-primary-soft text-primary' : 'hover:bg-surface-2')}>
                      <span className={cx('w-11 shrink-0 font-semibold', o.method === 'GET' ? 'text-ok' : o.method === 'DELETE' ? 'text-danger' : 'text-primary')}>{o.method}</span>
                      <span className="mono truncate">{o.p}</span>
                    </button>
                  ))}
                </div>
              )
            })}
          </nav>
          <a href="/openapi.json" download="openapi.json" className="mt-2 block px-1 text-xs text-primary hover:underline">openapi.json ↓</a>
        </Card>
        {op ? <TryIt key={selKey} op={op} spec={q.data} /> : (
          <Card><p className="py-10 text-center text-sm text-fg-muted">{q.data ? `왼쪽에서 엔드포인트를 고르세요 (${ops.length}개).` : '스펙을 불러오는 중…'}</p></Card>
        )}
      </div>
      <Card title="CLI ↔ REST" className="mt-4">
        <Kv rows={[['nexus login --device', 'POST /idp/device/code → /idp/token'], ['nexus catalog list', 'GET /v1/catalog'], ['nexus registry publish', 'POST /v0/publish'],
          ['nexus mcp scaffold', 'GET /api/v1/mcp-servers/templates/custom'], ['nexus approvals approve', 'POST /api/v1/approvals/{id}/approve'], ['nexus bundle export', 'POST /api/v1/admin/backup + docker save']]} />
        <CodeBlock className="mt-3" value={'nexus login --device --url ' + location.origin + '\nnexus catalog list --type mcp_server --json | jq .'} />
      </Card>
    </>
  )
}

/** 엔드포인트 시험 호출 (SCR-B07 ③~⑧) — 내 세션 토큰으로 실행, prod 쓰기 호출은 확인(MSG-002) */
function TryIt({ op, spec }: { op: Op; spec: Json }) {
  const { me } = useAuth()
  const sys = useGet('/api/v1/system/info', undefined, { staleTime: 60_000 })
  const params: Param[] = (op.o.parameters ?? []).map((p: Json) => resolve(spec, p) as Param).filter((p: Param) => ['path', 'query', 'header'].includes(p.in))
  const content: Json = resolve(spec, op.o.requestBody)?.content ?? {}
  const ctype = content['application/json'] ? 'json' : content['application/x-www-form-urlencoded'] ? 'form' : content['multipart/form-data'] ? 'multipart' : null
  const bodySchema = content['application/json']?.schema ?? content['application/x-www-form-urlencoded']?.schema
  const [vals, setVals] = useState<Record<string, string>>({})
  const [body, setBody] = useState(() => (bodySchema ? JSON.stringify(skeleton(spec, bodySchema), null, 2) : ''))
  const [res, setRes] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const [touched, setTouched] = useState(false)
  const errs = Object.fromEntries(params.map((p) => [`${p.in}:${p.name}`, validate(spec, p, vals[`${p.in}:${p.name}`] ?? '')]))
  let parsed: unknown
  let bodyErr: string | undefined
  if (ctype === 'json' || ctype === 'form') {
    try { parsed = body.trim() ? JSON.parse(body) : undefined } catch (e) { bodyErr = `JSON 형식 오류: ${(e as Error).message} (MSG-004)` }
  }
  const pathVals = params.filter((p) => p.in === 'path').map((p) => vals[`path:${p.name}`] ?? '')
  const path = op.p.replace(/\{([^}]+)\}/g, (_, n) => encodeURIComponent(vals[`path:${n}`] ?? '') || `{${n}}`)
  const query = Object.fromEntries(params.filter((p) => p.in === 'query' && vals[`query:${p.name}`]).map((p) => [p.name, vals[`query:${p.name}`]]))
  const headers = Object.fromEntries(params.filter((p) => p.in === 'header' && vals[`header:${p.name}`]).map((p) => [p.name, vals[`header:${p.name}`]]))
  const invalid = Object.values(errs).some(Boolean) || !!bodyErr
  const write = op.method !== 'GET' && op.method !== 'HEAD'
  const prod = sys.data?.env === 'prod'
  const url = location.origin + buildUrl(path, query)
  const curl = [`curl${op.method === 'GET' ? '' : ` -X ${op.method}`} ${shq(url)}`, '-H "Authorization: Bearer $NEXUS_TOKEN"',
    ...Object.entries(headers).map(([k, v]) => `-H ${shq(`${k}: ${v}`)}`),
    ...(parsed === undefined ? [] : ctype === 'form' ? Object.entries(parsed as Json).map(([k, v]) => `--data-urlencode ${shq(`${k}=${v}`)}`)
      : ["-H 'Content-Type: application/json'", `-d ${shq(JSON.stringify(parsed))}`])].join(' \\\n  ')
  const cli = CLI[`${op.method} ${op.p.replace(/\{[^}]+\}/g, '{}')}`]?.(pathVals, query, parsed)

  async function execute() {
    setTouched(true)
    if (invalid || ctype === 'multipart') return
    if (write && prod && !window.confirm(`prod 환경의 쓰기 API 입니다.\n${op.method} ${path}\n시험 실행할까요? 호출은 감사 로그에 기록됩니다. (MSG-002)`)) return
    setBusy(true)
    const t0 = performance.now()
    try {
      const r = await request(path, { method: op.method, query, headers, ...(parsed === undefined ? {} : ctype === 'form'
        ? { form: Object.fromEntries(Object.entries(parsed as Json).map(([k, v]) => [k, String(v)])) } : { body: parsed }) })
      setRes({ status: r.status, statusText: r.statusText, ms: performance.now() - t0, trace: r.headers.get('X-AgentNexus-Trace-Id'), body: await r.text() })
    } catch (e) {
      const ms = performance.now() - t0
      setRes(e instanceof ApiError ? { status: e.status, statusText: e.code, ms, trace: e.traceId, body: JSON.stringify(e.data) }
        : { status: 0, statusText: 'network_error', ms, body: String(e) })
    } finally {
      setBusy(false)
    }
  }

  let shownBody = res?.body ?? ''
  try { if (shownBody) shownBody = json(mask(JSON.parse(shownBody))) } catch { /* JSON 아님 — 원문 */ }
  return (
    <div className="grid min-w-0 grid-cols-[1fr_300px] gap-4">
      <Card title={<span className="flex items-center gap-2"><Badge tone={methodTone(op.method)}>{op.method}</Badge><span className="mono">{op.p}</span></span>}>
        <p className="text-sm">{op.summary}<span className="ml-2 text-xs text-fg-muted">태그 {op.tag} · {op.o.operationId}</span></p>
        {op.o.description && <p className="mt-1 whitespace-pre-line text-xs text-fg-muted">{op.o.description}</p>}
        {params.length > 0 && (
          <table className="mt-3 w-full text-sm" aria-label="파라미터">
            <thead className="text-left text-xs text-fg-muted"><tr className="border-b border-line"><th className="py-1">파라미터</th><th>위치</th><th className="w-1/2">값</th></tr></thead>
            <tbody>
              {params.map((p) => {
                const k = `${p.in}:${p.name}`
                const b = baseOf(spec, p.schema)
                const err = touched || vals[k] ? errs[k] : undefined
                return (
                  <tr key={k} className="border-b border-line last:border-0 align-top">
                    <td className="py-1.5"><span className="mono">{p.name}</span>{p.required && <span className="text-danger"> *</span>}<div className="text-[11px] text-fg-muted">{b.type ?? ''}{b.pattern ? ` · ${b.pattern}` : ''}</div></td>
                    <td className="py-1.5 text-xs text-fg-muted">{p.in}</td>
                    <td className="py-1.5">
                      {b.enum ? (
                        <Select aria-label={p.name} value={vals[k] ?? ''} onChange={(e) => setVals({ ...vals, [k]: e.target.value })}>
                          <option value="">{b.default != null ? `(기본 ${b.default})` : '—'}</option>{b.enum.map((v: unknown) => <option key={String(v)}>{String(v)}</option>)}
                        </Select>
                      ) : <Input aria-label={p.name} value={vals[k] ?? ''} placeholder={b.default != null ? `기본 ${b.default}` : p.description ?? ''} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />}
                      {err && <span role="alert" className="mt-0.5 block text-xs text-danger">{err}</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        {ctype === 'multipart' && <p className="mt-3 text-xs text-warn">multipart 업로드는 콘솔에서 시험할 수 없습니다 — 오른쪽 curl 로 호출하세요.</p>}
        {(ctype === 'json' || ctype === 'form') && (
          <div className="mt-3">
            <div className="mb-1 text-xs font-medium text-fg-muted">요청 본문 ({ctype === 'form' ? 'form — JSON 객체로 입력' : 'JSON'})</div>
            <Textarea aria-label="요청 본문" className="mono h-40 text-xs" value={body} onChange={(e) => setBody(e.target.value)} spellCheck={false} />
            {bodyErr && <span role="alert" className="block text-xs text-danger">{bodyErr}</span>}
          </div>
        )}
        <div className="mt-3 flex items-center gap-2">
          <span className="text-xs text-fg-muted">인증</span>
          <span className="rounded border border-line bg-surface-2 px-2 py-1 text-xs">내 세션 토큰 · {me?.name}</span>
          <Button variant="primary" className="ml-auto" loading={busy} disabled={ctype === 'multipart'} title={ctype === 'multipart' ? 'multipart 는 curl 로' : undefined} onClick={execute}>실행</Button>
        </div>
        {res && (
          <div className="mt-3" aria-label="응답">
            <div className="mb-1 flex items-center gap-2 text-xs">
              <Badge tone={res.status >= 200 && res.status < 300 ? 'ok' : res.status >= 400 && res.status < 500 ? 'warn' : 'danger'}>{res.status || 'ERR'} {res.statusText}</Badge>
              <span className="tabular-nums">{fmtMs(res.ms)}</span><span className="text-fg-muted">trace</span><TraceLink id={res.trace} />
              <span className="ml-auto text-fg-muted">시크릿 필드는 가립니다</span>
            </div>
            <CodeBlock value={shownBody.length > 100_000 ? `${shownBody.slice(0, 100_000)}\n… (잘림)` : shownBody || '(본문 없음)'} />
          </div>
        )}
      </Card>
      <Card title="같은 요청">
        <div className="text-xs font-medium text-fg-muted">CLI</div>
        {cli ? <CodeBlock value={cli} className="mt-1" maxH="max-h-40" /> : <p className="mt-1 text-xs text-fg-muted">대응 nexus 명령이 없습니다 — curl 을 사용하세요.</p>}
        <div className="mt-3 text-xs font-medium text-fg-muted">curl</div>
        <CodeBlock value={curl} className="mt-1" maxH="max-h-64" />
        <p className="mt-1 text-[11px] text-fg-muted">$NEXUS_TOKEN — 내 계정에서 발급한 PAT(anx_pat_…)</p>
        {write && (
          <p className={cx('mt-3 text-xs', prod ? 'text-danger' : 'text-[#a06d00] dark:text-warn')}>
            쓰기 API — {prod ? 'prod 환경이라 실행 전 확인 대화상자가 뜹니다 (MSG-002, 감사 기록).' : `실제로 변경됩니다 (환경 ${sys.data?.env ?? '—'}). prod 에서는 실행 전 확인을 거칩니다.`}
          </p>
        )}
      </Card>
    </div>
  )
}
