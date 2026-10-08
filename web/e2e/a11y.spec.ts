import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { CONSOLE, TAGS, login, report, scan, type Finding } from './a11y-shared'

// Epic 26 — 접근성 (D13-③): KWCAG 2.2 + WCAG 2.2 AA — axe-core 자동 점검(전 화면) + 키보드·포커스·건너뛰기 링크 · 시드 계정 local/test 전용

test('E26-01 콘솔 전 화면 axe-core WCAG 2.2 AA 위반 0', async ({ page }) => {
  test.setTimeout(300_000)
  await login(page, 'admin', '/console')
  const out: Finding[] = []
  for (const r of CONSOLE) await scan(page, r, out)
  report(out)
  expect(out, `위반 ${out.length}건`).toEqual([])
})

test('E26-02 채팅·대화 이력·로그인·첫 실행 axe-core 위반 0', async ({ page, browser }) => {
  const out: Finding[] = []
  const ctx = await browser.newContext()
  const anon = await ctx.newPage()
  await anon.goto('/login')
  const r0 = await new AxeBuilder({ page: anon }).withTags(TAGS).analyze()
  for (const v of r0.violations) out.push({ route: '/login', id: v.id, impact: v.impact ?? '', nodes: v.nodes.length, target: String(v.nodes[0]?.target ?? ''), help: v.help })
  await ctx.close()
  await login(page, 'user', '/chat')
  for (const r of ['/chat', '/chat?profile=tpl-qa', '/chat/history']) await scan(page, r, out)
  report(out)
  expect(out, `위반 ${out.length}건`).toEqual([])
})

test('E26-03 키보드 — 건너뛰기 링크 · 포커스 표시 · 화면 제목 (KWCAG 2.2)', async ({ page }) => {
  await login(page, 'operator', '/console/gateway')
  await page.goto('/console/gateway')  // 새 문서에서 첫 Tab
  await page.locator('h1').first().waitFor()
  await page.keyboard.press('Tab')
  const skip = page.getByRole('link', { name: '본문 바로가기' })
  await expect(skip).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('main')).toBeFocused()
  await expect(page).toHaveTitle(/게이트웨이 개요.*AgentNexus/)
  await page.goto('/console/observe/traces')
  await expect(page).toHaveTitle(/트레이스.*AgentNexus/)
  // 포커스 표시 — 키보드 포커스가 보이는 외곽선을 가진다
  await page.keyboard.press('Tab')
  await page.keyboard.press('Tab')
  const outline = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null
    if (!el) return 'none'
    const s = getComputedStyle(el)
    return `${s.outlineStyle} ${s.outlineWidth} ${s.boxShadow}`
  })
  expect(outline).not.toMatch(/^none 0px none$/)
})
