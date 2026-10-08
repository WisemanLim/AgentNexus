import { expect, test, type Page } from '@playwright/test'

// SCR-C08 — MCP 서버의 샘플링·추가 입력 요청을 채팅에서 동의 (데모 프로파일 demo-orders · 시드 계정 local/test 전용)
async function login(page: Page, username: string, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

const errors: string[] = []
test.beforeEach(({ page }) => {
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  page.on('console', (m) => m.type() === 'error' && !/status of 40[034]/.test(m.text()) && errors.push(`console: ${m.text()}`))
})
test.afterEach(async ({ page }, info) => {
  await page.screenshot({ path: `results/${info.title.replace(/[^\w가-힣-]+/g, '_')}.png`, fullPage: true })
})

test('E14-01 샘플링 동의(검토 후 전송) → 추가 입력 → 실행 완료 (C08 ⑤~⑧)', async ({ page }) => {
  await login(page, 'user', '/chat?profile=demo-orders')
  await page.getByRole('textbox', { name: '요청 입력' }).fill('오늘 주문 이상 건 분류하고 O-2404 반품 접수해줘')
  await page.keyboard.press('Enter')
  const card = page.getByText('샘플링 요청 — 동의 필요').locator('xpath=ancestor::section[1]')
  await expect(card).toBeVisible({ timeout: 30_000 })
  await expect(card.getByText('mcp/internal-orders 가 모델 호출(샘플링)을 요청합니다', { exact: false })).toBeVisible()
  await expect(card.getByText('domain-local')).toBeVisible()             // 내부 데이터 → 로컬 모델 (게이트웨이 결정)
  await expect(card.getByRole('button', { name: '편집 후 허용' })).toBeDisabled()
  await card.getByRole('button', { name: '허용', exact: true }).click()
  const review = page.getByText('샘플링 응답 검토').locator('xpath=ancestor::section[1]')
  await expect(review.getByRole('textbox', { name: '샘플링 응답 초안' })).toHaveValue(/\[domain-7b\]/, { timeout: 30_000 })
  await review.getByRole('textbox', { name: '샘플링 응답 초안' }).fill('O-2403: 이상 — 동일 카드 다건 주문 (검토자 확인)')
  await review.getByRole('button', { name: '서버로 보내기' }).click()
  const ask = page.getByText('추가 입력 요청').locator('xpath=ancestor::section[1]')
  await expect(ask.getByText('O-2404 반품 사유와 수량을 입력하세요')).toBeVisible({ timeout: 30_000 })
  await ask.getByLabel('reason').selectOption('파손')
  await ask.getByLabel('quantity').fill('2')
  await ask.getByRole('button', { name: '보내기' }).click()
  await expect(page.getByText(/trace .* · succeeded/)).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText('검토자 확인', { exact: false }).first()).toBeVisible()   // 검토·수정한 응답이 근거로 반영
})

test('E14-02 `/` 메뉴 — 리소스 첨부·프롬프트 인자 삽입 (C08 ①~④)', async ({ page }) => {
  await login(page, 'user', '/chat?profile=demo-orders')
  const input = page.getByRole('textbox', { name: '요청 입력' })
  await input.fill('/')
  const menu = page.getByRole('dialog', { name: 'MCP 리소스·프롬프트' })
  await expect(menu).toBeVisible()
  await menu.getByRole('button', { name: /orders:\/\/daily\/2026-09-30/ }).click()
  await expect(page.getByRole('button', { name: 'orders://daily/2026-09-30 제거' })).toBeVisible()   // 첨부 배지
  await page.getByRole('button', { name: '/ 리소스·프롬프트' }).click()
  await menu.getByRole('tab', { name: /프롬프트/ }).click()
  await menu.getByRole('button', { name: /anomaly_triage/ }).click()
  await expect(menu.getByRole('button', { name: '삽입' })).toBeDisabled()                 // 필수 인자 (MSG-003)
  await menu.getByLabel('인자 date').fill('2026-10-01')
  await menu.getByRole('button', { name: '삽입' }).click()
  await expect(input).toHaveValue(/^2026-10-01 주문 중 이상 건/)
})

test('E14-03 콘솔 런타임 오류 없음', async () => {
  expect(errors).toEqual([])
})
