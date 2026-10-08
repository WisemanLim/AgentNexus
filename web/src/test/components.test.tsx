import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createColumnHelper } from '@tanstack/react-table'
import { MemoryRouter, Route, Routes } from 'react-router'
import { Shell } from '@/components/Shell'
import { DataTable } from '@/components/DataTable'
import { ErrorBox, StatusBadge } from '@/components/ui'
import { ApiError, api, configureApi } from '@/lib/api'
import { AuthProvider, _test } from '@/lib/auth'
import { I18nProvider } from '@/lib/i18n'

const ME_USER = { sub: 'usr_user', name: '김사용', roles: ['user'], permissions: ['chat:use', 'catalog:read', 'agents:read', 'models:read'], role_labels: { user: '최종 사용자' }, kind: 'user' }

function mockFetch(routes: Record<string, unknown>) {
  return vi.fn(async (url: string) => {
    const path = url.split('?')[0]
    const body = routes[path]
    if (body === undefined) return new Response(JSON.stringify({ error: { message: 'nf', code: 'not_found' } }), { status: 404 })
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
}

const wrap = (ui: React.ReactNode, path = '/console') => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <I18nProvider><AuthProvider><MemoryRouter initialEntries={[path]}><Routes><Route path="/console" element={ui}><Route index element={<div>본문</div>} /></Route></Routes></MemoryRouter></AuthProvider></I18nProvider>
  </QueryClientProvider>
)

describe('Shell (SCR-B01)', () => {
  beforeEach(() => _test.setCurrent({ access_token: 'tok', expires_at: Date.now() + 60_000 }))
  afterEach(() => vi.unstubAllGlobals())

  it('권한 없는 메뉴는 숨기지 않고 비활성 + 사유 (ST-04)', async () => {
    vi.stubGlobal('fetch', mockFetch({ '/api/v1/me': ME_USER, '/api/v1/system/info': { env: 'local', version: '0.1.0', db_engine: 'sqlite', protocols: { mcp: '2025-11-25', a2a: '0.3.0' } },
      '/api/v1/notifications': { unread: 2, items: [] }, '/api/v1/approvals': { items: [{ id: 'a' }] } }))
    render(wrap(<Shell />))
    const nav = await screen.findByRole('navigation', { name: '주 메뉴' })
    await waitFor(() => expect(within(nav).getByRole('link', { name: /채팅/ })).toBeInTheDocument())
    const gw = within(nav).getByText('AI 게이트웨이').closest('[aria-disabled="true"]')
    expect(gw).toHaveAttribute('title', expect.stringContaining('gateway:read'))
    expect(within(nav).getByRole('link', { name: /에이전트/ })).toBeInTheDocument()
    expect(await screen.findByText('승인 대기 1')).toBeInTheDocument()
    expect(screen.getByText('본문')).toBeInTheDocument()
    expect(await screen.findByText(/● local/)).toBeInTheDocument()
  })

  it('⌘K 로 전역 검색 팔레트를 연다 (SCR-B03)', async () => {
    vi.stubGlobal('fetch', mockFetch({ '/api/v1/me': ME_USER, '/api/v1/system/info': {}, '/api/v1/notifications': { unread: 0, items: [] }, '/api/v1/approvals': { items: [] } }))
    render(wrap(<Shell />))
    await screen.findByRole('navigation', { name: '주 메뉴' })
    await userEvent.keyboard('{Meta>}k{/Meta}')
    expect(await screen.findByPlaceholderText(/2자 이상/)).toBeInTheDocument()
  })
})

describe('DataTable (CMP-03)', () => {
  const col = createColumnHelper<{ id: string; name: string }>()
  const columns = [col.accessor('id', { header: 'ID' }), col.accessor('name', { header: '이름' })]
  it('전역 필터와 빈 결과 문구 (ST-02)', async () => {
    render(<I18nProvider><DataTable data={[{ id: 'jira', name: 'Jira' }, { id: 'conf', name: 'Confluence' }]} columns={columns} /></I18nProvider>)
    expect(screen.getAllByRole('row')).toHaveLength(3)
    await userEvent.type(screen.getByRole('textbox'), 'conf')
    expect(screen.getAllByRole('row')).toHaveLength(2)
    await userEvent.clear(screen.getByRole('textbox'))
    await userEvent.type(screen.getByRole('textbox'), 'zzz')
    expect(screen.getByText('"zzz" 결과 없음')).toBeInTheDocument()
  })
  it('1만 행은 가상 스크롤로 일부만 렌더', () => {
    const many = Array.from({ length: 10_000 }, (_, i) => ({ id: `r${i}`, name: `n${i}` }))
    render(<I18nProvider><DataTable data={many} columns={columns} /></I18nProvider>)
    expect(screen.getAllByRole('row').length).toBeLessThan(200)
    expect(screen.getByText('10,000 / 10,000')).toBeInTheDocument()
  })
})

describe('api 클라이언트', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('게이트웨이 오류 형식을 정규화하고 trace_id 를 보존 (ST-03)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { message: '권한 부족: policy:write', code: 'forbidden' } }), { status: 403, headers: { 'X-AgentNexus-Trace-Id': 'abc' } })))
    const err = await api('/api/v1/x').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 403, code: 'forbidden', message: '권한 부족: policy:write', traceId: 'abc' })
    render(<MemoryRouter><I18nProvider><ErrorBox error={err} /></I18nProvider></MemoryRouter>)
    expect(screen.getByText('권한이 없습니다')).toBeInTheDocument()
  })
  it('401 이면 refresh 1회 후 재시도', async () => {
    let n = 0
    vi.stubGlobal('fetch', vi.fn(async () => (++n === 1 ? new Response('{}', { status: 401 }) : new Response('{"ok":true}', { status: 200 }))))
    const refresh = vi.fn(async () => true)
    configureApi({ token: () => 't', refresh })
    expect(await api('/api/v1/me')).toEqual({ ok: true })
    expect(refresh).toHaveBeenCalledOnce()
  })
  it('상태 배지는 텍스트를 함께 표시 (색만으로 구분 금지)', () => {
    render(<StatusBadge status="TASK_STATE_INPUT_REQUIRED" />)
    expect(screen.getByText('입력 필요')).toBeInTheDocument()
  })
})
