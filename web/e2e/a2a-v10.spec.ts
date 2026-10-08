import { expect, test, type Page } from '@playwright/test'

// Epic 33 — A2A 1.0 기본화 (D15-②): 시험 콘솔 기본 1.0(SendMessage) · 0.3 선택 시 message/send · 사이드바 지원 버전 — 시드 계정 local/test 전용
async function login(page: Page, username: string, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

test('E33-01 시험 콘솔 — 기본 A2A 1.0 SendMessage 로 완료 · 0.3 으로 바꾸면 message/send · 사이드바 1.0 · 0.3', async ({ page }) => {
  await login(page, 'dev', '/console/a2a/observability-analyst')
  await expect(page.getByRole('navigation', { name: '주 메뉴' })).toContainText('A2A 1.0 · 0.3')
  await page.getByRole('tab', { name: '시험 콘솔' }).click()
  await expect(page.getByLabel('프로토콜')).toHaveValue('1.0')
  await expect(page.getByText('SendMessage', { exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: 'text Part' }).fill('orders-api 장애 분석')
  await page.getByRole('button', { name: '보내기' }).click()
  await expect(page.locator('pre').filter({ hasText: 'TASK_STATE_COMPLETED' })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('button', { name: 'tasks/cancel' })).toBeDisabled()  // 1.0 상태도 정준형으로 — 끝난 Task 는 취소 불가
  await page.getByLabel('프로토콜').selectOption('0.3')
  await expect(page.getByText('message/send', { exact: true })).toBeVisible()
})
