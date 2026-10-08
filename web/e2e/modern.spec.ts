import { expect, test, type Page } from '@playwright/test'

// Epic 27 — MCP 2026-07-28 dual-era 표시 (Inspector 점검표 · 사이드바) — 시드 계정 local/test 전용
async function login(page: Page, username: string, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

test('E27-01 Inspector 점검표 — MCP 2026-07-28 server/discover 통과 · 사이드바 지원 버전', async ({ page }) => {
  await login(page, 'operator', '/console/mcp-servers/jira/inspect')
  await expect(page.getByRole('navigation', { name: '주 메뉴' })).toContainText('2026-07-28')
  await page.getByRole('button', { name: '점검', exact: true }).click()
  const item = page.getByText('MCP 2026-07-28 (server/discover)')
  await expect(item).toBeVisible({ timeout: 30_000 })
  await expect(item.locator('..')).toContainText('✓')
})
