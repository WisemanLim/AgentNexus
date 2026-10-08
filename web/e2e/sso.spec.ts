import { expect, test, type Page } from '@playwright/test'

// Epic 19 — 외부 IdP SSO (A10·J01·J02) — 데모 외부 IdP(/_demo/oidc, local 전용)로 실제 브로커 흐름
async function login(page: Page, username: string, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(`${username}-pass`)
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL((u) => !/^\/(login|idp)/.test(u.pathname))
}

async function partner(page: Page, username: string, action: '파트너 로그인' | '취소' = '파트너 로그인') {
  await expect(page.getByRole('heading', { name: '데모 파트너 IdP' })).toBeVisible()
  if (action === '파트너 로그인') {
    await page.getByLabel('파트너 계정').fill(username)
    await page.getByLabel('파트너 비밀번호').fill(`${username}-pass`)
  }
  await page.getByRole('button', { name: action }).click()
}

test.afterEach(async ({ page }, info) => {
  await page.screenshot({ path: `results/${info.title.replace(/[^\w가-힣-]+/g, '_')}.png`, fullPage: true })
})

test('E19-01 외부 IdP 버튼 → 파트너 로그인 → 그룹 매핑 역할(developer) 첫 화면 (A10 · FR-I00·I01)', async ({ page }) => {
  await page.goto('/login')
  await page.getByRole('button', { name: '데모 파트너 IdP 계정으로 로그인' }).click()
  await partner(page, 'partner-kim')
  await page.waitForURL(/\/console\/agents/)
  await expect(page.getByRole('heading', { level: 1, name: /에이전트/ })).toBeVisible()
  const me = await page.evaluate(async () => {
    const t = JSON.parse(sessionStorage.getItem('anx.auth') || 'null')
    return (await fetch('/api/v1/me', { headers: { Authorization: `Bearer ${t.access_token}` } })).json()
  })
  expect(me).toMatchObject({ sub: 'ext:idp-demo-partner:partner-kim', roles: ['developer'], groups: ['anx-developers'] })
})

test('E19-02 IdP 에서 취소 → 로그인 화면에 원인별 안내 (A10 · access_denied)', async ({ page }) => {
  await page.goto('/login')
  await page.getByRole('button', { name: '데모 파트너 IdP 계정으로 로그인' }).click()
  await partner(page, 'partner-kim', '취소')
  await page.waitForURL(/\/login\?error=access_denied/)
  await expect(page.getByRole('alert')).toContainText('IdP 에서 로그인을 취소했거나 거부했습니다')
  await expect(page.getByRole('alert')).toContainText('(access_denied)')
})

test('E19-03 IdP 마법사 — 디스커버리·PKCE 게이트 (J02 ②)', async ({ page }) => {
  await login(page, 'admin', '/console/identity/idp/new')
  await page.getByLabel('이름').fill('e2e 데모 IdP 2')
  await page.getByLabel('종류').selectOption('oidc')
  await page.getByRole('button', { name: '다음' }).click()
  await page.getByLabel('Issuer').fill('http://127.0.0.1:8800/_demo/nope')
  await page.getByRole('button', { name: '디스커버리 시험' }).click()
  await expect(page.getByLabel('디스커버리 결과')).toContainText('✗ OpenID 디스커버리')
  await expect(page.getByRole('button', { name: '다음' })).toBeDisabled()
  await page.getByLabel('Issuer').fill('http://127.0.0.1:8800/_demo/oidc')
  await page.getByLabel('서버 측 주소 (선택)').fill('http://api:8000/_demo/oidc')
  await page.getByRole('button', { name: '디스커버리 시험' }).click()
  await expect(page.getByLabel('디스커버리 결과')).toContainText('✓ PKCE S256 지원')
  await page.getByRole('button', { name: '다음' }).click()
  await expect(page.getByLabel('Redirect URI (IdP 에 등록)')).toHaveValue(/\/idp\/broker\/callback$/)
})

test('E19-04 IdP 목록 — 시험 로그인(새 창) → 클레임·역할 결과 (J01·J02 ④)', async ({ page }) => {
  await login(page, 'admin', '/console/identity/idp')
  const row = page.getByRole('row').filter({ hasText: '데모 파트너 IdP' })
  const [popup] = await Promise.all([page.waitForEvent('popup'), row.getByRole('button', { name: '시험 로그인' }).click()])
  await partner(popup, 'partner-kim')
  await expect(popup.getByRole('heading', { name: '시험 로그인 성공' })).toBeVisible()
  await popup.close()
  const result = page.getByRole('status').filter({ hasText: '시험 로그인 성공' })
  await expect(result).toBeVisible({ timeout: 15_000 })
  await expect(result).toContainText('developer')
  await expect(result).toContainText('anx-developers')
  await expect(row).toContainText('✓ developer')
})
