> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../../INDEX.md) · 디렉터리 개요: [lib_Abstract.md](lib_Abstract.md)

### 37. web/src/lib/api.ts
**파일 요약 / File Summary**
- 역할 / Role: 관리 REST 클라이언트 — 토큰 주입, 오류 정규화(ApiError), 401 시 1회 갏신 후 재시도·재로그인 트리거, URL 쿼리 빌더, JSON/form/multipart 본문, 편의 메서드(get·post·put·patch·del).
- 진입점 거리 / Distance: 1 (`web/src/lib/auth.tsx:3`, 모든 페이지가 간접 사용)
- LOC: 78
- 주요 export: `Json`, `ApiError`, `configureApi`, `Opts`, `buildUrl`, `request`, `toError`, `api`, `get`, `post`, `put`, `patch`, `del`
- 의존(내부): 없음(auth.tsx 가 `configureApi` 로 훅 주입)
- 외부 패키지: 브라우저 `fetch`·`URLSearchParams`·`FormData`
- 부수효과 / Side effects: 네트워크(fetch), 모듈 전역 `hooks`
- 동시성 / Concurrency: async
- 테스트 커버리지 흔적: 직접 단위 시험 없음(`web/src/test/components.test.tsx` 등이 간접 사용)

## 라인 리뷰 / Line Walk (Level 2)

```ts path=web/src/lib/api.ts lines=1-29
// 관리 REST 클라이언트 — 토큰 주입·오류 정규화(ST-03)·401 시 1회 갱신 후 재로그인(ST-11)
export type Json = Record<string, any>

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public data: Json = {},
    public traceId?: string | null,
  ) {
    super(message)
  }
}

type Hooks = { token: () => string | null; refresh: () => Promise<boolean>; expired: () => void }
const hooks: Hooks = { token: () => null, refresh: async () => false, expired: () => {} }
export function configureApi(h: Partial<Hooks>) {
  Object.assign(hooks, h)
}

export type Opts = { method?: string; body?: unknown; query?: Record<string, unknown>; headers?: Record<string, string>; signal?: AbortSignal; form?: Record<string, string> }

export function buildUrl(path: string, query?: Record<string, unknown>) {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(query || {})) if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  const s = qs.toString()
  return s ? `${path}${path.includes('?') ? '&' : '?'}${s}` : path
}
```
**분석 / Analysis (Level 2)**
- L4-14 `ApiError`: 상태·코드·메시지·데이터·traceId 를 담는 오류 — 화면이 traceId 로 서버 로그와 연결할 수 있다.
- L16-20 `hooks`·`configureApi`: 토큰 getter·refresh·만료 콜백을 auth.tsx 가 주입(의존성 역전 — api.ts 는 인증 구현을 모른다). 기본값은 no-op.
- L24-29 `buildUrl`: 쿼리 직렬화 — undefined·null·빈 문자열은 제외, 이미 `?` 가 있으면 `&`.
**짚어볼 점 / Watch-outs**
- L26 빈 문자열 쿼리를 제외한다 — 의도적으로 "비우면 전체" 시맨틱(서버와 맞춤). 단 `q=''` 로 빈 검색을 명시할 수 없다.

```ts path=web/src/lib/api.ts lines=31-52
export async function request(path: string, o: Opts = {}, retried = false): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json', ...(o.headers || {}) }
  const tok = o.headers?.Authorization ? null : hooks.token() // 호출자가 준 토큰(예: 첫 실행 설정 토큰)은 덮어쓰지 않는다
  if (tok) headers.Authorization = `Bearer ${tok}`
  let body: BodyInit | undefined
  if (o.form) {
    body = new URLSearchParams(o.form)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'
  } else if (o.body instanceof FormData) {
    body = o.body // multipart — 브라우저가 boundary 와 Content-Type 을 붙인다
  } else if (o.body !== undefined) {
    body = JSON.stringify(o.body)
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(buildUrl(path, o.query), { method: o.method || (body ? 'POST' : 'GET'), headers, body, signal: o.signal })
  if (res.status === 401 && tok && !retried) {
    if (await hooks.refresh()) return request(path, o, true)
    hooks.expired()
  }
  if (!res.ok) throw await toError(res)
  return res
}
```
**분석 / Analysis (Level 2)**
- L31-52 `request`: 헤더에 Accept·토큰(호출자가 Authorization 을 주지 않았을 때만 — 첫 실행 설정 토큰 보존, L33 주석), 본문 종류별 처리(form·FormData·JSON), 401 + 토큰 있음 + 미재시도면 **refresh 후 1회 재시도**, 실패면 만료 콜백, 응답 실패는 ApiError.
**짚어볼 점 / Watch-outs**
- L33 `o.headers?.Authorization ? null : hooks.token()` — 호출자가 명시 토큰을 주면 전역 토큰을 덮지 않는다(첫 실행 설정 토큰 흐름). 영리하지만 미묘하다.
- L46-49 재시도는 `retried` 플래그로 1회만 — 무한 루프 방지(좋음). refresh 성공 시 같은 요청 재시도, 실패 시 `expired()`(재로그인 모달).
- [위험:낮음] L39-40 `FormData` 면 Content-Type 을 설정하지 않아 브라우저가 boundary 를 붙인다(올바름). 단 `o.headers` 에 Content-Type 이 있으면 L32 에서 병합돼 multipart 가 깨질 수 있다(호출자 책임).

```ts path=web/src/lib/api.ts lines=54-78
export async function toError(res: Response): Promise<ApiError> {
  const trace = res.headers.get('X-AgentNexus-Trace-Id')
  let j: Json = {}
  try {
    j = await res.json()
  } catch {
    /* 본문 없음 */
  }
  const e = j.error && typeof j.error === 'object' ? j.error : j
  const msg = e.message || (typeof j.detail === 'string' ? j.detail : '') || j.error_description || res.statusText || `HTTP ${res.status}`
  return new ApiError(res.status, e.code || j.error || `http_${res.status}`, msg, e, trace)
}

export async function api<T = Json>(path: string, o: Opts = {}): Promise<T> {
  const res = await request(path, o)
  if (res.status === 204) return {} as T
  const text = await res.text()
  return (text ? JSON.parse(text) : {}) as T
}

export const get = <T = Json>(path: string, query?: Record<string, unknown>) => api<T>(path, { query })
export const post = <T = Json>(path: string, body: unknown = {}) => api<T>(path, { method: 'POST', body })
export const put = <T = Json>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body })
export const patch = <T = Json>(path: string, body: unknown) => api<T>(path, { method: 'PATCH', body })
export const del = <T = Json>(path: string) => api<T>(path, { method: 'DELETE' })
```
**분석 / Analysis (Level 2)**
- L54-65 `toError`: 응답 → ApiError — trace id 헤더, 본문 JSON(없어도 무시), problem+json(`error` 객체)·OpenAI(`detail`)·`error_description` 등 여러 형식에서 메시지 추출.
- L67-72 `api`: 요청 → 204 면 빈 객체, 아니면 텍스트 → JSON 파싱.
- L74-78: 편의 메서드.
**짚어볼 점 / Watch-outs**
- L71 `text ? JSON.parse(text) : {}` — 본문이 JSON 이 아니면(예: 비정형 500 text/plain) `JSON.parse` 예외가 호출부로 전파된다. `api` 를 쓰는 화면은 try/catch 로 ApiError 만 기대하는데, 이 경우 SyntaxError 가 샐 수 있다(`app/core/errors.py` 의 일반 500 처리 부재와 연결).
- L55 `X-AgentNexus-Trace-Id` 헤더 노출 — CORS 에서 이 헤더가 `Access-Control-Expose-Headers` 에 있어야 교차 출처에서 읽힌다(동일 출처 SPA 라 무방).

## 파일 평가 / File Assessment
- 강점: (1) 인증 구현을 훅으로 분리(테스트·교체 용이). (2) 401 단일 재시도·만료 콜백으로 토큰 갱신 흐름이 한 곳에. (3) 다양한 오류 형식을 ApiError 로 정규화 + traceId.
- 위험: (1) [낮음] 비 JSON 본문에서 `JSON.parse` 예외가 ApiError 를 우회. (2) [낮음] 명시 Content-Type 과 FormData 충돌 가능.
- 개선 권고(영향순): (1) L71 파싱을 try/catch 로 감싸 ApiError 로 변환. (2) multipart 시 Content-Type 헤더 무시.
- 재읽기 권고 레벨: 4 — 작고 안정적.
