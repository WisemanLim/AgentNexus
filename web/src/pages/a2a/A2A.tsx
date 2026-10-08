// SCR-G01 원격 에이전트 목록 · G02 등록 위저드(조회·점검 → 등록 요청) · G03 Agent Card 상세·캐시·재검증 · G04 인증 · G05/G06 A2A Task · G07 시험 콘솔
import { createColumnHelper } from '@tanstack/react-table'
import { useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, CodeBlock, ErrorBox, Field, Gate, Input, Kpi, Kv, Loading, Modal, PageHeader, Select, StatusBadge, Stepper, Tabs, Textarea, TraceLink, type Tone, cx, useToast } from '@/components/ui'
import { can, useAuth } from '@/lib/auth'
import { request, type Json } from '@/lib/api'
import { fmtAgo, fmtPct, fmtTime, shortId } from '@/lib/format'
import { send, useGet } from '@/lib/query'
import { readSse } from '@/lib/sse'

const col = createColumnHelper<Json>()
const TRUST: Record<string, Tone> = { verified: 'ok', trusted: 'primary', untrusted: 'warn', blocked: 'danger' }
export const TrustBadge = ({ tier }: { tier: string }) => <Badge tone={TRUST[tier] ?? 'muted'}>{tier === 'verified' ? '✔ ' : tier === 'blocked' ? '⛔ ' : ''}{tier}</Badge>

// ── Task 공통 (G05·G06·G07) ──
const TERMINAL = ['completed', 'failed', 'canceled', 'rejected']
const normState = (s?: string | null) => (s || '').replace(/^TASK_STATE_/, '').toLowerCase().replace(/_/g, '-')
const isOpen = (t: Json) => t.direction === 'outbound' && !TERMINAL.includes(normState(t.state))
// Task id 는 '/'·':' 를 포함할 수 있다 — API 경로는 세그먼트별 인코딩({task_id:path}), 콘솔 링크는 통째로 인코딩
const apiTask = (id: string) => `/api/v1/a2a/tasks/${id.split('/').map(encodeURIComponent).join('/')}`
const taskHref = (id: string) => `/console/a2a/tasks/${encodeURIComponent(id)}`
const safeDecode = (s: string) => {
  try {
    return decodeURIComponent(s)
  } catch {
    return s
  }
}
const textOf = (parts?: Json[]) => (parts ?? []).map((p) => p.text).filter(Boolean).join(' ').slice(0, 200)
const fmtDur = (ms: number) => {
  const s = Math.max(0, Math.round(ms / 1000))
  return s < 60 ? `${s}초` : s < 3600 ? `${Math.floor(s / 60)}분` : `${Math.floor(s / 3600)}시간 ${Math.floor((s % 3600) / 60)}분`
}

/** tasks/cancel — 확인(MSG-002) 후 게이트웨이가 원격에 전달. 취소하면 null. */
async function cancelTask(id: string, label: string): Promise<Json | null> {
  if (!window.confirm(`'${label}' Task 를 취소합니다. 원격 작업이 중단됩니다 (MSG-002)`)) return null
  const r = await send<Json>('POST', `${apiTask(id)}/cancel`)
  if (r.error) throw new Error(r.error.message)
  return r
}

// SCR-G01 ⑦ 신뢰 등급 기준 — 등급 부여는 승인자(E05)·보안 담당. 카탈로그 등급(official…) → 게이트웨이 등급
const TIER_INFO: [string, string, string][] = [
  ['verified', 'official', '플랫폼팀 운영 — 정책 허용 시 호출'],
  ['trusted', 'partner', '계약·보안 심사 통과 — 정책 허용 시 호출'],
  ['untrusted', 'community · external', '내부 개발·외부 조직 — 에이전트를 지정한 명시 허용 정책 필요'],
  ['blocked', '—', '호출 차단 (게이트웨이 거부)'],
]
const STATE_INFO: [ReactNode, string][] = [
  [<StatusBadge status="active" />, '/a2a/{id} 로 위임 가능'],
  [<StatusBadge status="pending" />, '승인 전 호출 불가 (MSG-008)'],
  [<StatusBadge status="disabled" />, '운영자가 중지 — 호출 거부'],
  [<Badge tone="warn">변경 감지</Badge>, '카드 변경 — 확인 전까지 이전 카드로 동작'],
]

export function A2AAgents() {
  const nav = useNavigate()
  const { me } = useAuth()
  const [sp, setSp] = useSearchParams()
  const q = useGet('/api/v1/a2a/agents', undefined, { refetchInterval: 30_000 })
  const tq = useGet(can(me, 'chat:use') ? '/api/v1/a2a/tasks' : null, { limit: 500 }, { refetchInterval: 30_000 })
  const [fw, setFw] = useState('')
  const [tier, setTier] = useState('')
  const [st, setSt] = useState('')
  const open = sp.get('register') === '1'
  const all: Json[] = q.data?.items ?? []
  const frameworks = [...new Set(all.map((a) => a.framework).filter(Boolean))] as string[]
  const rows = all.filter((a) => (!fw || a.framework === fw) && (!tier || a.trust_tier === tier) && (!st || (st === 'card_changed' ? a.card_changed : a.status === st)))
  const tasks: Json[] = tq.data?.items ?? []
  const n = (s: string) => tasks.filter((t) => normState(t.state) === s).length
  const today = tasks.filter((t) => new Date(t.created_at).toDateString() === new Date().toDateString())
  const failed = today.filter((t) => normState(t.state) === 'failed').length
  const tv = (v: number) => (tq.data ? v : '—')
  const columns = [
    col.accessor('name', { header: '에이전트', cell: ({ row }) => <div><div className="font-medium">{row.original.name}</div><div className="mono text-[11px] text-fg-muted">{row.original.id}</div></div> }),
    col.accessor('framework', { header: '프레임워크', cell: (c) => <span className="text-xs">{c.getValue() || '—'}</span> }),
    col.accessor('trust_tier', { header: '신뢰 등급', cell: (c) => <TrustBadge tier={c.getValue()} /> }),
    col.accessor('status', { header: '상태', cell: (c) => <StatusBadge status={c.getValue()} /> }),
    col.accessor('protocol_versions', { header: 'A2A', cell: ({ row }) => <>{(row.original.protocol_versions ?? []).map((v: string) => <Badge key={v} tone="a2a" className="mr-1">{v}</Badge>)}
      {(row.original.bindings ?? []).filter((b: string) => b !== 'JSONRPC').map((b: string) => <Badge key={b} className="mr-1">{b}</Badge>)}</> }),
    col.accessor('skills', { header: '스킬', enableSorting: false, cell: (c) => <span className="text-xs">{(c.getValue() ?? []).map((s: Json) => s.name || s.id).join(', ')}</span> }),
    col.display({ id: 'cap', header: '기능', cell: ({ row }) => <span className="text-xs">{row.original.streaming ? 'stream ' : ''}{row.original.push ? 'push' : ''}</span> }),
    col.accessor('owner_team', { header: '소유 팀', cell: (c) => <span className="text-xs">{c.getValue() || '—'}</span> }),
    col.accessor('gateway_path', { header: '게이트웨이 경로', cell: ({ row }) => row.original.status === 'pending' ? <span className="text-xs text-fg-muted">— 승인 후</span> : <span className="mono text-xs">{row.original.gateway_path}</span> }),
    col.accessor('card_changed', { header: '카드', cell: (c) => c.getValue() ? <Badge tone="warn">변경 감지</Badge> : '—' }),
    col.accessor('last_fetched_at', { header: '조회', cell: (c) => fmtAgo(c.getValue()) }),
  ]
  const filters = (
    <>
      <Select aria-label="프레임워크 필터" className="w-36" value={fw} onChange={(e) => setFw(e.target.value)}><option value="">프레임워크: 전체</option>{frameworks.map((f) => <option key={f}>{f}</option>)}</Select>
      <Select aria-label="신뢰 등급 필터" className="w-36" value={tier} onChange={(e) => setTier(e.target.value)}><option value="">신뢰 등급: 전체</option>{Object.keys(TRUST).map((t) => <option key={t}>{t}</option>)}</Select>
      <Select aria-label="상태 필터" className="w-36" value={st} onChange={(e) => setSt(e.target.value)}><option value="">상태: 전체</option><option value="active">활성</option><option value="pending">승인 대기</option><option value="disabled">중지</option><option value="card_changed">카드 변경 감지</option></Select>
    </>
  )
  return (
    <>
      <PageHeader title="원격 A2A 에이전트" crumbs={['A2A 에이전트']} desc="Agent Card 를 검증·캐시하고 /a2a/{id} 게이트웨이 경로로만 위임합니다 (0.3·1.0 상호 변환). 개발자 등록은 운영자 승인 후 활성(MSG-008)."
        actions={<Gate perm="a2a:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => setSp({ register: '1' })}>+ Agent Card 로 등록</Button>}</Gate>} />
      <div className="mb-4 grid grid-cols-4 gap-3">
        <Kpi label="등록 에이전트" value={q.data ? all.length : '—'} sub={`활성 ${all.filter((a) => a.status === 'active').length} · 대기 ${all.filter((a) => a.status === 'pending').length}`} />
        <Link to="/console/a2a/tasks?state=working" className="block hover:opacity-80"><Kpi label="진행 중 Task" value={tv(n('submitted') + n('working'))} sub={`working ${tv(n('working'))}`} /></Link>
        <Link to="/console/a2a/tasks?state=input-required" className="block hover:opacity-80"><Kpi label="입력 대기" value={tv(n('input-required'))} tone={n('input-required') ? 'warn' : undefined} sub="input-required (MSG-017)" /></Link>
        <Link to="/console/a2a/tasks?period=today" className="block hover:opacity-80"><Kpi label="오늘 Task" value={tv(today.length)} tone={failed ? 'danger' : undefined} sub={`실패 ${tv(failed)} · ${fmtPct(today.length ? failed / today.length : null)}`} /></Link>
      </div>
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading ? <Loading /> : <DataTable data={rows} columns={columns} toolbar={filters} empty="조건에 맞는 원격 에이전트가 없습니다." onRowClick={(r) => nav(`/console/a2a/${r.id}`)} />}
      <div className="mt-4 grid grid-cols-[1fr_380px] gap-4">
        <Card title="호출 경로 (게이트웨이 경유만)">
          <div className="flex items-center gap-2 text-center text-xs" aria-label="호출 경로">
            <div className="flex-1 rounded-lg border border-primary/50 px-2 py-3">호스트<br /><span className="text-fg-muted">Planner·Executor</span></div>
            <span aria-hidden>→</span>
            <div className="flex-1 rounded-lg border border-ok/60 px-2 py-3">게이트웨이 <span className="mono">/a2a/{'{id}'}</span><br /><span className="text-fg-muted">인증 주입·정책·감사</span></div>
            <span aria-hidden>→</span>
            <div className="flex-1 rounded-lg border border-a2a/60 px-2 py-3">원격 에이전트<br /><span className="text-fg-muted">Agent Card url</span></div>
          </div>
          <p className="mt-3 text-xs text-danger">직접 호출은 네트워크 정책으로 차단 (MSG-005) · 미등록 에이전트 호출은 거부 (MSG-006)</p>
        </Card>
        <Card title="신뢰 등급 기준">
          <ul className="space-y-1.5 text-xs">{TIER_INFO.map(([t, cat, d]) => <li key={t} className="flex items-center gap-2"><span className="w-24 shrink-0"><TrustBadge tier={t} /></span><span className="w-28 shrink-0 text-fg-muted">{cat}</span><span>{d}</span></li>)}</ul>
          <p className="mt-2 text-[11px] text-fg-muted">등급은 승인자(E05)·보안 담당이 부여합니다.</p>
          <h3 className="mt-3 mb-1.5 text-xs font-semibold">상태</h3>
          <ul className="space-y-1.5 text-xs">{STATE_INFO.map(([b, d], i) => <li key={i} className="flex items-center gap-2"><span className="w-24 shrink-0">{b}</span><span>{d}</span></li>)}</ul>
        </Card>
      </div>
      {open && <RegisterWizard onClose={() => setSp({})} onDone={(id) => nav(`/console/a2a/${id}`)} />}
    </>
  )
}

const CHECK_LABEL: Record<string, string> = { schema: '스키마', url_domain: 'url 도메인', tls: 'TLS', capabilities: 'capabilities', skills: 'skills', security_schemes: 'securitySchemes', signature: '서명(JWS)' }

// SCR-G02 원격 에이전트 등록 — ① URL ② 조회·점검(저장 안 함) ③ 등록 정보·인증 ④ 결과(승인 요청 또는 활성)
function RegisterWizard({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const { me } = useAuth()
  const [step, setStep] = useState(0)
  const [url, setUrl] = useState('')
  const [review, setReview] = useState<Json | null>(null)
  const [id, setId] = useState('')
  const [team, setTeam] = useState(me?.team ?? '')
  const [scheme, setScheme] = useState('exchange')
  const [secret, setSecret] = useState('')
  const [tier, setTier] = useState('untrusted')
  const [res, setRes] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const canTrust = can(me, 'policy:write')
  const approver = can(me, 'catalog:approve')
  const auth = (): Json => scheme === 'apiKey' ? { scheme: 'apiKey', header: 'X-API-Key' } : scheme === 'static' ? { scheme: 'bearer', token_exchange: false }
    : scheme === 'none' ? { scheme: 'none' } : { scheme: 'bearer', token_exchange: true }
  const run = async (fn: () => Promise<void>) => {
    setErr(null)
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }
  const fetchCard = () => run(async () => {
    const r = await send<Json>('POST', '/api/v1/a2a/agents', { url, dry_run: true })
    setReview(r)
    setId((r.card?.name ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60))
    setStep(1)
  })
  const register = () => run(async () => {
    setRes(await send<Json>('POST', '/api/v1/a2a/agents', { url, id: id || undefined, owner_team: team || undefined, trust_tier: tier, auth: auth(), secret: secret || undefined }))
    setStep(3)
  })
  const footer = step === 0 ? <><Button onClick={onClose}>취소</Button><Button variant="primary" loading={busy} disabled={!url} onClick={fetchCard}>조회·점검</Button></>
    : step === 1 ? <><Button onClick={() => setStep(0)}>이전</Button><Button variant="primary" disabled={!review?.ok} title={review?.ok ? undefined : '점검 실패 — 등록할 수 없습니다 (MSG-004)'} onClick={() => setStep(2)}>다음</Button></>
      : step === 2 ? <><Button onClick={() => setStep(1)}>이전</Button><Button variant="primary" loading={busy} disabled={!/^[a-z0-9][a-z0-9-]{1,62}$/.test(id)} onClick={register}>{approver ? '등록' : '등록 요청'}</Button></>
        : <><Button onClick={onClose}>닫기</Button><Button variant="primary" onClick={() => onDone(res!.id)}>에이전트 보기 →</Button></>
  return (
    <Modal open onClose={onClose} title="원격 에이전트 등록 (Agent Card)" wide footer={footer}>
      <Stepper steps={['URL', '조회·점검', '등록 정보', '결과']} current={step} />
      {err ? <div className="mb-3"><ErrorBox error={err} /></div> : null}
      {step === 0 && <Field label="URL" hint="https://…/.well-known/agent-card.json 또는 기준 URL — 게이트웨이가 조회하고 스키마를 검증합니다 (MSG-004)"><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://a2a-demo:8200" autoFocus /></Field>}
      {step === 1 && review && (
        <div className="grid grid-cols-[1fr_1fr] gap-4 text-sm">
          <div>
            <p className="mb-2 text-xs text-fg-muted">조회 {review.fetch_ms}ms · 권장 신뢰 등급 <Badge>{review.suggested_trust}</Badge></p>
            <ul className="space-y-1">{review.checks.map((c: Json) => <li key={c.key} className={c.ok ? 'text-ok' : c.warn ? 'text-warn' : 'text-danger'}>{c.ok ? '✓' : c.warn ? '!' : '✗'} <span className="text-fg">{CHECK_LABEL[c.key] ?? c.key}</span> <span className="text-xs text-fg-muted">{c.detail}</span></li>)}</ul>
          </div>
          <CodeBlock value={review.card} maxH="max-h-80" />
        </div>
      )}
      {step === 2 && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="ID (/a2a/{id})"><Input className="mono" value={id} onChange={(e) => setId(e.target.value)} /></Field>
          <Field label="소유 팀"><Input value={team} onChange={(e) => setTeam(e.target.value)} /></Field>
          <Field label="인증"><Select value={scheme} onChange={(e) => setScheme(e.target.value)}>
            <option value="exchange">사용자 위임 — RFC 8693 토큰 교환</option><option value="static">정적 Bearer 토큰</option><option value="apiKey">API 키 헤더</option><option value="none">없음</option></Select></Field>
          {(scheme === 'static' || scheme === 'apiKey') ? <Field label="자격 증명" hint="게이트웨이 시크릿 저장소에 암호화 저장 (MSG-018)"><Input type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} /></Field> : <div />}
          <Field label="신뢰 등급" hint={canTrust ? undefined : '보안 담당 권한 필요 — 승인자가 확정합니다'}><Select value={tier} disabled={!canTrust} onChange={(e) => setTier(e.target.value)}>{Object.keys(TRUST).map((t) => <option key={t}>{t}</option>)}</Select></Field>
          <p className="col-span-2 text-xs text-fg-muted">{approver ? '승인 권한이 있어 바로 활성화됩니다.' : '등록 요청은 승인 대기열(E05)로 가며, 승인 전에는 호출할 수 없습니다 (MSG-008).'}</p>
        </div>
      )}
      {step === 3 && res && (
        <div className="space-y-2 text-sm">
          {res.status === 'pending' ? <p className="text-warn">등록 요청됨 — 운영자 승인 후 활성화됩니다 (MSG-008)</p> : <p className="text-ok">등록 완료 — /a2a/{res.id} 로 위임할 수 있습니다</p>}
          <Kv rows={[['ID', <span className="mono">{res.id}</span>], ['상태', <StatusBadge status={res.status} />], ['신뢰', <TrustBadge tier={res.trust_tier} />], ['게이트웨이', <span className="mono">{res.gateway_path}</span>]]} />
        </div>
      )}
    </Modal>
  )
}

export function A2AAgent() {
  const { id = '' } = useParams()
  const toast = useToast()
  const q = useGet(`/api/v1/a2a/agents/${id}`)
  const [tab, setTab] = useState<'card' | 'cache' | 'auth' | 'try'>('card')
  const act = async (fn: () => Promise<unknown>, msg: string) => {
    try {
      await fn()
      toast('ok', msg)
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  if (q.error) return <ErrorBox error={q.error} />
  if (!q.data) return <Loading />
  const a = q.data
  return (
    <>
      <PageHeader title={a.name} crumbs={['A2A 에이전트', a.id]} desc={<span className="mono">{a.gateway_path} → {a.url}</span>}
        actions={<><TrustBadge tier={a.trust_tier} /><StatusBadge status={a.status} />
          <Link className="text-xs text-primary hover:underline" to={`/console/a2a/tasks?agent=${encodeURIComponent(a.id)}`}>Task 목록 →</Link>
          <Gate perm="a2a:write">{(ok) => <>
            <Button size="sm" disabled={!ok} onClick={() => act(() => send('POST', `/api/v1/a2a/agents/${id}/refresh`), 'Agent Card 다시 조회')}>카드 새로고침</Button>
            <Button size="sm" disabled={!ok} onClick={() => act(() => send('PATCH', `/api/v1/a2a/agents/${id}`, { status: a.status === 'active' ? 'disabled' : 'active' }), '상태 변경')}>{a.status === 'active' ? '중지' : '활성화'}</Button>
          </>}</Gate></>} />
      {a.status === 'pending' && <div role="status" className="mb-3 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-sm">등록 승인 대기 — 승인 전에는 호출할 수 없습니다 (MSG-008). <Link className="text-primary" to="/console/registry/approvals">승인 대기열 →</Link></div>}
      {a.card_changed && (
        <div role="alert" className="mb-3 flex items-center gap-3 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-sm">
          Agent Card 가 바뀌었습니다 — 스킬·인증 변경을 확인한 뒤 승인하세요.
          <Button size="sm" onClick={() => act(() => send('PATCH', `/api/v1/a2a/agents/${id}`, { acknowledge_card_change: true }), '변경 확인')}>변경 확인</Button>
        </div>
      )}
      <Tabs tabs={[{ id: 'card', label: 'Agent Card' }, { id: 'cache', label: '캐시·재검증' }, { id: 'auth', label: '인증 설정' }, { id: 'try', label: '시험 콘솔' }]} value={tab} onChange={setTab} />
      {tab === 'cache' && <CardCache a={a} />}
      {tab === 'auth' && (
        <div className="grid grid-cols-[1fr_1fr] gap-4">
          <AuthSettings a={a} onSaved={() => q.refetch()} />
          <div className="space-y-4"><TokenPolicy agentId={a.id} /><IssuedTokens agentId={a.id} /></div>
        </div>
      )}
      {tab === 'card' ? (
        <div className="grid grid-cols-[1fr_1fr] gap-4">
          <Card title="요약"><Kv rows={[['버전', a.version], ['A2A', (a.protocol_versions ?? []).join(', ')], ['바인딩', (a.bindings ?? []).join(' · ') || 'JSONRPC'], ['스트리밍', a.streaming ? '지원' : '—'], ['푸시', a.push ? '지원' : '—'],
            ['인증', <CodeBlock value={a.auth} maxH="max-h-24" />], ['자격증명', a.credential_configured ? '게이트웨이 시크릿 저장소' : '없음'], ['카드 해시', <span className="mono text-xs">{a.card_hash?.slice(0, 16)}</span>],
            ['스킬', <ul className="list-disc pl-4">{(a.skills ?? []).map((s: Json) => <li key={s.id}>{s.name} <span className="text-xs text-fg-muted">{s.description}</span></li>)}</ul>]]} /></Card>
          <Card title="agent-card.json"><CodeBlock value={a.card} maxH="max-h-[480px]" /></Card>
        </div>
      ) : tab === 'try' ? <TryConsole a={a} /> : null}
    </>
  )
}

// SCR-G03 ⑥⑦ — Agent Card 캐시(ETag·TTL)·재검증 이력·색인
function CardCache({ a }: { a: Json }) {
  const q = useGet(`/api/v1/a2a/agents/${a.id}/checks`)
  const c = a.cache ?? {}
  return (
    <div className="grid grid-cols-[320px_1fr] gap-4">
      <Card title="캐시">
        <Kv rows={[['ETag', <span className="mono text-xs">{c.etag || '—'}</span>], ['TTL', c.ttl_s ? `${c.ttl_s}s` : '—'], ['마지막 조회', fmtTime(c.last_fetched_at, true)], ['다음 재검증', c.next_check_at ? fmtTime(c.next_check_at, true) : '—'],
          ['카탈로그 색인', <div className="flex flex-wrap gap-1">{(a.index ?? []).map((t: string) => <Badge key={t}>{t}</Badge>)}</div>]]} />
        <p className="mt-2 text-xs text-fg-muted">If-None-Match 로 재검증 — 304 이면 캐시 유지, 바뀌면 변경 감지 알림과 함께 승인 전까지 이전 카드를 씁니다.</p>
      </Card>
      <Card title="재검증 이력" bodyClass="p-0">
        {q.error && <ErrorBox error={q.error} />}
        <table className="w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">시각</th><th>결과</th><th>HTTP</th><th>ETag</th><th>변경</th></tr></thead>
          <tbody>{(q.data?.items ?? []).map((r: Json) => <tr key={r.id} className="border-t border-line align-top">
            <td className="px-3 py-1.5 text-xs">{fmtTime(r.checked_at, true)}</td>
            <td><Badge tone={r.status === 'changed' ? 'warn' : r.status === 'error' ? 'danger' : 'ok'}>{({ ok: '정상', not_modified: '304 유지', changed: '변경', error: '오류' } as Record<string, string>)[r.status] ?? r.status}</Badge></td>
            <td className="text-xs">{r.http_status ?? '—'}</td><td className="mono text-[11px]">{r.etag ?? '—'}</td>
            <td className="text-xs">{r.error ? <span className="text-danger">{r.error}</span> : (r.changes ?? []).join(' · ') || '—'}</td></tr>)}
            {!q.data?.items?.length && <tr><td colSpan={5} className="px-3 py-6 text-center text-fg-muted">재검증 이력이 없습니다 — 「카드 새로고침」으로 바로 확인할 수 있습니다.</td></tr>}</tbody></table>
      </Card>
    </div>
  )
}

// SCR-G04 원격 에이전트 인증 — 사용자 위임 토큰 교환(기본) · 정적 Bearer · API 키 · mTLS (자격 증명은 게이트웨이 시크릿 저장소)
const schemeDecl = (d: Json) => d.type === 'http' ? [d.scheme, d.bearerFormat].filter(Boolean).join(' ') : d.type === 'apiKey' ? `${d.in ?? 'header'}: ${d.name ?? ''}`
  : d.type === 'oauth2' ? Object.keys(d.flows ?? {}).join(', ') : d.type === 'openIdConnect' ? d.openIdConnectUrl ?? '' : ''
function injection(d: Json, scheme: string, header: string): string {
  if (d.type === 'apiKey') return scheme === 'apiKey' ? `시크릿 참조 → ${header} 헤더` : '미주입 — 방식을 「API 키 헤더」로'
  if (d.type === 'http' && String(d.scheme).toLowerCase() === 'bearer') return scheme === 'exchange' ? '토큰 교환 → Bearer (사용자 위임)' : scheme === 'static' ? '정적 Bearer (시크릿 참조)' : '미주입'
  if (d.type === 'oauth2' || d.type === 'openIdConnect') return scheme === 'oauth2' ? 'client_credentials 발급 → Bearer (만료 30초 전 갱신)' : scheme === 'exchange' ? '토큰 교환 → Bearer (사용자 위임)' : '미주입 — 방식을 「OAuth2 client_credentials」로'
  if (d.type === 'mutualTLS') return 'mTLS 클라이언트 인증서'
  return '—'
}

function AuthSettings({ a, onSaved }: { a: Json; onSaved: () => void }) {
  const toast = useToast()
  const cur = a.auth ?? {}
  const [scheme, setScheme] = useState(cur.scheme === 'apiKey' ? 'apiKey' : cur.scheme === 'oauth2' ? 'oauth2' : cur.token_exchange === false ? 'static' : cur.scheme === 'none' ? 'none' : 'exchange')
  const [header, setHeader] = useState(cur.header ?? 'X-API-Key')
  const [secret, setSecret] = useState('')
  const [mtls, setMtls] = useState(!!(cur.tls?.ca_secret || cur.tls?.client_cert_secret))
  const [ca, setCa] = useState(cur.tls?.ca_secret ?? '')
  const [cert, setCert] = useState(cur.tls?.client_cert_secret ?? '')
  const schemes = Object.entries((a.card?.securitySchemes ?? {}) as Record<string, Json>)
  const oauth = schemes.map(([, d]) => d).find((d) => d.type === 'oauth2')
  const cardTokenUrl = (Object.values(oauth?.flows ?? {}) as Json[]).find((f) => f.tokenUrl)?.tokenUrl ?? ''
  const [tokenUrl, setTokenUrl] = useState<string>(cur.token_url ?? cardTokenUrl)
  const [clientId, setClientId] = useState<string>(cur.client_id ?? '')
  const [ccScopes, setCcScopes] = useState<string>((cur.scopes ?? []).join(' '))
  const [jwks, setJwks] = useState<string>(cur.push_verify?.jwks_url ?? '')
  const [pushIss, setPushIss] = useState<string>(cur.push_verify?.issuer ?? '')
  const [framework, setFramework] = useState<string>(a.framework_source === 'operator' ? a.framework ?? '' : '')
  const save = async () => {
    if (scheme === 'oauth2' && (!/^https?:\/\//.test(tokenUrl) || !clientId.trim())) return toast('error', 'OAuth2 토큰 URL·클라이언트 ID 를 입력하세요 (MSG-004)')
    const tls = mtls && (ca || cert) ? { ...(ca ? { ca_secret: ca } : {}), ...(cert ? { client_cert_secret: cert } : {}) } : null
    const auth: Json = scheme === 'apiKey' ? { scheme: 'apiKey', header } : scheme === 'static' ? { scheme: 'bearer', token_exchange: false }
      : scheme === 'oauth2' ? { scheme: 'oauth2', token_url: tokenUrl.trim(), client_id: clientId.trim(), scopes: ccScopes.split(/[\s,]+/).filter(Boolean) }
      : scheme === 'none' ? { scheme: 'none' } : { scheme: 'bearer', token_exchange: true }
    try {
      if (jwks && !/^https?:\/\//.test(jwks)) return toast('error', 'JWKS URL 은 http(s) 여야 합니다 (MSG-004)')
      await send('PATCH', `/api/v1/a2a/agents/${a.id}`, { auth: { ...auth, tls, push_verify: jwks ? { jwks_url: jwks, ...(pushIss ? { issuer: pushIss } : {}) } : null },
        secret: secret || undefined, framework })
      toast('ok', '인증 설정 저장 — 다음 호출부터 적용 · 감사 기록 (MSG-001)')
      setSecret('')
      onSaved()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  return (
    <Card title="원격 에이전트 인증 (게이트웨이가 주입)">
      <h3 className="mb-1.5 text-xs font-semibold">securitySchemes → 게이트웨이 주입</h3>
      <table className="mb-4 w-full text-sm" aria-label="securitySchemes">
        <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-2 py-1.5">스킴</th><th>카드 선언</th><th>주입 방식</th></tr></thead>
        <tbody>{schemes.map(([k, d]) => <tr key={k} className="border-t border-line"><td className="px-2 py-1.5 mono text-xs">{k}</td><td className="text-xs">{d.type} <span className="text-fg-muted">{schemeDecl(d)}</span></td><td className="text-xs">{injection(d, scheme, header)}</td></tr>)}
          {!schemes.length && <tr><td colSpan={3} className="px-2 py-3 text-center text-xs text-fg-muted">Agent Card 에 securitySchemes 선언이 없습니다.</td></tr>}</tbody>
      </table>
      <div className="grid grid-cols-2 gap-3">
        <Field label="방식"><Select value={scheme} onChange={(e) => setScheme(e.target.value)}>
          <option value="exchange">사용자 위임 — RFC 8693 토큰 교환 (act=host)</option><option value="static">정적 Bearer 토큰</option><option value="apiKey">API 키 헤더</option><option value="none">없음</option>
          <option value="oauth2">OAuth2 client_credentials (게이트웨이가 발급·캐시)</option>
        </Select></Field>
        {scheme === 'apiKey' && <Field label="헤더"><Input value={header} onChange={(e) => setHeader(e.target.value)} /></Field>}
        {(scheme === 'static' || scheme === 'apiKey' || scheme === 'oauth2') && <Field label={a.credential_configured ? `${scheme === 'oauth2' ? '클라이언트 시크릿' : '자격 증명'} (비우면 유지)` : scheme === 'oauth2' ? '클라이언트 시크릿' : '자격 증명'} hint="게이트웨이 시크릿 저장소에만 저장, 재표시 없음 (MSG-018)"><Input type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} /></Field>}
        <Field label="OAuth2 토큰 URL" hint={cardTokenUrl ? 'Agent Card securitySchemes 기본값 — 바꿀 수 있음 (운영은 https 만)' : '카드에 OAuth2 선언 없음'}><Input className="mono" value={tokenUrl} placeholder="https://…/token" disabled={scheme !== 'oauth2'} onChange={(e) => setTokenUrl(e.target.value)} /></Field>
        <Field label="OAuth2 클라이언트 ID"><Input className="mono" value={clientId} disabled={scheme !== 'oauth2'} onChange={(e) => setClientId(e.target.value)} placeholder="anx-gateway" /></Field>
        <Field label="OAuth2 스코프" hint="공백 구분"><Input className="mono" value={ccScopes} disabled={scheme !== 'oauth2'} onChange={(e) => setCcScopes(e.target.value)} placeholder="analyze" /></Field>
      </div>
      <h3 className="mt-4 mb-1.5 text-xs font-semibold">푸시 알림 서명 검증 · 프레임워크</h3>
      <div className="grid grid-cols-3 gap-3">
        <Field label="푸시 서명 JWKS URL" hint="비우면 검증 안 함 — 지정하면 서명·5분 이내 발급·본문 해시가 맞아야 수신 (불일치 401·감사)"><Input className="mono" value={jwks} onChange={(e) => setJwks(e.target.value)} placeholder="https://partner.example/.well-known/jwks.json" /></Field>
        <Field label="푸시 JWT 발급자 (선택)"><Input className="mono" value={pushIss} onChange={(e) => setPushIss(e.target.value)} /></Field>
        <Field label="프레임워크" hint={a.framework_source === 'card' ? `카드 추정: ${a.framework} — 비우면 추정값 사용` : '비우면 카드에서 추정'}><Input value={framework} onChange={(e) => setFramework(e.target.value)} placeholder="ADK · LangGraph · CrewAI…" /></Field>
      </div>
      <h3 className="mt-4 mb-1.5 text-xs font-semibold">mTLS (상호 인증)</h3>
      <label className="mb-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={mtls} onChange={(e) => setMtls(e.target.checked)} />mTLS 사용</label>
      <div className="grid grid-cols-2 gap-3">
        <Field label="mTLS CA 시크릿"><Input className="mono" value={ca} disabled={!mtls} onChange={(e) => setCa(e.target.value)} placeholder="tls/partner-ca" /></Field>
        <Field label="mTLS 클라이언트 인증서 시크릿"><Input className="mono" value={cert} disabled={!mtls} onChange={(e) => setCert(e.target.value)} placeholder="tls/gateway-client" /></Field>
      </div>
      <p className="mt-2 text-xs text-fg-muted">위임 방식은 요청 사용자 신원을 짧은 수명 토큰으로 바꿔 전달합니다(감사·추적 가능). 정적 자격 증명은 파트너 API 키 등 교환을 지원하지 않는 에이전트에만 쓰세요. 교환 실패는 MSG-019 로 표시됩니다.</p>
      <Gate perm="a2a:write">{(ok, why) => <Button className="mt-3" variant="primary" disabled={!ok} title={why} onClick={save}>저장</Button>}</Gate>
    </Card>
  )
}

// SCR-G04 ⑥ 토큰 정책 — audience a2a:{id} 의 토큰 교환 정책 (SCR-J04 와 같은 데이터)
const TTLS: [number, string][] = [[60, '1분'], [300, '5분'], [600, '10분'], [900, '15분']]
function TokenPolicy({ agentId }: { agentId: string }) {
  const { me } = useAuth()
  const q = useGet(can(me, 'identity:read') ? '/api/v1/identity/token-exchange/policies' : null)
  const aud = `a2a:${agentId}`
  const pol: Json | undefined = (q.data?.items ?? []).find((x: Json) => x.audience === aud)
  return (
    <Card title="토큰 정책 (원격 호출용 교환 토큰)">
      {!can(me, 'identity:read') ? <p className="text-sm text-fg-muted">권한 필요: identity:read — 보안·IAM 담당이 관리합니다.</p>
        : q.error ? <ErrorBox error={q.error} /> : !q.data ? <Loading /> : <TokenPolicyForm key={`${pol?.id}-${pol?.updated_at}`} aud={aud} pol={pol} onSaved={() => q.refetch()} />}
    </Card>
  )
}

function TokenPolicyForm({ aud, pol, onSaved }: { aud: string; pol?: Json; onSaved: () => void }) {
  const toast = useToast()
  const [ttl, setTtl] = useState<number>(pol?.ttl_seconds ?? 300)
  const [allowed, setAllowed] = useState((pol?.allowed_scopes ?? ['a2a:invoke']).join(' '))
  const [defaults, setDefaults] = useState((pol?.default_scopes ?? ['a2a:invoke']).join(' '))
  const [method, setMethod] = useState(pol?.method ?? 'rfc8693')
  const [enabled, setEnabled] = useState(pol?.enabled ?? true)
  const words = (s: string) => s.split(/[\s,]+/).filter(Boolean)
  const save = async () => {
    try {
      await send('PUT', '/api/v1/identity/token-exchange/policies', { audience: aud, ttl_seconds: ttl, allowed_scopes: words(allowed), default_scopes: words(defaults), method, enabled })
      toast('ok', '토큰 정책 저장 — 감사 기록 (MSG-001)')
      onSaved()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="토큰 수명"><Select value={ttl} onChange={(e) => setTtl(Number(e.target.value))}>
          {(TTLS.some(([v]) => v === ttl) ? TTLS : [...TTLS, [ttl, `${ttl}초`] as [number, string]]).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
        <Field label="갱신" hint="게이트웨이가 사용자·에이전트별로 캐시"><Input value="만료 30초 전 재발급" disabled readOnly /></Field>
        <Field label="허용 스코프" hint="공백 구분"><Input className="mono" value={allowed} onChange={(e) => setAllowed(e.target.value)} /></Field>
        <Field label="기본 스코프" hint="허용 스코프의 부분집합"><Input className="mono" value={defaults} onChange={(e) => setDefaults(e.target.value)} /></Field>
      </div>
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={method === 'obo'} onChange={(e) => setMethod(e.target.checked ? 'obo' : 'rfc8693')} />사용자별 토큰(OBO) · IdP 연계</label>
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />교환 사용</label>
      </div>
      {!pol && <p className="text-xs text-warn">이 에이전트의 교환 정책이 없습니다 — 저장하면 생성됩니다.</p>}
      <Gate perm="identity:write">{(ok, why) => <Button variant="primary" size="sm" disabled={!ok} title={why} onClick={save}>정책 저장</Button>}</Gate>
    </div>
  )
}

// SCR-G04 ⑦ 발급 토큰 — 원격 에이전트가 플랫폼 모델·MCP 를 호출할 때 쓰는 Access Token (subject a2a:{id})
function IssuedTokens({ agentId }: { agentId: string }) {
  const { me } = useAuth()
  const toast = useToast()
  const ok = can(me, 'tokens:issue')
  const q = useGet(ok ? '/api/v1/access-tokens' : null, { subject: `a2a:${agentId}` })
  const items: Json[] = q.data?.items ?? []
  const active = items.filter((t) => t.state === 'active')
  const revoke = async (ids: string[], label: string) => {
    if (!window.confirm(`${label} 을(를) 폐기합니다. 즉시 게이트웨이에 반영됩니다 (MSG-002)`)) return
    try {
      for (const tid of ids) await send('POST', `/api/v1/access-tokens/${tid}/revoke`)
      toast('ok', `토큰 ${ids.length}개 폐기 — 감사 기록`)
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  return (
    <Card title="발급 토큰 (원격 → 모델·MCP 호출용)" bodyClass="p-0"
      actions={ok ? <Button size="sm" variant="danger" disabled={!active.length} onClick={() => revoke(active.map((t) => t.id), `활성 토큰 ${active.length}개`)}>모두 폐기</Button> : undefined}>
      {!ok ? <p className="p-4 text-sm text-fg-muted">권한 필요: tokens:issue</p> : q.error ? <div className="p-3"><ErrorBox error={q.error} /></div> : (
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-1.5">토큰</th><th>대상·스코프</th><th>만료</th><th /></tr></thead>
          <tbody>{items.slice(0, 20).map((t) => <tr key={t.id} className="border-t border-line">
            <td className="px-3 py-1.5"><div className="mono text-xs">{shortId(t.id)}</div><div className="text-[11px] text-fg-muted">{t.name}</div></td>
            <td className="text-xs">{t.audience} <span className="mono text-fg-muted">{(t.scopes ?? []).join(' ')}</span></td>
            <td className="text-xs"><StatusBadge status={t.state} /> <span className="text-fg-muted">{fmtTime(t.expires_at, true)}</span></td>
            <td className="pr-3 text-right">{t.state === 'active' && <Button size="sm" onClick={() => revoke([t.id], shortId(t.id))}>폐기</Button>}</td></tr>)}
            {!items.length && <tr><td colSpan={4} className="px-3 py-6 text-center text-fg-muted">{q.isLoading ? '불러오는 중…' : '발급된 토큰이 없습니다.'}</td></tr>}</tbody>
        </table>
      )}
      <p className="border-t border-line px-3 py-2 text-xs text-fg-muted">폐기는 즉시 게이트웨이에 반영되고 감사에 기록됩니다. 발급은 <Link className="text-primary" to="/console/models/tokens">Access Tokens →</Link></p>
    </Card>
  )
}

// SCR-G07 시험 콘솔 — message/send(시험 API) · message/stream(게이트웨이 SSE) · Part(text·file·data) · contextId 이어 쓰기 · 푸시
type Ev = { at: string; text: string }
function evInfo(r?: Json): { taskId?: string; contextId?: string; state?: string } {
  if (!r) return {}
  const ev = r.statusUpdate ?? r.artifactUpdate ?? r.task ?? r.message ?? r
  return { taskId: ev.taskId ?? (ev.status ? ev.id : undefined), contextId: ev.contextId, state: ev.status?.state ? normState(ev.status.state) : undefined }
}
function evText(r: Json): string {
  const ev = r.statusUpdate ?? r.artifactUpdate ?? r.task ?? r.message ?? r
  const kind = r.statusUpdate ? 'status-update' : r.artifactUpdate ? 'artifact-update' : r.task ? 'task' : r.message ? 'message' : ev.kind ?? 'event'
  if (kind === 'artifact-update') return `artifact-update  ${ev.artifact?.name ?? ev.artifact?.artifactId ?? ''}${ev.append ? ' (append)' : ''}${ev.lastChunk ? ' (last)' : ''}`
  if (ev.status) return `${kind}  ${normState(ev.status.state)}${textOf(ev.status.message?.parts) ? ` — ${textOf(ev.status.message?.parts)}` : ''}`
  return `${kind}  ${textOf(ev.parts)}`
}

function TryConsole({ a }: { a: Json }) {
  const toast = useToast()
  const [mode, setMode] = useState<'send' | 'stream'>('send')
  const [part, setPart] = useState<'text' | 'file' | 'data'>('text')
  const [text, setText] = useState('orders-api 지연 원인 분석')
  const [dataTxt, setDataTxt] = useState('')
  const [dataErr, setDataErr] = useState('')
  const [file, setFile] = useState<{ name: string; mimeType: string; bytes: string } | null>(null)
  const [version, setVersion] = useState('1.0')  // D15-② A2A 1.0 기본 (0.3 은 선택)
  const [blocking, setBlocking] = useState(true)
  const [ctx, setCtx] = useState('')
  const [contexts, setContexts] = useState<string[]>([])
  const [cur, setCur] = useState<{ taskId?: string; contextId?: string; state?: string }>({})
  const [res, setRes] = useState<Json | null>(null)
  const [events, setEvents] = useState<Ev[]>([])
  const [busy, setBusy] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const abort = useRef<AbortController | null>(null)
  const [hook, setHook] = useState('')
  const [hookTok, setHookTok] = useState('')
  const [pushLog, setPushLog] = useState<string[]>([])
  const log = (t: string) => setEvents((e) => [...e, { at: new Date().toISOString(), text: t }])
  const track = (r?: Json) => {
    const x = evInfo(r)
    if (!x.taskId && !x.contextId && !x.state) return
    setCur((c) => ({ taskId: x.taskId ?? c.taskId, contextId: x.contextId ?? c.contextId, state: x.state ?? c.state }))
    if (x.contextId) setContexts((cs) => (cs.includes(x.contextId!) ? cs : [x.contextId!, ...cs].slice(0, 10)))
  }
  const rpc = (op: 'send' | 'stream', message: Json) => ({
    jsonrpc: '2.0', id: `try-${Date.now()}`, method: version === '1.0' ? (op === 'send' ? 'SendMessage' : 'SendStreamingMessage') : op === 'send' ? 'message/send' : 'message/stream',
    params: { message, ...(op === 'send' ? { configuration: version === '1.0' ? { returnImmediately: !blocking } : { blocking } } : {}) },
  })
  const pick = (f?: File) => {
    if (!f) return setFile(null)
    if (f.size > 1_000_000) return toast('error', '시험 콘솔의 file Part 는 1MB 이하만 보낼 수 있습니다.')
    const r = new FileReader()
    r.onload = () => setFile({ name: f.name, mimeType: f.type || 'application/octet-stream', bytes: String(r.result).split(',')[1] ?? '' })
    r.readAsDataURL(f)
  }
  const run = async () => {
    let data: Json | undefined
    if (dataTxt.trim()) {
      try {
        data = JSON.parse(dataTxt)
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('not object')
      } catch {
        setDataErr('data Part 는 JSON 객체여야 합니다 (MSG-004)')
        setPart('data')
        return
      }
    }
    setDataErr('')
    // input-required 인 Task 의 contextId 를 고르면 같은 Task 로 이어 쓴다 (taskId + contextId)
    const taskId = ctx && ctx === cur.contextId && cur.state === 'input-required' ? cur.taskId : undefined
    if (!ctx) setCur({})
    setRes(null)
    setEvents([])
    setBusy(true)
    try {
      if (mode === 'send') {  // file Part 도 시험 API 로 (서버가 base64·크기 검증 → 같은 게이트웨이 경로)
        const r = await send<Json>('POST', `/api/v1/a2a/agents/${a.id}/try`, { text, data, version, blocking, context_id: ctx || undefined, task_id: taskId,
          ...(file ? { files: [file] } : {}) })
        setRes(r)
        track(r.result)
      } else {  // 스트림은 게이트웨이 JSON-RPC 경로(/a2a/{id}) 그대로 — 같은 위임 토큰·가드·Task 영속
        const message: Json = { kind: 'message', role: 'user', parts: [{ kind: 'text', text }, ...(data ? [{ kind: 'data', data }] : []), ...(file ? [{ kind: 'file', file }] : [])],
          ...(taskId ? { taskId } : {}), ...(ctx ? { contextId: ctx } : {}) }
        const ac = new AbortController()
        abort.current = ac
        setStreaming(true)
        const resp = await request(`/a2a/${a.id}`, { method: 'POST', body: rpc('stream', message), headers: { Accept: 'text/event-stream' }, signal: ac.signal })
        if (!(resp.headers.get('content-type') ?? '').includes('event-stream')) {
          const r = await resp.json()
          setRes(r)
          if (r.error) log(`error  ${r.error.message}`)
        } else {
          await readSse(resp, (e) => {
            const m = e.data as Json
            setRes(m)
            if (m?.error) return log(`error  ${m.error.message}`)
            track(m?.result)
            log(evText(m?.result ?? {}))
          }, ac.signal)
        }
      }
    } catch (e: any) {
      if (e?.name === 'AbortError') log('구독 중지 — 원격 Task 는 계속 진행됩니다 (tasks/get·푸시로 이어 받음)')
      else setRes({ error: { message: e.message } })
    } finally {
      setBusy(false)
      setStreaming(false)
      abort.current = null
    }
  }
  const gwId = cur.taskId ? `${a.id}:${cur.taskId}` : ''
  const open = !!cur.taskId && !TERMINAL.includes(cur.state ?? '')
  const cancel = async () => {
    try {
      const r = await cancelTask(gwId, shortId(cur.taskId, 6))
      if (!r) return
      track(r.result)
      log(`tasks/cancel  ${normState(evInfo(r.result).state) || '요청됨'}`)
      toast('ok', 'tasks/cancel 요청 완료')
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const setPush = async () => {
    try {
      const r = await send<Json>('POST', `/a2a/${a.id}`, { jsonrpc: '2.0', id: `push-${Date.now()}`, method: 'tasks/pushNotificationConfig/set',
        params: { taskId: cur.taskId, pushNotificationConfig: { url: hook, ...(hookTok ? { token: hookTok } : {}) } } })
      if (r.error) throw new Error(r.error.message)
      setPushLog((l) => [...l, `${fmtTime(new Date().toISOString())} ${shortId(cur.taskId, 6)} 푸시 설정 — 게이트웨이 수신 후 이 URL 로 중계`])
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const authLabel = a.auth?.scheme === 'apiKey' ? `API 키 헤더 (${a.auth.header ?? 'X-API-Key'})` : a.auth?.scheme === 'none' ? '없음' : a.auth?.token_exchange === false ? '정적 Bearer' : '내 토큰 교환(위임) — 짧은 수명'
  return (
    <div className="grid grid-cols-[380px_1fr] gap-4">
      <Card title="요청">
        <div className="space-y-3">
          <Kv rows={[['대상', <span className="mono text-xs">{a.id} · {a.gateway_path}</span>], ['인증', <span className="text-xs">{authLabel} <span className="text-fg-muted">(인증 설정 탭)</span></span>]]} />
          <fieldset className="flex gap-4 text-sm" aria-label="메서드">
            <label className="flex items-center gap-1.5"><input type="radio" name="try-mode" checked={mode === 'send'} onChange={() => setMode('send')} />{version === '1.0' ? 'SendMessage' : 'message/send'}</label>
            <label className="flex items-center gap-1.5" title={a.streaming ? undefined : 'Agent Card 가 streaming 을 지원하지 않습니다'}><input type="radio" name="try-mode" disabled={!a.streaming} checked={mode === 'stream'} onChange={() => setMode('stream')} />{version === '1.0' ? 'SendStreamingMessage' : 'message/stream'}</label>
          </fieldset>
          <Tabs tabs={[{ id: 'text', label: 'text' }, { id: 'file', label: file ? 'file ●' : 'file' }, { id: 'data', label: dataTxt.trim() ? 'data ●' : 'data' }]} value={part} onChange={setPart} />
          {part === 'text' && <Textarea aria-label="text Part" className="h-28" value={text} onChange={(e) => setText(e.target.value)} />}
          {part === 'file' && (
            <div className="space-y-1 text-sm">
              <input type="file" aria-label="file Part" onChange={(e) => pick(e.target.files?.[0])} />
              {file && <p className="text-xs">{file.name} <span className="text-fg-muted">{file.mimeType}</span> <button type="button" className="text-primary" onClick={() => setFile(null)}>제거</button></p>}
              <p className="text-xs text-fg-muted">파일은 base64 bytes 로 게이트웨이 JSON-RPC 경로에 실립니다 (1MB 이하).</p>
            </div>
          )}
          {part === 'data' && <Field label="data Part (JSON 객체)" error={dataErr}><Textarea className="mono h-28 text-xs" value={dataTxt} onChange={(e) => setDataTxt(e.target.value)} placeholder='{"service": "payment-api"}' /></Field>}
          <div className="flex gap-2">
            <Select value={version} onChange={(e) => setVersion(e.target.value)} aria-label="프로토콜"><option value="1.0">A2A 1.0</option><option value="0.3">A2A 0.3</option></Select>
            {mode === 'send' && <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={blocking} onChange={(e) => setBlocking(e.target.checked)} />blocking</label>}
          </div>
          <Field label="contextId" hint={cur.state === 'input-required' ? '입력 요청(MSG-017) — 이 대화를 고르고 보내면 같은 Task 로 이어집니다' : '새 대화를 고르면 Task 가 새로 생성됩니다'}>
            <Select value={ctx} onChange={(e) => setCtx(e.target.value)}><option value="">새 대화</option>{contexts.map((c) => <option key={c} value={c}>{shortId(c, 6)}{c === cur.contextId && cur.state ? ` · ${cur.state}` : ''}</option>)}</Select>
          </Field>
          <Button variant="primary" className="w-full" onClick={run} loading={busy}>보내기</Button>
          <p className="text-xs text-fg-muted">게이트웨이 경로 그대로 — 위임 토큰(act=host)·trace 전파·Task 영속이 적용됩니다. 시험 호출도 정책·감사·비용에 기록됩니다.</p>
        </div>
      </Card>
      <div className="space-y-4">
        <Card title={`응답${mode === 'stream' ? ' 스트림(SSE)' : ''}${cur.taskId ? ` · ${shortId(cur.taskId, 6)}` : ''}`} actions={cur.state ? <StatusBadge status={cur.state} /> : undefined}>
          {cur.state === 'input-required' && <div role="status" className="mb-3 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-sm">원격 에이전트가 추가 입력을 요청했습니다 (MSG-017) — contextId 에서 이 대화를 고른 뒤 보내세요.</div>}
          {events.length > 0 && <CodeBlock className="mb-3" value={events.map((e) => `${fmtTime(e.at)}  ${e.text}`).join('\n')} maxH="max-h-64" />}
          {res ? <CodeBlock value={res} maxH="max-h-[420px]" /> : !events.length && <p className="text-sm text-fg-muted">응답(Task·Artifacts)이 여기에 표시됩니다.</p>}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={!streaming} title="스트림 구독만 중지 — Task 는 유지" onClick={() => abort.current?.abort()}>■ 중지</Button>
            <Button size="sm" variant="danger" disabled={!open} onClick={cancel}>tasks/cancel</Button>
            {gwId && <Link className="text-xs text-primary hover:underline" to={taskHref(gwId)}>Task 상세 →</Link>}
          </div>
        </Card>
        <Card title="푸시 알림 (webhook)">
          <div className="grid grid-cols-2 gap-3">
            <Field label="수신 URL" hint="게이트웨이가 원격에는 자체 수신 URL(/a2a/push/…)을 등록하고 받은 이벤트를 여기로 중계합니다"><Input className="mono" value={hook} onChange={(e) => setHook(e.target.value)} placeholder="https://hooks.example/a2a" /></Field>
            <Field label="알림 토큰" hint="X-A2A-Notification-Token 헤더로 전달 (MSG-018)"><Input type="password" autoComplete="off" value={hookTok} onChange={(e) => setHookTok(e.target.value)} /></Field>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <span className="text-sm" title="원격 에이전트가 서명한 푸시 JWT 를 JWKS 로 검증 — 인증 설정 탭에서 지정">서명(JWT) 검증 {a.push_verify ? <Badge tone="ok">켜짐</Badge> : <Badge>꺼짐 — 인증 설정에서 JWKS 지정</Badge>}</span>
            <Button size="sm" disabled={!open || !hook || !a.push} title={!a.push ? 'Agent Card 가 pushNotifications 를 지원하지 않습니다' : !open ? '진행 중 Task 가 있어야 합니다 (blocking 해제 또는 stream)' : undefined} onClick={setPush}>이 Task 에 푸시 설정</Button>
          </div>
          {pushLog.length > 0 && <ul className="mono mt-2 space-y-0.5 text-[11px] text-fg-muted">{pushLog.map((l, i) => <li key={i}>{l}</li>)}</ul>}
          <p className="mt-2 text-xs text-fg-muted">수신 기록은 Task 상세의 이벤트에서 확인합니다.</p>
        </Card>
      </div>
    </div>
  )
}

// SCR-G05 A2A Task 목록 — 상태 칩·에이전트·기간 필터, 진행 중 Task 취소(MSG-002), 행 → G06
const CHIP_STATES = ['submitted', 'working', 'input-required', 'completed', 'failed', 'canceled']
const PERIODS: [string, string][] = [['1h', '1시간'], ['24h', '24시간'], ['today', '오늘'], ['7d', '7일'], ['all', '전체']]
const sinceOf = (p: string) => p === 'today' ? new Date(new Date().toDateString()).getTime()
  : Date.now() - ({ '1h': 3600_000, '24h': 86400_000, '7d': 7 * 86400_000 } as Record<string, number>)[p]

export function A2ATasks() {
  const nav = useNavigate()
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const state = sp.get('state') ?? ''
  const agent = sp.get('agent') ?? ''
  const period = sp.get('period') ?? '24h'
  const setParam = (k: string, v: string) => {
    const n = new URLSearchParams(sp)
    if (v) n.set(k, v)
    else n.delete(k)
    setSp(n, { replace: true })
  }
  const q = useGet('/api/v1/a2a/tasks', { limit: 500 }, { refetchInterval: 5_000 })
  const all: Json[] = q.data?.items ?? []
  const since = period === 'all' ? 0 : sinceOf(period) || 0
  const scoped = all.filter((t) => (!agent || t.agent_id === agent) && (!since || new Date(t.updated_at ?? t.created_at).getTime() >= since))
  const rows = state ? scoped.filter((t) => normState(t.state) === state) : scoped
  const agents = [...new Map(all.map((t) => [t.agent_id, t.agent?.name ?? t.agent_id] as [string, string])).entries()]
  const cancel = async (t: Json) => {
    try {
      if (await cancelTask(t.id, shortId(t.remote_task_id || t.id, 6))) {
        toast('ok', 'tasks/cancel 요청 완료')
        q.refetch()
      }
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const columns = [
    col.accessor('id', { header: 'Task', cell: ({ row }) => <Link className="mono text-xs text-primary hover:underline" to={taskHref(row.original.id)} onClick={(e) => e.stopPropagation()}>{shortId(row.original.remote_task_id || row.original.id, 6)}</Link> }),
    col.accessor('agent_id', { header: '에이전트', cell: ({ row }) => <div><div className="text-sm">{row.original.agent?.name ?? row.original.agent_id}</div>{row.original.agent && <TrustBadge tier={row.original.agent.trust_tier} />}</div> }),
    col.accessor('direction', { header: '방향', cell: (c) => <Badge tone={c.getValue() === 'outbound' ? 'a2a' : 'primary'}>{c.getValue() === 'outbound' ? '위임(나감)' : '수신(들어옴)'}</Badge> }),
    col.accessor('state', { header: '상태', cell: (c) => <span className="flex items-center gap-1"><StatusBadge status={c.getValue()} />{normState(c.getValue()) === 'input-required' && <span className="text-[11px] text-warn">MSG-017</span>}</span> }),
    col.accessor('principal', { header: '요청자·세션', cell: ({ row }) => <div className="text-xs"><div>{row.original.principal || '—'}</div><div className="mono text-[11px] text-fg-muted">{row.original.session_id ? shortId(row.original.session_id, 6) : ''}</div></div> }),
    col.display({ id: 'elapsed', header: '경과', cell: ({ row }) => { const t = row.original; const end = TERMINAL.includes(normState(t.state)) ? new Date(t.updated_at).getTime() : Date.now(); return <span className="text-xs tabular-nums">{t.created_at ? fmtDur(end - new Date(t.created_at).getTime()) : '—'}</span> } }),
    col.accessor('artifacts_count', { header: 'Artifacts' }),
    col.accessor('push', { header: '푸시', cell: (c) => c.getValue() ? '✓' : '—' }),
    col.accessor('trace_id', { header: 'trace', cell: (c) => <span onClick={(e) => e.stopPropagation()}><TraceLink id={c.getValue()} /></span> }),
    col.accessor('updated_at', { header: '갱신', cell: (c) => fmtAgo(c.getValue()) }),
    col.display({ id: 'act', header: '', cell: ({ row }) => isOpen(row.original) ? <Button size="sm" variant="danger" onClick={(e) => { e.stopPropagation(); cancel(row.original) }}>취소</Button> : null }),
  ]
  const toolbar = (
    <>
      <Select aria-label="에이전트 필터" className="w-48" value={agent} onChange={(e) => setParam('agent', e.target.value)}><option value="">에이전트: 전체</option>{agents.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
      <Select aria-label="기간 필터" className="w-32" value={period} onChange={(e) => setParam('period', e.target.value)}>{PERIODS.map(([v, l]) => <option key={v} value={v}>기간: {l}</option>)}</Select>
    </>
  )
  return (
    <>
      <PageHeader title="A2A Task" crumbs={['A2A 에이전트', 'Task']} desc="위임·수신 Task 상태 전이와 Artifacts — 푸시 수신은 감시자(watcher)가 반영합니다." />
      <div role="group" aria-label="상태 필터" className="mb-3 flex flex-wrap gap-2">
        {CHIP_STATES.map((s) => (
          <button key={s} type="button" aria-pressed={state === s} onClick={() => setParam('state', state === s ? '' : s)}
            className={cx('flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs', state === s ? 'border-primary bg-primary-soft' : 'border-line bg-surface hover:bg-surface-2')}>
            <StatusBadge status={s} /><span className="font-semibold tabular-nums">{scoped.filter((t) => normState(t.state) === s).length}</span>
          </button>
        ))}
      </div>
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading ? <Loading /> : <DataTable data={rows} columns={columns} toolbar={toolbar} onRowClick={(r) => nav(taskHref(r.id))} empty="Task 가 없습니다." />}
      <div className="mt-4 grid grid-cols-[1fr_360px] gap-4">
        <Card title="Task 상태 머신">
          <div className="flex flex-wrap items-center gap-2 text-xs" aria-label="상태 머신">
            <StatusBadge status="submitted" /><span aria-hidden>→</span><StatusBadge status="working" /><span aria-hidden>⇄</span><StatusBadge status="input-required" />
            <span aria-hidden>→</span><span className="flex gap-1"><StatusBadge status="completed" /><StatusBadge status="failed" /><StatusBadge status="canceled" /></span>
          </div>
          <p className="mt-2 text-xs text-fg-muted">working ↔ input-required 는 반복될 수 있고, 종료 3상태(completed·failed·canceled)는 바뀌지 않습니다. 취소는 진행 중(submitted·working·input-required) 위임 Task 만 가능합니다.</p>
        </Card>
        <Card title="장기 Task 영속">
          <p className="text-xs text-fg-muted">Task 는 게이트웨이 DB 에 영속됩니다. 감시자가 진행 중 Task 를 tasks/get 으로 갱신하고 푸시 수신도 반영하므로, 게이트웨이·호스트가 재시작돼도 이어집니다.</p>
        </Card>
      </div>
    </>
  )
}

// SCR-G06 A2A Task 상세 — 상태 전이 타임라인 · 메시지·Part · input-required 응답(같은 contextId) · 이벤트 · Artifacts
function Timeline({ t }: { t: Json }) {
  const cur = normState(t.state)
  const done = TERMINAL.includes(cur)
  const tr: Json[] | undefined = Array.isArray(t.transitions) && t.transitions.length ? t.transitions : undefined
  const steps: { state: string; at?: string; planned?: boolean }[] = tr ? tr.map((x) => ({ state: normState(x.state), at: x.at ?? x.timestamp })) : [
    { state: 'submitted', at: t.created_at },
    ...(cur !== 'submitted' ? [{ state: 'working', at: cur === 'working' ? t.updated_at : undefined }] : []),
    ...(!done && cur !== 'submitted' && cur !== 'working' ? [{ state: cur, at: t.updated_at }] : []),
    ...(done ? [{ state: cur, at: t.updated_at }] : []),
  ]
  if (!done) steps.push({ state: 'completed', planned: true })
  return (
    <ol aria-label="상태 전이" className="flex flex-wrap items-center gap-2">
      {steps.map((s, i) => (
        <li key={i} className="flex items-center gap-2">
          {i > 0 && <span aria-hidden className="text-fg-muted">→</span>}
          <div className={cx('rounded-lg border px-3 py-1.5 text-center', s.planned ? 'border-dashed border-line text-fg-muted' : 'border-line')}>
            <div className="text-xs">{s.planned ? '종료 (대기)' : <StatusBadge status={s.state} />}</div>
            <div className="mono mt-0.5 text-[10px] text-fg-muted">{s.planned ? '예정' : s.at ? fmtTime(s.at) : '—'}</div>
          </div>
        </li>
      ))}
    </ol>
  )
}

function Parts({ parts }: { parts?: Json[] }) {
  return (
    <div className="space-y-1.5">
      {(parts ?? []).map((p, i) => {
        const kind = p.kind ?? (p.text != null ? 'text' : p.data !== undefined ? 'data' : 'file')
        if (kind === 'text') return <p key={i} className="whitespace-pre-wrap">{p.text}</p>
        if (kind === 'data') return <CodeBlock key={i} value={p.data} maxH="max-h-40" />
        const f = p.file ?? p
        const uri: string | undefined = f.uri ?? f.url
        return <div key={i} className="text-xs"><Badge>file</Badge> <span className="font-medium">{f.name ?? f.filename ?? 'file'}</span> <span className="text-fg-muted">{f.mimeType ?? f.mediaType ?? ''}</span>
          {uri && /^https?:\/\//.test(uri) && <a className="ml-1 text-primary" href={uri} target="_blank" rel="noreferrer">열기 ↗</a>}</div>
      })}
    </div>
  )
}

const partKinds = (a: Json) => [...new Set((a.parts ?? []).map((p: Json) => p.kind ?? (p.text != null ? 'text' : p.data !== undefined ? 'data' : 'file')))].join(', ')
const sizeOf = (a: Json) => (a.parts ?? []).reduce((n: number, p: Json) => n + (p.text != null ? new TextEncoder().encode(p.text).length
  : p.data !== undefined ? JSON.stringify(p.data).length : p.file?.bytes ? Math.floor((p.file.bytes.length * 3) / 4) : 0), 0)
const fmtBytes = (n: number) => (n < 1024 ? `${n}B` : n < 1048576 ? `${(n / 1024).toFixed(1)}KB` : `${(n / 1048576).toFixed(1)}MB`)

function Reply({ t, onSent }: { t: Json; onSent: () => void }) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [dataTxt, setDataTxt] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const go = async () => {
    let data: Json | undefined
    if (dataTxt.trim()) {
      try {
        data = JSON.parse(dataTxt)
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('not object')
      } catch {
        setErr('data Part 는 JSON 객체여야 합니다 (MSG-004)')
        return
      }
    }
    setErr('')
    setBusy(true)
    try {
      const r = await send<Json>('POST', `/api/v1/a2a/agents/${t.agent_id}/try`, { text, data, task_id: t.remote_task_id, context_id: t.context_id || undefined, blocking: false })
      if (r.error) throw new Error(r.error.message)
      toast('ok', '응답 전송 — 같은 contextId 로 이어갑니다')
      setText('')
      setDataTxt('')
      onSent()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="mt-4 space-y-2">
      <div role="status" className="rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-sm">원격 에이전트가 추가 입력을 요청했습니다 — 요청 사용자 채팅에 입력 요청이 전달됩니다 (MSG-017).</div>
      <Field label="응답 (같은 contextId)"><Textarea className="h-20" value={text} onChange={(e) => setText(e.target.value)} /></Field>
      <Field label="data Part (JSON, 선택)" error={err}><Textarea className="mono h-14 text-xs" value={dataTxt} onChange={(e) => setDataTxt(e.target.value)} placeholder='{"since": "2026-09-29"}' /></Field>
      <Gate perm="a2a:read">{(ok, why) => <Button variant="primary" disabled={!ok || !text.trim()} title={why} loading={busy} onClick={go}>응답 보내기</Button>}</Gate>
    </div>
  )
}

export function A2ATask() {
  const { id: raw = '' } = useParams()
  const id = safeDecode(raw)
  const { me } = useAuth()
  const toast = useToast()
  const q = useGet(apiTask(id), undefined, { refetchInterval: (query) => (TERMINAL.includes(normState((query.state.data as Json | undefined)?.state)) ? false : 5_000) })
  const t = q.data
  const ag = useGet(t && t.direction === 'outbound' && can(me, 'a2a:read') ? `/api/v1/a2a/agents/${t.agent_id}` : null)
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.refetch()} />
  if (!t) return <Loading />
  const st = normState(t.state)
  const agentName = ag.data?.name ?? t.agent_id
  const label = shortId(t.remote_task_id || t.id, 6)
  const download = async (art: Json) => {
    try {
      const aid = art.artifactId || art.artifact_id
      const res = await request(`${apiTask(t.id)}/artifacts/${encodeURIComponent(aid)}`, { headers: { Accept: '*/*' } })
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      Object.assign(document.createElement('a'), { href: url, download: art.name || `${aid}.${blob.type.includes('json') ? 'json' : 'txt'}` }).click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const cancel = async () => {
    try {
      if (await cancelTask(t.id, label)) {
        toast('ok', 'tasks/cancel 요청 완료')
        q.refetch()
      }
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const events: Json[] | undefined = Array.isArray(t.events) && t.events.length ? t.events : undefined
  const proto = (e: Json) => e.protocol ? (e.protocol.client === e.protocol.upstream ? `A2A ${e.protocol.client}` : `A2A ${e.protocol.client}→${e.protocol.upstream} 변환`) : ''
  const evLines = events ? events.map((e) => [fmtTime(e.at ?? e.ts), (e.source ?? '').toUpperCase(), e.kind ?? '', normState(e.state), e.text ?? e.artifact ?? '', proto(e), e.http_status ?? ''].filter(Boolean).join('  '))
    : (t.history ?? []).map((m: Json) => `${m.role ?? m.kind ?? ''}  ${textOf(m.parts)}`)
  return (
    <>
      <PageHeader title={`${label} — ${agentName}`} crumbs={['A2A 에이전트', 'Task', label]} desc={<span className="mono text-xs">{t.id}</span>}
        actions={isOpen(t) ? <Button variant="danger" size="sm" onClick={cancel}>tasks/cancel</Button> : undefined} />
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <StatusBadge status={t.state} />
        <Badge title={t.context_id ?? undefined}>contextId {t.context_id ? shortId(t.context_id, 6) : '—'}</Badge>
        <span className="flex items-center gap-1">trace <TraceLink id={t.trace_id} /></span>
        <Badge>요청자 {t.principal || '—'}</Badge>
        <Badge tone={t.direction === 'outbound' ? 'a2a' : 'primary'}>{t.direction === 'outbound' ? '위임(나감)' : '수신(들어옴)'}</Badge>
        {t.direction === 'outbound' && can(me, 'a2a:read') && <Link className="text-primary hover:underline" to={`/console/a2a/${t.agent_id}`}>에이전트 →</Link>}
      </div>
      <Card className="mb-4" title="상태 전이">
        <Timeline t={t} />
        {!t.transitions && <p className="mt-2 text-[11px] text-fg-muted">중간 전이 시각은 기록되지 않아 생성·최종 갱신 시각만 표시합니다 (—).</p>}
      </Card>
      <div className="grid grid-cols-[1fr_1fr] gap-4">
        <Card title="메시지 · Part">
          <div className="space-y-3 text-sm">
            {(t.history ?? []).map((m: Json, i: number) => (
              <div key={m.messageId ?? i}>
                <div className="mb-1 text-xs text-fg-muted">{m.role === 'agent' ? `agent (${agentName})` : 'user (호스트·Executor)'}</div>
                <div className={cx('rounded-md border px-3 py-2', m.role === 'agent' ? 'border-line' : 'border-primary/30 bg-primary-soft/40')}><Parts parts={m.parts} /></div>
              </div>
            ))}
            {!t.history?.length && <p className="text-fg-muted">{t.status_message || '메시지 이력이 없습니다.'}</p>}
          </div>
          {st === 'input-required' && t.direction === 'outbound' && <Reply t={t} onSent={() => q.refetch()} />}
        </Card>
        <div className="space-y-4">
          <Card title="스트리밍·푸시 이벤트">
            <CodeBlock value={evLines.join('\n') || '(이벤트 없음)'} maxH="max-h-56" />
            <p className="mt-2 text-xs text-fg-muted">{t.push ? '푸시 알림 구독 중 — 원격 → 게이트웨이(/a2a/push/…) 수신 후 반영·중계합니다.' : '푸시 미설정 — 감시자가 tasks/get 으로 진행 중 상태를 반영합니다.'}
              {!events && ' 이벤트 수신 로그는 아직 제공되지 않아 메시지 이력으로 대신 표시합니다.'}</p>
          </Card>
          <Card title="Artifacts" bodyClass="p-0">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-1.5">이름</th><th>유형</th><th>크기</th><th /></tr></thead>
              <tbody>{(t.artifacts ?? []).map((art: Json) => (
                <tr key={art.artifactId || art.artifact_id} className="border-t border-line align-top">
                  <td className="px-3 py-1.5"><details><summary className="cursor-pointer">{art.name || shortId(art.artifactId || art.artifact_id, 6)}</summary><div className="mt-1 text-xs"><Parts parts={art.parts} /></div></details>
                    {art.description && <div className="text-[11px] text-fg-muted">{art.description}</div>}</td>
                  <td className="py-1.5 text-xs">{partKinds(art)}</td>
                  <td className="py-1.5 text-xs tabular-nums">{fmtBytes(sizeOf(art))}</td>
                  <td className="py-1.5 pr-3 text-right"><Button size="sm" onClick={() => download(art)}>다운로드</Button></td>
                </tr>
              ))}
                {!t.artifacts?.length && <tr><td colSpan={4} className="px-3 py-6 text-center text-fg-muted">Artifact 가 없습니다.</td></tr>}</tbody>
            </table>
            <p className="border-t border-line px-3 py-2 text-xs text-fg-muted">수신 Artifact 는 게이트웨이 입력 가드(정제)를 거쳐 Task 에 저장됩니다 · 갱신 {fmtTime(t.updated_at, true)}</p>
          </Card>
        </div>
      </div>
    </>
  )
}
