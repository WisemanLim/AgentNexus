import { createHmac } from 'node:crypto'
import { expect, test, type Page } from '@playwright/test'

// Epic 20 — 첫 실행 마법사(A05~A09)·배포 프로파일(L01)·에어갭 배너(ST-09·CMP-16)·재로그인(ST-11)·MFA 로그인
test.describe.configure({ mode: 'serial' })

async function login(page: Page, username: string, path: string, otp?: string) {
  await page.goto(path)
  await page.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await page.getByLabel('사용자 이름 / Username').fill(username)
  await page.getByLabel('비밀번호 / Password').fill(username === 'nexus-admin' ? PW : `${username}-pass`)
  if (otp) await page.getByLabel('MFA 코드 (등록한 경우)').fill(otp)
  await page.getByRole('button', { name: '로그인' }).click()
}

const PW = 'Orion-Setup-2026!e2e'
let SECRET = ''

function totp(secret: string, t = Date.now() / 1000) {
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const c of secret.replace(/\s|=/g, '')) bits += A.indexOf(c).toString(2).padStart(5, '0')
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)))
  const ctr = Buffer.alloc(8)
  ctr.writeBigUInt64BE(BigInt(Math.floor(t / 30)))
  const mac = createHmac('sha1', key).update(ctr).digest()
  const off = mac[mac.length - 1] & 0xf
  return String((mac.readUInt32BE(off) & 0x7fffffff) % 1_000_000).padStart(6, '0')
}

test.afterEach(async ({ page }, info) => {
  await page.screenshot({ path: `results/${info.title.replace(/[^\w가-힣-]+/g, '_')}.png`, fullPage: true })
})

test('E20-01 설정 코드 게이트 → ① 관리자·MFA → 건너뛰기 → ⑤ 60초 자가 시험 (A05~A09)', async ({ page, request }) => {
  const adm = await (await request.post('/idp/token', { form: { grant_type: 'password', username: 'admin', password: 'admin-pass', client_id: 'demo' } })).json()
  const st = await (await request.get('/api/v1/setup/state')).json()
  test.skip(st.completed, '로컬 첫 실행이 이미 완료됨')
  const code = (await (await request.post('/api/v1/setup/code', { headers: { Authorization: `Bearer ${adm.access_token}` } })).json()).code
  await page.goto('/setup')
  await expect(page.getByRole('heading', { name: '일회용 설정 코드' })).toBeVisible()
  await page.getByLabel('설정 코드').fill('AAAA-BBBB-CCCC')
  await page.getByRole('button', { name: '시작' }).click()
  await expect(page.getByText('설정 코드가 올바르지 않습니다')).toBeVisible()
  await page.getByLabel('설정 코드').fill(code)
  await page.getByRole('button', { name: '시작' }).click()
  await expect(page.getByText(/단계 \d\/5/)).toBeVisible()
  while (await page.getByRole('button', { name: '이전' }).isEnabled()) await page.getByRole('button', { name: '이전' }).click()
  await expect(page.getByText('단계 1/5')).toBeVisible()
  await page.getByLabel('이메일').fill('nexus-admin@agentnexus.local')
  await page.getByLabel('비밀번호', { exact: true }).fill('short')
  await expect(page.getByLabel('비밀번호 정책')).toContainText('· 14자 이상')
  await page.getByLabel('비밀번호', { exact: true }).fill(PW)
  await page.getByLabel('비밀번호 확인').fill(PW)
  await expect(page.getByLabel('비밀번호 정책')).toContainText('✓ 14자 이상')
  await page.getByRole('button', { name: '저장하고 다음 →' }).click()
  await expect(page.getByText('MFA 를 등록하고 확인 코드 6자리')).toBeVisible()  // MFA 건너뛰기 없음
  await page.getByRole('button', { name: 'TOTP 등록 시작' }).click()
  const uri = await page.locator('pre').filter({ hasText: 'otpauth://' }).innerText()
  SECRET = new URL(uri.replace('otpauth://', 'https://')).searchParams.get('secret')!
  await page.getByLabel('확인 코드 (6자리)').fill(totp(SECRET))
  await page.getByRole('button', { name: '저장하고 다음 →' }).click()
  await expect(page.getByText('단계 2/5')).toBeVisible()
  await expect(page.getByRole('table', { name: '공급자 어댑터' })).toContainText('내장 Mock')
  for (let k = 0; k < 3; k++) await page.getByRole('button', { name: '건너뛰기' }).click()
  await expect(page.getByText('단계 5/5')).toBeVisible()
  await page.getByRole('button', { name: '자가 시험 실행' }).click()
  const res = page.getByLabel('자가 시험 결과')
  await expect(res).toContainText('오케스트레이션·콘솔 노출', { timeout: 70_000 })
  await expect(res).toContainText('목표 60초 이내')
})

test('E20-02 MFA 등록 관리자는 6자리 없이 로그인 불가 (A05 ③)', async ({ page }) => {
  test.skip(!SECRET, 'E20-01 이 MFA 를 등록하지 않음')
  await login(page, 'nexus-admin', '/console')
  await expect(page.getByRole('alert')).toContainText('MFA 코드가 필요하거나 올바르지 않습니다')
  await page.getByLabel('비밀번호 / Password').fill(PW)
  await page.getByLabel('MFA 코드 (등록한 경우)').fill(totp(SECRET))
  await page.getByRole('button', { name: '로그인' }).click()
  await page.waitForURL(/\/console/)
  await expect(page.getByRole('heading', { level: 1, name: /운영 개요/ })).toBeVisible()
})

test('E20-03 배포 프로파일 미리보기·에어갭 적용 → 전역 배너 → 되돌리기 (L01 · ST-09 · CMP-16)', async ({ page }) => {
  await login(page, 'admin', '/console/admin')
  await page.waitForURL(/\/console\/admin/)
  await page.getByRole('radio', { name: /AI 게이트웨이만/ }).click()
  const pv = page.getByRole('region', { name: '변경 미리보기' })
  await expect(pv).toContainText('host')
  await expect(pv).toContainText('AGENTNEXUS_COMPONENTS=gateway,identity')
  await page.getByRole('radio', { name: /전체 플랫폼/ }).click()
  await page.getByLabel(/에어갭 \(외부 SaaS 차단/).check()
  await expect(pv).toContainText('상용 LLM 공급자')
  page.once('dialog', (d) => d.accept())
  await page.getByRole('button', { name: '적용…' }).click()
  await expect(page.getByLabel('전역 알림')).toContainText('에어갭 모드', { timeout: 15_000 })
  await expect(page.getByRole('banner').getByText('에어갭')).toBeVisible()
  await page.getByLabel(/에어갭 \(외부 SaaS 차단/).uncheck()
  page.once('dialog', (d) => d.accept())
  await page.getByRole('button', { name: '적용…' }).click()
  await expect(page.getByLabel('전역 알림').getByText('에어갭 모드')).toHaveCount(0, { timeout: 70_000 })
})

test('E20-04 세션 만료 → 재로그인 모달(새 창) → 입력 보존 (ST-11 · MSG-024)', async ({ page, request }) => {
  const adm = await (await request.post('/idp/token', { form: { grant_type: 'password', username: 'security', password: 'security-pass', client_id: 'demo' } })).json()
  const sec = { Authorization: `Bearer ${adm.access_token}` }
  await login(page, 'user', '/chat')
  await page.waitForURL(/\/chat/)
  await page.getByRole('textbox', { name: '요청 입력' }).fill('만료 전에 쓰던 긴 요청 — 보존되어야 함')
  // 세션 무효화 — 관리자가 계정을 잠시 비활성화하면 다음 API 호출(상단 바 폴링)이 401, 갱신도 실패한다
  expect((await request.patch('/api/v1/identity/users/usr_user', { headers: sec, data: { status: 'disabled' } })).ok()).toBe(true)
  const modal = page.getByRole('dialog', { name: '세션이 만료되었습니다' })
  try {
    await expect(modal).toBeVisible({ timeout: 40_000 })
  } finally {
    await request.patch('/api/v1/identity/users/usr_user', { headers: sec, data: { status: 'active' } })
  }
  const [popup] = await Promise.all([page.waitForEvent('popup'), modal.getByRole('button', { name: '다시 로그인 (새 창)' }).click()])
  await popup.getByRole('button', { name: '조직 계정(OIDC)으로 로그인' }).click()
  await popup.getByLabel('사용자 이름 / Username').fill('user')
  await popup.getByLabel('비밀번호 / Password').fill('user-pass')
  await popup.getByRole('button', { name: '로그인' }).click()
  await popup.waitForEvent('close', { timeout: 20_000 })
  await expect(modal).toBeHidden()
  await expect(page.getByRole('textbox', { name: '요청 입력' })).toHaveValue('만료 전에 쓰던 긴 요청 — 보존되어야 함')
})
