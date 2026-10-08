import { expect, test, type Page } from '@playwright/test'

// 시드 계정(local/test 전용, app/seed.py) — 운영 환경에는 존재하지 않는다
async function login(page: Page, username: string, path = '/console') {
  await page.goto(path)
  await expect(page).toHaveURL(/\/login/)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await expect(page).toHaveURL(/\/idp\/authorize/)
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

const errors: string[] = []
test.beforeEach(({ page }) => {
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  page.on('console', (m) => m.type() === 'error' && !/status of 40[34]/.test(m.text()) && errors.push(`console: ${m.text()}`))
})
test.afterEach(async ({ page }, info) => {
  await page.screenshot({ path: `results/${info.title.replace(/[^\w가-힣-]+/g, '_')}.png`, fullPage: true })
})

test('E12-01 운영자 로그인(PKCE) → 운영 개요 KPI·차트 (A10·B02)', async ({ page }) => {
  await login(page, 'operator')
  await expect(page).toHaveURL(/\/console$/)
  await expect(page.getByRole('heading', { name: /운영 개요/ })).toBeVisible()
  await expect(page.getByText('요청 QPS')).toBeVisible()
  await expect(page.getByLabel('프로토콜별 호출 차트').locator('svg').first()).toBeVisible()
  await expect(page.getByRole('navigation', { name: '주 메뉴' }).getByRole('link', { name: /AI 게이트웨이/ })).toBeVisible()
})

test('E12-02 ⌘K 전역 검색 → MCP 도구 → 서버 상세·규칙 (B03·F02)', async ({ page }) => {
  await login(page, 'operator')
  await expect(page.getByRole('heading', { name: /운영 개요/ })).toBeVisible()
  await page.keyboard.press('Meta+k')
  await page.getByPlaceholder(/2자 이상/).fill('jira')
  await page.getByRole('option', { name: /jira\.search_issues/ }).first().click()
  await expect(page).toHaveURL(/\/console\/mcp-servers\/jira$/)
  await expect(page.getByRole('cell', { name: 'search_issues' }).first()).toBeVisible()
  await expect(page.getByLabel('transition_issue 승인 필요')).toBeChecked()
})

test('E12-03 카탈로그·정책 시뮬레이터·그래프 캔버스 (E01·H08·D02)', async ({ page }) => {
  await login(page, 'admin')
  await page.goto('/console/catalog?type=mcp_server')
  await expect(page.getByRole('heading', { name: '통합 카탈로그' })).toBeVisible()
  await expect(page.locator('tbody tr').first()).toBeVisible()
  await page.goto('/console/gateway/policies')
  await page.locator('tbody tr').first().click()
  await page.getByRole('tab', { name: '시뮬레이터' }).click()
  await page.getByRole('button', { name: '평가' }).click()
  await expect(page.getByText(/^사유$/)).toBeVisible()
  await page.goto('/console/agents/tpl-incident/graph')
  await expect(page.locator('.react-flow__node')).toHaveCount(4)
  await page.locator('.react-flow__node').filter({ hasText: 'Planner' }).click()
  await expect(page.getByRole('dialog', { name: 'Planner 노드 설정' })).toBeVisible()
})

test('E12-04 트레이스 목록 → 워터폴 (K02·K03)', async ({ page }) => {
  await login(page, 'operator')
  await page.goto('/console/observe/traces?since=1440')
  await page.locator('tbody tr').first().click()
  await expect(page).toHaveURL(/\/console\/observe\/traces\/[0-9a-f]{32}/)
  await expect(page.getByRole('heading', { name: /워터폴/ })).toBeVisible()
  await expect(page.getByText('전체 지연')).toBeVisible()
})

test('E12-05 최종 사용자 → /chat · 스트리밍 응답·근거 인용 (C01·C02·C03)', async ({ page }) => {
  await login(page, 'user', '/')
  await expect(page).toHaveURL(/\/chat$/)
  await page.getByRole('button', { name: /사내 Q&A/ }).click()
  await page.getByRole('button', { name: /장애 대응 런북/ }).click()
  await page.getByRole('button', { name: /보내기/ }).click()
  await expect(page).toHaveURL(/\/chat\/ses_/)
  const plan = page.getByRole('complementary', { name: '실행 계획' })
  await expect(plan.getByText('완료').first()).toBeVisible()
  const cite = page.getByRole('button', { name: '[1]', exact: true })
  await expect(cite).toBeVisible({ timeout: 60_000 })
  await cite.click()
  await expect(page.getByRole('dialog', { name: /근거 원문/ })).toBeVisible()
})

test('E12-06 고위험 쓰기 도구 승인 모달 → 실행 재개 (C04·FL-02)', async ({ page }) => {
  await login(page, 'user', '/chat?profile=tpl-issue-report')
  await page.getByRole('textbox', { name: '요청 입력' }).fill('ORION 이번 주 이슈 요약해서 주간 보고 페이지로 만들어줘')
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: /검토하기/ }).click({ timeout: 60_000 })
  const modal = page.getByRole('dialog', { name: /도구 실행 승인/ })
  await expect(modal.getByText('confluence.create_page', { exact: true })).toBeVisible()
  await expect(modal.getByText('고위험', { exact: true })).toBeVisible()
  await modal.getByRole('button', { name: '승인하고 실행' }).click()
  await expect(page.getByText(/trace .* · succeeded/)).toBeVisible({ timeout: 60_000 })
})

test('E12-07 권한 없는 메뉴는 비활성 + 사유 (ST-04)', async ({ page }) => {
  await login(page, 'user', '/console/catalog')
  const nav = page.getByRole('navigation', { name: '주 메뉴' })
  const gw = nav.locator('[aria-disabled="true"]', { hasText: 'AI 게이트웨이' })
  await expect(gw).toHaveAttribute('title', /gateway:read/)
  await expect(nav.getByRole('link', { name: '카탈로그', exact: true })).toBeVisible()
})

test('E12-08 콘솔 런타임 오류 없음', async () => {
  expect(errors).toEqual([])
})
