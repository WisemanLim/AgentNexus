> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../../INDEX.md) · 디렉터리 개요: [lib_Abstract.md](lib_Abstract.md)

### 36. web/src/lib/auth.tsx
**파일 요약 / File Summary**
- 역할 / Role: 콘솔 인증 — OIDC Authorization Code + PKCE(S256), 토큰 sessionStorage(탭 범위) 저장, refresh 회전, 역할별 홈·권한 확인(`can`), AuthProvider 컨텍스트, 세션 만료 시 재로그인 팝업(postMessage).
- 진입점 거리 / Distance: 1 (`web/src/main.tsx:9`, `web/src/App.tsx:6`)
- LOC: 168
- 주요 export: `CLIENT_ID`, `Me`, `randomString`, `pkceChallenge`, `redirectUri`, `startLogin`, `completeLogin`, `homeFor`, `can`, `AuthProvider`, `useAuth`, `RELOGIN_MSG`, `relayTokens`, `_test`
- 의존(내부): `./api`(api·configureApi)
- 외부 패키지: react, Web Crypto(`crypto.subtle`·`getRandomValues`)
- 부수효과 / Side effects: `sessionStorage` 읽기·쓰기, `location.assign`(리다이렉트), `window.open`(팝업), `message` 이벤트 리스너, 모듈 전역 `current`·`refreshing`
- 동시성 / Concurrency: 브라우저 단일 스레드, refresh 단일 비행(`refreshing` 프라미스 공유)
- 테스트 커버리지 흔적: `web/src/test/auth.test.ts`(`can`·`homeFor`·`pkceChallenge`·`randomString`)

## 라인 리뷰 / Line Walk (Level 2)

```tsx path=web/src/lib/auth.tsx lines=1-27
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
```
**분석 / Analysis (Level 2)**
- L1: 설계 요약 — 토큰은 **sessionStorage**(탭 범위, 탭 닫으면 사라짐 — localStorage 보다 탈취 노출이 적다).
- L10-13 `Me`: 서버가 주는 사용자 표현(권한 배열 포함).
- L15-16 `b64url`: ArrayBuffer → base64url. `String.fromCharCode(...new Uint8Array(buf))` 는 버퍼가 매우 크면 스택 한계에 걸리지만 PKCE·랜덤(수십 바이트)이라 안전.
- L18-20 `randomString`: CSPRNG(`crypto.getRandomValues`) — PKCE verifier·state 에 적합.
- L22-25 `pkceChallenge`: 보안 컨텍스트(HTTPS·localhost) 아니면 `crypto.subtle` 이 없어 명확한 오류.
- L27 `redirectUri`: 현재 origin 기준 — 배포 도메인과 일치해야 서버가 수락.
**짚어볼 점 / Watch-outs**
- sessionStorage 토큰은 **XSS 에 노출**된다(스크립트가 읽을 수 있음) — 더 안전한 방식은 HttpOnly 쿠키지만, 이 앱은 SPA + Bearer 구조라 trade-off 를 택했다. CSP·의존성 관리로 XSS 를 막는 것이 전제다.

```tsx path=web/src/lib/auth.tsx lines=29-60
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
```
**분석 / Analysis (Level 2)**
- L29-43 `load`/`save`: sessionStorage 접근을 try/catch(사생활 모드·차단 시 메모리만) — 견고한 처리.
- L45-46: 모듈 전역 `current`(현재 토큰)·`refreshing`(진행 중 갱신 프라미스).
- L48-51 `store`: 응답 → 토큰 + 만료 시각(`expires_in`).
- L53-60 `refresh`: refresh_token 이 있을 때만, `??=` 로 **동시 갱신을 하나로 합친다**(여러 401 이 동시에 나도 refresh 1회) — 좋은 패턴. 성공·실패 후 `finally` 로 플래그 해제.
**짚어볼 점 / Watch-outs**
- L49 `expires_at` 은 계산하지만 코드 어디서도 **선제 갱신에 쓰지 않는다** — 토큰이 만료되면 401 → `api.ts` 의 refresh 재시도로만 처리한다(반응형). 만료 임박 시 미리 갱신하면 사용자 체감이 낫다.

```tsx path=web/src/lib/auth.tsx lines=62-96
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
```
**분석 / Analysis (Level 2)**
- L63-72 `startLogin`: verifier·state 생성 → PENDING 에 저장 → `/idp/authorize` 로 리다이렉트. idpHint 로 외부 IdP 바로 위임(브로커).
- L74-85 `completeLogin`: PENDING 복원 → **state 일치 확인(CSRF 방지)** → 코드 교환(code_verifier 로 PKCE 완성) → 저장.
- L87-93 `homeFor`: 역할별 기본 화면.
- L95-96 `can`: 권한 확인(`*` 또는 포함).
**짚어볼 점 / Watch-outs**
- L75 `JSON.parse(sessionStorage.getItem(PENDING) || 'null')` 는 try/catch 없음 — PENDING 이 손상되면 예외가 던져진다(로그인 콜백에서 오류). `load()` 처럼 감싸는 편이 안전.
- L77 state 불일치 검사로 CSRF·혼동 공격 방지(좋음). nonce 검증(id_token)은 서버·여기 모두 없음 — PKCE + state 로 충분하다고 본 설계.
- L95-96 `can` 은 UI 게이팅일 뿐 — 실제 권한은 서버가 강제한다.

```tsx path=web/src/lib/auth.tsx lines=98-116
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

```
**분석 / Analysis (Level 2)**
- L98-99 컨텍스트 타입·기본값.
- L104-115 `AuthProvider`·`logout`: 로그아웃은 로컬 토큰 삭제 + **서버 refresh 폐기**(`/idp/revoke`) — CLI 와 달리 서버 측 세션도 끝낸다(좋음).
**짚어볼 점 / Watch-outs**
- L111-114 `current = null` 후 `rt` 로 revoke — 로컬 먼저 지우고 서버 폐기는 실패해도 무시(사용자 경험 우선, 서버 토큰은 만료까지 유효할 수 있음).

```tsx path=web/src/lib/auth.tsx lines=117-161
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
```
**분석 / Analysis (Level 2)**
- L117-127 `reload`: `/api/v1/me` 로 사용자 로드, 실패면 `me=null`. silent 모드(재로그인)는 로딩 화면을 바꾸지 않아 입력 보존.
- L129-141: `configureApi` 로 api.ts 에 토큰 getter·refresh·만료 콜백 주입. 만료 시 로그인 화면이 아니면 `expired=true`(재로그인 모달).
- L143-152: **postMessage 리스너** — origin 일치 + 타입 확인 후 팝업이 보낸 토큰 수용, 재조회.
- L154-156 `relogin`: `/login?popup=1` 팝업.
**짚어볼 점 / Watch-outs**
- L145 `e.origin !== location.origin` 검사로 **다른 출처의 postMessage 를 거부**(토큰 주입 방지 — 중요한 보안 검사, 올바름). 타입·토큰 존재도 확인.
- L146 `store(e.data.tokens)` — origin 은 검증하지만 토큰 **내용**은 신뢰한다. 같은 origin 의 다른 창(팝업)만 보낼 수 있으므로 신뢰 범위 안이다. `relayTokens`(L166)가 보내는 형식과 결합.
- L129·L143 두 effect 모두 `[reload]` 의존 — `reload` 가 `useCallback([])` 이라 안정적(재등록 없음).

```tsx path=web/src/lib/auth.tsx lines=163-168
export const useAuth = () => useContext(AuthCtx)
export const RELOGIN_MSG = RELOGIN
/** 재로그인 팝업 → 원래 창으로 넘길 토큰 (ST-11) */
export const relayTokens = () => current && { access_token: current.access_token, refresh_token: current.refresh_token,
  expires_in: Math.max(60, Math.round((current.expires_at - Date.now()) / 1000)) }
export const _test = { store, setCurrent: (t: Tokens | null) => (current = t) }
```
**분석 / Analysis (Level 2)**
- L163-164 `useAuth`·`RELOGIN_MSG`.
- L166-167 `relayTokens`: 팝업이 원래 창으로 넘길 토큰(만료 최소 60초).
- L168 `_test`: 시험용 내부 접근(store·setCurrent).
**짚어볼 점 / Watch-outs**
- L168 `_test` export 가 프로덕션 번들에도 포함된다 — 콘솔에서 `_test.setCurrent(...)` 로 토큰을 주입할 수 있다(이미 XSS 가 있으면 어차피 가능하므로 추가 위험은 작지만, 개발 전용 export 는 빌드에서 제외하는 편이 깔끔하다).

## 파일 평가 / File Assessment
- 척도: (1) PKCE + state CSRF 방지, postMessage origin 검증, refresh 단일 비행, sessionStorage 접근 try/catch 등 인증 보안 기본이 탄탄하다. (2) 로그아웃이 서버 refresh 폐기까지. (3) 재로그인 입력 보존 설계.
- 위험: (1) [낮음] sessionStorage 토큰의 XSS 노출(설계 trade-off). (2) [낮음] `completeLogin` 의 PENDING 파싱 미보호, 선제 갱신 미사용, `_test` 프로덕션 노출.
- 개선 권고(영향순): (1) 엄격한 CSP 로 XSS 억제(토큰 저장 방식 전제). (2) PENDING 파싱 try/catch, 만료 임박 선제 refresh. (3) `_test` 를 `import.meta.env.DEV` 가드.
- 재읽기 권고 레벨: 3.
