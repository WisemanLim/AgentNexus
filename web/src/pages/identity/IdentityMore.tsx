// SCR-J01 IdP 연동 목록 · J02 IdP 연동 추가(OIDC 4단계) · J04 토큰 교환 정책(RFC 8693) · J06 MCP Authorization(서버별) · J07 워크로드 아이덴티티
import { createColumnHelper } from '@tanstack/react-table'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, CodeBlock, ErrorBox, Field, Gate, Input, Kv, Modal, PageHeader, Select, StatusBadge, Stepper, Textarea, cx, useToast, type Tone } from '@/components/ui'
import type { Json } from '@/lib/api'
import { fmtAgo, fmtMs } from '@/lib/format'
import { send, useGet } from '@/lib/query'

const col = createColumnHelper<Json>()
const I = '/api/v1/identity'

/** 시험 로그인 (SCR-J02 ④) — 새 창에서 외부 IdP 로그인 → 클레임·역할 매핑 결과를 기다린다(로그인 세션은 만들지 않음). */
function useTestLogin() {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Json | null>(null)
  const run = async (id: string) => {
    setBusy(true)
    setResult(null)
    try {
      const { url, state } = await send<Json>('POST', `${I}/idps/${id}/test-login`)
      window.open(url, 'anx-idp-test', 'width=520,height=680')
      for (let i = 0; i < 180; i++) {
        await new Promise((r) => setTimeout(r, 1000))
        const r = await send<Json>('GET', `${I}/idps/${id}/test-login/${state}`)
        if (r.status !== 'pending') {
          setResult(r)
          return r
        }
      }
      const timeout = { status: 'error', code: 'timeout', detail: '시험 로그인 창에서 3분 안에 끝나지 않았습니다.' }
      setResult(timeout)
      return timeout
    } catch (e: any) {
      const r = { status: 'error', code: 'start_failed', detail: e.message }
      setResult(r)
      return r
    } finally {
      setBusy(false)
    }
  }
  return { busy, result, run, setResult }
}

function TestLoginResult({ r }: { r: Json }) {
  return r.status === 'ok' ? (
    <div className="rounded-md border border-ok/40 bg-ok/10 p-3 text-sm" role="status">
      <div className="font-medium text-ok">시험 로그인 성공</div>
      <Kv rows={[['sub', <span className="mono text-xs">{r.sub}</span>], ['이름·메일', `${r.name ?? '—'} · ${r.email ?? '—'}`],
        ['그룹', (r.groups ?? []).join(', ') || '없음'], ['매핑된 역할', <span className="font-medium">{(r.roles ?? []).join(', ')}</span>],
        ['받은 클레임', <span className="mono text-[11px]">{(r.claims ?? []).join(' ')}</span>]]} />
    </div>
  ) : <div className="rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger" role="alert">시험 로그인 실패 ({r.code}) — {r.detail}</div>
}

const EDITABLE = ['name', 'client_id', 'client_secret', 'internal_url', 'scopes', 'audience', 'groups_claim', 'roles_claim'] as const
const LABEL: Record<string, string> = { name: '이름', client_id: 'Client ID', client_secret: 'Client Secret (바꿀 때만)', internal_url: '서버 측 주소 (선택)',
  scopes: '요청 스코프', audience: 'Audience', groups_claim: '그룹 클레임', roles_claim: '역할 클레임' }

/** IdP 별 사용자 수·마지막 동기화(JIT 로그인) — 외부 사용자 sub 는 ext:{idp_id}:{sub} */
function idpUsers(users: Json[], idp: Json) {
  const mine = users.filter((u) => (idp.kind === 'builtin' ? !String(u.sub).startsWith('ext:') : String(u.sub).startsWith(`ext:${idp.id}:`)))
  const last = mine.map((u) => u.last_login_at).filter(Boolean).sort().pop() as string | undefined
  return { count: mine.length, last }
}

function IdpDetail({ idp, users, onEdit, onToggle }: { idp: Json; users: Json[]; onEdit: () => void; onToggle: () => void }) {
  const st = idpUsers(users, idp)
  if (idp.kind === 'builtin') return (
    <Card title={`${idp.name} — 상세`}>
      <Kv rows={[['issuer', <span className="mono text-xs">{idp.issuer}</span>], ['사용자', `${st.count}명 (로컬 계정)`],
        ['역할', '콘솔에서 직접 부여 (사용자·역할 화면)'], ['조치', '내장 IdP 는 삭제·비활성화할 수 없습니다 — 외부 IdP 장애 때도 로그인 가능']]} />
    </Card>
  )
  return (
    <Card title={<>{idp.name} — 연동 상세 <span className="ml-1"><StatusBadge status={idp.enabled ? idp.status : 'disabled'} /></span></>}
      actions={<Gate perm="identity:write">{(ok, why) => <>
        <Button size="sm" disabled={!ok} title={why} onClick={onEdit}>편집</Button>
        <Button size="sm" variant={idp.enabled ? 'danger' : 'primary'} disabled={!ok} title={why} onClick={onToggle}>{idp.enabled ? '비활성화' : '다시 사용'}</Button>
      </>}</Gate>}>
      <Kv rows={[
        ['issuer', <span className="mono text-xs">{idp.issuer}</span>], ['client_id', <span className="mono text-xs">{idp.client_id ?? '—'}</span>],
        ['client_secret', idp.client_secret ? <span className="text-xs">•••••••• (시크릿 저장소, MSG-018)</span> : <span className="text-xs text-fg-muted">없음 (공개 클라이언트)</span>],
        ['스코프', <span className="mono text-xs">{idp.scopes}</span>],
        ['그룹 클레임', <span className="text-xs"><span className="mono">{idp.groups_claim}</span> → 콘솔 역할: {Object.entries(idp.role_mapping ?? {}).map(([g, r]) => `${g}→${r}`).join(', ') || '매핑 없음 (모두 user)'}</span>],
        ['역할 클레임', <span className="mono text-xs">{idp.roles_claim}</span>],
        ['프로비저닝', '첫 로그인 때 사용자 생성 (JIT) · 로그인마다 그룹 클레임 갱신'],
        ['서명 키', <span className="mono text-[11px]">{idp.jwks_uri ?? '디스커버리로 확인'}</span>],
        ['Redirect URI', <span className="mono text-[11px]">{idp.redirect_uri}</span>],
        ['사용자', `${st.count}명 · 마지막 로그인 ${fmtAgo(st.last)}`],
        ['마지막 시험', idp.last_tested_at ? `${fmtAgo(idp.last_tested_at)} · ${idp.last_test?.status === 'ok' ? '성공' : idp.last_test?.status === 'error' ? `실패 (${idp.last_test.code ?? ''})` : '디스커버리만'}` : '미시험'],
        ['로그인 노출', idp.login_enabled ? '콘솔·채팅 로그인 버튼 (브로커 경유)' : '노출 안 함 — Bearer 토큰 검증만'],
      ]} />
      <p className="mt-3 text-xs text-fg-muted">비활성화하면 이 IdP 로 새로 로그인할 수 없고 이 IdP 토큰 검증도 멈춥니다 (MSG-002). 이미 로그인한 세션은 사용자·역할 화면에서 강제 종료하세요.</p>
    </Card>
  )
}

export function Idps() {
  const toast = useToast()
  const q = useGet(`${I}/idps`)
  const users = useGet(`${I}/users`, { limit: 1000 })
  const [res, setRes] = useState<Json | null>(null)
  const [edit, setEdit] = useState<Json | null>(null)
  const [selId, setSelId] = useState<string | null>(null)
  const tl = useTestLogin()
  const [testing, setTesting] = useState<string | null>(null)
  const act = async (fn: () => Promise<unknown>, msg?: string) => {
    try {
      await fn()
      q.refetch()
      if (msg) toast('ok', msg)
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const save = () => act(async () => {
    const body: Json = Object.fromEntries(EDITABLE.filter((k) => k !== 'client_secret' || edit!.client_secret).map((k) => [k, edit![k] || (k === 'internal_url' ? null : edit![k])]))
    body.role_mapping = JSON.parse(edit!.mapping || '{}')
    await send('PATCH', `${I}/idps/${edit!.id}`, body)
    setEdit(null)
  }, '저장했습니다')
  const openEdit = (r: Json) => setEdit({ ...r, client_secret: '', mapping: JSON.stringify(r.role_mapping ?? {}, null, 2) })
  // 사용 끄기 = 비활성화 (MSG-002 확인) — 켜기는 바로
  const setEnabled = (r: Json, on: boolean) => {
    if (!on && !confirm(`IdP "${r.name}" 을(를) 비활성화할까요? 이 IdP 로 새로 로그인할 수 없고 이 IdP 가 발급한 토큰 검증도 멈춥니다 (MSG-002).`)) return
    act(() => send('PATCH', `${I}/idps/${r.id}`, { enabled: on }), on ? '사용합니다' : '비활성화했습니다 — 감사 기록')
  }
  const items = q.data?.items ?? []
  const userItems: Json[] = users.data?.items ?? []
  const sel = items.find((x: Json) => x.id === selId)
  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation()  // 행 안 조작은 행 선택(상세)과 분리
  return (
    <>
      <PageHeader title="IdP 연동" crumbs={['Identity', 'IdP 연동']} desc="Entra ID·Okta·Keycloak 등 외부 OIDC IdP — 콘솔 SSO(브로커 로그인)와 Bearer 토큰 검증, 그룹 클레임 → 콘솔 역할 (FR-I00·I01). 내장 IdP 는 항상 사용 가능."
        actions={<Gate perm="identity:write">{(ok, why) => <Link to="/console/identity/idp/new"><Button variant="primary" disabled={!ok} title={why}>+ IdP 연동</Button></Link>}</Gate>} />
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={items} onRowClick={(r) => setSelId(r.id)} empty="연동된 외부 IdP 가 없습니다 — 내장 IdP 로 로그인 중" columns={[
        col.accessor('name', { header: '이름' }), col.accessor('kind', { header: '종류', cell: (c) => <Badge tone="primary">{c.getValue()}</Badge> }),
        col.accessor('issuer', { header: 'issuer', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
        col.display({ id: 'users', header: '사용자', cell: ({ row }) => users.data ? <span className="tabular-nums">{idpUsers(userItems, row.original).count}</span> : '—' }),
        col.accessor('status', { header: '상태', cell: (c) => <StatusBadge status={c.row.original.enabled === false ? 'disabled' : c.getValue()} /> }),
        col.display({ id: 'sync', header: '마지막 로그인', cell: ({ row }) => <span className="text-xs">{users.data ? fmtAgo(idpUsers(userItems, row.original).last) : '—'}</span> }),
        col.display({ id: 'lt', header: '시험 로그인', cell: ({ row }) => row.original.kind === 'builtin' ? '—' : row.original.last_test?.status === 'ok'
          ? <span className="text-xs text-ok">✓ {(row.original.last_test.roles ?? []).join(', ') || '확인됨'}</span> : row.original.last_test?.status === 'error' ? <span className="text-xs text-danger">실패</span> : <span className="text-xs text-fg-muted">미시험</span> }),
        col.display({ id: 'en', header: '사용 · 로그인 노출', cell: ({ row }) => row.original.kind === 'builtin' ? '항상' : (
          <Gate perm="identity:write">{(ok, why) => {
            const untested = !row.original.login_enabled && row.original.last_test?.status !== 'ok'
            return (
              <span className="flex gap-3 text-xs" onClick={stop}>
                <label className="flex items-center gap-1"><input type="checkbox" aria-label={`${row.original.name} 사용`} disabled={!ok} title={why} checked={row.original.enabled}
                  onChange={(e) => setEnabled(row.original, e.target.checked)} />사용</label>
                <label className="flex items-center gap-1" title={why ?? (untested ? '시험 로그인에 성공해야 로그인 화면에 노출할 수 있습니다 (SCR-J02 ④)' : undefined)}><input type="checkbox" aria-label={`${row.original.name} 로그인 노출`} disabled={!ok || untested} checked={row.original.login_enabled}
                  onChange={(e) => act(() => send('PATCH', `${I}/idps/${row.original.id}`, { login_enabled: e.target.checked }))} />로그인 버튼</label>
              </span>
            )
          }}</Gate>) }),
        col.display({ id: 'a', header: '', cell: ({ row }) => row.original.kind === 'builtin' ? null : (
          <Gate perm="identity:write">{(ok, why) => (
            <span className="flex justify-end gap-1" onClick={stop}>
              <Button size="sm" disabled={!ok} title={why} onClick={() => act(async () => setRes(await send<Json>('POST', `${I}/idps/${row.original.id}/test`)))}>디스커버리</Button>
              <Button size="sm" disabled={!ok || !row.original.enabled} title={why ?? (row.original.enabled ? undefined : '비활성화된 IdP 는 시험 로그인할 수 없습니다')} loading={tl.busy && testing === row.original.id} onClick={() => { setTesting(row.original.id); tl.run(row.original.id).then(() => q.refetch()) }}>시험 로그인</Button>
              <Button size="sm" variant="ghost" disabled={!ok} title={why} onClick={() => openEdit(row.original)}>편집</Button>
              <Button size="sm" variant="ghost" disabled={!ok} title={why} onClick={() => confirm(`IdP "${row.original.name}" 연동을 삭제할까요? 이 IdP 로 로그인하던 사용자는 다시 로그인할 수 없습니다 (MSG-002).`) && act(() => send('DELETE', `${I}/idps/${row.original.id}`), '삭제했습니다')}>삭제</Button>
            </span>
          )}</Gate>) }),
      ]} />
      {tl.result && testing && <div className="mt-3"><TestLoginResult r={tl.result} /></div>}
      {sel ? <div className="mt-4"><IdpDetail idp={sel} users={userItems} onEdit={() => openEdit(sel)} onToggle={() => setEnabled(sel, !sel.enabled)} /></div>
        : items.length > 1 && <p className="mt-3 text-xs text-fg-muted">행을 누르면 연동 상세(issuer·client_id·스코프·그룹 클레임·서명 키)를 봅니다.</p>}
      <Modal open={!!res} onClose={() => setRes(null)} title="디스커버리 시험 결과"><CodeBlock value={res ?? {}} /></Modal>
      <Modal open={!!edit} onClose={() => setEdit(null)} title={`IdP 편집 — ${edit?.name ?? ''}`} footer={<><Button onClick={() => setEdit(null)}>취소</Button><Button variant="primary" onClick={save}>저장</Button></>}>
        {edit && <div className="grid grid-cols-2 gap-3">
          {EDITABLE.map((k) => <Field key={k} label={LABEL[k]}><Input type={k === 'client_secret' ? 'password' : 'text'} autoComplete="off" className={k.endsWith('url') ? 'mono' : ''} value={edit[k] ?? ''} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></Field>)}
          <Field label="그룹 → 콘솔 역할 (JSON)"><Textarea className="mono h-28 text-xs" value={edit.mapping} onChange={(e) => setEdit({ ...edit, mapping: e.target.value })} /></Field>
        </div>}
      </Modal>
    </>
  )
}

const ROLES: Record<string, string> = { admin: '플랫폼 관리자', operator: '플랫폼 운영자', developer: '에이전트 개발자', security: '보안·IAM 담당', finops: 'FinOps 담당', user: '최종 사용자' }
const isUrl = (v: string) => /^https?:\/\/[^\s/]+/.test(v)

function parseMapping(mapping: string): { rows: [string, unknown][]; error?: string } {
  try {
    const m = JSON.parse(mapping || '{}')
    if (!m || typeof m !== 'object' || Array.isArray(m)) throw new Error('객체({"그룹": "역할"}) 형식이어야 합니다')
    const rows = Object.entries(m)
    const bad = rows.filter(([, r]) => !ROLES[String(r)]).map(([, r]) => String(r))
    return { rows, error: bad.length ? `알 수 없는 역할: ${bad.join(', ')}` : undefined }
  } catch (e: any) {
    return { rows: [], error: `역할 매핑 JSON 오류: ${e.message}` }
  }
}

/** ⑦ 클레임 매핑 미리보기 — IdP 그룹 → 콘솔 역할. 미매핑 그룹은 최종 사용자(채팅만). */
function MappingPreview({ mapping }: { mapping: string }) {
  const { rows, error } = parseMapping(mapping)
  if (!rows.length && error) return null  // JSON 오류는 아래 오류 줄에 표시
  return (
    <table className="w-full text-xs" aria-label="클레임 매핑 미리보기">
      <thead className="text-left text-fg-muted"><tr><th className="py-1 font-medium">IdP 그룹</th><th className="py-1 font-medium">콘솔 역할</th></tr></thead>
      <tbody>
        {rows.map(([g, r]) => <tr key={g} className="border-t border-line"><td className="mono py-1">{g}</td>
          <td className={cx('py-1', !ROLES[String(r)] && 'text-danger')}>{ROLES[String(r)] ? `${ROLES[String(r)]} (${r})` : `알 수 없는 역할: ${String(r)}`}</td></tr>)}
        <tr className="border-t border-line text-fg-muted"><td className="py-1">미매핑 그룹</td><td className="py-1">최종 사용자 (user · 채팅만)</td></tr>
      </tbody>
    </table>
  )
}

export function IdpWizard() {
  const nav = useNavigate()
  const toast = useToast()
  const [step, setStep] = useState(0)
  const [f, setF] = useState<Json>({ name: '', kind: 'entra', issuer: '', internal_url: '', client_id: '', client_secret: '', audience: '', scopes: 'openid profile email', groups_claim: 'groups', roles_claim: 'roles' })
  const [mapping, setMapping] = useState('{\n  "AgentNexus-Operators": "operator",\n  "AgentNexus-Developers": "developer"\n}')
  const [broker, setBroker] = useState(true)
  const [err, setErr] = useState<unknown>(null)
  const [disc, setDisc] = useState<Json | null>(null)
  const [created, setCreated] = useState<Json | null>(null)
  const tl = useTestLogin()
  const hint: Record<string, string> = { entra: 'https://login.microsoftonline.com/<tenant>/v2.0', okta: 'https://<org>.okta.com/oauth2/default', keycloak: 'https://<host>/realms/<realm>', oidc: 'https://idp.example.com' }
  const issuerBad = !!f.issuer && !isUrl(f.issuer)
  const discover = async () => {
    setErr(null)
    try {
      setDisc(await send<Json>('POST', `${I}/idps/discover`, { issuer: f.issuer, internal_url: f.internal_url || null }))
    } catch (e) {
      setErr(e)
    }
  }
  const gate = !!disc && disc.discovery && disc.jwks && disc.pkce_s256
  // ④ 초안 저장 — 비활성(enabled=false)으로 만든다: 시험 전에는 이 IdP 토큰을 받지 않는다. 시험 로그인은 비활성 초안에서도 가능.
  // 이미 초안이 있으면(시험 실패 후 수정) 같은 연결을 PATCH — issuer·종류는 초안 저장 뒤 바꿀 수 없다.
  const saveDraft = async () => {
    setErr(null)
    try {
      const role_mapping = JSON.parse(mapping || '{}')
      const common = { name: f.name, client_id: f.client_id, scopes: f.scopes, groups_claim: f.groups_claim, roles_claim: f.roles_claim, role_mapping,
        audience: f.audience || undefined, internal_url: f.internal_url || undefined, client_secret: f.client_secret || undefined }
      setCreated(created
        ? await send<Json>('PATCH', `${I}/idps/${created.id}`, { ...common, audience: f.audience || null, internal_url: f.internal_url || null })
        : await send<Json>('POST', `${I}/idps`, { ...common, kind: f.kind, issuer: f.issuer, enabled: false, login_enabled: false }))
      tl.setResult(null)
      setStep(3)
    } catch (e) {
      setErr(e instanceof SyntaxError ? { message: `역할 매핑 JSON 오류: ${e.message}` } : e)
    }
  }
  // 최종 저장·활성화 — 시험 로그인 성공 뒤에만. 브로커 경유면 콘솔·채팅 로그인 버튼에 노출, 아니면 Bearer 토큰 직접 신뢰만.
  const activate = async () => {
    try {
      await send('PATCH', `${I}/idps/${created!.id}`, { enabled: true, ...(broker ? { login_enabled: true } : {}) })
      toast('ok', broker ? '저장했습니다 — 콘솔 로그인 화면에 노출합니다 (MSG-001)' : '저장했습니다 — 로그인 버튼 없이 Bearer 토큰 검증만 사용합니다 (MSG-001)')
      nav('/console/identity/idp')
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const discard = async () => {
    if (!confirm(`초안 "${created!.name}" 을(를) 삭제할까요? 이 연동 설정이 없어지고 되돌릴 수 없습니다 (MSG-002).`)) return
    try {
      await send('DELETE', `${I}/idps/${created!.id}`)
      nav('/console/identity/idp')
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const Check = ({ ok, label }: { ok: boolean; label: string }) => <li className={ok ? 'text-ok' : 'text-danger'}>{ok ? '✓' : '✗'} {label}</li>
  const ep = disc?.endpoints ?? {}
  const tested = tl.result?.status === 'ok'
  const mapErr = parseMapping(mapping).error
  return (
    <>
      <PageHeader title="IdP 연동 추가 (OIDC)" crumbs={['Identity', 'IdP 연동', '추가']} desc="저장 전에 디스커버리·PKCE 를 확인하고, 초안 저장 후 시험 로그인에 성공해야 최종 저장·활성화할 수 있습니다." />
      <Card>
        <Stepper steps={['공급자 선택', 'Issuer·디스커버리', '클라이언트·클레임 매핑', '시험 로그인·저장']} current={step} />
        {step === 0 && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="이름"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="사내 Entra ID" /></Field>
            <Field label="종류" hint={created ? '초안 저장 뒤에는 바꿀 수 없습니다' : undefined}><Select value={f.kind} disabled={!!created} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="entra">Microsoft Entra ID</option><option value="okta">Okta</option><option value="keycloak">Keycloak</option><option value="oidc">일반 OIDC</option></Select></Field>
          </div>
        )}
        {step === 1 && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Issuer" hint={created ? '초안 저장 뒤에는 바꿀 수 없습니다 — 초안 삭제 후 다시 추가' : hint[f.kind]} error={issuerBad ? 'URL 형식이 아닙니다 — https://… (MSG-004)' : undefined}>
                <Input className="mono" value={f.issuer} readOnly={!!created} onChange={(e) => { setF({ ...f, issuer: e.target.value }); setDisc(null) }} /></Field>
              <Field label="서버 측 주소 (선택)" hint="브라우저와 게이트웨이가 보는 주소가 다를 때 (분리 DNS·사설망)"><Input className="mono" value={f.internal_url} onChange={(e) => { setF({ ...f, internal_url: e.target.value }); setDisc(null) }} /></Field>
            </div>
            <Button onClick={discover} disabled={!f.issuer || issuerBad}>디스커버리 시험</Button>
            {disc && <ul className="space-y-1 text-sm" aria-label="디스커버리 결과">
              <Check ok={disc.discovery} label="OpenID 디스커버리 · issuer 일치" />
              {disc.discovery && <Check ok={!!(ep.authorization_endpoint && ep.token_endpoint && ep.jwks_uri)} label="authorization · token · jwks_uri 엔드포인트" />}
              <Check ok={disc.jwks} label={`JWKS 서명 키 ${disc.keys}개`} />
              <Check ok={disc.pkce_s256} label="PKCE S256 지원 (필수)" />{disc.error && <li className="text-xs text-danger">{disc.error}</li>}
              {disc.discovery && !disc.pkce_s256 && <li className="text-xs text-danger">PKCE(S256)를 지원하지 않는 IdP 는 연동할 수 없습니다 — code_challenge_methods_supported: {(ep.code_challenge_methods_supported ?? []).join(', ') || '없음'}</li>}
            </ul>}
          </div>
        )}
        {step === 2 && (
          <div className="grid grid-cols-2 gap-3">
            <div className="flex items-start gap-1">
              <div className="min-w-0 flex-1"><Field label="Redirect URI (IdP 에 등록)" hint="IdP 앱 등록의 리디렉션 URI 에 그대로 붙여 넣으세요"><Input className="mono" readOnly value={disc?.redirect_uri ?? ''} onFocus={(e) => e.target.select()} /></Field></div>
              <Button className="mt-5" type="button" onClick={() => navigator.clipboard?.writeText(disc?.redirect_uri ?? '').then(() => toast('ok', '복사했습니다'))}>복사</Button>
            </div>
            <Field label="요청 스코프"><Input value={f.scopes} onChange={(e) => setF({ ...f, scopes: e.target.value })} /></Field>
            <Field label="Client ID"><Input value={f.client_id} onChange={(e) => setF({ ...f, client_id: e.target.value })} /></Field>
            <Field label="Client Secret" hint={created ? '바꿀 때만 입력 — 시크릿 저장소에만 암호화 보관 (MSG-018)' : '시크릿 저장소에만 암호화 보관 (MSG-018)'}><Input type="password" autoComplete="off" value={f.client_secret} onChange={(e) => setF({ ...f, client_secret: e.target.value })} /></Field>
            <Field label="Audience (Bearer 토큰 aud, 비우면 Client ID)"><Input value={f.audience} onChange={(e) => setF({ ...f, audience: e.target.value })} /></Field>
            <Field label="그룹 클레임"><Input value={f.groups_claim} onChange={(e) => setF({ ...f, groups_claim: e.target.value })} /></Field>
            <Field label="그룹 → 콘솔 역할 (JSON)" hint="역할: admin · operator · developer · security · finops · user · 매핑 없으면 user"><Textarea className="mono h-32 text-xs" value={mapping} onChange={(e) => setMapping(e.target.value)} /></Field>
            <div className="rounded-md border border-line p-3"><div className="mb-1 text-xs font-medium text-fg-muted">클레임 매핑 미리보기</div><MappingPreview mapping={mapping} />
              {mapErr && <p role="alert" className="mt-1 text-xs text-danger">{mapErr} (MSG-004)</p>}</div>
            <label className="col-span-2 flex items-start gap-2 rounded-md border border-line p-3 text-sm">
              <input type="checkbox" className="mt-1" checked={broker} onChange={(e) => setBroker(e.target.checked)} />
              <span><span className="font-medium">브로커 경유 (권장)</span>
                <span className="block text-xs text-fg-muted">켜면 AgentNexus 브로커가 PKCE 로그인을 대신하고 콘솔·채팅 로그인 버튼에 노출합니다. 끄면 로그인 버튼 없이 이 IdP 가 발급한 Bearer 토큰만 직접 신뢰합니다.</span></span>
            </label>
          </div>
        )}
        {step === 3 && created && (
          <div className="space-y-3 text-sm">
            <div className="flex items-center gap-2"><Badge tone={tested ? 'ok' : 'warn'}>{tested ? '시험 성공' : '대기'}</Badge>
              <span>초안으로 저장했습니다 — 아직 로그인 화면에 보이지 않습니다. 새 창(Code + PKCE)으로 시험 로그인해 받은 클레임과 역할 매핑을 확인하세요.</span></div>
            <Kv rows={[['issuer', <span className="mono text-xs">{created.issuer}</span>], ['client_id', <span className="mono text-xs">{created.client_id}</span>],
              ['Redirect URI', <span className="mono text-xs">{created.redirect_uri}</span>], ['활성화 방식', broker ? '브로커 경유 — 콘솔·채팅 로그인 버튼' : '직접 신뢰 — Bearer 토큰 검증만']]} />
            <Button variant="primary" loading={tl.busy} onClick={() => tl.run(created.id)}>시험 로그인 (새 창)</Button>
            {tl.result && <TestLoginResult r={tl.result} />}
            {tl.result && !tested && <p className="text-xs text-fg-muted">이전 단계로 돌아가 클라이언트·클레임을 고친 뒤 다시 시험하세요 — 같은 초안이 갱신됩니다.</p>}
            <p className="text-xs text-fg-muted">초안 자동 저장 · 시험 로그인 성공 전에는 활성화할 수 없습니다 (ST-03).</p>
          </div>
        )}
        {err ? <div className="mt-3"><ErrorBox error={err} /></div> : null}
        <div className="mt-4 flex justify-end gap-2">
          {step === 3 && <Gate perm="identity:write">{(ok, why) => <Button variant="ghost" disabled={!ok} title={why} onClick={discard}>초안 삭제</Button>}</Gate>}
          {step > 0 && <Button onClick={() => setStep(step - 1)}>이전</Button>}
          {step === 0 && <Button variant="primary" disabled={!f.name} onClick={() => setStep(1)}>다음</Button>}
          {step === 1 && <Button variant="primary" disabled={!gate} title={gate ? undefined : '디스커버리·JWKS·PKCE S256 확인 후 진행'} onClick={() => setStep(2)}>다음</Button>}
          {step === 2 && <Gate perm="identity:write">{(ok, why) => <Button variant="primary" disabled={!ok || !f.client_id || !!mapErr} title={why ?? (mapErr ? `${mapErr} (MSG-004)` : f.client_id ? undefined : 'Client ID 는 필수입니다 (MSG-003)')} onClick={saveDraft}>{created ? '초안 갱신' : '초안 저장'}</Button>}</Gate>}
          {step === 3 && <Button onClick={() => nav('/console/identity/idp')}>나중에 (목록으로)</Button>}
          {step === 3 && <Gate perm="identity:write">{(ok, why) => <Button variant="primary" disabled={!ok || !tested} title={why ?? (tested ? undefined : '시험 로그인에 성공해야 저장·활성화할 수 있습니다')} onClick={activate}>저장·활성화</Button>}</Gate>}
        </div>
      </Card>
    </>
  )
}

const TTL_MAX = 900  // SCR-J04 — 교환 토큰은 최대 15분 (API ttl_seconds le=900)
const splitScopes = (v: string | string[]) => (Array.isArray(v) ? v : String(v ?? '').split(/[\s,]+/).filter(Boolean))
const ttlLabel = (s: number) => (s % 60 === 0 ? `${s / 60}분` : `${s}s`)

/** ⑧ 교환 시뮬레이터 — 토큰을 발급하지 않고 정책 판정만 (허용·거부 스코프·수명·MSG-019 사유). */
function ExchangeSimulator({ audiences }: { audiences: string[] }) {
  const [aud, setAud] = useState<string | null>(null)
  const [scopes, setScopes] = useState('')
  const [r, setR] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)
  const audience = (aud ?? audiences[0] ?? '').trim()
  const run = async () => {
    setBusy(true)
    setErr(null)
    try {
      setR({ ...(await send<Json>('POST', `${I}/token-exchange/simulate`, { audience, scopes: splitScopes(scopes) })), audience })
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card title="교환 시뮬레이터" className="mt-4">
      <div className="grid grid-cols-[1fr_1fr_auto] items-end gap-3">
        <Field label="대상 audience"><Input className="mono" list="anx-tx-audiences" value={aud ?? audiences[0] ?? ''} placeholder="mcp:<서버>" onChange={(e) => { setAud(e.target.value); setR(null) }} /></Field>
        <datalist id="anx-tx-audiences">{audiences.map((a) => <option key={a} value={a} />)}</datalist>
        <Field label="요청 스코프 (공백 구분, 비우면 기본 스코프)"><Input className="mono" value={scopes} onChange={(e) => { setScopes(e.target.value); setR(null) }} /></Field>
        <Button variant="primary" disabled={!audience} loading={busy} onClick={run}>시뮬레이션</Button>
      </div>
      {err ? <div className="mt-3"><ErrorBox error={err} /></div> : null}
      {r && (r.ok ? (
        <div role="status" className="mt-3 rounded-md border border-ok/40 bg-ok/10 p-3 text-sm">
          <div className="font-medium text-ok">✓ 교환 허용 — 토큰은 발급하지 않았습니다</div>
          <CodeBlock className="mt-2" maxH="max-h-40" value={`aud:    ${r.audience}\nscope:  ${(r.granted ?? []).join(' ')}\nexp:    +${r.ttl_seconds}s (${ttlLabel(r.ttl_seconds)})\nmethod: ${r.method}\nact:    AgentNexus 게이트웨이 (사용자 토큰은 백엔드로 전달하지 않음)`} />
        </div>
      ) : (
        <div role="alert" className="mt-3 rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
          ✗ 교환 거부 — {r.reason ?? '정책 판정 실패 (MSG-019)'}
          {(r.denied ?? []).length > 0 && <div className="mt-1 text-xs">거부 스코프: <span className="mono">{r.denied.join(' ')}</span></div>}
        </div>
      ))}
      <p className="mt-2 text-xs text-fg-muted">유효 스코프 = 사용자 권한 ∩ 정책 허용 스코프 — 교집합이 비거나 허용 밖 스코프를 요청하면 교환이 실패하고 채팅에 MSG-019 로 표시됩니다.</p>
    </Card>
  )
}

export function TokenExchange() {
  const toast = useToast()
  const q = useGet(`${I}/token-exchange/policies`)
  const [edit, setEdit] = useState<Json | null>(null)
  const ttl = Number(edit?.ttl_seconds)
  const ttlErr = edit && (!Number.isInteger(ttl) || ttl < 30 || ttl > TTL_MAX) ? `TTL 은 30~${TTL_MAX}초 (최대 15분, MSG-004)` : undefined
  const audErr = edit && !/^(mcp|a2a|model|api):[A-Za-z0-9._*-]+$/.test(edit.audience ?? '') ? 'audience 는 필수 — mcp:<서버> 형식 (MSG-003·MSG-004)' : undefined
  const save = async () => {
    try {
      await send('PUT', `${I}/token-exchange/policies`, { audience: edit!.audience, allowed_scopes: splitScopes(edit!.allowed_scopes), default_scopes: splitScopes(edit!.default_scopes ?? []),
        ttl_seconds: ttl, method: edit!.method, enabled: edit!.enabled })
      setEdit(null)
      q.refetch()
      toast('ok', '저장했습니다 — 게이트웨이에 즉시 반영 (MSG-001)')
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const items: Json[] = q.data?.items ?? []
  return (
    <>
      <PageHeader title="토큰 교환 정책 (RFC 8693)" crumbs={['Identity', '토큰 교환']} desc="게이트웨이가 사용자 토큰을 대상(MCP·A2A·모델)별 짧은 수명·최소 스코프 토큰으로 바꿉니다 (FR-I02)."
        actions={<Gate perm="identity:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => setEdit({ audience: 'mcp:', allowed_scopes: '', default_scopes: '', ttl_seconds: 300, method: 'rfc8693', enabled: true })}>+ 정책</Button>}</Gate>} />
      <div className="mb-3 rounded-md border border-primary/30 bg-primary-soft px-3 py-2 text-xs">원칙: 사용자 토큰을 백엔드에 그대로 넘기지 않습니다 — 게이트웨이가 검증한 뒤 대상별 최소 스코프·최대 15분 토큰으로 교환하고, 정책 없는 대상은 호출을 거부합니다 (MSG-019).</div>
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={items} onRowClick={(r) => setEdit({ ...r, allowed_scopes: (r.allowed_scopes ?? []).join(' '), default_scopes: (r.default_scopes ?? []).join(' ') })} columns={[
        col.accessor('audience', { header: 'audience', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
        col.accessor('audience_type', { header: '대상' }),
        col.accessor('allowed_scopes', { header: '허용 스코프', enableSorting: false, cell: (c) => <span className="mono text-xs">{(c.getValue() ?? []).join(' ')}</span> }),
        col.accessor('default_scopes', { header: '기본', enableSorting: false, cell: (c) => <span className="mono text-xs">{(c.getValue() ?? []).join(' ')}</span> }),
        col.accessor('ttl_seconds', { header: 'TTL', cell: (c) => `${c.getValue()}s` }),
        col.accessor('method', { header: '방식', cell: (c) => <Badge>{c.getValue()}</Badge> }),
        col.accessor('enabled', { header: '사용', cell: (c) => <StatusBadge status={c.getValue() ? 'enabled' : 'disabled'} /> }),
      ]} />
      <ExchangeSimulator audiences={items.filter((x) => x.enabled).map((x) => x.audience)} />
      <Modal open={!!edit} onClose={() => setEdit(null)} title="토큰 교환 정책" footer={<><Button onClick={() => setEdit(null)}>취소</Button><Gate perm="identity:write">{(ok, why) => <Button variant="primary" disabled={!ok || !!ttlErr || !!audErr} title={why ?? ttlErr ?? audErr} onClick={save}>저장</Button>}</Gate></>}>
        {edit && <div className="space-y-3">
          <Field label="audience" hint="mcp:<서버> · a2a:<에이전트> · model:<모델> · api:<이름>" error={audErr}><Input className="mono" value={edit.audience} onChange={(e) => setEdit({ ...edit, audience: e.target.value })} /></Field>
          <Field label="허용 스코프 (공백 구분)" hint="교환 토큰에 넣을 수 있는 최대 범위 — 쓰기 스코프는 고위험 승인 후에만"><Input className="mono" value={edit.allowed_scopes} onChange={(e) => setEdit({ ...edit, allowed_scopes: e.target.value })} /></Field>
          <Field label="기본 스코프"><Input className="mono" value={edit.default_scopes} onChange={(e) => setEdit({ ...edit, default_scopes: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="TTL(초)" hint={ttlErr ? undefined : `최대 ${TTL_MAX}초 (15분)${Number.isFinite(ttl) && ttl > 0 ? ` · ${ttlLabel(ttl)}` : ''}`} error={ttlErr}><Input type="number" min={30} max={TTL_MAX} value={edit.ttl_seconds} onChange={(e) => setEdit({ ...edit, ttl_seconds: e.target.value })} /></Field>
            <Field label="방식" hint={edit.method === 'obo' ? 'Entra On-Behalf-Of — actor 는 게이트웨이 워크로드' : 'subject=사용자 토큰 · actor=게이트웨이 (J07)'}><Select value={edit.method} onChange={(e) => setEdit({ ...edit, method: e.target.value })}><option value="rfc8693">RFC 8693</option><option value="obo">OBO (Entra)</option></Select></Field>
          </div>
          <p className="text-xs text-fg-muted">유효 스코프 = 사용자 권한 ∩ 정책 허용 스코프 · 저장하면 감사에 기록되고 게이트웨이에 즉시 반영됩니다.</p>
        </div>}
      </Modal>
    </>
  )
}

// 판정 (SCR-J06 ①) — 비준수 우선 정렬. 게이트웨이 안에서 도는 stdio·hosted-openapi 는 OAuth 대상이 아님(로컬 전용).
const LOCAL_TRANSPORTS = ['stdio', 'hosted-openapi']
function verdict(r: Json, transport?: string): { rank: number; tone: Tone; label: string } {
  if (r.auth_type !== 'oauth2') return transport && LOCAL_TRANSPORTS.includes(transport) ? { rank: 3, tone: 'muted', label: '로컬 전용' } : { rank: 0, tone: 'danger', label: '비준수' }
  return r.resource_metadata && r.authorization_server && r.token_exchange_policy ? { rank: 2, tone: 'ok', label: '준수' } : { rank: 1, tone: 'warn', label: '경고' }
}
const CHECK_LABEL: Record<string, string> = { prm: '① 보호 리소스 메타데이터 (RFC 9728 PRM)', authorization_server: '② 인가 서버 메타데이터 발견 (RFC 8414)',
  scopes_supported: '③ 서버 선언 스코프 (scopes_supported)', exchange_policy: '④ 토큰 교환 정책 (aud=mcp:서버)', tool_scopes: '⑤ 도구별 스코프 ⊆ 정책 허용 스코프' }

/** 다시 탐색 전 — 목록 값으로 만든 점검표(⑤는 탐색해야 확인). 다시 탐색 후 — 서버 응답 checks[{key, ok, detail}]. */
function staticChecks(r: Json): Json[] {
  const oauth = r.auth_type === 'oauth2'
  return [
    { key: 'prm', ok: !oauth || !!r.resource_metadata, detail: r.resource_metadata ?? (oauth ? 'PRM 없음' : '인증 없음') },
    { key: 'authorization_server', ok: !oauth || !!r.authorization_server, detail: r.authorization_server ?? '—' },
    { key: 'scopes_supported', ok: true, detail: (r.scopes_supported ?? []).join(', ') || '선언 없음' },
    { key: 'exchange_policy', ok: !!r.token_exchange_policy, detail: r.token_exchange_policy ? '정책 있음' : '교환 정책 없음' },
    { key: 'tool_scopes', ok: null, detail: '다시 탐색으로 확인' },
  ]
}

function McpAuthDetail({ r, transport, onDone }: { r: Json; transport?: string; onDone: () => void }) {
  const toast = useToast()
  const [res, setRes] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  const rediscover = async () => {
    setBusy(true)
    try {
      const out = await send<Json>('POST', `${I}/mcp-auth/${encodeURIComponent(r.server_id)}/rediscover`)
      setRes(out)
      onDone()
      toast(out.checks?.every((c: Json) => c.ok) ? 'ok' : 'info', out.checks?.every((c: Json) => c.ok) ? '다시 탐색 — 모든 항목 통과' : '다시 탐색 — 확인이 필요한 항목이 있습니다')
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  const checks: Json[] = res?.checks ?? staticChecks(r)
  const local = r.auth_type !== 'oauth2' && !!transport && LOCAL_TRANSPORTS.includes(transport)
  const scopeIssue = checks.find((c) => c.key === 'tool_scopes' && c.ok === false)
  return (
    <div className="mt-4 grid grid-cols-[3fr_2fr] gap-3">
      <Card title={<>발견·인가 과정 — <span className="mono">{r.server_id}</span></>}
        actions={<Gate perm="mcp:write">{(ok, why) => <Button size="sm" disabled={!ok} title={why} loading={busy} onClick={rediscover}>다시 탐색</Button>}</Gate>}>
        <ul className="space-y-1.5 text-sm" aria-label="MCP Authorization 점검표">
          {checks.map((c) => (
            <li key={c.key} className="flex gap-2">
              <span aria-hidden className={c.ok == null ? 'text-fg-muted' : c.ok ? 'text-ok' : 'text-danger'}>{c.ok == null ? '○' : c.ok ? '✓' : '✗'}</span>
              <span className="min-w-0"><span>{CHECK_LABEL[c.key] ?? c.key}</span> <span className="mono block text-[11px] break-all text-fg-muted">{String(c.detail ?? '')}</span></span>
            </li>
          ))}
        </ul>
        {res?.refresh && <p className={cx('mt-2 text-xs', res.refresh.error ? 'text-danger' : 'text-fg-muted')}>
          tools/list — {res.refresh.error ? `실패: ${res.refresh.error}` : `도구 ${(res.refresh.tools ?? []).length}개 · ${fmtMs(res.refresh.latency_ms)}`}</p>}
        <div className="mt-3 text-xs font-medium text-fg-muted">PRM 원문 (요약)</div>
        <CodeBlock className="mt-1" maxH="max-h-48" value={{ resource: r.resource_metadata, authorization_servers: r.authorization_server ? [r.authorization_server] : [],
          scopes_supported: r.scopes_supported ?? [], tool_scopes: r.tool_scopes ?? {}, ...(res?.refresh?.auth && Object.keys(res.refresh.auth).length ? { discovered: res.refresh.auth } : {}) }} />
      </Card>
      <Card title="예외·조치">
        <div className="space-y-2 text-xs">
          {r.auth_type !== 'oauth2' && !local && <p className="rounded-md border border-danger/40 bg-danger/10 p-2 text-danger">{r.server_id}: PRM 없음 — 사용자 신원이 업스트림으로 전파되지 않습니다. 서버에 PRM(RFC 9728)을 추가한 뒤 [다시 탐색], 임시 예외는 보안 승인 + 만료일로 (MSG-005).</p>}
          {r.auth_type === 'oauth2' && !r.token_exchange_policy && <p className="rounded-md border border-warn/50 bg-warn/10 p-2">교환 정책이 없어 사용자 위임 호출이 거부됩니다 — <Link className="text-primary hover:underline" to="/console/identity/token-exchange">토큰 교환 정책</Link>에 mcp:{r.server_id} 를 추가하세요 (MSG-019).</p>}
          {scopeIssue && <p className="rounded-md border border-warn/50 bg-warn/10 p-2">스코프 경고: {String(scopeIssue.detail)} — 도구에 필요한 최소 스코프만 허용하도록 정책을 조정하세요.</p>}
          {local && <p className="text-fg-muted">게이트웨이 안에서 실행되는 {transport} 서버는 OAuth 대상이 아닙니다 — 백엔드 자격 증명은 게이트웨이 시크릿에만 둡니다 (MSG-018).</p>}
          {verdict(r, transport).label === '준수' && !scopeIssue && <p className="text-ok">조치할 항목이 없습니다.</p>}
        </div>
      </Card>
    </div>
  )
}

export function McpAuth() {
  const q = useGet(`${I}/mcp-auth`)
  const servers = useGet('/api/v1/mcp-servers')  // 전송 방식(로컬 전용 판정) — 권한이 없으면 원격으로 본다
  const [selId, setSelId] = useState<string | null>(null)
  const transport = Object.fromEntries((servers.data?.items ?? []).map((x: Json) => [x.id, x.transport])) as Record<string, string>
  const items = [...(q.data?.items ?? [])].sort((x: Json, y: Json) => verdict(x, transport[x.server_id]).rank - verdict(y, transport[y.server_id]).rank)
  const sel = items.find((x) => x.server_id === selId)
  return (
    <>
      <PageHeader title="MCP Authorization (서버별)" crumbs={['Identity', 'MCP Authorization']} desc="RFC 9728 보호 리소스 메타데이터 → 인가 서버 → 도구별 최소 스코프 — 서버별로 위임(사용자 신원) 또는 서비스 자격 증명 (FR-I03)." />
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={items} onRowClick={(r) => setSelId(r.server_id)} columns={[
        col.accessor('server_id', { header: '서버', cell: (c) => <Link className="mono text-primary" onClick={(e) => e.stopPropagation()} to={`/console/mcp-servers/${c.getValue()}`}>{c.getValue()}</Link> }),
        col.display({ id: 'tr', header: '전송', cell: ({ row }) => <span className="text-xs">{transport[row.original.server_id] ?? '—'}</span> }),
        col.accessor('auth_type', { header: '방식', cell: (c) => <Badge tone={c.getValue() === 'none' ? 'warn' : 'primary'}>{c.getValue()}</Badge> }),
        col.accessor('resource_metadata', { header: 'PRM (RFC 9728)', cell: (c) => c.getValue() ? <span className="text-xs text-ok">✓ 발견</span> : <span className="text-xs text-fg-muted">{c.row.original.auth_type === 'oauth2' ? '✗ 없음' : '해당 없음'}</span> }),
        col.accessor('mode', { header: '신원', cell: (c) => <span className="text-xs">{c.getValue() === 'delegation' ? '사용자 위임(OBO)' : c.getValue()}</span> }),
        col.accessor('authorization_server', { header: '인가 서버', cell: (c) => <span className="mono text-[11px]">{c.getValue() ?? '—'}</span> }),
        col.accessor('tool_scopes', { header: '도구별 스코프', enableSorting: false, cell: (c) => <span className="mono text-[11px]">{Object.entries(c.getValue() ?? {}).map(([t, s]) => `${t}=${s}`).join(' ')}</span> }),
        col.accessor('credential_configured', { header: '자격증명', cell: (c) => (c.getValue() ? '시크릿 저장소' : '—') }),
        col.display({ id: 'v', header: '판정', cell: ({ row }) => { const v = verdict(row.original, transport[row.original.server_id]); return <Badge tone={v.tone}>{v.label}</Badge> } }),
      ]} />
      {sel ? <McpAuthDetail key={sel.server_id} r={sel} transport={transport[sel.server_id]} onDone={() => q.refetch()} />
        : <p className="mt-2 text-xs text-fg-muted">행을 누르면 PRM·인가 서버·스코프 점검표와 [다시 탐색]을 봅니다. 인증 없는(none) 서버는 정책·감사는 적용되지만 사용자 신원이 업스트림으로 전파되지 않습니다 — OAuth(RFC 9728) 서버를 권장합니다.</p>}
    </>
  )
}

export function Workloads() {
  const toast = useToast()
  const q = useGet(`${I}/workloads`)
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ name: '', kind: 'agent', scopes: 'gateway:invoke' })
  const [secret, setSecret] = useState<Json | null>(null)
  const create = async () => {
    try {
      setSecret(await send<Json>('POST', `${I}/workloads`, { ...f, scopes: f.scopes.split(/[\s,]+/).filter(Boolean) }))
      setOpen(false)
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  return (
    <>
      <PageHeader title="워크로드(에이전트·서비스) 아이덴티티" crumbs={['Identity', '워크로드']} desc="에이전트·MCP 서버·서비스의 client_credentials 신원 (SPIFFE ID) — 사람이 아닌 호출자도 정책·감사 대상 (FR-I04)."
        actions={<Gate perm="identity:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => setOpen(true)}>+ 워크로드</Button>}</Gate>} />
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={q.data?.items ?? []} columns={[
        col.accessor('name', { header: '이름' }), col.accessor('kind', { header: '종류', cell: (c) => <Badge>{c.getValue()}</Badge> }),
        col.accessor('client_id', { header: 'client_id', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
        col.accessor('spiffe_id', { header: 'SPIFFE ID', cell: (c) => <span className="mono text-[11px]">{c.getValue()}</span> }),
        col.accessor('scopes', { header: '스코프', enableSorting: false, cell: (c) => <span className="mono text-xs">{(c.getValue() ?? []).join(' ')}</span> }),
        col.accessor('rotated_at', { header: '교체', cell: (c) => fmtAgo(c.getValue()) }),
        col.display({ id: 'r', header: '', cell: ({ row }) => <Gate perm="identity:write">{(ok) => <Button size="sm" disabled={!ok} onClick={() => confirm('시크릿을 교체할까요? 기존 시크릿은 즉시 무효가 됩니다.') && send<Json>('POST', `${I}/workloads/${row.original.id}/rotate`).then(setSecret).catch((e) => toast('error', e.message))}>시크릿 교체</Button>}</Gate> }),
      ]} />
      <Modal open={open} onClose={() => setOpen(false)} title="워크로드 추가" footer={<><Button onClick={() => setOpen(false)}>취소</Button><Button variant="primary" disabled={!f.name} onClick={create}>생성</Button></>}>
        <div className="space-y-3">
          <Field label="이름 (영문 소문자·숫자·하이픈)"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="종류"><Select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="agent">agent</option><option value="mcp_server">mcp_server</option><option value="service">service</option></Select></Field>
          <Field label="스코프"><Input className="mono" value={f.scopes} onChange={(e) => setF({ ...f, scopes: e.target.value })} /></Field>
        </div>
      </Modal>
      <Modal open={!!secret} onClose={() => setSecret(null)} title="client_secret — 지금만 표시">
        {secret && <><Kv rows={[['client_id', <span className="mono">{secret.client_id}</span>]]} /><CodeBlock className="mt-2" value={secret.client_secret} maxH="max-h-20" /><p className="mt-2 text-xs text-warn">안전한 곳에 보관하세요 — 다시 표시되지 않습니다 (MSG-018).</p></>}
      </Modal>
    </>
  )
}
