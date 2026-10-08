import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useParams } from 'react-router'
import { ReloginModal } from '@/components/Relogin'
import { Shell } from '@/components/Shell'
import { ErrorBox, Loading } from '@/components/ui'
import { homeFor, useAuth } from '@/lib/auth'
import { Login, LoginCallback } from '@/pages/Login'

const page = <T extends Record<string, any>>(load: () => Promise<T>, name: keyof T) =>
  lazy(() => load().then((m) => ({ default: m[name] as any })))

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
