// 채팅 셀 — SCR-C01 홈·새 대화(준비 상태) · C02 실행 계획 패널 · C03 스트리밍·근거 인용·응답 메타·피드백·다시 생성·편집 · C04 도구 실행 승인(맥락·세션 기억) · C06 부분 실패·재시도·계속 · C07 대화 목록
import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router'
import { ExtendTime } from '@/components/ExtendTime'
import { GlobalBanners, TopBar } from '@/components/Shell'
import { Badge, Button, Card, CodeBlock, Drawer, ErrorBox, Kv, Loading, Modal, RiskChip, Select, SkipLink, StatusBadge, Textarea, TraceLink, TypeBadge, cx, useToast } from '@/components/ui'
import { request, type Json } from '@/lib/api'
import { can, useAuth } from '@/lib/auth'
import { fmtAgo, fmtMs, fmtNum, fmtUsd } from '@/lib/format'
import { readSse } from '@/lib/sse'
import { queryClient, send, useGet } from '@/lib/query'
import { ConsentCard, SlashMenu } from './Consent'
import { DelegationCards } from './Delegation'
import { initialRun, isEnd, progress, reduceRun, replay, type Approval, type Citation, type RunState } from './runState'

const EXAMPLES: Record<string, string[]> = {
  'tpl-qa': ['장애 대응 런북 알려줘', '휴가 규정에서 연차 이월 기준 알려줘'],
  'tpl-issue-report': ['ORION 이번 주 이슈 요약해서 주간 보고 페이지로 만들어줘', 'ORI-1423 관련 문서와 결정 사항 찾아줘'],
  'tpl-incident': ['orders-api 장애 원인 분석', '결제 API 지연 원인 분석을 SRE 에이전트에 맡겨줘'],
}
const NODE_LABEL: Record<string, string> = { planner: 'Planner', retriever: 'Retriever', summarize: 'Summarize', executor: 'Executor', remote: '원격 A2A' }

type Ctx = { profiles: Json[]; refreshSessions: () => void }
type Pending = { content: string; data_class?: string; attachments?: { name: string; mime_type?: string; text?: string; attachment_id?: string; kind?: string }[] }
const ACCEPT = '.txt,.md,.csv,.json,.log,.yaml,.yml,.pdf,.docx,.xlsx,.png,.jpg,.jpeg,.gif,.webp'
const MAX_MB = 20

export function ChatLayout() {
  const { me } = useAuth()
  const { sid } = useParams()
  const nav = useNavigate()
  const sessions = useGet('/api/v1/sessions', undefined, { refetchInterval: 30_000 })
  const profiles = useGet('/api/v1/agents')
  const items: Json[] = profiles.data?.items?.filter((p: Json) => p.status === 'published') ?? []
  return (
    <div className="flex h-full min-w-[768px]">
      <SkipLink />
      <aside className="flex w-[240px] shrink-0 flex-col border-r border-line bg-surface">
        <Link to="/chat" className="flex h-12 items-center gap-2 px-4 font-semibold text-navy">
          <span className="grid size-7 place-items-center rounded-full bg-primary text-sm text-white">N</span>AgentNexus 채팅
        </Link>
        <div className="px-3"><Button variant="primary" className="w-full" onClick={() => nav('/chat')}>+ 새 대화</Button></div>
        <nav aria-label="대화 목록" className="mt-3 flex-1 overflow-auto px-2">
          {(sessions.data?.items ?? []).map((s: Json) => (
            <NavLink key={s.id} to={`/chat/${s.id}`} className={({ isActive }) => cx('block truncate rounded-md px-3 py-2 text-sm', isActive ? 'bg-primary-soft font-medium text-primary' : 'hover:bg-surface-2')} title={s.title}>
              {s.title}<span className="block text-[11px] font-normal text-fg-muted">{fmtAgo(s.updated_at || s.created_at)}</span>
            </NavLink>
          ))}
          {!sessions.data?.items?.length && <p className="px-3 text-xs text-fg-muted">대화 이력이 없습니다.</p>}
        </nav>
        <Link to="/chat/history" className="border-t border-line px-4 py-2 text-xs text-fg-muted hover:text-fg">대화 이력 · 검색 →</Link>
        {can(me, 'agents:read') && can(me, 'observe:read') && <Link to="/console" className="border-t border-line px-4 py-2 text-xs text-fg-muted hover:text-fg">관리 콘솔 →</Link>}
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar extra={<span className="text-sm text-fg-muted">{sid ? '' : '새 대화'}</span>} />
        <GlobalBanners />
        <main id="main" tabIndex={-1} className="flex min-h-0 flex-1 flex-col outline-none">
          <Outlet context={{ profiles: items, refreshSessions: () => sessions.refetch() } satisfies Ctx} />
        </main>
      </div>
    </div>
  )
}

/** 첨부 업로드 (FR-A01, SCR-C01) — PDF·docx·xlsx·이미지·텍스트 20MB 이하, 서버가 텍스트를 추출해 요약 근거로 쓴다. */
async function readFiles(files: FileList | null) {
  const out: NonNullable<Pending['attachments']> = []
  for (const f of Array.from(files ?? []).slice(0, 5)) {
    if (f.size > MAX_MB * 1024 * 1024) throw new Error(`${f.name}: ${MAX_MB}MB 를 넘는 첨부는 지원하지 않습니다 (MSG-004).`)
    const fd = new FormData()
    fd.append('file', f)
    const r = await (await request('/api/v1/chat/attachments', { method: 'POST', body: fd })).json()
    out.push({ attachment_id: r.id, name: r.name, mime_type: r.mime_type, kind: r.kind })
  }
  return out
}

function Composer({ onSend, busy, onStop, placeholder, seed, profileId }: { onSend: (p: Pending) => void; busy?: boolean; onStop?: () => void; placeholder?: string; seed?: { text: string; n: number }; profileId?: string }) {
  const [text, setText] = useState('')
  const [slash, setSlash] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (seed?.n) {
      setText(seed.text)
      input.current?.focus()
    }
  }, [seed?.n, seed?.text])
  const [dc, setDc] = useState('')
  const [att, setAtt] = useState<NonNullable<Pending['attachments']>>([])
  const [err, setErr] = useState('')
  const submit = () => {
    if (!text.trim() || busy) return
    onSend({ content: text.trim(), data_class: dc || undefined, attachments: att.length ? att : undefined })
    setText('')
    setAtt([])
  }
  return (
    <div className="relative rounded-xl border border-primary/30 bg-surface p-3">
      {slash && profileId && (
        <SlashMenu profileId={profileId} onClose={() => setSlash(false)} onAttach={(a) => setAtt((x) => [...x.filter((y) => y.name !== a.name), a].slice(0, 5))}
          onInsert={(t) => { setText(t); input.current?.focus() }} />
      )}
      <Textarea ref={input} aria-label="요청 입력" rows={2} value={text} placeholder={placeholder || '자연어로 요청하세요 · / 로 MCP 리소스·프롬프트 · Enter 전송, Shift+Enter 줄바꿈'} className="resize-none border-0 px-0 focus:outline-none"
        onChange={(e) => { if (e.target.value === '/' && profileId) { setSlash(true); setText('') } else setText(e.target.value) }} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() } }} />
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <label className="cursor-pointer rounded border border-line px-2 py-1 hover:bg-surface-2">📎 첨부
          <input type="file" multiple className="sr-only" accept={ACCEPT} onChange={(e) => readFiles(e.target.files).then((a) => { setAtt(a); setErr('') }).catch((x) => setErr(x.message))} />
        </label>
        {profileId && <button type="button" className="rounded border border-line px-2 py-1 hover:bg-surface-2" onClick={() => setSlash((s) => !s)} aria-expanded={slash}>/ 리소스·프롬프트</button>}
        {att.map((a) => <Badge key={a.name}>{a.kind === 'image' ? '🖼' : a.kind === 'document' ? '📄' : '📝'} {a.name} <button type="button" aria-label={`${a.name} 제거`} onClick={() => setAtt(att.filter((x) => x !== a))}>✕</button></Badge>)}
        <Select aria-label="데이터 등급" value={dc} onChange={(e) => setDc(e.target.value)} className="h-7 w-40 text-xs">
          <option value="">데이터 등급: 프로파일 기본</option><option value="public">public</option><option value="internal">internal (로컬 모델)</option><option value="confidential">confidential (로컬 모델)</option>
        </Select>
        {err && <span role="alert" className="text-danger">{err}</span>}
        <span className="ml-auto" />
        {busy && onStop ? <Button size="sm" variant="secondary" onClick={onStop}>■ 중지</Button> : <Button size="sm" variant="primary" disabled={!text.trim()} onClick={submit}>보내기 ↵</Button>}
      </div>
    </div>
  )
}

export function ChatHome() {
  const { profiles } = useOutletContext<Ctx>()
  const { me } = useAuth()
  const nav = useNavigate()
  const [sp, setSp] = useSearchParams()
  const toast = useToast()
  const blocks = useGet('/api/v1/agents/tools')
  const ready = useGet('/api/v1/chat/profiles')
  const readiness: Record<string, Json> = Object.fromEntries((ready.data?.items ?? []).map((c: Json) => [c.id, c]))
  const selected = sp.get('profile') || profiles.find((p) => p.id === 'tpl-qa')?.id || profiles[0]?.id
  const profile = profiles.find((p) => p.id === selected)
  const risk = useMemo(() => Object.fromEntries((blocks.data?.tools ?? []).map((t: Json) => [t.qualified, t])), [blocks.data])
  const [busy, setBusy] = useState(false)
  const [seed, setSeed] = useState({ text: '', n: 0 })
  const start = async (p: Pending) => {
    setBusy(true)
    try {
      const s = await send<Json>('POST', '/api/v1/sessions', { profile_id: selected, title: p.content.slice(0, 40) })
      nav(`/chat/${s.id}`, { state: { send: p } })
    } catch (e: any) {
      toast('error', e.message)
      setBusy(false)
    }
  }
  return (
    <div className="mx-auto w-full max-w-4xl flex-1 overflow-auto p-6">
      <h1 className="text-xl font-semibold text-navy">안녕하세요, {me?.name} 님 — 무엇을 도와드릴까요?</h1>
      <p className="mt-1 text-sm text-fg-muted">프로파일을 고르고 자연어로 요청하세요. 모든 도구·모델 호출은 AI 게이트웨이를 거칩니다.</p>
      <div className="mt-5 grid grid-cols-3 gap-3">
        {profiles.map((p) => {
          const rd = readiness[p.id]
          const off = rd ? !rd.ready : false
          return (
          <button key={p.id} type="button" onClick={() => setSp({ profile: p.id })} aria-pressed={p.id === selected} disabled={off}
            title={off ? `준비 안 됨 — 미설정: ${(rd.missing ?? []).join(', ')} (운영자에게 즉시 사용 요청)` : undefined}
            className={cx('rounded-lg border p-4 text-left disabled:cursor-not-allowed disabled:opacity-60', p.id === selected ? 'border-primary bg-primary-soft/50 ring-1 ring-primary' : 'border-line bg-surface hover:border-primary/40')}>
            <div className="flex items-center gap-2"><TypeBadge type="profile" />{off && <Badge tone="warn">준비 안 됨</Badge>}{p.id === selected && <span className="ml-auto text-xs text-primary">✓ 선택됨</span>}</div>
            <div className="mt-2 font-semibold">{p.name}</div>
            <p className="mt-1 line-clamp-2 text-xs text-fg-muted">{p.description}</p>
            <div className="mt-2 flex flex-wrap gap-1">
              {(p.tools ?? []).slice(0, 2).map((t: string) => <Badge key={t} tone="mcp">MCP · {t.split('.')[0]}</Badge>)}
              {(p.remote_agents ?? []).slice(0, 1).map((a: string) => <Badge key={a} tone="a2a">A2A · {a}</Badge>)}
            </div>
            {off && <p className="mt-1 text-[11px] text-warn">미설정: {(rd.missing ?? []).join(', ')}</p>}
          </button>
          )
        })}
      </div>
      {profile && (
        <>
          <h2 className="mt-6 mb-2 text-sm font-semibold">예시 요청 — 누르면 입력창에 채워집니다</h2>
          <div className="grid grid-cols-2 gap-2">
            {(EXAMPLES[profile.id] ?? (readiness[profile.id]?.sample ? [readiness[profile.id].sample] : [`${profile.name} 로 무엇을 할 수 있나요?`])).map((ex) => (
              <button key={ex} type="button" className="rounded-full border border-line bg-surface px-4 py-2 text-left text-sm hover:border-primary/40"
                onClick={() => setSeed({ text: ex, n: seed.n + 1 })}>↗ {ex}</button>
            ))}
          </div>
          <Card title="이 프로파일이 쓰는 연결" className="mt-4">
            <ul className="space-y-1.5 text-sm">
              {(profile.tools ?? []).map((t: string) => (
                <li key={t} className="flex items-center gap-2"><TypeBadge type="mcp" /><span className="mono">{t}</span>
                  {risk[t] ? <RiskChip risk={risk[t].risk} /> : <Badge tone="muted">미허용</Badge>}{risk[t]?.approval_required && <Badge tone="warn">승인 필요</Badge>}</li>
              ))}
              {(profile.remote_agents ?? []).map((a: string) => <li key={a} className="flex items-center gap-2"><TypeBadge type="a2a" /><span className="mono">{a}</span></li>)}
              {(profile.models ?? []).map((m: string) => <li key={m} className="flex items-center gap-2"><TypeBadge type="model" /><span className="mono">{m}</span><span className="text-xs text-fg-muted">라우팅 규칙 적용</span></li>)}
            </ul>
            <p className="mt-2 text-xs text-fg-muted">쓰기(고위험) 도구는 실행 전 승인을 요청합니다 (SCR-C04).</p>
          </Card>
        </>
      )}
      {!profiles.length && <p className="mt-4 text-sm text-fg-muted">게시된 프로파일이 없습니다. 개발자에게 카탈로그 즉시 사용을 요청하세요.</p>}
      <div className="mt-5"><Composer onSend={start} busy={busy} seed={seed} profileId={selected} /></div>
    </div>
  )
}

function ApprovalModal({ approval, onClose, onDecided }: { approval: Approval | null; onClose: () => void; onDecided: () => void }) {
  const toast = useToast()
  const detail = useGet(approval ? `/api/v1/approvals/${approval.id}` : null)
  const [busy, setBusy] = useState('')
  const [remember, setRemember] = useState(false)
  const [full, setFull] = useState(false)
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])
  if (!approval) return null
  const ctx: Json = detail.data?.context ?? {}
  const exp = detail.data?.expires_at ? new Date(detail.data.expires_at).getTime() - now : null
  const decide = async (action: 'approve' | 'reject') => {
    if (action === 'reject' && !confirm('이 도구 호출을 거부할까요? 해당 단계는 건너뜁니다 (MSG-002).')) return
    setBusy(action)
    try {
      await send('POST', `/api/v1/approvals/${approval.id}/${action}`, { note: action === 'approve' ? '채팅에서 승인' : '채팅에서 거부', ...(action === 'approve' && remember ? { remember: true } : {}) })
      toast('ok', action === 'approve' ? '승인했습니다 — 실행을 재개합니다.' : '거부했습니다 — 해당 단계를 건너뜁니다.')
      queryClient.invalidateQueries({ predicate: (q) => String(q.queryKey[0]).startsWith('/api/v1/approvals') })
      onDecided()
      onClose()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy('')
    }
  }
  const argText = JSON.stringify(approval.args ?? {}, null, 2)
  return (
    <Modal open onClose={onClose} title={`도구 실행 승인 — ${approval.risk === 'high' ? '고위험' : '정책 승인'} 쓰기 작업`} wide
      footer={<><Button onClick={() => decide('reject')} loading={busy === 'reject'}>거부</Button><Button variant="primary" onClick={() => decide('approve')} loading={busy === 'approve'}>승인하고 실행</Button></>}>
      <div className="flex items-center gap-2"><TypeBadge type="mcp" /><span className="mono font-semibold">{approval.tool}</span><span className="text-xs text-fg-muted">{ctx.impact?.server}</span><RiskChip risk={approval.risk} /></div>
      <div className="mt-3 grid grid-cols-2 gap-4 text-sm">
        <div className="space-y-3">
          <Kv rows={[['요청 출처', ctx.source?.profile ? `${ctx.source.profile} › ${NODE_LABEL[ctx.source.node] ?? ctx.source.node ?? '—'}${ctx.source.step ? ` (${ctx.source.step.replace('/', '단계 중 ')})` : ''}` : approval.step],
            ['실행 신원', ctx.identity ? <span>{ctx.identity.name} — {ctx.identity.method}<br /><span className="mono text-xs">{ctx.identity.audience} · scope {(ctx.identity.scopes ?? []).join(' ') || '정책 기본'}</span></span> : '—'],
            ['대화', <span>{ctx.source?.session ?? '—'} · trace <TraceLink id={ctx.source?.trace_id} /></span>],
            ['승인자', approval.approver === 'approvers' ? '별도 승인자(직무 분리)' : '요청자 본인']]} />
          <div><div className="mb-1 text-xs font-medium text-fg-muted">인자 (스키마 검증 ✓)</div><CodeBlock value={full || argText.length < 600 ? argText : `${argText.slice(0, 600)}…`} maxH="max-h-56" />
            {argText.length >= 600 && <Button size="sm" variant="ghost" onClick={() => setFull(!full)}>{full ? '접기' : `본문 전체 보기 (${(argText.length / 1024).toFixed(1)}KB)`}</Button>}</div>
        </div>
        <div className="space-y-3">
          <section aria-label="영향 요약"><div className="mb-1 text-xs font-medium text-fg-muted">영향 (MSG-010)</div>
            <ul className="list-disc space-y-0.5 pl-5 text-sm"><li>대상: {ctx.impact?.target ?? approval.tool} — {ctx.impact?.description || '쓰기 작업'}</li><li>공개 범위: {ctx.impact?.visibility ?? '—'}</li><li>되돌리기: {ctx.impact?.reversible ?? '—'}</li></ul></section>
          <section aria-label="정책 근거"><div className="mb-1 text-xs font-medium text-fg-muted">정책 근거</div>
            <ul className="space-y-0.5 text-xs">{(ctx.checks ?? []).map((c: Json) => <li key={c.key} className={c.ok ? 'text-ok' : 'text-danger'}>{c.ok ? '✓' : '✗'} <span className="text-fg">{c.detail}</span></li>)}</ul></section>
          <p className={cx('text-sm', exp != null && exp < 60_000 ? 'text-danger' : 'text-fg-muted')}>⏱ {exp == null ? '—' : exp > 0 ? `남은 시간 ${Math.floor(exp / 60000)}:${String(Math.floor((exp % 60000) / 1000)).padStart(2, '0')} — 만료 시 자동 거부` : '만료됨 — 자동 거부'}</p>
          {detail.data && <div className="flex flex-wrap items-center gap-2"><ExtendTime id={approval.id} extensions={detail.data.extensions} max={detail.data.max_extensions} step={detail.data.extend_step_s}
            left={exp == null ? null : Math.max(0, Math.round(exp / 1000))} onExtended={() => detail.refetch()} /></div>}
          <label className={cx('flex items-center gap-2 text-sm', ctx.remember_allowed === false && 'text-fg-muted')} title={ctx.remember_allowed === false ? '고위험 도구는 매번 승인합니다 (Host 승인 경계)' : undefined}>
            <input type="checkbox" checked={remember} disabled={ctx.remember_allowed === false} onChange={(e) => setRemember(e.target.checked)} />이 세션 동안 다시 묻지 않기{ctx.remember_allowed === false && ' — 고위험 도구는 선택 불가(항상 승인)'}</label>
        </div>
      </div>
      <p className="mt-3 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-xs">{approval.tool} 는 쓰기 작업입니다. 인자와 영향을 확인한 뒤 승인하세요 (MSG-010). 결정·인자 해시는 감사 로그에 남습니다.</p>
    </Modal>
  )
}

const KIND_LABEL: Record<string, string> = { retriever: 'MCP', executor: 'MCP 쓰기', remote: 'A2A', summarize: '모델', planner: '계획' }

// SCR-C06 부분 실패·재시도 — 작업별 결과 표 · 실패만 재시도 · 부분 결과로 계속 · trace
function PartialCard({ run, onRetry, onContinue }: { run: Json; onRetry: () => void; onContinue: () => void }) {
  const steps: Json[] = run.steps ?? []
  const nodes: Record<string, Json> = Object.fromEntries((run.meta?.nodes ?? []).map((n: Json) => [n.step, n]))
  const bad = steps.filter((s) => ['failed', 'rejected', 'skipped'].includes(s.status) && !(s.error ?? '').startsWith('분기 미선택') && !(s.error ?? '').startsWith('조건 불충족'))
  const ok = steps.filter((s) => s.status === 'succeeded')
  const [failFirst, setFailFirst] = useState(true)
  const rows = failFirst ? [...bad, ...steps.filter((s) => !bad.includes(s))] : steps
  if (!bad.length) return null
  return (
    <Card className="border-warn/50" title={<span>⚠ {run.status === 'partial' ? '부분 실패' : '실행 실패'} — {ok.length}개 작업 완료, {bad.length}개 미완료{bad[0] ? ` (${bad[0].tool || bad[0].agent || bad[0].name}: ${(bad[0].error ?? '').slice(0, 60)})` : ''}{run.status === 'partial' ? '. 부분 결과를 표시합니다 (MSG-014)' : ''}</span>}
      actions={<label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={failFirst} onChange={(e) => setFailFirst(e.target.checked)} />실패 먼저</label>}>
      <table className="w-full text-xs">
        <thead className="text-left text-fg-muted"><tr><th className="py-1">작업</th><th>유형</th><th>결과</th><th>사유 · 시도</th><th>지연</th></tr></thead>
        <tbody>{rows.map((s) => <tr key={s.name} className={cx('border-t border-line', bad.includes(s) && 'bg-danger/5')}>
          <td className="py-1">{NODE_LABEL[s.node] ?? s.node} · <span className="mono">{s.tool || s.agent || s.name}</span></td><td>{KIND_LABEL[s.node] ?? s.node}</td>
          <td><StatusBadge status={s.status} /></td><td>{s.error ? `${s.error.slice(0, 80)}` : s.output?.count != null ? `${s.output.count}건` : '—'}{s.attempts > 1 ? ` · 재시도 ${s.attempts}/${s.attempts}` : ''}</td>
          <td>{fmtMs(nodes[s.name]?.latency_ms)}</td></tr>)}</tbody>
      </table>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" onClick={onRetry}>↻ 실패만 재시도</Button>
        {!run.meta?.accepted_partial && <Button size="sm" onClick={onContinue}>부분 결과로 계속</Button>}
        <span className="text-xs text-fg-muted">trace <TraceLink id={run.trace_id} /> (실패 스팬)</span>
      </div>
      {run.status === 'partial' && <p className="mt-2 text-xs text-fg-muted">▨ 빠진 근거: {bad.map((s) => s.tool || s.agent || s.name).join(', ')} — 그 근거가 필요한 항목은 비워 두었습니다.</p>}
    </Card>
  )
}

// SCR-C03 ⑦⑧ 근거 요약·응답 메타 · C06 ⑥⑦ 폴백·한도 안내 — 실행이 끝난 뒤 실행 메타로
function RunFooter({ runId, status, sid, message, onRegenerate, onRetry, onContinue }: { runId: string; status: string; sid?: string; message?: Json; onRegenerate?: () => void; onRetry: () => void; onContinue: () => void }) {
  const toast = useToast()
  const q = useGet(`/api/v1/runs/${runId}`, { v: status })
  const [fb, setFb] = useState<string | null | undefined>(undefined)
  const r = q.data
  if (!r) return null
  const m: Json = r.meta ?? {}
  const rating = fb === undefined ? message?.feedback?.rating ?? null : fb
  const rate = async (v: 'up' | 'down') => {
    if (!sid || !message) return
    const next = rating === v ? null : v
    try {
      await send('POST', `/api/v1/sessions/${sid}/messages/${message.id}/feedback`, { rating: next })
      setFb(next)
      if (next) toast('ok', '피드백을 남겼습니다 — 품질 개선에 씁니다.')
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const copy = () => navigator.clipboard?.writeText(r.result ?? '').then(() => toast('ok', '답변을 복사했습니다'))
  return (
    <div className="space-y-2">
      {['partial', 'failed'].includes(r.status) && <PartialCard run={r} onRetry={onRetry} onContinue={onContinue} />}
      {(m.fallbacks ?? []).map((f: Json, i: number) => <div key={i} role="status" className="rounded-md border border-model/40 bg-model/5 px-3 py-2 text-xs">↪ {f.requested} 가 응답하지 않아 {f.chosen} 로 전환했습니다 (게이트웨이 폴백 · MSG-015)</div>)}
      {(m.rate_limited ?? []).map((x: Json, i: number) => <div key={i} role="status" className="rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-xs">⏱ {x.tool} 요청 한도 초과(429) — {x.action === 'wait' ? `${x.retry_after}초 뒤 게이트웨이가 자동 재시도` : '재시도 가능 시각이 길어 제외'} (MSG-012)</div>)}
      {r.status !== 'failed' && <div aria-label="이 답변의 근거" className="rounded-md border border-line bg-surface-2 px-3 py-2 text-xs">이 답변의 근거 — 인용 {m.sources?.cited ?? 0} · 검색했지만 미사용 {m.sources?.unused ?? 0} · {m.sanitized ? `응답 소독: 지시문·민감정보 ${m.sanitized}건 제거` : '도구 응답의 지시문은 실행하지 않음'} (MSG-025){(m.regenerated ?? []).length ? ' · 인용 누락으로 1회 재생성' : ''}</div>}
      {m.local_only && r.data_class !== 'public' && <div className="rounded-md border border-model/40 bg-model/5 px-3 py-2 text-xs">🔒 내부 데이터 포함 — 로컬 모델({(m.models ?? []).join(', ')})로만 처리했습니다 (MSG-011)</div>}
      <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
        <span>trace <TraceLink id={r.trace_id} /> · {r.status}</span><span>· {fmtUsd(m.cost)}</span><span>· 토큰 {fmtNum((m.tokens_in ?? 0) + (m.tokens_out ?? 0))}</span>
        {m.first_token_ms != null && <span>· 첫 토큰 {fmtMs(m.first_token_ms)}</span>}
        <button type="button" className="rounded px-1 hover:bg-surface-2" onClick={copy} aria-label="답변 복사">⧉ 복사</button>
        {message && <><button type="button" aria-pressed={rating === 'up'} aria-label="좋아요" className={cx('rounded px-1 hover:bg-surface-2', rating === 'up' && 'bg-ok/15')} onClick={() => rate('up')}>👍</button>
          <button type="button" aria-pressed={rating === 'down'} aria-label="싫어요" className={cx('rounded px-1 hover:bg-surface-2', rating === 'down' && 'bg-danger/10')} onClick={() => rate('down')}>👎</button></>}
        {onRegenerate && <button type="button" className="rounded px-1 hover:bg-surface-2" onClick={onRegenerate}>↻ 다시 생성</button>}
      </div>
    </div>
  )
}

function UserBubble({ t, editable, onEdit }: { t: Turn; editable: boolean; onEdit: (text: string) => void }) {
  const [edit, setEdit] = useState(false)
  const [text, setText] = useState(t.user)
  if (edit) {
    return (
      <div className="ml-auto w-[80%] rounded-xl border border-primary/40 bg-surface p-2">
        <Textarea aria-label="메시지 편집" rows={2} value={text} onChange={(e) => setText(e.target.value)} />
        <div className="mt-1 flex justify-end gap-2"><Button size="sm" onClick={() => { setEdit(false); setText(t.user) }}>취소</Button>
          <Button size="sm" variant="primary" disabled={!text.trim() || text.trim() === t.user} onClick={() => { setEdit(false); onEdit(text.trim()) }}>새 분기로 재전송</Button></div>
        <p className="mt-1 text-[11px] text-fg-muted">편집하면 새 분기로 실행하고, 이전 대화는 이력에 남습니다.</p>
      </div>
    )
  }
  return (
    <div className="group ml-auto flex max-w-[80%] flex-col items-end gap-0.5">
      {t.kind && <span className="text-[11px] text-fg-muted">{t.kind === 'regenerate' ? '↻ 다시 생성' : '✎ 편집 후 재전송'}</span>}
      <div className="rounded-xl bg-primary-soft px-4 py-2 text-sm whitespace-pre-wrap">{t.user}</div>
      {editable && t.runId && <button type="button" className="text-[11px] text-fg-muted opacity-0 group-hover:opacity-100 focus:opacity-100" onClick={() => setEdit(true)}>✎ 편집</button>}
    </div>
  )
}

function CitationPanel({ c, runId, onClose }: { c: Citation | null; runId?: string; onClose: () => void }) {
  const run = useGet(c && runId ? `/api/v1/runs/${runId}` : null)
  if (!c) return null
  const src = (run.data?.steps ?? []).flatMap((s: Json) => s.output?.sources ?? []).find((x: Json) => x.ref === c.ref)
  return (
    <Drawer open onClose={onClose} title={`근거 원문 [${c.n}]`}>
      <div className="flex items-center gap-2"><TypeBadge type={c.agent ? 'a2a' : 'mcp'} /><span className="mono text-xs">{c.tool || c.agent}</span></div>
      <h3 className="mt-2 font-semibold">{c.title}</h3>
      {run.isLoading && <Loading />}
      {src ? <p className="mt-3 rounded-md bg-warn/10 p-3 text-sm whitespace-pre-wrap">{src.text}</p> : !run.isLoading && <p className="mt-3 text-sm text-fg-muted">원문 미리보기가 없습니다 (권한 밖이거나 원격 Task 결과).</p>}
      <Kv rows={[['참조', <span className="mono text-xs">{c.ref}</span>]]} />
      {/^https?:\/\//.test(c.ref ?? '') && <a href={c.ref} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm text-primary">원문 열기 ↗ (내 권한으로)</a>}
      <p className="mt-4 text-xs text-fg-muted">근거는 사용자 권한(OBO 토큰)으로 조회되며, 응답 소독 규칙이 적용된 뒤 표시됩니다 (MSG-025).</p>
    </Drawer>
  )
}

function Answer({ st, onCite }: { st: RunState; onCite: (c: Citation) => void }) {
  const parts = st.text.split(/(\[\d+\])/g)
  return (
    <div className="rounded-xl border border-line bg-surface p-4 text-sm leading-6">
      <div className="whitespace-pre-wrap">
        {parts.map((p, i) => {
          const m = /^\[(\d+)\]$/.exec(p)
          const c = m && st.citations.find((x) => x.n === Number(m[1]))
          return c ? <button key={i} type="button" onClick={() => onCite(c)} className="mx-0.5 rounded bg-primary-soft px-1 text-xs text-primary">{p}</button> : <span key={i}>{p}</span>
        })}
        {ACTIVE(st) && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-fg align-middle" aria-hidden />}
      </div>
      {st.citations.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          <span className="text-xs text-fg-muted">인용</span>
          {st.citations.map((c) => <button key={c.n} type="button" onClick={() => onCite(c)} className="rounded-full border border-primary/30 px-2 py-0.5 text-xs text-primary hover:bg-primary-soft">[{c.n}] {c.title}</button>)}
        </div>
      )}
    </div>
  )
}
const ACTIVE = (st: RunState) => st.status === 'running'

function PlanPanel({ st, onStop, onRetry, onApprove }: { st: RunState; onStop: () => void; onRetry: () => void; onApprove: (a: Approval) => void }) {
  const [showJson, setShowJson] = useState(false)
  const pr = progress(st)
  const elapsed = st.startedAt ? ((st.finishedAt ?? Date.now()) - st.startedAt) : null
  return (
    <aside aria-label="실행 계획" className="flex w-[320px] shrink-0 flex-col border-l border-line bg-surface">
      <div className="border-b border-line px-4 py-3">
        <div className="flex items-center justify-between"><h2 className="font-semibold text-navy">실행 계획</h2><StatusBadge status={st.status === 'idle' ? 'queued' : st.status} /></div>
        <div className="mt-1 flex items-center gap-2 text-xs text-fg-muted">trace <TraceLink id={st.traceId} /> {elapsed != null && <span>· {fmtMs(elapsed)}</span>}</div>
      </div>
      <ol className="flex-1 space-y-2 overflow-auto p-3">
        <li className="rounded-md border-l-4 border-ok bg-surface-2 p-2 text-xs">
          <div className="flex items-center justify-between"><strong>Planner — 계획</strong><Badge tone={st.order.length ? 'ok' : 'muted'}>{st.order.length ? '완료' : '대기'}</Badge></div>
          <div className="text-fg-muted">{st.planSource === 'template' ? `템플릿 계획${st.planNote ? ` (폴백: ${st.planNote})` : ''}` : st.planSource ? 'LLM 계획' : '—'}{st.planModel ? ` · 계획 모델 폴백 → ${st.planModel} (MSG-015)` : ''}</div>
        </li>
        {st.revisions.map((r) => (
          <li key={r.round} className="rounded-md border-l-4 border-warn bg-warn/10 p-2 text-xs" aria-label={`재계획 ${r.round}`}>
            <div className="flex items-center justify-between"><strong>🔁 재계획 {r.round}/{st.revisions.length}</strong><Badge tone="warn">{r.source === 'llm' ? 'LLM' : '대체 계획'}</Badge></div>
            <div className="mt-0.5 text-fg-muted">이전: {r.previous.join(', ')} → {r.steps.join(', ')}</div>
            <div className="mt-0.5 line-clamp-2 text-fg-muted" title={r.reason}>사유: {r.reason}</div>
          </li>
        ))}
        {st.order.map((n, i) => {
          const s = st.steps[n]
          const tone = { succeeded: 'border-ok', failed: 'border-danger', running: 'border-primary', waiting_approval: 'border-warn', waiting_input: 'border-a2a', skipped: 'border-muted', replanned: 'border-muted', pending: 'border-line' }[s.status]
          const ap = s.approvalId ? st.approvals[s.approvalId] : undefined
          return (
            <li key={n} className={cx('rounded-md border-l-4 bg-surface-2 p-2 text-xs', tone)}>
              <div className="flex items-center justify-between gap-2"><strong>{i + 1}. {NODE_LABEL[s.node] ?? s.node}</strong><StatusBadge status={s.status === 'pending' ? 'queued' : s.status} /></div>
              <div className="mono mt-0.5 truncate text-fg-muted" title={s.tool || s.agent}>{s.tool || s.agent || n}{s.dependsOn?.length ? ` ← ${s.dependsOn.join(', ')}` : ''}</div>
              {s.when && <div className="mono text-fg-muted">조건: {s.when}</div>}
              {s.attempt && <div className="text-warn">재시도 {s.attempt}회</div>}
              {s.dryRun && <Badge tone="warn">드라이런 — 실행하지 않음</Badge>}
              {s.preview && <div className="mt-1 line-clamp-3 whitespace-pre-wrap">{s.preview}</div>}
              {s.error && <div className="mt-1 text-danger">{s.error}</div>}
              {ap?.status === 'pending' && <Button size="sm" variant="primary" className="mt-1.5" onClick={() => onApprove(ap)}>승인 검토</Button>}
            </li>
          )
        })}
      </ol>
      <div className="space-y-2 border-t border-line p-3">
        {pr.all > 0 && <div className="text-xs text-fg-muted">하위 작업 {pr.done}/{pr.all} 완료{pr.failed ? ` · 실패 ${pr.failed}` : ''}</div>}
        <div className="flex gap-2">
          <Button size="sm" className="flex-1" onClick={() => setShowJson(true)} disabled={!st.order.length}>계획 JSON</Button>
          {['partial', 'failed'].includes(st.status) ? <Button size="sm" variant="primary" className="flex-1" onClick={onRetry}>실패만 재시도</Button>
            : <Button size="sm" variant="danger" className="flex-1" onClick={onStop} disabled={!['running', 'waiting_approval', 'waiting_input'].includes(st.status)}>■ 중지</Button>}
        </div>
      </div>
      <Modal open={showJson} onClose={() => setShowJson(false)} title="실행 계획 JSON" wide><CodeBlock value={st.order.map((n) => st.steps[n])} /></Modal>
    </aside>
  )
}

type Turn = { id: string; user: string; runId?: string; answer?: string; answerMsg?: Json; kind?: string }

// 세션마다 새 인스턴스(스트림·상태 분리)
export function ChatSession() {
  const { sid } = useParams()
  return <ChatSessionView key={sid} />
}

function ChatSessionView() {
  const { sid } = useParams()
  const loc = useLocation()
  const nav = useNavigate()
  const toast = useToast()
  const { refreshSessions, profiles } = useOutletContext<Ctx>()
  const session = useGet(`/api/v1/sessions/${sid}`)
  const [st, dispatch] = useReducer((s: RunState, a: { type: 'reset'; state: RunState } | { type: 'ev'; ev: any }) => (a.type === 'reset' ? a.state : reduceRun(s, a.ev)), initialRun())
  const [turns, setTurns] = useState<Turn[]>([])
  const [approval, setApproval] = useState<Approval | null>(null)
  const [cite, setCite] = useState<Citation | null>(null)
  const abort = useRef<AbortController | null>(null)
  const sentOnce = useRef(false)
  const mounted = useRef(false)
  const bottom = useRef<HTMLDivElement>(null)

  // 이력 복원: 마지막 run 은 이벤트 재생 → 진행 중이면 이어서 구독
  useEffect(() => {
    if (!session.data) return
    const msgs: Json[] = session.data.messages ?? []
    const t: Turn[] = []
    for (const m of msgs) {
      if (m.role === 'user') t.push({ id: m.id, user: m.content, runId: m.run_id, kind: (m.parts ?? []).find((x: Json) => ['regenerate', 'edited'].includes(x.type))?.type })
      else if (t.length) { t[t.length - 1].answer = m.content; t[t.length - 1].answerMsg = m }
    }
    setTurns((prev) => (abort.current && t.length < prev.length ? prev : t))
    const last = t[t.length - 1]
    if (last?.runId && !abort.current) {
      request(`/api/v1/runs/${last.runId}/events`).then((r) => r.json()).then((j) => {
        const state = replay(j.items, { ...initialRun(), runId: last.runId })
        dispatch({ type: 'reset', state })
        if (['running', 'idle'].includes(state.status)) follow(last.runId!, state.lastEventId)
      }).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.data])

  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end' }) }, [st.text, turns.length])

  const consume = async (res: Response, ctl: AbortController) => {
    try {
      await readSse(res, (ev) => { dispatch({ type: 'ev', ev }); if (isEnd(ev.event)) ctl.abort() }, ctl.signal)
    } catch (e: any) {
      if (e?.name !== 'AbortError') toast('error', `스트림 끊김: ${e.message}`)
    } finally {
      if (abort.current === ctl) abort.current = null
      session.refetch()
      refreshSessions()
    }
  }

  const follow = async (rid: string, after: number) => {
    abort.current?.abort()
    const ctl = new AbortController()
    abort.current = ctl
    try {
      const res = await request(`/api/v1/runs/${rid}/events`, { query: { after }, headers: { Accept: 'text/event-stream' }, signal: ctl.signal })
      await consume(res, ctl)
    } catch (e: any) {
      if (e?.name !== 'AbortError') toast('error', e.message)
    }
  }

  const sendMsg = async (p: Pending) => {
    abort.current?.abort()
    const ctl = new AbortController()
    abort.current = ctl
    dispatch({ type: 'reset', state: { ...initialRun(), status: 'running', startedAt: Date.now() } })
    setTurns((t) => [...t, { id: `local-${Date.now()}`, user: p.content }])
    try {
      const res = await request(`/api/v1/sessions/${sid}/messages`, { method: 'POST', body: p, headers: { Accept: 'text/event-stream' }, signal: ctl.signal })
      await consume(res, ctl)
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        toast('error', e.message)
        dispatch({ type: 'ev', ev: { event: 'run.failed', data: { error: e.message } } })
      }
    }
  }

  useEffect(() => {
    const pending = (loc.state as any)?.send as Pending | undefined
    if (pending && !sentOnce.current) {
      sentOnce.current = true
      nav(loc.pathname, { replace: true, state: null })
      sendMsg(pending)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // 언마운트 시 스트림 중단 — StrictMode 의 모의 언마운트(즉시 재마운트)는 건너뛴다
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      setTimeout(() => { if (!mounted.current) abort.current?.abort() }, 0)
    }
  }, [])

  const stop = async () => {
    if (st.runId) await send('POST', `/api/v1/runs/${st.runId}/cancel`).catch((e) => toast('error', e.message))
    abort.current?.abort()
    toast('info', '중지했습니다 — 받은 부분은 보존됩니다.')
  }
  const answerInput = async (step: string, text: string) => {
    if (!st.runId) return
    await send('POST', `/api/v1/runs/${st.runId}/input`, { step, text })
    dispatch({ type: 'ev', ev: { event: 'input.resolved', data: { step } } })
    follow(st.runId, st.lastEventId)
  }
  const retry = async () => {
    if (!st.runId) return
    try {
      await send('POST', `/api/v1/runs/${st.runId}/retry`)
      dispatch({ type: 'ev', ev: { event: 'run.retry', data: {} } })
      follow(st.runId, st.lastEventId)
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const afterDecision = () => { if (st.runId) setTimeout(() => follow(st.runId!, st.lastEventId), 300) }
  // 다시 생성·편집 후 재전송 (SCR-C03 ①⑧) — 새 실행을 같은 세션 분기로 붙이고 이벤트를 따라간다
  const resend = async (path: string, shown: string, kind: string, body?: Json) => {
    try {
      const r = await send<Json>('POST', path, body)
      setTurns((t) => [...t, { id: r.message_id, user: shown, runId: r.run_id, kind }])
      dispatch({ type: 'reset', state: { ...initialRun(), runId: r.run_id, status: 'running', startedAt: Date.now() } })
      follow(r.run_id, 0)
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const continuePartial = async (rid: string) => {
    try {
      const r = await send<Json>('POST', `/api/v1/runs/${rid}/continue`)
      if (r.result === 'resumed') {
        dispatch({ type: 'ev', ev: { event: 'run.retry', data: {} } })
        follow(rid, st.lastEventId)
      } else toast('ok', '부분 결과로 확정했습니다.')
      queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === `/api/v1/runs/${rid}` })
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const retryRun = async (rid: string) => {
    try {
      await send('POST', `/api/v1/runs/${rid}/retry`)
      if (rid === st.runId) {
        dispatch({ type: 'ev', ev: { event: 'run.retry', data: {} } })
        follow(rid, st.lastEventId)
      } else toast('ok', '실패한 작업을 다시 실행합니다.')
    } catch (e: any) {
      toast('error', e.message)
    }
  }

  if (session.error) return <div className="p-6"><ErrorBox error={session.error} /></div>
  const profile = profiles.find((p) => p.id === session.data?.profile_id)
  const pendingAps = Object.values(st.approvals).filter((a) => a.status === 'pending')
  const liveConsents = Object.values(st.consents).filter((c) => ['pending', 'review'].includes(c.status))
  const lastIdx = turns.length - 1
  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-line px-6 py-2 text-sm">
          <TypeBadge type="profile" /><span className="font-medium">{profile?.name ?? session.data?.profile_id ?? '…'}</span>
          <Badge tone="model">라우팅: 자동(데이터 등급·비용)</Badge>
          {st.dataClass && st.dataClass !== 'public' && <Badge tone="warn">{st.dataClass}</Badge>}
        </div>
        <div className="flex-1 space-y-4 overflow-auto px-6 py-4">
          {!session.data && <Loading />}
          {turns.map((t, i) => (
            <div key={t.id} className="space-y-3">
              <UserBubble t={t} editable={st.status !== 'running' && !t.id.startsWith('local-')} onEdit={(text) => resend(`/api/v1/sessions/${sid}/messages/${t.id}/edit`, text, 'edited', { content: text })} />
              {i === lastIdx && st.status !== 'idle' ? (
                <>
                  {pendingAps.map((a) => (
                    <Card key={a.id} className="border-warn/50" title={<span className="flex items-center gap-2">승인 필요: <span className="mono">{a.tool}</span><RiskChip risk={a.risk} /></span>}
                      actions={<Button size="sm" variant="primary" onClick={() => setApproval(a)}>검토하기 → C04</Button>}>
                      <p className="text-xs text-fg-muted">Executor 단계에서 쓰기 도구를 실행하기 전에 인자·영향을 확인합니다. 승인 전에는 실행이 멈춰 있습니다.</p>
                    </Card>
                  ))}
                  {liveConsents.map((c) => (
                    <ConsentCard key={c.id + c.status} c={c} onDone={(id) => dispatch({ type: 'ev', ev: { event: `${c.kind}.resolved`, data: { approval_id: id, status: 'submitted' } } })} />
                  ))}
                  {st.runId && st.order.some((n) => st.steps[n]?.node === 'remote') && <DelegationCards runId={st.runId} active={st.status === 'running'} inputs={st.inputs} onReply={answerInput} />}
                  {(st.text || st.status === 'running') && <Answer st={st} onCite={setCite} />}
                  {st.rateLimited.length > 0 && st.status === 'running' && <div role="status" className="rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-xs">⏱ 요청 한도 초과(429) — {st.rateLimited.at(-1)!.retry_after}초 뒤 자동 재시도 중 (MSG-012)</div>}
                  {st.runId && ['succeeded', 'partial', 'failed', 'canceled'].includes(st.status)
                    ? <RunFooter runId={st.runId} status={st.status} sid={sid} message={t.answerMsg} onRetry={retry} onContinue={() => continuePartial(st.runId!)}
                        onRegenerate={t.answerMsg ? () => resend(`/api/v1/sessions/${sid}/messages/${t.answerMsg!.id}/regenerate`, t.user, 'regenerate') : undefined} />
                    : st.status !== 'running' && <div className="text-xs text-fg-muted">trace <TraceLink id={st.traceId} /> · {st.status}</div>}
                </>
              ) : t.answer ? (
                <>
                  <div className="rounded-xl border border-line bg-surface p-4 text-sm whitespace-pre-wrap">{t.answer}</div>
                  {t.runId && <RunFooter runId={t.runId} status="done" sid={sid} message={t.answerMsg} onRetry={() => retryRun(t.runId!)} onContinue={() => continuePartial(t.runId!)}
                    onRegenerate={t.answerMsg ? () => resend(`/api/v1/sessions/${sid}/messages/${t.answerMsg!.id}/regenerate`, t.user, 'regenerate') : undefined} />}
                </>
              ) : null}
            </div>
          ))}
          <div ref={bottom} />
        </div>
        <div className="px-6 pb-4"><Composer onSend={sendMsg} busy={st.status === 'running'} onStop={stop} profileId={session.data?.profile_id} placeholder={st.status === 'running' ? '응답 생성 중 — 중지 후 이어서 입력' : undefined} /></div>
      </div>
      <PlanPanel st={st} onStop={stop} onRetry={retry} onApprove={setApproval} />
      <ApprovalModal approval={approval} onClose={() => setApproval(null)} onDecided={afterDecision} />
      <CitationPanel c={cite} runId={st.runId} onClose={() => setCite(null)} />
    </div>
  )
}
