> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [src_Abstract.md](src_Abstract.md)

### 08. web/src/App.tsx
**파일 요약 / File Summary**
- 역할 / Role: 콘솔·채팅 라우팅 표. 60여 화면을 `React.lazy` 로 코드 분할하고, `/console/*`·`/chat/*` 는 `RequireAuth` 로 보호. 세션 만료 재로그인 모달을 항상 렌더.
- 진입점 거리 / Distance: 0 (`main.tsx` 가 렌더하는 루트 컴포넌트)
- LOC: 179
- 주요 export: `App`
- 의존(내부): `@/components/{Relogin,Shell,ui}`, `@/lib/auth`(useAuth·homeFor), `@/pages/**`(지연 로딩)
- 외부 패키지: react(lazy·Suspense), react-router(Routes·Route·Navigate·useLocation·useParams)
- 부수효과 / Side effects: 없음(라우팅 선언), 지연 청크 네트워크 로드
- 동시성 / Concurrency: none
- 테스트 커버리지 흔적: `web/e2e/screens.spec.ts`·`a11y.spec.ts`(화면 순회), 단위 시험 없음

## 라인 리뷰 / Line Walk (Level 2)

```tsx path=web/src/App.tsx lines=1-7
import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useParams } from 'react-router'
import { ReloginModal } from '@/components/Relogin'
import { Shell } from '@/components/Shell'
import { ErrorBox, Loading } from '@/components/ui'
import { homeFor, useAuth } from '@/lib/auth'
import { Login, LoginCallback } from '@/pages/Login'
```
**분석 / Analysis (Level 2)**
- L1: `lazy`·`Suspense` 로 화면별 번들 분할 — 초기 로딩 크기를 줄인다(vite `chunkSizeWarningLimit: 2000`, `web/vite.config.ts`).
- L3-7: 항상 필요한 컴포넌트(셸·로딩·로그인)는 정적 import — 로그인 화면은 지연 로딩하지 않아 첫 진입이 빠르다.

```tsx path=web/src/App.tsx lines=9-10
const page = <T extends Record<string, any>>(load: () => Promise<T>, name: keyof T) =>
  lazy(() => load().then((m) => ({ default: m[name] as any })))
```
**분석 / Analysis (Level 2)**
- L9-10 `page()`: 이름 있는 export(named export)를 `lazy` 가 요구하는 `{ default }` 모양으로 바꾸는 헬퍼. `React.lazy` 는 default export 만 받기 때문에 필요한 어댑터다.
**짚어볼 점 / Watch-outs**
- `as any` 두 번으로 타입 검사를 끈다 — `name` 이 모듈에 없는 이름이어도 컴파일된다(아래 L58-68 의 `n as never` 와 결합하면 오타가 런타임에만 드러난다).

```tsx path=web/src/App.tsx lines=12-57
const Overview = page(() => import('@/pages/Overview'), 'Overview')
const System = page(() => import('@/pages/System'), 'System')
const ApiExplorer = page(() => import('@/pages/System'), 'ApiExplorer')
const Me = page(() => import('@/pages/Me'), 'Me')
const Setup = page(() => import('@/pages/Setup'), 'Setup')
const ChatLayout = page(() => import('@/pages/chat/Chat'), 'ChatLayout')
const ChatHome = page(() => import('@/pages/chat/Chat'), 'ChatHome')
const ChatSession = page(() => import('@/pages/chat/Chat'), 'ChatSession')
const Agents = page(() => import('@/pages/agents/Agents'), 'Agents')
const AgentGraph = page(() => import('@/pages/agents/AgentGraph'), 'AgentGraph')
const AgentCode = page(() => import('@/pages/agents/Agents'), 'AgentCode')
const AgentTest = page(() => import('@/pages/agents/Agents'), 'AgentTest')
const HostSessions = page(() => import('@/pages/agents/Agents'), 'HostSessions')
const Catalog = page(() => import('@/pages/catalog/Catalog'), 'Catalog')
const CatalogItem = page(() => import('@/pages/catalog/Catalog'), 'CatalogItem')
const RegistryApprovals = page(() => import('@/pages/catalog/Catalog'), 'RegistryApprovals')
const McpServers = page(() => import('@/pages/mcp/Mcp'), 'McpServers')
const McpServer = page(() => import('@/pages/mcp/Mcp'), 'McpServer')
const Inspector = page(() => import('@/pages/mcp/Mcp'), 'Inspector')
const A2AAgents = page(() => import('@/pages/a2a/A2A'), 'A2AAgents')
const A2AAgent = page(() => import('@/pages/a2a/A2A'), 'A2AAgent')
const A2ATasks = page(() => import('@/pages/a2a/A2A'), 'A2ATasks')
const A2ATask = page(() => import('@/pages/a2a/A2A'), 'A2ATask')
const Providers = page(() => import('@/pages/models/ModelsMore'), 'Providers')
const Budgets = page(() => import('@/pages/observe/ObserveMore'), 'Budgets')
const Slos = page(() => import('@/pages/observe/ObserveMore'), 'Slos')
const Replay = page(() => import('@/pages/observe/ObserveMore'), 'Replay')
const ReportWizard = page(() => import('@/pages/observe/ObserveMore'), 'ReportWizard')
const Rbac = page(() => import('@/pages/admin/Admin'), 'Rbac')
const GatewayOverview = page(() => import('@/pages/gateway/Gateway'), 'GatewayOverview')
const RoutingLog = page(() => import('@/pages/gateway/Gateway'), 'RoutingLog')
const Policies = page(() => import('@/pages/gateway/Gateway'), 'Policies')
const PolicyEditor = page(() => import('@/pages/gateway/Gateway'), 'PolicyEditor')
const Limits = page(() => import('@/pages/gateway/Gateway'), 'Limits')
const Approvals = page(() => import('@/pages/gateway/Gateway'), 'Approvals')
const Models = page(() => import('@/pages/models/Models'), 'Models')
const Playground = page(() => import('@/pages/models/Models'), 'Playground')
const Users = page(() => import('@/pages/identity/Identity'), 'Users')
const Propagation = page(() => import('@/pages/identity/Identity'), 'Propagation')
const Traces = page(() => import('@/pages/observe/Observe'), 'Traces')
const Trace = page(() => import('@/pages/observe/Observe'), 'Trace')
const Logs = page(() => import('@/pages/observe/Observe'), 'Logs')
const Cost = page(() => import('@/pages/observe/Observe'), 'Cost')
const Audit = page(() => import('@/pages/observe/Observe'), 'Audit')
const Admin = page(() => import('@/pages/admin/Admin'), 'Admin')
const ChatHistory = page(() => import('@/pages/chat/Delegation'), 'ChatHistory')
```
**분석 / Analysis (Level 2)**
- L12-57: 화면별 지연 컴포넌트 선언. 같은 모듈(예: `@/pages/System`)을 여러 번 `import()` 해도 번들러·브라우저가 모듈을 한 번만 로드·캐시한다(중복 다운로드 없음).

```tsx path=web/src/App.tsx lines=58-70
const G = (n: string) => page(() => import('@/pages/gateway/GatewayMore'), n as never)
const RoutingTools = G('RoutingTools'), Security = G('Security'), Secrets = G('Secrets'), Governance = G('Governance')
const SanitizeRules = G('SanitizeRules'), ModelApi = G('ModelApi'), McpProxy = G('McpProxy'), A2AProxy = G('A2AProxy'), RoutingModels = G('RoutingModels')
const IdM = (n: string) => page(() => import('@/pages/identity/IdentityMore'), n as never)
const Idps = IdM('Idps'), IdpWizard = IdM('IdpWizard'), TokenExchange = IdM('TokenExchange'), McpAuth = IdM('McpAuth'), Workloads = IdM('Workloads')
const ModelWizard = page(() => import('@/pages/models/ModelsMore'), 'ModelWizard')
const AccessTokens = page(() => import('@/pages/models/ModelsMore'), 'AccessTokens')
const Rg = (n: string) => page(() => import('@/pages/catalog/RegistryMore'), n as never)
const Publish = Rg('Publish'), Sources = Rg('Sources'), Sbom = Rg('Sbom')
const Mm = (n: string) => page(() => import('@/pages/mcp/McpMore'), n as never)
const SaasConfig = Mm('SaasConfig'), OpenApiWizard = Mm('OpenApiWizard'), CustomTemplate = Mm('CustomTemplate')
const Pipeline = page(() => import('@/pages/observe/ObserveMore'), 'Pipeline')
const Metrics = page(() => import('@/pages/observe/ObserveMore'), 'Metrics')
```
**분석 / Analysis (Level 2)**
- L58-68: 같은 모듈의 여러 화면을 짧게 선언하는 팩토리(`G`·`IdM`·`Rg`·`Mm`). 이름 인자를 `n as never` 로 캐스팅해 `keyof T` 제약을 우회한다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] `as never` 캐스팅 때문에 `G('Secrts')` 같은 오타가 타입 오류 없이 빌드되고, 해당 화면 진입 시 `m[name]` 이 `undefined` → React 가 "Element type is invalid" 로 렌더 실패한다. `G = <K extends keyof typeof import('@/pages/gateway/GatewayMore')>(n: K)` 처럼 제네릭으로 묶거나 L12-57 방식으로 통일 권장.

```tsx path=web/src/App.tsx lines=72-91
function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading, authed } = useAuth()
  const loc = useLocation()
  if (loading) return <div className="p-8"><Loading /></div>
  if (!authed || !me) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />
  return <>{children}</>
}

function Home() {
  const { me, loading } = useAuth()
  if (loading) return null
  return <Navigate to={me ? homeFor(me) : '/login'} replace />
}

function AgentRedirect() {
  const { id } = useParams()
  return <Navigate to={`/console/agents/${id}/graph`} replace />
}

const S = ({ children }: { children: ReactNode }) => <Suspense fallback={<Loading />}>{children}</Suspense>
```
**분석 / Analysis (Level 2)**
- L72-78 `RequireAuth`: 인증 확인 중이면 로딩, 미인증이면 현재 경로를 `next` 쿼리로 담아 `/login` 으로 `replace` 이동(뒤로 가기 시 보호 화면으로 돌아오지 않음). `encodeURIComponent` 로 쿼리 인젝션 방지.
- L80-84 `Home`: 루트·알 수 없는 경로 → 역할별 홈(`homeFor`, `web/src/lib/auth.tsx:87-93`) 또는 로그인.
- L86-89: 구 경로 `agents/:id` → `agents/:id/graph` 리다이렉트.
- L91 `S`: Suspense 래퍼 — 각 화면이 자기 로딩 상태를 갖게 한다.
**짚어볼 점 / Watch-outs**
- L76 `authed || me` 조건: `authed` 는 토큰 존재 또는 만료 상태(`web/src/lib/auth.tsx:158`)라 **만료 모달 상태에서도 화면을 유지**한다(입력 보존, ST-11 설계). 대신 `me` 가 없으면 로그인으로 보낸다.
- 클라이언트 측 보호는 UX 일 뿐 — 실제 권한은 API 가 강제한다(서버 `require(...)`). 메뉴 비활성 처리(`Shell.tsx`)도 마찬가지.

```tsx path=web/src/App.tsx lines=93-179
export function App() {
  return (
    <>
    <ReloginModal />
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/login/callback" element={<LoginCallback />} />
      <Route path="/setup/*" element={<S><Setup /></S>} />
      <Route path="/chat" element={<RequireAuth><S><ChatLayout /></S></RequireAuth>}>
        <Route index element={<S><ChatHome /></S>} />
        <Route path="history" element={<S><ChatHistory /></S>} />
        <Route path=":sid" element={<S><ChatSession /></S>} />
      </Route>
      <Route path="/console" element={<RequireAuth><Shell /></RequireAuth>}>
        <Route index element={<S><Overview /></S>} />
        <Route path="system" element={<S><System /></S>} />
        <Route path="me" element={<S><Me /></S>} />
        <Route path="api" element={<S><ApiExplorer /></S>} />
        <Route path="agents" element={<S><Agents /></S>} />
        <Route path="agents/:id" element={<AgentRedirect />} />
        <Route path="agents/:id/graph" element={<S><AgentGraph /></S>} />
        <Route path="agents/:id/code" element={<S><AgentCode /></S>} />
        <Route path="agents/:id/test" element={<S><AgentTest /></S>} />
        <Route path="host/sessions" element={<S><HostSessions /></S>} />
        <Route path="catalog" element={<S><Catalog /></S>} />
        <Route path="catalog/:id" element={<S><CatalogItem /></S>} />
        <Route path="registry/approvals" element={<S><RegistryApprovals /></S>} />
        <Route path="mcp-servers" element={<S><McpServers /></S>} />
        <Route path="mcp-servers/:id" element={<S><McpServer /></S>} />
        <Route path="mcp-servers/:id/inspect" element={<S><Inspector /></S>} />
        <Route path="a2a" element={<S><A2AAgents /></S>} />
        <Route path="a2a/tasks" element={<S><A2ATasks /></S>} />
        <Route path="a2a/tasks/:id" element={<S><A2ATask /></S>} />
        <Route path="a2a/:id" element={<S><A2AAgent /></S>} />
        <Route path="gateway" element={<S><GatewayOverview /></S>} />
        <Route path="gateway/routing/log" element={<S><RoutingLog /></S>} />
        <Route path="gateway/policies" element={<S><Policies /></S>} />
        <Route path="gateway/policies/:id" element={<S><PolicyEditor /></S>} />
        <Route path="gateway/limits" element={<S><Limits /></S>} />
        <Route path="gateway/approvals" element={<S><Approvals /></S>} />
        <Route path="models" element={<S><Models /></S>} />
        <Route path="models/playground" element={<S><Playground /></S>} />
        <Route path="identity/users" element={<S><Users /></S>} />
        <Route path="identity/propagation" element={<S><Propagation /></S>} />
        <Route path="observe/traces" element={<S><Traces /></S>} />
        <Route path="observe/traces/:id" element={<S><Trace /></S>} />
        <Route path="observe/logs" element={<S><Logs /></S>} />
        <Route path="observe/cost" element={<S><Cost /></S>} />
        <Route path="audit" element={<S><Audit /></S>} />
        <Route path="admin" element={<S><Admin /></S>} />
        <Route path="gateway/routing/tools" element={<S><RoutingTools /></S>} />
        <Route path="gateway/routing/models" element={<S><RoutingModels /></S>} />
        <Route path="gateway/security" element={<S><Security /></S>} />
        <Route path="gateway/secrets" element={<S><Secrets /></S>} />
        <Route path="gateway/governance" element={<S><Governance /></S>} />
        <Route path="gateway/governance/rules" element={<S><SanitizeRules /></S>} />
        <Route path="gateway/model-api" element={<S><ModelApi /></S>} />
        <Route path="gateway/mcp" element={<S><McpProxy /></S>} />
        <Route path="gateway/a2a" element={<S><A2AProxy /></S>} />
        <Route path="models/new" element={<S><ModelWizard /></S>} />
        <Route path="models/tokens" element={<S><AccessTokens /></S>} />
        <Route path="identity/idp" element={<S><Idps /></S>} />
        <Route path="identity/idp/new" element={<S><IdpWizard /></S>} />
        <Route path="identity/token-exchange" element={<S><TokenExchange /></S>} />
        <Route path="identity/mcp-auth" element={<S><McpAuth /></S>} />
        <Route path="identity/workloads" element={<S><Workloads /></S>} />
        <Route path="registry/publish" element={<S><Publish /></S>} />
        <Route path="registry/sources" element={<S><Sources /></S>} />
        <Route path="catalog/sbom" element={<S><Sbom /></S>} />
        <Route path="mcp-servers/:id/config" element={<S><SaasConfig /></S>} />
        <Route path="mcp-servers/new/openapi" element={<S><OpenApiWizard /></S>} />
        <Route path="mcp-servers/new/custom" element={<S><CustomTemplate /></S>} />
        <Route path="observe/pipeline" element={<S><Pipeline /></S>} />
        <Route path="observe/metrics" element={<S><Metrics /></S>} />
        <Route path="models/providers" element={<S><Providers /></S>} />
        <Route path="observe/budgets" element={<S><Budgets /></S>} />
        <Route path="observe/slo" element={<S><Slos /></S>} />
        <Route path="observe/replay/:sid" element={<S><Replay /></S>} />
        <Route path="audit/reports/new" element={<S><ReportWizard /></S>} />
        <Route path="admin/rbac" element={<S><Rbac /></S>} />
        <Route path="*" element={<ErrorBox error={{ status: 404, message: '화면이 없습니다. 나머지 기능은 REST API·nexus CLI 로 제공됩니다 (/console/api).' }} />} />
      </Route>
      <Route path="*" element={<Home />} />
    </Routes>
    </>
  )
}
```
**분석 / Analysis (Level 2)**
- L96 `<ReloginModal/>`: 라우트 밖에서 항상 렌더 — 어느 화면에서든 세션 만료 시 같은 모달.
- L98-100: `/login`·`/login/callback`·`/setup/*` 은 인증 없이 접근(첫 실행 마법사는 일회용 코드로 보호 — 서버 측).
- L101-105: `/chat` 레이아웃 + 하위(index·history·:sid) — 중첩 라우트는 `ChatLayout` 의 `<Outlet/>` 에 렌더된다.
- L106-174: `/console` 은 `Shell`(좌 내비·상단 바) 아래 화면들. 경로 순서: `a2a/tasks` 가 `a2a/:id` 보다 먼저 선언되어 있다 — React Router v6+ 는 순서가 아니라 **구체성 점수**로 매칭하므로 순서와 무관하게 정적 세그먼트가 우선한다(읽는 사람 혼동 방지를 위해 언급).
- L162-164: `mcp-servers/:id/config` 와 `mcp-servers/new/openapi` — `new` 가 `:id` 로 오인되지 않는 것도 구체성 규칙 덕분.
- L173: 콘솔 하위의 알 수 없는 경로는 404 박스(서버 API·CLI 안내).
- L175: 그 밖의 모든 경로 → `Home`(역할별 홈).
**짚어볼 점 / Watch-outs**
- 서버(`app/main.py:170-172`)는 `/console`·`/chat`·`/login`·`/setup` 프리픽스만 index.html 로 폴백한다 — 여기서 L175 `*` 로 받는 다른 경로(예: `/foo`)를 새로고침하면 서버가 SPA 가 아닌 404 problem+json 을 준다(클라이언트 내 이동에서만 동작).
- 라우트 수가 많아 권한·메뉴(`Shell.tsx`)와 경로 문자열이 두 곳에서 관리된다 — 경로 상수 모듈로 일원화하면 누락을 줄일 수 있다.

## 파일 평가 / File Assessment
- 강점: (1) 화면 단위 코드 분할과 Suspense 경계. (2) 보호 경로 리다이렉트가 `next` 를 보존. (3) 만료 상태에서 화면 유지(입력 보존 UX).
- 위험: (1) `as any`/`as never` 로 lazy export 이름 오타가 런타임에만 드러남. (2) 서버 SPA 폴백 프리픽스와 클라이언트 catch-all 의 불일치.
- 개선 권고(영향순): (1) 팩토리를 제네릭 `keyof` 로 타입 안전하게. (2) 최상위 Error Boundary(청크 로드 실패 처리). (3) 경로 상수 일원화.
- 재읽기 권고 레벨: 3 — 선언적 라우팅 표라 구조만 이해하면 된다.
