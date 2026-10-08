import { expect, test, type Page } from '@playwright/test'

// Epic 35 — SLO·오류 예산 (D15-④): 기본 SLO 2종 카드 · 예산 잔량·소진율 표 · 목표 수정(사유 필수) · 지금 평가 — 시드 계정 local/test 전용
async function login(page: Page, username: string, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

test('E35-01 SLO 화면 — 기본 SLO · 소진율 표 · 목표 수정은 사유 필수 · 지금 평가', async ({ page }) => {
  await login(page, 'admin', '/console/observe/slo')
  await expect(page).toHaveTitle(/SLO·오류 예산.*AgentNexus/)
  const avail = page.locator('section').filter({ has: page.getByRole('heading', { name: '데이터 경로 가용성 (NFR-A1)' }) })
  await expect(avail).toContainText('목표 99.950%')
  await expect(avail.getByRole('table', { name: '창별 소진율' })).toContainText('1h')
  await avail.getByRole('button', { name: '목표 수정' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('목표 (%)').fill('99.900')
  await dialog.getByRole('button', { name: '저장' }).click()
  await expect(page.getByText('변경 사유를 입력하세요', { exact: false })).toBeVisible()
  await dialog.getByLabel('변경 사유 (필수 · 감사)').fill('E35 e2e')
  await dialog.getByRole('button', { name: '저장' }).click()
  await expect(avail).toContainText('목표 99.900%', { timeout: 10_000 })
  await avail.getByRole('button', { name: '목표 수정' }).click()
  await dialog.getByLabel('목표 (%)').fill('99.950')
  await dialog.getByLabel('변경 사유 (필수 · 감사)').fill('되돌림')
  await dialog.getByRole('button', { name: '저장' }).click()
  await expect(avail).toContainText('목표 99.950%', { timeout: 10_000 })
  await page.getByRole('button', { name: '지금 평가' }).click()
  await expect(page.getByText(/상태 변화 없음|상태 변경/)).toBeVisible()
})
