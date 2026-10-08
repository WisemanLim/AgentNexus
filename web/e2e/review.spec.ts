import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

// Epic 21 — 등록 → 심사 → 사용 (E01·E03·E05 · G02 · D10 · F03 · H07/H08) — 시드 계정 local/test 전용
async function login(page: Page, username: string, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

function nextDialog(page: Page, answer: boolean | string): Promise<string> {
  return new Promise((resolve) => page.once('dialog', async (d) => {
    resolve(d.message())
    if (answer === false) await d.dismiss()
    else await d.accept(typeof answer === 'string' ? answer : undefined)
  }))
}

async function bearer(request: APIRequestContext, username: string) {
  const tok = await (await request.post('/idp/token', { form: { grant_type: 'password', username, password: `${username}-pass`, client_id: 'demo' } })).json()
  return { Authorization: `Bearer ${tok.access_token}` }
}

const serverJson = (name: string, tools: string[]) => ({
  $schema: 'https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json', name, description: 'E21 심사 시험', version: '1.0.0',
  remotes: [{ type: 'streamable-http', url: 'https://mcp.example.test/e21/mcp' }], _meta: { auth: { type: 'oauth2' }, tools, license: 'MIT' },
})

test.afterEach(async ({ page }, info) => {
  await page.screenshot({ path: `results/${info.title.replace(/[^\w가-힣-]+/g, '_')}.png`, fullPage: true })
})

test('E21-01 심사 패널 — 점검·도구 위험도·신뢰·허용목록 → 승인 → 카탈로그 신뢰 필터 (E05·E01)', async ({ page, request }) => {
  const name = `io.e21e2e/notes-${Date.now()}`
  expect((await request.post('/v0/publish', { headers: await bearer(request, 'dev'), data: serverJson(name, ['notes.search', 'notes.delete_all']) })).status()).toBe(201)
  await login(page, 'operator', '/console/registry/approvals')
  const row = page.getByRole('row').filter({ hasText: name })
  await expect(row.getByText('통과')).toBeVisible()
  await row.getByRole('button', { name: '심사' }).click()
  const drawer = page.getByRole('dialog', { name: /심사 —/ })
  await expect(drawer.getByText('도구 위험도 — 자동 분류')).toBeVisible()
  await expect(drawer.getByLabel('notes.delete_all 위험도')).toHaveValue('high')
  await drawer.getByLabel('신뢰 등급').selectOption('partner')
  await drawer.getByLabel('허용목록 그룹 (선택)').fill('anx-developers')
  await drawer.getByRole('button', { name: '승인', exact: true }).click()
  await expect(page.getByText(/승인 — .*허용목록/)).toBeVisible()
  await expect(row).toHaveCount(0)
  await page.goto(`/console/catalog?q=${encodeURIComponent(name)}&trust=partner`)
  await expect(page.getByRole('row').filter({ hasText: name }).getByText('partner')).toBeVisible()
})

test('E21-02 정책 2인 검토 — 초안 → 회귀 시험 → 게시 요청(사유) → 작성자 외 승인 (H07·H08)', async ({ page, browser }) => {
  const name = `e21 e2e deny ${Date.now()}`
  await login(page, 'security', '/console/gateway/policies/new')
  await page.getByLabel('이름').fill(name)
  await page.getByRole('textbox', { name: /^대상/ }).fill('e21e2e.*')
  await page.getByLabel('효과').selectOption('deny')
  await page.getByRole('button', { name: '초안 저장' }).click()
  await expect(page.getByText('초안 저장 — 회귀 시험을 실행하세요')).toBeVisible()
  await page.getByRole('button', { name: '회귀 시험' }).click()
  await expect(page.getByText(/최근 요청 재평가/)).toBeVisible()
  const reason = nextDialog(page, 'E2E 쓰기 차단')
  await page.getByRole('button', { name: '게시 요청' }).click()
  expect(await reason).toContain('MSG-003')
  await expect(page.getByText(/게시 요청 — 작성자가 아닌/)).toBeVisible()
  await expect(page.getByRole('button', { name: '승인·게시' })).toBeDisabled()  // 작성자는 승인 불가
  const admin = await (await browser.newContext()).newPage()
  await login(admin, 'admin', '/console/gateway/policies?tab=review')
  await admin.goto('/console/gateway/policies?tab=review')
  const row = admin.getByRole('row').filter({ hasText: name })
  const note = nextDialog(admin, '확인')
  await row.getByRole('button', { name: '승인·게시' }).click()
  await note
  await expect(admin.getByText(`게시했습니다 — ${name}`)).toBeVisible()
  await admin.goto('/console/gateway/policies')
  await expect(admin.getByRole('row').filter({ hasText: name }).getByText('v1')).toBeVisible()
  await admin.context().close()
})

test('E21-03 즉시 사용 위저드 — 등록됨 → 시험 교환 → 정책 기본값(Rego) → 60초 노출 확인 (E03)', async ({ page, request }) => {
  const items = (await (await request.get('/v1/catalog?type=a2a_agent&q=observability-analyst', { headers: await bearer(request, 'operator') })).json()).items
  await login(page, 'operator', `/console/catalog/${items[0].id}`)
  await page.getByRole('button', { name: '즉시 사용' }).click()
  const wiz = page.getByRole('dialog', { name: /즉시 사용 —/ })
  await expect(wiz.getByText(/등록됨 — observability-analyst/)).toBeVisible()
  await wiz.getByRole('button', { name: '다음' }).click()
  await wiz.getByRole('button', { name: '시험 교환' }).click()
  await expect(wiz.getByText(/시험 교환 성공 — .*a2a:observability-analyst/)).toBeVisible()
  await wiz.getByRole('button', { name: '다음' }).click()
  await expect(wiz.getByText(/package agentnexus\.gateway\.agent_allow_observability_analyst/)).toBeVisible()
  await wiz.getByRole('button', { name: '적용 → 노출 확인' }).click()
  await expect(wiz.getByText(/노출 확인 — 호스트 도구 목록·콘솔 카탈로그에 보입니다/)).toBeVisible({ timeout: 60_000 })
})

test('E21-04 Agent Card 편집 → 게시 요청 → 승인 후 공개 (D10)', async ({ page, request }) => {
  const pid = `e21-card-${Date.now() % 100000}`
  const dev = await bearer(request, 'dev')
  expect((await request.post('/api/v1/agents', { headers: dev, data: { id: pid, name: pid, yaml: 'planner: {mode: template, max_steps: 2}\nworkers: {summarize: {model: domain-local}}\ndefault_plan:\n  - {id: s1, agent: summarize, args: {message: "{{request}}"}}\n' } })).status()).toBe(201)
  expect((await request.post(`/api/v1/agents/${pid}/publish`, { headers: dev })).status()).toBe(200)
  await login(page, 'dev', `/console/agents/${pid}/graph`)
  await page.getByRole('button', { name: 'Agent Card 편집' }).click()
  const ed = page.getByRole('dialog', { name: `Agent Card 편집 — ${pid}` })
  await ed.getByLabel('버전 (SemVer)').fill('1.0')
  await ed.getByRole('button', { name: '저장·검증' }).click()
  await expect(ed.getByText(/version: SemVer 형식/)).toBeVisible()
  await ed.getByLabel('버전 (SemVer)').fill('1.0.1')
  await ed.getByRole('button', { name: '게시 요청' }).click()
  await expect(page.getByText(/게시 요청 — 승인 대기열/)).toBeVisible()
  expect((await request.get(`/a2a/platform/${pid}/.well-known/agent-card.json`)).status()).toBe(404)  // 승인 전 비공개
  const op = await bearer(request, 'operator')
  const rows = (await (await request.get('/api/v1/registry/approvals', { headers: op })).json()).items
  const row = rows.find((r: any) => r.item.type === 'agent_profile' && r.item.name === pid)
  expect((await request.post(`/v1/catalog/${row.item.id}/approve`, { headers: op, data: { version: '1.0.1', note: 'e2e' } })).status()).toBe(200)
  expect((await (await request.get(`/a2a/platform/${pid}/.well-known/agent-card.json`)).json()).version).toBe('1.0.1')
})

test('E21-05 MCP 설정 적용 전 시험 — 실패면 적용 불가 (F03)', async ({ page, request }) => {
  await request.post('/api/v1/mcp-servers/dynatrace/refresh', { headers: await bearer(request, 'operator') })
  await login(page, 'operator', '/console/mcp-servers/dynatrace/config')
  const endpoint = page.getByLabel('Streamable HTTP 엔드포인트')
  const good = await endpoint.inputValue()
  await page.getByRole('button', { name: '다음' }).click()
  await page.getByRole('button', { name: '다음' }).click()
  await page.getByRole('button', { name: '적용 전 시험' }).click()
  await expect(page.getByText(/시험 통과 — 아직 저장하지 않았습니다/)).toBeVisible()
  await expect(page.getByRole('button', { name: '적용', exact: true })).toBeEnabled()
  for (let i = 0; i < 3; i++) await page.getByRole('button', { name: '이전' }).click()
  await endpoint.fill('http://mcp-servers:8100/nope/mcp')
  await page.getByRole('button', { name: '다음' }).click()
  await page.getByRole('button', { name: '다음' }).click()
  await page.getByRole('button', { name: '적용 전 시험' }).click()
  await expect(page.getByText(/시험 실패 — 적용하지 않습니다/)).toBeVisible()
  await expect(page.getByRole('button', { name: '적용', exact: true })).toBeDisabled()
  const srv = await (await request.get('/api/v1/mcp-servers/dynatrace', { headers: await bearer(request, 'operator') })).json()
  expect(srv.endpoint).toBe(good)  // 시험은 저장하지 않는다
})

test('E21-06 원격 에이전트 등록 위저드 — 조회·점검 → 등록 요청(승인 대기) (G02)', async ({ page, request }) => {
  const id = `e21-demo-${Date.now() % 100000}`
  await login(page, 'dev', '/console/a2a?register=1')
  const wiz = page.getByRole('dialog', { name: '원격 에이전트 등록 (Agent Card)' })
  await wiz.getByLabel('URL').fill('http://a2a-demo:8200')
  await wiz.getByRole('button', { name: '조회·점검' }).click()
  await expect(wiz.getByText('스키마')).toBeVisible()
  await wiz.getByRole('button', { name: '다음' }).click()
  await wiz.getByLabel('ID (/a2a/{id})').fill(id)
  await expect(wiz.getByLabel('신뢰 등급')).toBeDisabled()  // 신뢰 등급은 승인자가 확정
  await wiz.getByRole('button', { name: '등록 요청' }).click()
  await expect(wiz.getByText(/등록 요청됨 — 운영자 승인 후 활성화됩니다/)).toBeVisible()
  const op = await bearer(request, 'operator')
  const a = await (await request.get(`/api/v1/a2a/agents/${id}`, { headers: op })).json()
  expect(a.status).toBe('pending')
  await request.post(`/v1/catalog/${a.catalog_item_id}/reject`, { headers: op, data: { note: 'e2e 정리' } })
})
