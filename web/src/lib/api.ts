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
