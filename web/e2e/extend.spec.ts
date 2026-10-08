import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { TAGS } from './a11y-shared'

// Epic 30 — 동의·승인 시간 연장 (D14-③, WCAG 2.2.1): 동의 카드 「시간 연장」 → 남은 시간 증가 · 승인 대기열 드로어 연장 — 시드 계정 local/test 전용
async function login(page: Page, username: string, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

const secondsLeft = async (card: ReturnType<Page['locator']>) => Number((await card.getByText(/⏱ \d+초 남음/).textContent())?.match(/(\d+)초/)?.[1])

test('E30-01 동의 카드 시간 연장 — +60초 · 접근성 위반 0 · 거부로 마무리', async ({ page }) => {
  await login(page, 'user', '/chat?profile=demo-orders')
  await page.getByRole('textbox', { name: '요청 입력' }).fill('오늘 주문 이상 건 분류해줘')
  await page.keyboard.press('Enter')
  const card = page.getByText('샘플링 요청 — 동의 필요').locator('xpath=ancestor::section[1]')
  await expect(card).toBeVisible({ timeout: 30_000 })
  const before = await secondsLeft(card)
  await card.getByRole('button', { name: /시간 연장/ }).click()
  await expect.poll(() => secondsLeft(card), { timeout: 10_000 }).toBeGreaterThan(before + 50)
  const res = await new AxeBuilder({ page }).include('main').withTags(TAGS).analyze()
  expect(res.violations.map((v) => `${v.id}: ${v.nodes[0]?.target}`)).toEqual([])
  await card.getByRole('button', { name: '거부' }).click()
  await expect(card).toBeHidden({ timeout: 30_000 })
})

test('E30-02 승인 대기열 — 드로어에서 시간 연장 (+30분, 남은 연장 표시)', async ({ page, request }) => {
  const tok = (await (await request.post('/idp/token', { form: { grant_type: 'password', username: 'user', password: 'user-pass', client_id: 'demo' } })).json()).access_token
  // 고위험 쓰기 도구 호출 → 승인 대기 생성 (요청자 = user)
  const call = await request.post('/mcp/jira', { headers: { Authorization: `Bearer ${tok}`, Accept: 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25' },
    data: { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'transition_issue', arguments: { key: 'ORI-1440', to: 'Done' } } } })
  expect(JSON.stringify(await call.json())).toContain('approval')
  await login(page, 'security', '/console/gateway/approvals')
  const row = page.getByRole('row').filter({ hasText: 'transition_issue' }).first()
  await row.click()
  const drawer = page.getByRole('dialog')
  const extend = drawer.getByRole('button', { name: '시간 연장 (+30분)' })
  await expect(extend).toBeVisible()
  await expect(extend).toHaveAttribute('title', /남은 연장 10회/)
  await extend.click()
  await expect(extend).toHaveAttribute('title', /남은 연장 9회/, { timeout: 10_000 })
})
