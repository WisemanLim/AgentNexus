import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

// Epic 22 — 채팅·오케스트레이션 편집기 깊이 (C01·C03·C04·C06·C07 · D03·D06·D07·D08·D09) — 시드 계정 local/test 전용
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

async function profile(request: APIRequestContext, id: string, yaml: string) {
  const dev = await bearer(request, 'dev')
  expect((await request.post('/api/v1/agents', { headers: dev, data: { id, name: id, yaml } })).status()).toBe(201)
  expect((await request.post(`/api/v1/agents/${id}/publish`, { headers: dev, data: {} })).status()).toBe(200)
}

const errors: string[] = []
test.beforeEach(async ({ page }) => { page.on('pageerror', (e) => errors.push(e.message)) })
test.afterEach(async ({ page }, info) => {
  await page.screenshot({ path: `results/${info.title.replace(/[^\w가-힣-]+/g, '_')}.png`, fullPage: true })
})

test('E22-01 응답 메타·근거 요약·👍·다시 생성·편집 후 재전송 (C03)', async ({ page }) => {
  await login(page, 'user', '/chat?profile=tpl-qa')
  await page.getByRole('textbox', { name: '요청 입력' }).fill('장애 대응 런북 알려줘')
  await page.getByRole('button', { name: /보내기/ }).click()
  const evidence = page.getByLabel('이 답변의 근거')
  await expect(evidence).toContainText(/인용 \d+ · 검색했지만 미사용 \d+/, { timeout: 60_000 })
  await expect(evidence).toContainText('MSG-025')
  await page.getByRole('button', { name: '좋아요' }).click()
  await expect(page.getByRole('button', { name: '좋아요' })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: '↻ 다시 생성' }).click()
  await expect(page.getByText('↻ 다시 생성', { exact: true }).first()).toBeVisible()
  await expect(page.getByLabel('이 답변의 근거')).toHaveCount(2, { timeout: 60_000 })
  await page.getByRole('button', { name: '✎ 편집' }).first().click()
  await page.getByRole('textbox', { name: '메시지 편집' }).fill('결제 장애 런북 알려줘')
  await page.getByRole('button', { name: '새 분기로 재전송' }).click()
  await expect(page.getByText('✎ 편집 후 재전송')).toBeVisible()
  await expect(page.getByLabel('이 답변의 근거')).toHaveCount(3, { timeout: 60_000 })
})

test('E22-02 승인 모달 — 요청 출처·실행 신원·영향·정책 근거·고위험은 기억 불가 (C04)', async ({ page }) => {
  await login(page, 'user', '/chat?profile=tpl-issue-report')
  await page.getByRole('textbox', { name: '요청 입력' }).fill('ORION 이번 주 이슈 요약해서 주간 보고 페이지로 만들어줘')
  await page.getByRole('button', { name: /보내기/ }).click()
  await page.getByRole('button', { name: '검토하기 → C04' }).click({ timeout: 60_000 })
  const modal = page.getByRole('dialog', { name: /도구 실행 승인/ })
  await expect(modal.getByText('요청 출처')).toBeVisible()
  await expect(modal.getByText(/OBO 토큰 교환/)).toBeVisible()
  await expect(modal.getByLabel('영향 요약')).toContainText('되돌리기')
  await expect(modal.getByLabel('정책 근거')).toContainText('approval_required = true')
  await expect(modal.getByRole('checkbox', { name: /다시 묻지 않기/ })).toBeDisabled()
  await modal.getByRole('button', { name: '승인하고 실행' }).click()
  await expect(page.getByLabel('이 답변의 근거')).toBeVisible({ timeout: 60_000 })
})

test('E22-03 부분 실패 카드 — 작업별 결과·부분 결과로 계속 (C06)', async ({ page, request }) => {
  const id = `e22-part-${Date.now() % 100000}`
  await profile(request, id, `planner: {mode: template, max_steps: 3}
workers: {retriever: {tools: [jira.search_issues, jira.get_issue]}, summarize: {model: domain-local}}
retry: {max_attempts: 1}
default_plan:
  - {id: ok, agent: retriever, tool: jira.search_issues, args: {query: "{{request}}"}}
  - {id: bad, agent: retriever, tool: jira.get_issue, args: {key: NOPE-1}}
  - {id: sum, agent: summarize, depends_on: [ok, bad]}
`)
  await login(page, 'user', `/chat?profile=${id}`)
  await page.getByRole('textbox', { name: '요청 입력' }).fill('결제 지연')
  await page.getByRole('button', { name: /보내기/ }).click()
  await expect(page.getByText(/부분 실패 — 2개 작업 완료, 1개 미완료/)).toBeVisible({ timeout: 60_000 })
  await expect(page.getByRole('cell', { name: /jira.get_issue/ })).toBeVisible()
  await expect(page.getByText('▨ 빠진 근거: jira.get_issue')).toBeVisible()
  await page.getByRole('button', { name: '부분 결과로 계속' }).click()
  await expect(page.getByText('부분 결과로 확정했습니다.')).toBeVisible()
})

test('E22-04 대화 이력 — 본문 검색·필터·미리보기·내보내기 (C07) · 준비 안 된 프로파일 비활성 (C01)', async ({ page, request }) => {
  const id = `e22-ghost-${Date.now() % 100000}`
  await profile(request, id, `planner: {mode: template, max_steps: 1}
workers: {retriever: {tools: [ghost.search]}}
default_plan:
  - {id: s1, agent: retriever, tool: ghost.search}
`)
  await login(page, 'user', '/chat')
  const card = page.getByRole('button', { name: new RegExp(id) })
  await expect(card).toBeDisabled()
  await expect(card).toContainText('미설정: MCP ghost')
  await page.goto('/chat/history')
  await page.getByLabel('제목·내용 검색').fill('런북')
  await expect(page).toHaveURL(/q=%EB%9F%B0%EB%B6%81/)
  await page.getByRole('row').nth(1).click()
  await expect(page.getByText('마지막 응답')).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '내보내기 .md' }).click()
  expect((await download).suggestedFilename()).toMatch(/\.md$/)
  await page.getByLabel('출처').selectOption('api')
  await expect(page.getByText(/검색 결과 0/)).toBeVisible()
})

test('E22-05 테스트 실행 — 드라이런·노드별 결과·게시 전 점검 (D08) · 코드만 저장·게시본 비교·되돌리기 (D07)', async ({ page, request }) => {
  const id = `e22-ed-${Date.now() % 100000}`
  await profile(request, id, `planner: {mode: template, max_steps: 3}
workers: {retriever: {tools: [jira.search_issues]}, executor: {tools: [confluence.create_page]}}
default_plan:
  - {id: s1, agent: retriever, tool: jira.search_issues, args: {query: "{{request}}"}}
  - {id: s2, agent: executor, tool: confluence.create_page, depends_on: [s1], args: {space: ORION, title: e2e, body: x}}
`)
  await login(page, 'dev', `/console/agents/${id}/test`)
  await expect(page.getByRole('checkbox', { name: '쓰기 도구 드라이런' })).toBeChecked()
  await page.getByLabel('테스트 요청').fill('결제')
  await page.getByRole('button', { name: '실행', exact: true }).click()
  await expect(page.getByText('[드라이런] confluence.create_page → 실행하지 않음 (승인 필요 도구) · 인자 기록됨')).toBeVisible({ timeout: 60_000 })
  await expect(page.getByRole('cell', { name: /s2 · executor/ })).toBeVisible()
  await expect(page.getByText(/최근 테스트 1회 중 성공 1/)).toBeVisible()
  await page.goto(`/console/agents/${id}/code`)
  const box = page.getByRole('textbox', { name: '그래프 YAML' })
  await box.fill((await box.inputValue()).replace('jira.search_issues', 'jira.nope_tool'))
  await expect(page.getByText(/workers.retriever.tools 에 없는 도구|default_plan\[0\]\.tool/).first()).toBeVisible()
  const ask = nextDialog(page, true)
  await page.getByRole('button', { name: '저장', exact: true }).click()
  expect(await ask).toContain('MSG-001')
  await expect(page.getByText('코드만 저장됨 (그래프 반영 보류)')).toBeVisible()
  await page.getByRole('button', { name: '게시본 비교' }).click()
  await expect(page.getByRole('dialog', { name: /게시본 v1 대비 변경/ })).toContainText('workers.retriever')
  await page.keyboard.press('Escape')
  const rv = nextDialog(page, true)
  await page.getByRole('button', { name: '되돌리기' }).click()
  await rv
  await expect(page.getByText('그래프 ↔ 코드 동기 ✓')).toBeVisible()
  await page.getByLabel('정의 형식').selectOption('adk')
  await expect(page.getByText(/root_agent = /)).toBeVisible()
})

test('E22-06 Planner·팬아웃 설정 드로어와 실패 시뮬레이션 (D03·D06) · Host 세션 KPI (D09)', async ({ page }) => {
  await login(page, 'dev', '/console/agents/tpl-incident/graph')
  await page.locator('.react-flow__node').filter({ hasText: 'Planner' }).click()
  const dlg = page.getByRole('dialog', { name: 'Planner 노드 설정' })
  await expect(dlg.getByLabel('분해 프롬프트')).toBeVisible()
  await expect(dlg.getByText(/재계획 조건/)).toBeVisible()
  await expect(dlg.getByRole('checkbox', { name: '검색 결과 0건' })).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('checkbox', { name: 's1' }).last().check()
  await page.getByRole('button', { name: '시뮬레이션' }).click()
  await expect(page.getByText(/→ (합류 성립|전체 실패|모든 단계)/)).toBeVisible()
  await page.goto('/console/host/sessions')
  await expect(page.getByText('활성 세션')).toBeVisible()
  await expect(page.getByText('재연결 중').first()).toBeVisible()
})

test('E22-07 콘솔 런타임 오류 없음', async () => {
  expect(errors).toEqual([])
})
