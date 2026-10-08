import { expect, test, type Page } from '@playwright/test'

// Epic 18 — 원격 에이전트 추가 입력(C05)·바이너리 첨부(C01)·재계획 표시(C02) (시드 계정 local/test 전용)
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
  page.on('console', (m) => m.type() === 'error' && !/status of 40[034]|status of 502/.test(m.text()) && errors.push(`console: ${m.text()}`))
})
test.afterEach(async ({ page }, info) => {
  await page.screenshot({ path: `results/${info.title.replace(/[^\w가-힣-]+/g, '_')}.png`, fullPage: true })
})

test('E18-01 원격 에이전트가 되물으면 카드 안에서 답하고 같은 Task 로 이어서 완료 (C05 · MSG-017)', async ({ page }) => {
  await login(page, 'user', '/chat?profile=tpl-incident')
  await page.getByRole('textbox', { name: '요청 입력' }).fill('장애 원인 분석해줘')
  await page.keyboard.press('Enter')
  const reply = page.getByRole('textbox', { name: '원격 에이전트에 답하기' })
  await expect(reply).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText(/어떤 서비스의 장애를 분석할까요/).first()).toBeVisible()
  await expect(page.getByRole('complementary', { name: '실행 계획' }).getByText('입력 대기').first()).toBeVisible()
  await reply.fill('orders-api')
  await page.getByRole('button', { name: '답하고 이어서 실행' }).click()
  await expect(page.getByText(/trace .* · succeeded/)).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('[aria-label="Task 상태"]').first()).toContainText('completed')
})

// 최소 PDF — 비압축 스트림의 Tj 텍스트 (서버가 표준 라이브러리로 추출)
const PDF = Buffer.from('%PDF-1.4\n1 0 obj << /Length 44 >>\nstream\nBT /F1 12 Tf 72 712 Td (E18 rollout window Friday) Tj ET\nendstream\nendobj\ntrailer << >>\n%%EOF\n', 'latin1')

test('E18-02 PDF 첨부 업로드 → 근거 인용 (C01 · FR-A01)', async ({ page }) => {
  await login(page, 'user', '/chat?profile=tpl-qa')
  await page.locator('input[type=file]').setInputFiles({ name: 'rollout.pdf', mimeType: 'application/pdf', buffer: PDF })
  await expect(page.getByRole('button', { name: 'rollout.pdf 제거' })).toBeVisible()
  await expect(page.getByText('📄 rollout.pdf')).toBeVisible()
  await page.getByRole('textbox', { name: '요청 입력' }).fill('첨부한 배포 일정 요약')
  await page.keyboard.press('Enter')
  await expect(page.getByText(/trace .* · succeeded/)).toBeVisible({ timeout: 60_000 })
  const cites = page.getByRole('button', { name: /^\[\d+\]$/ })
  await expect(cites.first()).toBeVisible()
  await cites.last().click()
  await expect(page.getByRole('dialog', { name: /근거 원문/ })).toContainText('rollout.pdf')
})

test('E18-03 실패 단계 → 재계획 1/1 표시 후 완료 (C02 · FR-A02.1)', async ({ page, request }) => {
  const tok = await (await request.post('/idp/token', { form: { grant_type: 'password', username: 'dev', password: 'dev-pass', client_id: 'demo' } })).json()
  const auth = { Authorization: `Bearer ${tok.access_token}` }
  const id = `e18-replan-${Date.now()}`
  const yaml = `planner:
  mode: template
  replan: {max_rounds: 1, fallback_plan: [{id: alt, agent: retriever, tool: confluence.search_pages, args: {query: 런북}}, {id: sum, agent: summarize, depends_on: [alt]}]}
workers: {retriever: {tools: ["jira.*", confluence.search_pages]}, summarize: {model: domain-local}}
retry: {max_attempts: 1}
default_plan:
  - {id: s1, agent: retriever, tool: jira.no_such_tool, args: {}}
  - {id: s2, agent: summarize, depends_on: [s1]}
`
  expect((await request.post('/api/v1/agents', { headers: auth, data: { id, name: `E18 재계획 ${id}`, yaml } })).status()).toBe(201)
  expect((await request.post(`/api/v1/agents/${id}/publish`, { headers: auth, data: {} })).status()).toBe(200)
  await login(page, 'user', `/chat?profile=${id}`)
  await page.getByRole('textbox', { name: '요청 입력' }).fill('런북 요약')
  await page.keyboard.press('Enter')
  const plan = page.getByRole('complementary', { name: '실행 계획' })
  await expect(plan.getByLabel('재계획 1')).toContainText('재계획 1/1', { timeout: 60_000 })
  await expect(plan.getByLabel('재계획 1')).toContainText('이전: s1')
  await expect(plan.getByText('재계획됨').first()).toBeVisible()
  await expect(page.getByText(/trace .* · succeeded/)).toBeVisible({ timeout: 60_000 })
})

test('E18-04 콘솔 런타임 오류 없음', () => {
  expect(errors).toEqual([])
})
