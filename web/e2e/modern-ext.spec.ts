import { expect, test, type Page } from '@playwright/test'

// Epic 29 — MCP 2026-07-28 확장: 서버 상세의 업스트림 프로토콜(발견·선택) · 적용 전 시험의 프로토콜 점검 — 시드 계정 local/test 전용
async function login(page: Page, username: string, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

test('E29-01 서버 상세 — 업스트림 프로토콜 발견 2026-07-28 · 고정 전환 후 세션 방식 · 자동 복귀', async ({ page }) => {
  await login(page, 'operator', '/console/mcp-servers/dynatrace')
  await page.getByRole('button', { name: '도구 새로고침' }).click()
  const card = page.locator('section').filter({ has: page.getByRole('heading', { name: '업스트림 프로토콜' }) })
  await expect(card).toContainText('2026-07-28', { timeout: 30_000 })
  await expect(card).toContainText('세션 없이 요청마다')
  const select = page.getByLabel('업스트림 프로토콜 선택')
  await select.selectOption('legacy')
  await expect(card).toContainText('initialize · 세션', { timeout: 30_000 })
  await select.selectOption('auto')
  await expect(card).toContainText('세션 없이 요청마다', { timeout: 30_000 })
})

test('E29-02 적용 전 시험 — 프로토콜 점검 항목', async ({ page }) => {
  await login(page, 'operator', '/console/mcp-servers/jira/config')
  await page.getByRole('button', { name: '다음' }).click()
  await page.getByRole('button', { name: '다음' }).click()
  await page.getByRole('button', { name: '적용 전 시험' }).click()
  await expect(page.getByText('프로토콜', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/MCP 2026-07-28 — 세션 없이/)).toBeVisible()
})
