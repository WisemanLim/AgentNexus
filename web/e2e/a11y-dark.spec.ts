import { expect, test } from '@playwright/test'
import { CONSOLE, login, report, scan, type Finding } from './a11y-shared'

// Epic 28 — 어두운 테마 접근성 (D14-①): 시스템 다크(prefers-color-scheme) · 명시 다크(anx.theme=dark) 에서 axe 위반 0
test.describe('시스템 다크', () => {
  test.use({ colorScheme: 'dark' })

  test('E28-01 콘솔 전 화면 — 시스템 다크 axe 위반 0', async ({ page }) => {
    test.setTimeout(300_000)
    await login(page, 'admin', '/console')
    const out: Finding[] = []
    for (const r of CONSOLE) await scan(page, r, out)
    report(out)
    expect(out, `위반 ${out.length}건`).toEqual([])
  })

  test('E28-02 채팅·대화 이력 — 시스템 다크 axe 위반 0', async ({ page }) => {
    await login(page, 'user', '/chat')
    const out: Finding[] = []
    for (const r of ['/chat', '/chat?profile=tpl-qa', '/chat/history']) await scan(page, r, out)
    report(out)
    expect(out, `위반 ${out.length}건`).toEqual([])
  })
})

test('E28-03 명시 다크(토글) — 밝은 시스템에서도 다크 토큰 적용 · 주요 화면 위반 0', async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem('anx.theme', 'dark') } catch { /* 무시 */ } })
  await login(page, 'operator', '/console')
  expect(await page.evaluate(() => document.documentElement.getAttribute('data-theme'))).toBe('dark')
  const out: Finding[] = []
  for (const r of ['/console', '/console/gateway', '/console/mcp-servers', '/console/observe/traces', '/console/admin']) await scan(page, r, out)
  report(out)
  expect(out, `위반 ${out.length}건`).toEqual([])
})

test.describe('명시 라이트 + 시스템 다크', () => {
  test.use({ colorScheme: 'dark' })

  test('E28-04 명시 라이트(토글)는 시스템 다크여도 밝은 토큰·dark: 변형 미적용 · 위반 0', async ({ page }) => {
    await page.addInitScript(() => { try { localStorage.setItem('anx.theme', 'light') } catch { /* 무시 */ } })
    await login(page, 'operator', '/console')
    const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
    expect(bg).toBe('rgb(245, 246, 250)')
    const out: Finding[] = []
    for (const r of ['/console', '/console/gateway', '/console/system', '/console/observe/traces']) await scan(page, r, out)
    report(out)
    expect(out, `위반 ${out.length}건`).toEqual([])
  })
})
