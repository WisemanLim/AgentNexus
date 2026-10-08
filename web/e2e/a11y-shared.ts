import AxeBuilder from '@axe-core/playwright'
import { expect, type Page } from '@playwright/test'

// 접근성 점검 공용 — 화면 목록·axe 스캔 (a11y.spec · a11y-dark.spec)
export const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']

export async function login(page: Page, username: string, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

export const CONSOLE = [
  '/console', '/console/system', '/console/api', '/console/me',
  '/console/agents', '/console/agents/tpl-incident/graph', '/console/agents/tpl-incident/code', '/console/agents/tpl-incident/test', '/console/host/sessions',
  '/console/catalog', '/console/registry/approvals', '/console/registry/publish', '/console/registry/sources', '/console/catalog/sbom',
  '/console/mcp-servers', '/console/mcp-servers/jira', '/console/mcp-servers/jira/inspect', '/console/mcp-servers/jira/config',
  '/console/mcp-servers/new/openapi', '/console/mcp-servers/new/custom',
  '/console/a2a', '/console/a2a/tasks', '/console/a2a/observability-analyst',
  '/console/gateway', '/console/gateway/routing/log', '/console/gateway/policies', '/console/gateway/limits', '/console/gateway/approvals',
  '/console/gateway/routing/models', '/console/gateway/routing/tools', '/console/gateway/security', '/console/gateway/secrets',
  '/console/gateway/governance', '/console/gateway/governance/rules', '/console/gateway/model-api', '/console/gateway/mcp', '/console/gateway/a2a',
  '/console/models', '/console/models/providers', '/console/models/playground', '/console/models/new', '/console/models/tokens',
  '/console/identity/users', '/console/identity/propagation', '/console/identity/idp', '/console/identity/idp/new',
  '/console/identity/token-exchange', '/console/identity/mcp-auth', '/console/identity/workloads',
  '/console/observe/traces', '/console/observe/logs', '/console/observe/cost', '/console/observe/pipeline', '/console/observe/metrics', '/console/observe/slo',
  '/console/observe/budgets', '/console/audit', '/console/audit/reports/new', '/console/admin', '/console/admin/rbac',
]

export type Finding = { route: string; id: string; impact: string; nodes: number; target: string; help: string }

export async function scan(page: Page, route: string, out: Finding[]) {
  await page.goto(route)
  await page.waitForLoadState('networkidle')
  await expect(page.locator('main, [role="main"]').first()).toBeVisible()
  const res = await new AxeBuilder({ page }).withTags(TAGS).analyze()
  for (const v of res.violations) {
    out.push({ route, id: v.id, impact: v.impact ?? '', nodes: v.nodes.length, target: String(v.nodes[0]?.target ?? ''), help: v.help })
  }
}

export function report(out: Finding[]) {
  if (out.length) console.log(out.map((f) => `${f.route} · ${f.id}(${f.impact}) ×${f.nodes} — ${f.target} — ${f.help}`).join('\n'))
}
