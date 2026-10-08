import { expect, test, type Page } from '@playwright/test'

// Epic 24 — 남은 설계 요소 (H02 게시·이력 · H13 키별 허용 별칭 · F04 OBO · F06 가상 사용자 · B05 헬스 · G04 푸시 서명) — 시드 계정 local/test 전용
async function login(page: Page, username: string, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

const errors: string[] = []
test.beforeEach(async ({ page }) => { page.on('pageerror', (e) => errors.push(e.message)) })
test.afterEach(async ({ page }, info) => {
  await page.screenshot({ path: `results/${info.title.replace(/[^\w가-힣-]+/g, '_')}.png`, fullPage: true })
})

test('E24-01 라우팅 규칙 버전 게시·이력 (H02 ⑤)', async ({ page }) => {
  await login(page, 'operator', '/console/gateway/routing/models')
  const hist = page.getByLabel('게시 이력')
  await expect(hist).toBeVisible()
  const publish = hist.getByRole('button', { name: '버전 게시' })
  if (await publish.isEnabled()) {
    page.once('dialog', (d) => d.accept('e2e 게시'))
    await publish.click()
    await expect(page.getByText(/게시했습니다/)).toBeVisible()
  }
  await expect(hist.getByRole('cell', { name: /^v\d+/ }).first()).toBeVisible()
})

test('E24-02 클라이언트 키 허용 별칭 (H13 ④)', async ({ page }) => {
  await login(page, 'operator', '/console/gateway/model-api')
  const name = `e2e-key-${Date.now() % 100000}`
  await page.getByLabel('키 이름').fill(name)
  await page.getByLabel('허용 별칭', { exact: true }).selectOption('domain-local')
  await page.getByRole('button', { name: '발급' }).click()
  await expect(page.getByText('지금만 표시됩니다 (MSG-018)')).toBeVisible()
  await expect(page.getByRole('row').filter({ hasText: name })).toContainText('domain-local')
})

test('E24-03 OpenAPI 마법사 OBO 선택 (F04 ③) · Inspector 가상 사용자 (F06 ⑤)', async ({ page }) => {
  await login(page, 'dev', '/console/mcp-servers/jira/inspect?tool=search_issues')
  await page.getByRole('checkbox', { name: '시험 전용 가상 사용자로 호출' }).check()
  await page.getByLabel(/시험 역할/).fill('user')
  await page.getByRole('button', { name: 'tools/call 실행' }).click()
  await expect(page.getByText(/시험 주체 test:usr_dev/)).toBeVisible({ timeout: 30_000 })
  await page.goto('/console/mcp-servers/new/openapi')
  await page.getByRole('button', { name: '다음' }).click()
  const obo = page.getByRole('radio', { name: /사용자 토큰 전파 \(OBO\)/ })
  await obo.check()
  await expect(page.getByLabel(/교환 스코프/)).toHaveValue('api:call')
})

test('E24-04 구성요소 헬스·재시작 (B05 ②) · 푸시 서명 JWKS (G04·G07)', async ({ page }) => {
  await login(page, 'admin', '/console/system')
  await expect(page.getByLabel('구성요소 표')).toContainText('gateway')
  await page.goto('/console/a2a/observability-analyst')
  await page.getByRole('tab', { name: '인증 설정' }).click()
  await expect(page.getByLabel(/푸시 서명 JWKS URL/)).toBeVisible()
  await expect(page.getByLabel(/^프레임워크/)).toBeVisible()
})

test('E24-05 콘솔 런타임 오류 없음', async () => {
  expect(errors).toEqual([])
})
