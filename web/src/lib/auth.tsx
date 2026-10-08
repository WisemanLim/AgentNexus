// OIDC Authorization Code + PKCE(S256) — 내장 IdP(/idp) 또는 연동 IdP. 토큰은 sessionStorage(탭 범위), 갱신은 refresh 회전.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, configureApi, type Json } from './api'

export const CLIENT_ID = 'agentnexus-console'
const KEY = 'anx.auth'
const PENDING = 'anx.pkce'

type Tokens = { access_token: string; refresh_token?: string; expires_at: number }
export type Me = {
  sub: string; name: string; email?: string; roles: string[]; team?: string; permissions: string[]
  role_labels: Record<string, string>; kind: string
}

const b64url = (buf: ArrayBuffer | Uint8Array) =>
  btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

export function randomString(bytes = 32) {
  return b64url(crypto.getRandomValues(new Uint8Array(bytes)))
}

export async function pkceChallenge(verifier: string) {
  if (!crypto.subtle) throw new Error('PKCE 에는 보안 컨텍스트(HTTPS 또는 localhost)가 필요합니다.')
  return b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))
}

export const redirectUri = () => `${location.origin}/login/callback`

function load(): Tokens | null {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) || 'null')
  } catch {
    return null
  }
}
function save(t: Tokens | null) {
  try {
    if (t) sessionStorage.setItem(KEY, JSON.stringify(t))
    else sessionStorage.removeItem(KEY)
  } catch {
    /* 저장 불가(사생활 모드) — 메모리만 */
  }
}

let current: Tokens | null = load()
let refreshing: Promise<boolean> | null = null

function store(res: Json) {
  current = { access_token: res.access_token, refresh_token: res.refresh_token, expires_at: Date.now() + (res.expires_in || 3600) * 1000 }
  save(current)
}

async function refresh(): Promise<boolean> {
  if (!current?.refresh_token) return false
  refreshing ??= api('/idp/token', { form: { grant_type: 'refresh_token', refresh_token: current.refresh_token, client_id: CLIENT_ID } })
    .then((r) => (store(r), true))
    .catch(() => false)
    .finally(() => (refreshing = null))
  return refreshing
}

/** 로그인 시작 — idpHint 를 주면 내장 IdP 가 그 외부 IdP(Entra·Okta·Keycloak)로 바로 위임한다 (브로커, SCR-A10). */
export async function startLogin(returnTo = '/', idpHint?: string) {
  const verifier = randomString(48)
  const state = randomString(16)
  sessionStorage.setItem(PENDING, JSON.stringify({ verifier, state, returnTo }))
  const q = new URLSearchParams({
    response_type: 'code', client_id: CLIENT_ID, redirect_uri: redirectUri(), scope: 'openid profile email',
    state, code_challenge: await pkceChallenge(verifier), code_challenge_method: 'S256', ...(idpHint ? { idp_hint: idpHint } : {}),
  })
  location.assign(`/idp/authorize?${q}`)
}

export async function completeLogin(params: URLSearchParams): Promise<string> {
  const pending = JSON.parse(sessionStorage.getItem(PENDING) || 'null')
  sessionStorage.removeItem(PENDING)
  if (!pending || pending.state !== params.get('state')) throw new Error('state 불일치 — 로그인을 다시 시작하세요.')
  const code = params.get('code')
  if (!code) throw Object.assign(new Error(params.get('error_description') || '인가 코드가 없습니다.'), { code: params.get('error') || 'login_failed' })
  const res = await api('/idp/token', {
    form: { grant_type: 'authorization_code', code, redirect_uri: redirectUri(), client_id: CLIENT_ID, code_verifier: pending.verifier },
  })
  store(res)
  return pending.returnTo || '/'
}

export function homeFor(me: Me): string {
  const r = new Set(me.roles)
  if (r.has('admin') || r.has('operator') || r.has('security')) return '/console'
  if (r.has('developer')) return '/console/agents'
  if (r.has('finops')) return '/console/observe/cost'
  return '/chat'
}

export const can = (me: Me | null | undefined, perm?: string) =>
  !perm || !!me && (me.permissions.includes('*') || me.permissions.includes(perm))

type Ctx = { me: Me | null; loading: boolean; logout: () => void; reload: (silent?: boolean) => Promise<void>; authed: boolean; expired: boolean; relogin: () => void }
const AuthCtx = createContext<Ctx>({ me: null, loading: true, logout: () => {}, reload: async () => {}, authed: false, expired: false, relogin: () => {} })

/** 재로그인 창(팝업)에서 받은 토큰 — 같은 출처만 (ST-11) */
const RELOGIN = 'anx-relogin'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(!!current)
  const [expired, setExpired] = useState(false)

  const logout = useCallback(() => {
    const rt = current?.refresh_token
    current = null
    save(null)
    setMe(null)
    if (rt) api('/idp/revoke', { form: { token: rt, client_id: CLIENT_ID } }).catch(() => {})
  }, [])

  const reload = useCallback(async (silent = false) => {
    if (!current) return setLoading(false)
    if (!silent) setLoading(true)  // 재로그인(ST-11)은 조용히 — 로딩 화면으로 바꾸면 페이지가 다시 마운트돼 입력이 사라진다
    try {
      setMe(await api<Me>('/api/v1/me'))
    } catch {
      setMe(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    configureApi({
      token: () => current?.access_token ?? null,
      refresh,
      expired: () => {
        current = null
        save(null)
        // 세션 만료 (ST-11) — 화면을 떠나지 않고 재로그인 모달로 입력을 보존한다. 로그인 화면 자체에서는 그대로.
        if (!location.pathname.startsWith('/login')) setExpired(true)
      },
    })
    reload()
  }, [reload])

  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.origin !== location.origin || e.data?.type !== RELOGIN || !e.data.tokens?.access_token) return
      store(e.data.tokens)
      setExpired(false)
      reload(true)
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [reload])

  const relogin = useCallback(() => {
    window.open('/login?popup=1', 'anx-relogin', 'width=520,height=720')
  }, [])

  const value = useMemo(() => ({ me, loading, logout, reload, authed: !!current || expired, expired, relogin }),
    [me, loading, logout, reload, expired, relogin])
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>
}

export const useAuth = () => useContext(AuthCtx)
export const RELOGIN_MSG = RELOGIN
/** 재로그인 팝업 → 원래 창으로 넘길 토큰 (ST-11) */
export const relayTokens = () => current && { access_token: current.access_token, refresh_token: current.refresh_token,
  expires_in: Math.max(60, Math.round((current.expires_at - Date.now()) / 1000)) }
export const _test = { store, setCurrent: (t: Tokens | null) => (current = t) }
