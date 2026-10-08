import { expect, test, type Page } from '@playwright/test'

// Epic 16 — 남은 P0 화면 렌더·주요 흐름 (시드 계정 local/test 전용)
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

const SCREENS: [string, RegExp][] = [
  ['/console/gateway/routing/tools', /MCP·A2A 라우팅/], ['/console/gateway/security', /Security — 인증·mTLS·가드/], ['/console/gateway/secrets', /시크릿 저장소/],
  ['/console/gateway/governance', /허용목록·신뢰 등급/], ['/console/gateway/governance/rules', /소독 규칙/], ['/console/gateway/model-api', /Unified Model API·클라이언트 키/],
  ['/console/gateway/mcp', /MCP 프록시 세션/], ['/console/gateway/a2a', /A2A 프록시·Agent Card 캐시/], ['/console/models/new', /도메인 특화 모델 등록/],
  ['/console/models/tokens', /Access Tokens/], ['/console/identity/idp', /IdP 연동/], ['/console/identity/idp/new', /IdP 연동 추가/],
  ['/console/identity/token-exchange', /토큰 교환 정책/], ['/console/identity/mcp-auth', /MCP Authorization/], ['/console/identity/workloads', /워크로드/],
  ['/console/registry/publish', /메타데이터 등록/], ['/console/registry/sources', /레지스트리 소스·미러/], ['/console/catalog/sbom', /SBOM·라이선스/],
  ['/console/mcp-servers/jira/config', /서버 설정 — /], ['/console/mcp-servers/new/openapi', /Internal API 서버 생성/], ['/console/mcp-servers/new/custom', /Custom 서버 템플릿/],
  ['/console/gateway/routing/models', /모델 라우팅 규칙/], ['/console/observe/pipeline', /관측 파이프라인 상태/], ['/console/observe/metrics', /메트릭 대시보드·알림 규칙/], ['/console/a2a/observability-analyst', /관측 분석 에이전트/],
]

test('E16-01 남은 P0 화면 25종 렌더 + L03 역할 편집 (H03·H05·H06·H10·H11·H13~H15·I03·I05·J01·J02·J04·J06·J07·E04·E06·E08·F03~F05·K01·K05·G03/G04)', async ({ page }) => {
  await login(page, 'admin', '/console')
  for (const [path, heading] of SCREENS) {
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 1, name: heading }), path).toBeVisible()
  }
  await page.goto('/console/identity/users')
  await page.getByRole('row').filter({ hasText: 'user2' }).click()
  await expect(page.getByRole('dialog', { name: /역할·상태/ })).toBeVisible()   // L03 역할 편집
  await page.goto('/console/a2a/observability-analyst')
  await page.getByRole('tab', { name: '인증 설정' }).click()
  await expect(page.getByText('원격 에이전트 인증 (게이트웨이가 주입)')).toBeVisible()
})

test('E16-02 인증서 시크릿 저장 → 값은 마스킹만 (H06·MSG-018)', async ({ page }) => {
  await login(page, 'admin', '/console/gateway/secrets')
  await page.getByRole('button', { name: '+ 시크릿' }).click()
  const name = `tls/e2e-${Date.now()}`
  await page.getByLabel('이름').fill(name)
  await page.getByLabel('종류').selectOption('certificate')
  await page.getByLabel(/PEM/).fill('-----BEGIN CERTIFICATE-----\nMIIBe2e\n-----END CERTIFICATE-----')
  await page.getByRole('button', { name: '저장' }).click()
  const row = page.getByRole('row').filter({ hasText: name })
  await expect(row).toBeVisible()
  await expect(row).not.toContainText('BEGIN CERTIFICATE')
})

test('E16-03 server.json 등록 — 검증 실패(MSG-007) → 수정 → 승인 요청 (E04)', async ({ page }) => {
  await login(page, 'dev', '/console/registry/publish')
  const box = page.getByRole('textbox', { name: 'server.json' })
  await box.fill('{"name": "io.e2e/broken"}')
  await page.getByRole('button', { name: '검증' }).click()
  await expect(page.getByText('server.json 검증 실패 (MSG-007)')).toBeVisible()
  const name = `io.e2e/tool-${Date.now()}`
  await box.fill(JSON.stringify({ name, title: 'E2E Tool', description: 'e2e', version: '1.0.0', remotes: [{ type: 'streamable-http', url: 'http://e2e:8080/mcp' }] }))
  await page.getByRole('button', { name: '검증' }).click()
  await page.getByRole('button', { name: '승인 요청(게시)' }).click()
  await expect(page.getByText(/게시됨 — .*@1\.0\.0/)).toBeVisible()
})

test('E16-04 OpenAPI → 도구 마법사 (F04) · IdP 마법사 단계 (J02)', async ({ page }) => {
  await login(page, 'admin', '/console/mcp-servers/new/openapi')
  const ns = `oa-${Date.now() % 100000}`
  await page.getByRole('button', { name: '다음' }).click()
  await page.getByLabel('네임스페이스 (/mcp/{id})').fill(ns)
  await page.getByRole('button', { name: '다음' }).click()
  await expect(page.getByRole('cell', { name: 'orders_get' })).toBeVisible()  // operationId '.' → '_'
  await page.getByRole('button', { name: '생성·등록' }).click()
  await expect(page.getByText(new RegExp(`생성 — /mcp/${ns}`))).toBeVisible({ timeout: 30_000 })
  await page.goto('/console/identity/idp/new')
  await page.getByLabel('이름').fill('e2e Okta')
  await page.getByLabel('종류').selectOption('okta')
  await page.getByRole('button', { name: '다음' }).click()
  await expect(page.getByText('https://<org>.okta.com/oauth2/default')).toBeVisible()
})

test('E16-05 대화 이력 (C07) · 원격 위임 카드 (C05)', async ({ page }) => {
  await login(page, 'user', '/chat?profile=tpl-incident')
  await page.getByRole('textbox', { name: '요청 입력' }).fill('결제 API 지연 원인 분석')
  await page.keyboard.press('Enter')
  await expect(page.getByText(/원격 에이전트 위임 — observability-analyst/).first()).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText(/trace .* · succeeded/)).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('[aria-label="Task 상태"]').first()).toContainText('completed')
  await page.getByRole('link', { name: /대화 이력/ }).click()
  await expect(page.getByRole('heading', { name: '대화 이력' })).toBeVisible()
  await page.getByRole('row').filter({ hasText: '결제 API 지연 원인 분석' }).first().click()
  await page.getByRole('button', { name: '이어하기 →' }).click()
  await expect(page).toHaveURL(/\/chat\/ses_/)
})

test('E16-06 Security 가드 시험 (H05)', async ({ page }) => {
  await login(page, 'security', '/console/gateway/security')
  await page.getByRole('button', { name: '스캔' }).click()
  await expect(page.locator('pre').filter({ hasText: /findings|blocked|masked/i }).first()).toBeVisible()
})

test('E16-07 콘솔 런타임 오류 없음', async () => {
  expect(errors).toEqual([])
})
