// SCR-A05~A09 첫 실행 마법사 — ① 관리자·IdP ② LLM 공급자 ③ 레지스트리·MCP ④ 관측·연동 ⑤ 템플릿·즉시 사용 확인.
// 진입: 일회용 설정 코드(설치 로그·`nexus install code`) 또는 관리자 로그인. 진행 상태는 서버에 저장 — 다시 접속하면 이어서.
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { Badge, Button, Card, CodeBlock, ErrorBox, Field, Input, Kv, Loading, Select, Stepper, cx, useToast } from '@/components/ui'
import { api, type Json } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useGet } from '@/lib/query'

const SK = 'anx.setup'
const STEPS = ['identity', 'models', 'registry', 'observability', 'templates'] as const
const LABELS = ['관리자·IdP', 'LLM 공급자', '레지스트리·MCP', '관측·연동', '템플릿·확인']
const ROLES = ['admin', 'operator', 'security', 'developer', 'finops', 'user']
type Call = <T = Json>(method: string, path: string, body?: unknown) => Promise<T>

function useSetupCall(): [Call, string | null, (t: string | null) => void] {
  const [tok, setTok] = useState<string | null>(() => { try { return sessionStorage.getItem(SK) } catch { return null } })
  const set = (t: string | null) => {
    try { if (t) sessionStorage.setItem(SK, t); else sessionStorage.removeItem(SK) } catch { /* 무시 */ }
    setTok(t)
  }
  const call: Call = (method, path, body) => api(`/api/v1/setup${path}`, { method, body, headers: tok ? { Authorization: `Bearer ${tok}` } : undefined })
  return [call, tok, set]
}

export function Setup() {
  const { me } = useAuth()
  const nav = useNavigate()
  const state = useGet('/api/v1/setup/state')
  const [call, tok, setTok] = useSetupCall()
  const admin = !!me && (me.roles.includes('admin') || me.roles.includes('operator'))
  if (state.error) return <Frame><ErrorBox error={state.error} onRetry={() => state.refetch()} /></Frame>
  if (!state.data) return <Frame><Loading rows={4} /></Frame>
  if (state.data.completed) {
    return (
      <Frame>
        <Card title="첫 실행을 마쳤습니다">
          <p className="text-sm">설정은 콘솔의 각 화면에서 바꿀 수 있습니다. 설정 코드는 더 이상 쓰이지 않습니다.</p>
          <div className="mt-4 flex gap-2"><Button variant="primary" onClick={() => nav(me ? '/console' : '/login')}>{me ? '운영 개요로' : '로그인'}</Button></div>
        </Card>
      </Frame>
    )
  }
  if (!tok && !admin) return <Frame><CodeEntry onToken={setTok} /></Frame>
  return <Frame airgap={state.data.airgap}><Wizard call={call} onExpired={() => setTok(null)} loggedIn={admin} /></Frame>
}

function Frame({ children, airgap }: { children: ReactNode; airgap?: boolean }) {
  return (
    <div className="min-h-full bg-bg p-8">
      <div className="mx-auto max-w-5xl">
        <div className="mb-4 flex items-center gap-2"><span className="grid size-8 place-items-center rounded-full bg-primary font-semibold text-white">N</span>
          <h1 className="text-xl font-semibold text-navy">AgentNexus · 첫 실행 설정</h1>{airgap && <Badge tone="warn">에어갭 (ST-09)</Badge>}</div>
        {children}
      </div>
    </div>
  )
}

function CodeEntry({ onToken }: { onToken: (t: string) => void }) {
  const [code, setCode] = useState('')
  const [err, setErr] = useState<unknown>(null)
  const go = async () => {
    setErr(null)
    try {
      const r = await api<Json>('/api/v1/setup/claim', { method: 'POST', body: { code: code.trim() } })
      onToken(r.token)
    } catch (e) {
      setErr(e)
    }
  }
  return (
    <Card title="일회용 설정 코드">
      <p className="mb-3 text-sm text-fg-muted">설치가 끝나면 api 로그(또는 <code>nexus install code</code>)에 30분짜리 일회용 코드가 표시됩니다. 코드 없이는 첫 실행 설정에 들어올 수 없습니다.</p>
      <div className="flex max-w-md gap-2">
        <Input aria-label="설정 코드" className="mono uppercase" placeholder="7Q2M-XXXX-XXXX" value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && go()} />
        <Button variant="primary" disabled={code.trim().length < 12} onClick={go}>시작</Button>
      </div>
      {err ? <div className="mt-3"><ErrorBox error={err} /></div> : null}
      <p className="mt-3 text-xs text-fg-muted">이미 관리자라면 <a className="text-primary underline" href="/login?next=/setup">로그인</a> 후 이어서 진행할 수 있습니다.</p>
    </Card>
  )
}

function Wizard({ call, onExpired, loggedIn }: { call: Call; onExpired: () => void; loggedIn: boolean }) {
  const toast = useToast()
  const [prog, setProg] = useState<Json | null>(null)
  const [i, setI] = useState(0)
  const [err, setErr] = useState<unknown>(null)
  useEffect(() => {
    call<Json>('GET', '/progress').then((p) => { setProg(p); setI(STEPS.indexOf(p.current)) }).catch((e) => {
      if (e.status === 401 || e.status === 403) onExpired()
      else setErr(e)
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <ErrorBox error={err} />
  if (!prog) return <Loading rows={4} />
  const advance = (p: Json) => { setProg(p); setI((x) => Math.min(x + 1, STEPS.length - 1)) }
  const skip = async () => {
    try {
      advance(await call<Json>('POST', `/${STEPS[i]}/skip`))
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const status = (s: string) => prog.steps[s]?.status
  return (
    <Card>
      <div className="mb-1 flex items-center justify-between text-xs text-fg-muted"><span>단계 {i + 1}/5</span><span>진행 상태는 서버에 저장 — 다시 접속하면 이어서 진행</span></div>
      <Stepper steps={LABELS.map((l, k) => `${l}${status(STEPS[k]) === 'done' ? ' ✓' : status(STEPS[k]) === 'skipped' ? ' (건너뜀)' : ''}`)} current={i} />
      <div className="mt-2">
        {i === 0 && <IdentityStep call={call} onDone={advance} />}
        {i === 1 && <ModelsStep call={call} onDone={advance} />}
        {i === 2 && <RegistryStep call={call} onDone={advance} />}
        {i === 3 && <ObservabilityStep call={call} onDone={advance} />}
        {i === 4 && <TemplatesStep call={call} prog={prog} loggedIn={loggedIn} onExpired={onExpired} />}
      </div>
      <div className="mt-4 flex justify-between border-t border-line pt-3">
        <Button disabled={i === 0} onClick={() => setI(i - 1)}>이전</Button>
        <div className="flex gap-2">
          {i > 0 && i < 4 && <Button onClick={skip}>건너뛰기</Button>}
          {i < 4 && status(STEPS[i]) !== 'todo' && <Button onClick={() => setI(i + 1)}>다음 →</Button>}
        </div>
      </div>
    </Card>
  )
}

function useStepData(call: Call, path: string) {
  const [data, setData] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  useEffect(() => { call<Json>('GET', path).then(setData).catch(setErr) }, [path]) // eslint-disable-line react-hooks/exhaustive-deps
  return { data, err, setData }
}

function Save({ onClick, label = '저장하고 다음 →' }: { onClick: () => Promise<void>; label?: string }) {
  const [busy, setBusy] = useState(false)
  return <div className="mt-4 flex justify-end"><Button variant="primary" loading={busy} onClick={async () => { setBusy(true); try { await onClick() } finally { setBusy(false) } }}>{label}</Button></div>
}

// ── ① 관리자·IdP (A05) ──
function IdentityStep({ call, onDone }: { call: Call; onDone: (p: Json) => void }) {
  const [a, setA] = useState({ username: 'nexus-admin', email: '', password: '', confirm: '' })
  const [mfa, setMfa] = useState<Json | null>(null)
  const [otp, setOtp] = useState('')
  const [kind, setKind] = useState('none')
  const [idp, setIdp] = useState({ name: '', issuer: '', internal_url: '', client_id: '', client_secret: '' })
  const [map, setMap] = useState<[string, string][]>([['nexus-admins', 'operator'], ['sec-team', 'security'], ['agent-dev', 'developer'], ['finops', 'finops'], ['all-staff', 'user']])
  const [disc, setDisc] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const pw = a.password
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length
  const rule = (ok: boolean, t: string) => <li className={ok ? 'text-ok' : 'text-fg-muted'}>{ok ? '✓' : '·'} {t}</li>
  const submit = async () => {
    setErr(null)
    try {
      const body: Json = { admin: { username: a.username, email: a.email, password: a.password }, mfa: { secret: mfa?.secret, code: otp } }
      if (kind !== 'none') body.idp = { kind, ...idp, internal_url: idp.internal_url || null, client_secret: idp.client_secret || null, mapping: Object.fromEntries(map.filter(([g]) => g)) }
      onDone(await call<Json>('POST', '/identity', body))
    } catch (e) {
      setErr(e)
    }
  }
  return (
    <div className="grid grid-cols-3 gap-4">
      <section>
        <h3 className="mb-2 font-semibold">초기 관리자</h3>
        <Field label="관리자 ID"><Input value={a.username} onChange={(e) => setA({ ...a, username: e.target.value })} /></Field>
        <Field label="이메일"><Input type="email" value={a.email} onChange={(e) => setA({ ...a, email: e.target.value })} /></Field>
        <Field label="비밀번호"><Input type="password" autoComplete="new-password" value={a.password} onChange={(e) => setA({ ...a, password: e.target.value })} /></Field>
        <Field label="비밀번호 확인"><Input type="password" autoComplete="new-password" value={a.confirm} onChange={(e) => setA({ ...a, confirm: e.target.value })} /></Field>
        <ul className="text-xs" aria-label="비밀번호 정책">{rule(pw.length >= 14, '14자 이상')}{rule(classes >= 3, '3종 문자 이상')}{rule(!!pw && pw === a.confirm, '확인 일치')}<li className="text-fg-muted">· 유출·흔한 비밀번호 목록 대조는 저장할 때</li></ul>
        <h3 className="mb-2 mt-4 font-semibold">MFA (필수)</h3>
        <div className="flex gap-3 text-sm"><label className="flex items-center gap-1"><input type="radio" checked readOnly />TOTP 앱</label><label className="flex items-center gap-1 text-fg-muted" title="다음 릴리스"><input type="radio" disabled />WebAuthn 키</label></div>
        {!mfa ? <Button className="mt-2" onClick={async () => setMfa(await call<Json>('POST', `/mfa?account=${encodeURIComponent(a.username)}`))}>TOTP 등록 시작</Button> : (
          <div className="mt-2 space-y-2 text-xs">
            <p>인증 앱에 아래 키를 등록하세요 (QR 대신 수동 입력·URI).</p>
            <CodeBlock value={mfa.secret.replace(/(.{4})/g, '$1 ').trim()} maxH="max-h-12" />
            <CodeBlock value={mfa.otpauth} maxH="max-h-16" />
            <Field label="확인 코드 (6자리)"><Input inputMode="numeric" maxLength={6} className="mono" value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))} /></Field>
          </div>
        )}
      </section>
      <section>
        <h3 className="mb-2 font-semibold">외부 IdP 연합 (선택)</h3>
        <div className="space-y-1 text-sm" role="radiogroup" aria-label="외부 IdP">
          {[['none', '내장 IdP 로컬 계정만'], ['entra', 'Microsoft Entra ID'], ['okta', 'Okta'], ['keycloak', 'Keycloak']].map(([k, l]) => (
            <label key={k} className="flex items-center gap-2"><input type="radio" name="idpkind" checked={kind === k} onChange={() => setKind(k)} />{l}</label>
          ))}
        </div>
        {kind !== 'none' && (
          <div className="mt-2 space-y-1">
            <Field label="이름"><Input value={idp.name} onChange={(e) => setIdp({ ...idp, name: e.target.value })} placeholder="사내 Entra ID" /></Field>
            <Field label="OIDC Issuer (디스커버리)"><Input className="mono" value={idp.issuer} onChange={(e) => { setIdp({ ...idp, issuer: e.target.value }); setDisc(null) }} /></Field>
            <Field label="서버 측 주소 (선택)"><Input className="mono" value={idp.internal_url} onChange={(e) => setIdp({ ...idp, internal_url: e.target.value })} /></Field>
            <Field label="클라이언트 ID"><Input value={idp.client_id} onChange={(e) => setIdp({ ...idp, client_id: e.target.value })} /></Field>
            <Field label="클라이언트 시크릿" hint="게이트웨이 시크릿 저장소로 이동, 다시 표시 안 함 (MSG-018)"><Input type="password" autoComplete="off" value={idp.client_secret} onChange={(e) => setIdp({ ...idp, client_secret: e.target.value })} /></Field>
            <Button size="sm" disabled={!idp.issuer} onClick={async () => setDisc(await call<Json>('POST', '/idp/discover', { issuer: idp.issuer, internal_url: idp.internal_url || null }))}>연결 시험</Button>
            {disc && <p className={cx('text-xs', disc.discovery && disc.jwks && disc.pkce_s256 ? 'text-ok' : 'text-danger')} role="status">
              {disc.discovery && disc.jwks ? `✓ 디스커버리 · JWKS ${disc.keys}개 · PKCE ${disc.pkce_s256 ? 'S256 ✓' : '미지원 ✗'}` : `✗ ${disc.error}`}</p>}
            <p className="text-[11px] text-fg-muted">저장 후 Identity › IdP 연동에서 시험 로그인에 성공하면 로그인 화면에 나타납니다.</p>
          </div>
        )}
      </section>
      <section>
        <h3 className="mb-2 font-semibold">IdP 그룹 → 콘솔 역할</h3>
        {kind === 'none' ? <p className="text-sm text-fg-muted">외부 IdP 를 쓰지 않으면 역할은 Identity › 사용자에서 직접 줍니다.</p> : (
          <>
            <table className="w-full text-xs"><thead><tr className="text-left text-fg-muted"><th>IdP 그룹</th><th>콘솔 역할</th></tr></thead><tbody>
              {map.map(([g, r], k) => (
                <tr key={k}><td className="pr-1 py-0.5"><Input aria-label={`그룹 ${k + 1}`} value={g} onChange={(e) => setMap(map.map((x, j) => (j === k ? [e.target.value, x[1]] : x)))} /></td>
                  <td><Select aria-label={`역할 ${k + 1}`} value={r} onChange={(e) => setMap(map.map((x, j) => (j === k ? [x[0], e.target.value] : x)))}>{ROLES.map((x) => <option key={x}>{x}</option>)}</Select></td></tr>
              ))}
            </tbody></table>
            <Button size="sm" variant="ghost" onClick={() => setMap([...map, ['', 'user']])}>+ 행</Button>
            <p className="mt-1 text-[11px] text-fg-muted">매핑 없는 사용자 = 최종 사용자 · 관리자(admin·operator) 그룹 최소 1개 필수</p>
          </>
        )}
      </section>
      <div className="col-span-3">
        {err ? <ErrorBox error={err} /> : null}
        <Save onClick={async () => {
          if (a.password !== a.confirm) { setErr({ message: '비밀번호 확인이 일치하지 않습니다.' }); return }
          if (!mfa || otp.length !== 6) { setErr({ message: 'MFA 를 등록하고 확인 코드 6자리를 입력하세요 (건너뛰기 없음).' }); return }
          await submit()
        }} />
      </div>
    </div>
  )
}

// ── ② LLM 공급자 (A06) ──
function ModelsStep({ call, onDone }: { call: Call; onDone: (p: Json) => void }) {
  const toast = useToast()
  const { data, err } = useStepData(call, '/models')
  const [rows, setRows] = useState<Record<string, { enabled: boolean; api_key: string }>>({})
  const [aliases, setAliases] = useState<Record<string, string>>({})
  const [tests, setTests] = useState<Record<string, Json>>({})
  useEffect(() => {
    if (!data) return
    setRows(Object.fromEntries(data.providers.map((p: Json) => [p.id, { enabled: p.enabled, api_key: '' }])))
    setAliases(data.aliases)
  }, [data])
  if (err) return <ErrorBox error={err} />
  if (!data) return <Loading />
  const models: Json[] = data.models
  return (
    <div className="space-y-4">
      {data.airgap && <p role="alert" className="rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-xs">에어갭 모드에서는 상용 공급자 행이 비활성입니다 (ST-09) — 로컬 모델만 켤 수 있습니다.</p>}
      <table className="w-full text-sm" aria-label="공급자 어댑터">
        <thead><tr className="text-left text-xs text-fg-muted"><th>공급자</th><th>유형</th><th>모델</th><th>연결</th><th>API 키</th><th>사용</th></tr></thead>
        <tbody>{data.providers.map((p: Json) => (
          <tr key={p.id} className="border-t border-line">
            <td className="py-1.5">{p.name}</td><td><Badge tone={p.category === 'local' ? 'ok' : 'primary'}>{p.category === 'local' ? '로컬' : '상용'}</Badge></td>
            <td className="mono text-xs">{p.models.join(', ') || '—'}</td>
            <td className="text-xs">{tests[p.id] ? (tests[p.id].ok ? `✓ ${tests[p.id].latency_ms}ms` : `✗ ${tests[p.id].error ?? ''}`.slice(0, 60))
              : p.detected ? (p.detected.ok ? `✓ ${p.detected.latency_ms != null ? `${p.detected.latency_ms}ms` : '감지'}` : `— ${p.detected.note}`) : p.has_key ? '키 있음' : '키 없음'}</td>
            <td>{p.category === 'commercial' && <Input type="password" autoComplete="off" aria-label={`${p.name} API 키`} disabled={!!p.disabled_reason} value={rows[p.id]?.api_key ?? ''}
              onChange={(e) => setRows({ ...rows, [p.id]: { ...rows[p.id], api_key: e.target.value } })} placeholder={p.has_key ? '•••• (저장됨)' : 'sk-…'} />}</td>
            <td className="space-x-1 whitespace-nowrap"><input type="checkbox" aria-label={`${p.name} 사용`} disabled={!!p.disabled_reason} title={p.disabled_reason ?? undefined} checked={rows[p.id]?.enabled ?? false}
              onChange={(e) => setRows({ ...rows, [p.id]: { ...rows[p.id], enabled: e.target.checked } })} />
              <Button size="sm" variant="ghost" disabled={!!p.disabled_reason} onClick={async () => { try { setTests({ ...tests, [p.id]: await call<Json>('POST', '/models/test', { provider_id: p.id }) }) } catch (e: any) { toast('error', e.message) } }}>시험</Button></td>
          </tr>
        ))}</tbody>
      </table>
      <p className="text-xs text-fg-muted">키는 게이트웨이 시크릿에만 저장하고 다시 표시하지 않습니다 (MSG-018). 저장 후 [시험] 으로 연결을 확인하세요.</p>
      <div className="grid grid-cols-2 gap-4">
        <table className="text-sm" aria-label="모델 별칭">
          <thead><tr className="text-left text-xs text-fg-muted"><th>별칭</th><th>대상 모델</th></tr></thead>
          <tbody>{Object.entries(aliases).map(([al, t]) => (
            <tr key={al}><td className="mono py-1 pr-2">{al}</td><td><Select aria-label={`별칭 ${al}`} value={t} onChange={(e) => setAliases({ ...aliases, [al]: e.target.value })}>
              {models.filter((m) => al !== 'internal-data' || ['local', 'domain'].includes(m.category)).map((m) => <option key={m.id} value={m.id}>{m.id} ({m.category})</option>)}</Select></td></tr>
          ))}</tbody>
        </table>
        <div className="space-y-2 text-xs">
          <p className="rounded-md border border-primary/30 bg-primary-soft px-3 py-2">internal-data 는 로컬·도메인 모델만 (MSG-011 · 정책 기본값)</p>
          <CodeBlock value={`${data.endpoint}\n# 클라이언트는 별칭만 사용 → 모델 교체는 설정으로`} maxH="max-h-20" />
        </div>
      </div>
      <Save onClick={async () => {
        try {
          onDone(await call<Json>('POST', '/models', { providers: Object.entries(rows).map(([id, r]) => ({ id, enabled: r.enabled, api_key: r.api_key || undefined })), aliases }))
        } catch (e: any) {
          toast('error', e.message)
        }
      }} />
    </div>
  )
}

// ── ③ 레지스트리·참조 MCP 서버 (A07) ──
function RegistryStep({ call, onDone }: { call: Call; onDone: (p: Json) => void }) {
  const toast = useToast()
  const { data, err } = useStepData(call, '/registry')
  const [mirror, setMirror] = useState(false)
  const [approval, setApproval] = useState('official_auto')
  const [servers, setServers] = useState<Record<string, boolean>>({})
  useEffect(() => {
    if (!data) return
    setApproval(data.approval)
    setMirror(data.sources.some((s: Json) => s.kind === 'docker' && s.enabled))
    setServers(Object.fromEntries(data.servers.map((s: Json) => [s.id, s.status === 'active'])))
  }, [data])
  if (err) return <ErrorBox error={err} />
  if (!data) return <Loading />
  return (
    <div className="grid grid-cols-2 gap-4 text-sm">
      <section className="space-y-2">
        <h3 className="font-semibold">레지스트리 (Discovery only)</h3>
        <label className="flex items-center gap-2"><input type="radio" checked readOnly />공식 MCP Registry 자체 호스팅 — server.json 스키마 · /v0/servers</label>
        <label className={cx('flex items-center gap-2', data.airgap && 'text-fg-muted')} title={data.airgap ? '에어갭 — 비활성 (ST-09)' : undefined}>
          <input type="checkbox" disabled={data.airgap} checked={mirror && !data.airgap} onChange={(e) => setMirror(e.target.checked)} />Docker MCP Registry 미러 동기화{data.airgap && ' — 에어갭 비활성'}</label>
        <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-xs">레지스트리는 메타데이터만 제공 — 실행 트래픽은 통과하지 않고 MCP 호출은 항상 게이트웨이</p>
        <h3 className="pt-2 font-semibold">등록 승인 정책</h3>
        <label className="flex items-center gap-2"><input type="radio" name="appr" checked={approval === 'official_auto'} onChange={() => setApproval('official_auto')} />참조(official) 서버 자동 승인, 이후 신규 등록은 승인 대기열</label>
        <label className="flex items-center gap-2"><input type="radio" name="appr" checked={approval === 'all_pending'} onChange={() => setApproval('all_pending')} />전부 승인 대기열 (E05)</label>
      </section>
      <section>
        <h3 className="mb-2 font-semibold">참조 MCP 서버 — 함께 기동할 서버</h3>
        <div className="space-y-2">{data.servers.map((s: Json) => (
          <label key={s.id} className="flex items-start gap-2 rounded-md border border-line p-2">
            <input type="checkbox" aria-label={`${s.name} 사용`} checked={!!servers[s.id]} onChange={(e) => setServers({ ...servers, [s.id]: e.target.checked })} />
            <span className="flex-1"><span className="font-medium">{s.name}</span> <Badge tone="ok">{s.trust}</Badge><br />
              <span className="text-xs text-fg-muted">인증 {s.auth} · 도구 {s.tools}개 · Streamable HTTP · 헬스 {s.health}</span></span>
          </label>
        ))}</div>
        <p className="mt-2 text-xs text-fg-muted">켠 서버는 게이트웨이 뒤에서 헬스 점검·server.json 자동 등록. 백엔드 설정은 MCP 서버 › 설정(F03).</p>
      </section>
      <div className="col-span-2"><Save onClick={async () => {
        try { onDone(await call<Json>('POST', '/registry', { mirror_docker: mirror && !data.airgap, approval, servers })) } catch (e: any) { toast('error', e.message) }
      }} /></div>
    </div>
  )
}

// ── ④ 관측·기존 스택 (A08) ──
function ObservabilityStep({ call, onDone }: { call: Call; onDone: (p: Json) => void }) {
  const toast = useToast()
  const { data, err } = useStepData(call, '/observability')
  const [f, setF] = useState<Json>({ mode: 'builtin', otlp_endpoint: '', grafana_url: '', jaeger_url: '', spans_days: 14, logs_days: 30, audit_days: 365, pii: true })
  const [test, setTest] = useState<Json | null>(null)
  useEffect(() => {
    if (!data) return
    setF({ mode: data.mode, otlp_endpoint: data.otlp_endpoint ?? '', grafana_url: data.grafana_url ?? '', jaeger_url: data.jaeger_url ?? '',
      spans_days: data.retention.spans_days, logs_days: data.retention.logs_days, audit_days: Math.max(365, data.retention.audit_days), pii: data.pii_masking })
  }, [data])
  if (err) return <ErrorBox error={err} />
  if (!data) return <Loading />
  const sel = (k: string, opts: [number, string][]) => <Select aria-label={k} value={f[k]} onChange={(e) => setF({ ...f, [k]: Number(e.target.value) })}>{opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
  return (
    <div className="grid grid-cols-2 gap-4 text-sm">
      <section className="space-y-2">
        <h3 className="font-semibold">관측 스택</h3>
        <label className="flex items-center gap-2"><input type="radio" name="obs" checked={f.mode === 'builtin'} onChange={() => setF({ ...f, mode: 'builtin' })} />내장 스택 사용 (trace 저장소·/metrics·Usage & Cost)</label>
        <label className="flex items-center gap-2"><input type="radio" name="obs" checked={f.mode === 'external'} onChange={() => setF({ ...f, mode: 'external' })} />기존 스택에 연동 (OTLP·Grafana·Jaeger)</label>
        {f.mode === 'external' && <div className="space-y-1">
          <Field label="OTLP 수신 엔드포인트"><Input className="mono" value={f.otlp_endpoint} onChange={(e) => setF({ ...f, otlp_endpoint: e.target.value })} placeholder="http://otel-collector:4318" /></Field>
          <Field label="Grafana URL"><Input className="mono" value={f.grafana_url} onChange={(e) => setF({ ...f, grafana_url: e.target.value })} /></Field>
          <Field label="Jaeger 조회 URL"><Input className="mono" value={f.jaeger_url} onChange={(e) => setF({ ...f, jaeger_url: e.target.value })} /></Field>
          <Button size="sm" disabled={!f.otlp_endpoint} onClick={async () => setTest(await call<Json>('POST', '/observability/test', { otlp_endpoint: f.otlp_endpoint }))}>연결 시험</Button>
          {test && <p role="status" className={cx('text-xs', test.ok ? 'text-ok' : 'text-danger')}>{test.ok ? `✓ OTLP 수신 ${test.status}` : `✗ ${test.error ?? test.status} — 재시도`}</p>}
        </div>}
        <p className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs">{data.license_note}</p>
      </section>
      <section className="space-y-2">
        <h3 className="font-semibold">보존 기간</h3>
        <div className="grid grid-cols-3 gap-2 text-xs"><Field label="트레이스">{sel('spans_days', [[7, '7일'], [14, '14일'], [30, '30일']])}</Field>
          <Field label="로그">{sel('logs_days', [[14, '14일'], [30, '30일'], [90, '90일']])}</Field>
          <Field label="감사(불변)">{sel('audit_days', [[365, '1년'], [730, '2년'], [1095, '3년']])}</Field></div>
        <label className="flex items-center gap-2"><input type="checkbox" checked={f.pii} onChange={(e) => setF({ ...f, pii: e.target.checked })} />로그·트레이스 PII 마스킹 (MSG-021) — 이메일·전화·주민번호 패턴, 저장 전 적용</label>
        {!f.pii && <p className="text-xs text-danger">끄려면 보안·IAM 담당 승인이 필요합니다 (Security H05).</p>}
        <h3 className="pt-2 font-semibold">계측 대상 (GenAI 시맨틱 컨벤션)</h3>
        <ul className="list-disc pl-5 text-xs">{data.instrumentation.map((x: string) => <li key={x}>{x}</li>)}</ul>
      </section>
      <div className="col-span-2"><Save onClick={async () => {
        try {
          onDone(await call<Json>('POST', '/observability', { mode: f.mode, otlp_endpoint: f.otlp_endpoint || null, grafana_url: f.grafana_url || null, jaeger_url: f.jaeger_url || null,
            retention: { spans_days: f.spans_days, logs_days: f.logs_days, audit_days: f.audit_days }, pii_masking: f.pii }))
        } catch (e: any) {
          toast('error', e.message)
        }
      }} /></div>
    </div>
  )
}

// ── ⑤ 템플릿·즉시 사용 확인 (A09) ──
function TemplatesStep({ call, prog, loggedIn, onExpired }: { call: Call; prog: Json; loggedIn: boolean; onExpired: () => void }) {
  const toast = useToast()
  const nav = useNavigate()
  const { data, err } = useStepData(call, '/templates')
  const [pick, setPick] = useState<Record<string, boolean>>({})
  const [test, setTest] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (data) setPick(Object.fromEntries(data.items.map((t: Json) => [t.id, t.ready]))) }, [data])
  if (err) return <ErrorBox error={err} />
  if (!data) return <Loading />
  const chosen = Object.keys(pick).filter((k) => pick[k])
  const run = async () => {
    setBusy(true)
    try {
      setTest(await call<Json>('POST', '/templates/test', { template: chosen.find((c) => c === 'tpl-issue-report') ?? chosen[0] }))
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  const finish = async () => {
    try {
      await call('POST', '/templates', { templates: chosen, self_test: test })
      await call('POST', '/complete')
      toast('ok', '첫 실행을 마쳤습니다')
      onExpired()
      nav(loggedIn ? '/console' : '/login')
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const sum = (s: string) => prog.steps[s]?.status === 'done' ? '✓' : prog.steps[s]?.status === 'skipped' ? '건너뜀 — 설정에서 다시' : '미완료'
  return (
    <div className="space-y-4 text-sm">
      <div className="grid grid-cols-3 gap-3">{data.items.map((t: Json) => (
        <label key={t.id} className={cx('rounded-lg border p-3', pick[t.id] ? 'border-primary' : 'border-line')}>
          <div className="flex items-center gap-2"><input type="checkbox" aria-label={`${t.name} 선택`} checked={!!pick[t.id]} onChange={(e) => setPick({ ...pick, [t.id]: e.target.checked })} />
            <span className="font-medium">{t.name}</span><Badge tone={t.ready ? 'ok' : 'warn'}>{t.ready ? '준비됨' : t.missing.join(', ')}</Badge></div>
          <p className="mt-1 text-xs text-fg-muted">필요: {t.needs.mcp.map((m: string) => `MCP ${m}`).join(' · ')}{t.needs.remote.length ? ` · 원격 ${t.needs.remote.join(', ')}` : ''}</p>
          <p className="text-xs text-fg-muted">정책 기본값: {t.policy_default}</p>
        </label>
      ))}</div>
      <Card title="즉시 사용 자가 시험 (목표 60초 이내)" actions={<Button size="sm" disabled={!chosen.length} loading={busy} onClick={run}>자가 시험 실행</Button>}>
        {!test ? <p className="text-xs text-fg-muted">선택 → 인증 설정 → 정책 기본값 → 오케스트레이션·콘솔 노출 단계별 소요 시간을 잽니다.</p> : (
          <div aria-label="자가 시험 결과">
            <table className="w-full text-xs"><tbody>{test.stages.map((s: Json) => (
              <tr key={s.stage} className={cx(s.stage === test.slowest && test.total_seconds > 60 && 'text-warn')}><td className="py-0.5">{s.ok ? '✓' : '✗'} {s.stage}</td><td className="text-fg-muted">{s.note}</td><td className="text-right tabular-nums">{s.seconds}s</td></tr>
            ))}</tbody></table>
            <p className={cx('mt-2 font-medium', test.passed ? 'text-ok' : 'text-warn')}>{test.total_seconds}초 — {test.passed ? '목표 60초 이내 (FR-W04)' : `기준 미달 — 느린 단계: ${test.slowest}`}</p>
          </div>
        )}
      </Card>
      <Card title="설정 요약"><Kv rows={[['관리자·IdP', sum('identity')], ['LLM', sum('models')], ['레지스트리', sum('registry')], ['관측', sum('observability')]]} /></Card>
      <div className="flex justify-end"><Button variant="primary" disabled={!chosen.length} onClick={finish}>완료 → 콘솔</Button></div>
    </div>
  )
}
