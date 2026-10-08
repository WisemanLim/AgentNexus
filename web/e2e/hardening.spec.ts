import { expect, test, type Page } from '@playwright/test'

// Epic 17 — 확인 대화상자(MSG-002)·거부 사유 필수(MSG-003) (시드 계정 local/test 전용)
async function login(page: Page, username: string, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

/** 다음 대화상자 1개에 응답하고 그 메시지를 돌려준다 (confirm·prompt). */
function nextDialog(page: Page, answer: boolean | string): Promise<string> {
  return new Promise((resolve) => page.once('dialog', async (d) => {
    resolve(d.message())
    if (answer === false) await d.dismiss()
    else await d.accept(typeof answer === 'string' ? answer : undefined)
  }))
}

test.afterEach(async ({ page }, info) => {
  await page.screenshot({ path: `results/${info.title.replace(/[^\w가-힣-]+/g, '_')}.png`, fullPage: true })
})

test('E17-01 Access Token 폐기는 확인 후에만 (I05 · MSG-002)', async ({ page }) => {
  await login(page, 'admin', '/console/models/tokens')
  const name = `e17-${Date.now()}`
  await page.getByRole('button', { name: '+ 발급' }).click()
  await page.getByRole('dialog').getByLabel('이름').fill(name)
  await page.getByRole('dialog').getByRole('button', { name: '발급' }).click()
  const row = page.getByRole('row').filter({ hasText: name })
  await expect(row.getByText('활성')).toBeVisible()
  const msg = nextDialog(page, false)
  await row.getByRole('button', { name: '폐기' }).click()
  expect(await msg).toContain('MSG-002')
  await expect(row.getByText('활성')).toBeVisible() // 취소 → 그대로
  const ok = nextDialog(page, true)
  await row.getByRole('button', { name: '폐기' }).click()
  await ok
  await expect(row.getByRole('button', { name: '폐기' })).toHaveCount(0)
  await expect(row.getByText('폐기', { exact: true })).toBeVisible() // 상태 배지
})

test('E17-02 소독 규칙 삭제 확인 (H11 · MSG-002)', async ({ page }) => {
  await login(page, 'admin', '/console/gateway/governance/rules')
  const name = `e17-rule-${Date.now()}`
  await page.getByLabel('이름').fill(name)
  await page.getByLabel('정규식').fill('e17-secret-[0-9]+')
  await page.getByRole('button', { name: '추가' }).click()
  const row = page.getByRole('row').filter({ hasText: name })
  await expect(row).toBeVisible()
  const msg = nextDialog(page, false)
  await row.getByRole('button', { name: '삭제' }).click()
  expect(await msg).toContain(name)
  await expect(row).toBeVisible()
  const ok = nextDialog(page, true)
  await row.getByRole('button', { name: '삭제' }).click()
  await ok
  await expect(row).toHaveCount(0)
})

test('E17-03 등록 거부는 사유 필수 (E05 · MSG-003)', async ({ page, request }) => {
  const tok = await (await request.post('/idp/token', { form: { grant_type: 'password', username: 'dev', password: 'dev-pass', client_id: 'demo' } })).json()
  const name = `io.e17/reject-${Date.now()}`
  const pub = await request.post('/v0/publish', {
    headers: { Authorization: `Bearer ${tok.access_token}` },
    data: { $schema: 'https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json', name, description: 'E17 거부 시험', version: '1.0.0',
      remotes: [{ type: 'streamable-http', url: 'https://mcp.example.test/e17/mcp' }], _meta: { auth: { type: 'oauth2' }, tools: ['e17.a'] } },
  })
  expect(pub.status()).toBe(201)
  await login(page, 'operator', '/console/registry/approvals')
  const row = page.getByRole('row').filter({ hasText: name })
  const cancel = nextDialog(page, false)
  await row.getByRole('button', { name: '거부' }).click()
  expect(await cancel).toContain('MSG-003')
  await expect(row).toBeVisible() // 사유 없이 취소 → 대기 유지
  const reason = nextDialog(page, '권한 범위 과다')
  await row.getByRole('button', { name: '거부' }).click()
  await reason
  await expect(row).toHaveCount(0)
  const item = await (await request.get(`/v0/servers?search=${encodeURIComponent(name)}`)).json()
  expect(JSON.stringify(item)).not.toContain(name) // 거부된 항목은 발견 불가
})
