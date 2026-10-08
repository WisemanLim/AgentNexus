// SCR-A10 로그인(OIDC SSO) — Authorization Code + PKCE(S256). 만료 복귀 시 MSG-024, 로그인 후 역할별 첫 화면.
import { useEffect, useState } from 'react'
import { useGet } from '@/lib/query'
import { Navigate, useNavigate, useSearchParams } from 'react-router'
import { Badge, Button } from '@/components/ui'
import { RELOGIN_MSG, completeLogin, homeFor, relayTokens, startLogin, useAuth, type Me } from '@/lib/auth'
import { api } from '@/lib/api'

// 브로커 로그인 실패 원인 (서버 broker.ERRORS 와 같은 코드) — 원인별로 다음 행동을 안내한다
const CAUSE: Record<string, string> = {
  access_denied: 'IdP 에서 로그인을 취소했거나 거부했습니다. 다시 시도하세요.',
  invalid_state: '로그인 요청이 만료되었거나 이미 사용되었습니다. 처음부터 다시 로그인하세요.',
  idp_unavailable: 'IdP 에 연결할 수 없습니다. 잠시 후 다시 시도하거나 다른 방법으로 로그인하세요.',
  token_invalid: 'IdP 토큰 검증에 실패했습니다. 관리자에게 IdP 설정(서명 키·Client ID)을 확인해 달라고 하세요.',
  user_disabled: '비활성화된 계정입니다. 관리자에게 문의하세요.',
  idp_disabled: '이 IdP 는 지금 사용할 수 없습니다. 다른 방법으로 로그인하세요.',
}
const FIRST = [['플랫폼 운영자·보안', '/console'], ['에이전트 개발자', '/console/agents'], ['FinOps', '/console/observe/cost'], ['최종 사용자', '/chat']]

export function Login() {
  const [sp] = useSearchParams()
  const { me, loading } = useAuth()
  const [err, setErr] = useState('')
  const providers = useGet('/idp/providers')
  const next = sp.get('next') || ''
  const cause = sp.get('error')
  if (sp.get('popup')) {
    try { sessionStorage.setItem('anx.popup', '1') } catch { /* 무시 */ }  // 재로그인 창 (ST-11) — 콜백에서 원래 창으로 토큰을 넘긴다
  }
  if (!loading && me && !sp.get('popup')) return <Navigate to={next || homeFor(me)} replace />
  const go = (hint?: string) => startLogin(next, hint).catch((e) => setErr(String(e.message || e)))
  return (
    <div className="grid min-h-full place-items-center p-4">
      <div className="flex w-full max-w-3xl flex-wrap items-start justify-center gap-6">
        <main className="w-[360px] rounded-xl border border-line bg-surface p-8 shadow-sm">
          <div className="mx-auto grid size-14 place-items-center rounded-full bg-primary text-2xl font-semibold text-white">N</div>
          <h1 className="mt-3 text-center text-xl font-semibold text-navy">AgentNexus</h1>
          <p className="mb-5 text-center text-sm text-fg-muted">조직 계정으로 로그인</p>
          {sp.get('expired') && <div role="status" className="mb-3 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-xs">로그인이 만료되었습니다. 다시 로그인하면 이어갑니다 (MSG-024).</div>}
          {cause && <div role="alert" className="mb-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs">{CAUSE[cause] ?? sp.get('desc') ?? '로그인에 실패했습니다.'} <span className="mono text-fg-muted">({cause})</span></div>}
          {(providers.data?.items ?? []).map((p: { id: string; name: string; kind: string }) => (
            <Button key={p.id} className="mb-2 h-10 w-full" onClick={() => go(p.id)}>{p.name} 계정으로 로그인</Button>
          ))}
          <Button variant="primary" className="h-10 w-full" onClick={() => go()}>조직 계정(OIDC)으로 로그인</Button>
          <p className="mt-3 text-center text-xs text-fg-muted">{(providers.data?.items ?? []).length ? '외부 IdP 계정의 그룹이 콘솔 역할로 매핑됩니다.' : '연동 IdP(Entra ID·Okta·Keycloak)는 운영자가 Identity › IdP 연동에서 추가합니다.'}</p>
          {err && <p role="alert" className="mt-3 text-xs text-danger">{err}</p>}
          <p className="mt-6 text-center text-[11px] text-fg-muted">OIDC Authorization Code + PKCE(S256) · state 검증 · 토큰은 이 탭에만 보관</p>
        </main>
        <aside className="w-[260px] rounded-xl border border-line bg-surface p-4 text-sm">
          <h2 className="mb-2 font-semibold text-navy">로그인 후 첫 화면</h2>
          <table className="w-full text-xs"><tbody>
            {FIRST.map(([r, p]) => <tr key={r} className="border-b border-line last:border-0"><td className="py-1.5">{r}</td><td className="mono text-right text-fg-muted">{p}</td></tr>)}
          </tbody></table>
          <p className="mt-2 text-xs text-fg-muted">역할은 IdP 그룹 클레임으로 로그인마다 동기화됩니다. <Badge tone="muted">SCR-L03</Badge></p>
        </aside>
      </div>
    </div>
  )
}

// StrictMode(개발) 의 effect 이중 실행에도 인가 코드는 한 번만 교환
let inflight: Promise<string> | null = null

export function LoginCallback() {
  const [sp] = useSearchParams()
  const nav = useNavigate()
  const { reload } = useAuth()
  const [err, setErr] = useState('')
  useEffect(() => {
    let active = true
    inflight ??= completeLogin(sp).finally(() => setTimeout(() => (inflight = null), 0))
    inflight
      .then(async (returnTo) => {
        let popup = false
        try { popup = sessionStorage.getItem('anx.popup') === '1' && !!window.opener } catch { /* 무시 */ }
        if (popup) {  // 재로그인 창 — 원래 창(입력 보존)에 토큰을 넘기고 닫는다
          sessionStorage.removeItem('anx.popup')
          window.opener.postMessage({ type: RELOGIN_MSG, tokens: relayTokens() }, location.origin)
          window.close()
          return
        }
        await reload()
        const me = await api<Me>('/api/v1/me')
        if (active) nav(returnTo && !['/', '/console'].includes(returnTo) ? returnTo : homeFor(me), { replace: true })
      })
      .catch((e) => {
        if (!active) return
        if (e?.code && e.code !== 'login_failed') nav(`/login?${new URLSearchParams({ error: e.code, desc: e.message })}`, { replace: true })  // 원인별 안내
        else setErr(String(e.message || e))
      })
    return () => { active = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <div className="grid min-h-full place-items-center">
      {err ? (
        <div role="alert" className="rounded-lg border border-danger/30 bg-surface p-6 text-sm">
          <p className="text-danger">로그인 실패: {err}</p>
          <Button className="mt-3" onClick={() => nav('/login')}>다시 로그인</Button>
        </div>
      ) : <p className="text-sm text-fg-muted">로그인 처리 중…</p>}
    </div>
  )
}
