// SCR-B01 공통 셀 — 좌 내비 11메뉴(216px, 권한 없는 메뉴는 비활성+사유) · 상단 바(⌘K·환경·승인 대기·알림·사용자) · 콘텐츠
import { useEffect, useState, type ReactNode } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { can, useAuth } from '@/lib/auth'
import { fmtAgo } from '@/lib/format'
import { useT, type Lang } from '@/lib/i18n'
import { send, useGet, useSend } from '@/lib/query'
import { CommandPalette } from './CommandPalette'
import { Badge, Button, Drawer, Empty, SkipLink, Tabs, cx, useToast } from './ui'

type NavItem = { key: Parameters<ReturnType<typeof useT>['t']>[0]; to: string; perm: string; icon: string; sub?: { to: string; label: string; perm?: string }[] }
export const NAV: NavItem[] = [
  { key: 'nav.overview', to: '/console', perm: 'observe:read', icon: '◎', sub: [{ to: '/console', label: '운영 개요' }, { to: '/console/system', label: '구성요소 상태', perm: 'admin:read' }, { to: '/console/api', label: 'API·OpenAPI', perm: 'agents:read' }, { to: '/setup', label: '첫 실행 마법사', perm: 'admin:write' }] },
  { key: 'nav.chat', to: '/chat', perm: 'chat:use', icon: '✉' },
  { key: 'nav.agents', to: '/console/agents', perm: 'agents:read', icon: '⚙', sub: [{ to: '/console/agents', label: '에이전트 프로파일' }, { to: '/console/host/sessions', label: 'MCP Host 세션', perm: 'mcp:read' }] },
  { key: 'nav.catalog', to: '/console/catalog', perm: 'catalog:read', icon: '▦', sub: [{ to: '/console/catalog', label: '통합 카탈로그' }, { to: '/console/registry/publish', label: 'server.json 등록', perm: 'registry:publish' }, { to: '/console/registry/approvals', label: '등록 승인 대기열', perm: 'catalog:approve' }, { to: '/console/registry/sources', label: '레지스트리 소스·미러', perm: 'catalog:write' }, { to: '/console/catalog/sbom', label: 'SBOM·라이선스' }] },
  { key: 'nav.mcp', to: '/console/mcp-servers', perm: 'mcp:read', icon: '⧉', sub: [{ to: '/console/mcp-servers', label: '서버 인스턴스' }, { to: '/console/mcp-servers/new/openapi', label: 'Internal API (OpenAPI)', perm: 'mcp:write' }, { to: '/console/mcp-servers/new/custom', label: 'Custom 템플릿' }] },
  { key: 'nav.a2a', to: '/console/a2a', perm: 'a2a:read', icon: '⇄', sub: [{ to: '/console/a2a', label: '원격 에이전트' }, { to: '/console/a2a/tasks', label: 'A2A Task' }] },
  { key: 'nav.gateway', to: '/console/gateway', perm: 'gateway:read', icon: '⛨', sub: [{ to: '/console/gateway', label: '게이트웨이 개요' }, { to: '/console/gateway/routing/log', label: '라우팅 결정 로그' }, { to: '/console/gateway/policies', label: '정책' }, { to: '/console/gateway/limits', label: 'Rate limits·예산' }, { to: '/console/gateway/approvals', label: '사람 승인 대기열' }, { to: '/console/gateway/routing/models', label: '모델 라우팅 규칙' }, { to: '/console/gateway/routing/tools', label: 'MCP·A2A 라우팅' }, { to: '/console/gateway/security', label: 'Security·mTLS' }, { to: '/console/gateway/secrets', label: '시크릿 저장소' }, { to: '/console/gateway/governance', label: '허용목록·신뢰 등급' }, { to: '/console/gateway/governance/rules', label: '소독 규칙' }, { to: '/console/gateway/model-api', label: 'Model API·클라이언트 키' }, { to: '/console/gateway/mcp', label: 'MCP 프록시 세션' }, { to: '/console/gateway/a2a', label: 'A2A 프록시·카드 캐시' }] },
  { key: 'nav.models', to: '/console/models', perm: 'models:read', icon: '◇', sub: [{ to: '/console/models', label: '공급자·모델' }, { to: '/console/models/providers', label: '공급자 어댑터', perm: 'models:write' }, { to: '/console/models/playground', label: '모델 플레이그라운드', perm: 'chat:use' }, { to: '/console/models/new', label: '도메인 모델 등록', perm: 'models:write' }, { to: '/console/models/tokens', label: 'Access Tokens', perm: 'tokens:issue' }] },
  { key: 'nav.identity', to: '/console/identity/users', perm: 'identity:read', icon: '⚿', sub: [{ to: '/console/identity/users', label: '사용자·역할' }, { to: '/console/identity/idp', label: 'IdP 연동' }, { to: '/console/identity/token-exchange', label: '토큰 교환 정책' }, { to: '/console/identity/propagation', label: '토큰 전파 추적' }, { to: '/console/identity/mcp-auth', label: 'MCP Authorization' }, { to: '/console/identity/workloads', label: '워크로드' }] },
  { key: 'nav.observe', to: '/console/observe/traces', perm: 'observe:read', icon: '≋', sub: [{ to: '/console/observe/traces', label: '트레이스' }, { to: '/console/observe/logs', label: '로그' }, { to: '/console/observe/cost', label: 'Usage & Cost' }, { to: '/console/observe/pipeline', label: '파이프라인 상태' }, { to: '/console/observe/metrics', label: '메트릭·알림 규칙' }, { to: '/console/observe/slo', label: 'SLO·오류 예산' }, { to: '/console/observe/budgets', label: '예산·경보', perm: 'cost:read' }, { to: '/console/audit', label: '감사 로그·체인', perm: 'audit:read' }, { to: '/console/audit/reports/new', label: '규제 리포트', perm: 'audit:read' }] },
  { key: 'nav.admin', to: '/console/admin', perm: 'admin:read', icon: '⚒', sub: [{ to: '/console/admin', label: '플랫폼 설정' }, { to: '/console/admin/rbac', label: '역할·권한(RBAC)', perm: 'identity:read' }] },
]

function Sidebar() {
  const { me } = useAuth()
  const { t } = useT()
  const { pathname } = useLocation()
  const info = useGet('/api/v1/system/info')
  const [collapsed, setCollapsed] = useState(false)
  const group = [...NAV].sort((a, b) => b.to.length - a.to.length).find((n) => pathname === n.to || pathname.startsWith(`${n.to}/`) || n.sub?.some((s) => pathname.startsWith(s.to)))
  return (
    <nav aria-label="주 메뉴" className={cx('flex shrink-0 flex-col border-r border-line bg-surface transition-[width]', collapsed ? 'w-16' : 'w-[216px]')}>
      <Link to="/console" className="flex h-12 items-center gap-2 px-4 font-semibold text-navy">
        <span className="grid size-7 place-items-center rounded-full bg-primary text-sm text-white">N</span>
        {!collapsed && 'AgentNexus'}
      </Link>
      <ul className="flex-1 space-y-0.5 overflow-auto px-2 py-2">
        {NAV.map((n) => {
          const ok = can(me, n.perm)
          const active = group === n
          return (
            <li key={n.to}>
              {ok ? (
                <NavLink to={n.to} end={n.to === '/console'} title={collapsed ? t(n.key) : undefined}
                  className={cx('flex h-8 items-center gap-2 rounded-md px-2.5 text-sm', active ? 'bg-primary-soft font-semibold text-primary' : 'text-fg hover:bg-surface-2')}>
                  <span aria-hidden className="w-4 text-center">{n.icon}</span>{!collapsed && t(n.key)}
                </NavLink>
              ) : (
                <span aria-disabled="true" title={`${t('common.noperm')}: ${n.perm}`} className="flex h-8 cursor-not-allowed items-center gap-2 rounded-md px-2.5 text-sm text-fg-muted opacity-60">
                  <span aria-hidden className="w-4 text-center">{n.icon}</span>{!collapsed && t(n.key)}
                </span>
              )}
              {active && !collapsed && n.sub && (
                <ul className="mt-0.5 mb-1 ml-6 space-y-0.5 border-l border-line pl-2">
                  {n.sub.map((s) => can(me, s.perm || n.perm) ? (
                    <li key={s.to}><NavLink to={s.to} end className={({ isActive }) => cx('block rounded px-2 py-1 text-xs', isActive ? 'font-semibold text-primary' : 'text-fg-muted hover:text-fg')}>{s.label}</NavLink></li>
                  ) : (
                    <li key={s.to}><span role="link" aria-disabled="true" title={`${t('common.noperm')}: ${s.perm}`} className="block cursor-not-allowed px-2 py-1 text-xs text-fg-muted opacity-50">{s.label}</span></li>
                  ))}
                </ul>
              )}
            </li>
          )
        })}
      </ul>
      <div className="border-t border-line px-3 py-2 text-[11px] leading-5 text-fg-muted">
        {!collapsed && info.data && (
          <>
            <div><Badge tone="ok">● {info.data.env}</Badge> <span className="ml-1">{info.data.db_engine}</span></div>
            <div>v{info.data.version} · MCP {info.data.protocols?.mcp?.current ?? info.data.protocols?.mcp}{(info.data.protocols?.mcp_supported ?? []).includes('2026-07-28') ? ' · 2026-07-28' : ''}</div>
            <div>A2A {(info.data.protocols?.a2a_supported ?? info.data.protocols?.a2a?.supported ?? []).join(' · ') || info.data.protocols?.a2a}</div>
          </>
        )}
        <button type="button" onClick={() => setCollapsed((c) => !c)} className="mt-1 text-fg-muted hover:text-fg" aria-label="메뉴 접기">{collapsed ? '»' : '« 접기'}</button>
      </div>
    </nav>
  )
}

// SCR-B04 알림 분류 — app/api/ops.py NOTIFY_CATEGORIES 와 같은 키
const NOTE_CATS: Record<string, { label: string; action: string }> = {
  approval: { label: '승인', action: '승인하기 →' }, system: { label: '경보', action: '상태 보기 →' }, budget: { label: '비용', action: '예산 보기 →' },
  registry: { label: '등록', action: '검토하기 →' }, security: { label: '보안', action: '확인하기 →' },
}
const sevTone = (s?: string) => (s === 'danger' || s === 'error' ? 'danger' : s === 'warn' || s === 'warning' ? 'warn' : 'primary') as 'danger' | 'warn' | 'primary'

function Notifications({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [cat, setCat] = useState('all')
  const [prefs, setPrefs] = useState(false)
  const q = useGet('/api/v1/notifications', { category: cat === 'all' ? undefined : cat }, { enabled: open, refetchInterval: open ? 15_000 : false })
  const mark = useSend((ids: string[]) => send('POST', '/api/v1/notifications/read', { ids }), ['/api/v1/notifications'])
  const items: any[] = q.data?.items ?? []
  const counts: Record<string, number> = q.data?.counts ?? {}
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  return (
    <Drawer open={open} onClose={onClose} title={`알림 센터 (${q.data?.unread ?? 0} 안 읽음)`}>
      <div className="mb-3 flex items-center justify-end gap-2">
        <Button size="sm" variant="ghost" aria-expanded={prefs} onClick={() => setPrefs((v) => !v)}>⚙ 수신 설정</Button>
        <Button size="sm" disabled={!items.some((n) => !n.read)} onClick={() => mark.mutate(items.filter((n) => !n.read).map((n) => n.id))}>모두 읽음</Button>
      </div>
      {prefs && <NotifyPrefs onDone={() => setPrefs(false)} />}
      <Tabs value={cat} onChange={setCat} tabs={[{ id: 'all', label: `전체 ${total}` },
        ...Object.entries(NOTE_CATS).map(([id, c]) => ({ id, label: `${c.label} ${counts[id] ?? 0}` }))]} />
      {!items.length && <Empty>알림이 없습니다.</Empty>}
      <ul className="space-y-2">
        {items.map((n) => {
          const c = NOTE_CATS[n.category]
          const tone = sevTone(n.severity)
          return (
            <li key={n.id} className={cx('rounded-md border border-l-4 p-3 text-sm', tone === 'danger' ? 'border-l-danger' : tone === 'warn' ? 'border-l-warn' : 'border-l-primary',
              n.read ? 'border-line opacity-70' : 'border-primary/30 bg-primary-soft/40')}>
              <div className="flex items-center gap-2">
                <Badge tone={tone} title={n.kind}>{c?.label ?? n.kind}</Badge>
                <span className="font-medium">{n.title}</span>
                <span className="ml-auto shrink-0 text-xs text-fg-muted">{fmtAgo(n.ts)}</span>
              </div>
              {n.body && <p className="mt-1 text-fg-muted">{n.body}</p>}
              <div className="mt-1 flex gap-2 text-xs">
                {n.link && <Link to={n.link} onClick={() => { if (!n.read) mark.mutate([n.id]); onClose() }} className="text-primary hover:underline">{c?.action ?? '열기 →'}</Link>}
                {!n.read && <button type="button" className="text-fg-muted hover:text-fg" onClick={() => mark.mutate([n.id])}>읽음</button>}
              </div>
            </li>
          )
        })}
      </ul>
      <p className="mt-4 text-xs text-fg-muted">읽음 처리는 개인별 · 알림은 30일 보존 (MSG-001) · 끈 분류는 목록·개수에서 제외됩니다.</p>
    </Drawer>
  )
}

/** 알림 수신 설정 (SCR-B04 ①) — 분류별 끄기 · 채널(콘솔·메일·Slack) */
function NotifyPrefs({ onDone }: { onDone: () => void }) {
  const toast = useToast()
  const q = useGet('/api/v1/notifications/preferences')
  const [muted, setMuted] = useState<string[] | null>(null)
  const [channels, setChannels] = useState<Record<string, boolean> | null>(null)
  const save = useSend((b: unknown) => send('PUT', '/api/v1/notifications/preferences', b), ['/api/v1/notifications'])
  const cats: string[] = q.data?.categories ?? Object.keys(NOTE_CATS)
  const m: string[] = muted ?? q.data?.muted ?? []
  const ch: Record<string, boolean> = channels ?? q.data?.channels ?? {}
  return (
    <section aria-label="수신 설정" className="mb-4 space-y-3 rounded-md border border-line bg-surface-2/50 p-3 text-sm">
      <fieldset>
        <legend className="mb-1 text-xs font-semibold text-fg-muted">분류별 수신</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {cats.map((c) => (
            <label key={c} className="flex items-center gap-1.5">
              <input type="checkbox" checked={!m.includes(c)} onChange={(e) => setMuted(e.target.checked ? m.filter((x) => x !== c) : [...m, c])} />
              {NOTE_CATS[c]?.label ?? c}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend className="mb-1 text-xs font-semibold text-fg-muted">채널</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {[['inapp', '콘솔'], ['email', '메일'], ['slack', 'Slack']].map(([k, l]) => (
            <label key={k} className="flex items-center gap-1.5">
              <input type="checkbox" checked={!!ch[k]} onChange={(e) => setChannels({ ...ch, [k]: e.target.checked })} />{l}
            </label>
          ))}
        </div>
        <p className="mt-1 text-xs text-fg-muted">외부 발송(Slack 웹훅)은 플랫폼 알림 채널 설정(SCR-L06)을 따릅니다.</p>
      </fieldset>
      {save.error && <p role="alert" className="text-xs text-danger">{save.error.message}</p>}
      <div className="flex justify-end gap-2">
        <Button size="sm" onClick={onDone}>취소</Button>
        <Button size="sm" variant="primary" loading={save.isPending} disabled={!q.data}
          onClick={() => save.mutate({ muted: m, channels: ch }, { onSuccess: () => { toast('ok', '수신 설정을 저장했습니다.'); onDone() } })}>저장</Button>
      </div>
    </section>
  )
}

function ThemeToggle() {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem('anx.theme') || 'system' } catch { return 'system' }
  })
  useEffect(() => {
    const el = document.documentElement
    if (theme === 'system') el.removeAttribute('data-theme')
    else el.setAttribute('data-theme', theme)
    try { localStorage.setItem('anx.theme', theme) } catch { /* 무시 */ }
  }, [theme])
  const next = { system: 'light', light: 'dark', dark: 'system' }[theme] as string
  return <Button variant="ghost" size="sm" aria-label={`테마: ${theme}`} title={`테마: ${theme}`} onClick={() => setTheme(next)}>{theme === 'dark' ? '☾' : theme === 'light' ? '☀' : '◐'}</Button>
}

/** 상단 고정 배너 (CMP-16) — 에어갭(ST-09)·첫 실행 미완료·감사 체인 손상(MSG-022) */
export function GlobalBanners() {
  const q = useGet('/api/v1/system/banners', undefined, { refetchInterval: 60_000 })
  const items: { id: string; tone: 'info' | 'warn' | 'danger'; text: string; link?: string }[] = q.data?.items ?? []
  if (!items.length) return null
  const cls = { info: 'border-primary/30 bg-primary-soft text-primary', warn: 'border-warn/40 bg-warn/10 text-[#8a5a00] dark:text-warn', danger: 'border-danger/40 bg-danger/10 text-danger' }
  return (
    <div className="shrink-0 space-y-px" aria-label="전역 알림">
      {items.map((b) => (
        <div key={b.id} role={b.tone === 'danger' ? 'alert' : 'status'} className={cx('flex items-center gap-2 border-b px-4 py-1.5 text-xs', cls[b.tone])}>
          <span className="font-medium">{b.text}</span>
          {b.link && <Link to={b.link} className="ml-auto underline">자세히 →</Link>}
        </div>
      ))}
    </div>
  )
}

export function TopBar({ onSearch, extra }: { onSearch?: () => void; extra?: ReactNode }) {
  const { me, logout } = useAuth()
  const { t, lang, setLang } = useT()
  const nav = useNavigate()
  const [notif, setNotif] = useState(false)
  const [menu, setMenu] = useState(false)
  const unread = useGet('/api/v1/notifications', undefined, { refetchInterval: 30_000 })
  const pending = useGet('/api/v1/approvals', { status: 'pending' }, { refetchInterval: 15_000, enabled: can(me, 'chat:use') })
  const regPending = useGet('/api/v1/registry/approvals', undefined, { refetchInterval: 30_000, enabled: can(me, 'catalog:approve') })
  const count = (pending.data?.items?.length ?? 0) + (regPending.data?.items?.length ?? 0)
  const sys = useGet('/api/v1/system/info', undefined, { staleTime: 60_000 })
  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
      {onSearch && (
        <button type="button" onClick={onSearch} className="flex h-8 w-80 items-center gap-2 rounded-md border border-line bg-surface-2 px-3 text-left text-sm text-fg-muted hover:border-primary/40">
          <span aria-hidden>⌕</span><span className="flex-1 truncate">{t('top.search')}</span><kbd className="rounded border border-line px-1 text-[10px]">⌘K</kbd>
        </button>
      )}
      {extra}
      <div className="ml-auto flex items-center gap-2">
        {sys.data && <span title={`환경 ${sys.data.env} · DB ${sys.data.db_engine}${sys.data.airgap ? ' · 에어갭' : ''}`} className="flex items-center gap-1 text-xs">
          <Badge tone={sys.data.env === 'prod' ? 'danger' : 'ok'}>{sys.data.env}</Badge>{sys.data.airgap && <Badge tone="primary">에어갭</Badge>}
        </span>}
        <Link to={can(me, 'approvals:decide') || can(me, 'policy:write') ? '/console/gateway/approvals' : '/chat'}
          className={cx('rounded-full px-3 py-1 text-xs font-medium', count ? 'bg-warn/20 text-[#8a5a00] dark:text-warn' : 'bg-surface-2 text-fg-muted')}>
          {t('top.approvals')} {count}
        </Link>
        <Button variant="ghost" size="sm" aria-label={t('top.notifications')} onClick={() => setNotif(true)}>🔔 {unread.data?.unread ? <Badge tone="danger">{unread.data.unread}</Badge> : null}</Button>
        <select aria-label="언어" value={lang} onChange={(e) => setLang(e.target.value as Lang)} className="h-7 rounded border border-line bg-surface px-1 text-xs">
          <option value="ko">ko</option><option value="en">en</option>
        </select>
        <ThemeToggle />
        <div className="relative">
          <button type="button" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((m) => !m)} className="grid size-8 place-items-center rounded-full bg-primary-soft text-sm font-semibold text-primary">
            {(me?.name || '?').slice(0, 1)}
          </button>
          {menu && (
            <div role="menu" className="absolute right-0 z-50 mt-1 w-56 rounded-md border border-line bg-surface p-1 text-sm shadow-lg" onMouseLeave={() => setMenu(false)}>
              <div className="px-3 py-2">
                <div className="font-medium">{me?.name}</div>
                <div className="text-xs text-fg-muted">{me?.roles.map((r) => me.role_labels[r] || r).join(' · ')}</div>
              </div>
              <button role="menuitem" type="button" className="block w-full rounded px-3 py-1.5 text-left hover:bg-surface-2" onClick={() => { setMenu(false); nav('/console/me') }}>{t('top.me')}</button>
              <button role="menuitem" type="button" className="block w-full rounded px-3 py-1.5 text-left hover:bg-surface-2" onClick={() => { setMenu(false); nav('/chat') }}>{t('nav.chat')}</button>
              <button role="menuitem" type="button" className="block w-full rounded px-3 py-1.5 text-left text-danger hover:bg-surface-2" onClick={() => { logout(); nav('/login') }}>{t('top.logout')}</button>
            </div>
          )}
        </div>
      </div>
      <Notifications open={notif} onClose={() => setNotif(false)} />
    </header>
  )
}

export function Shell() {
  const [palette, setPalette] = useState(false)
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPalette((p) => !p)
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])
  return (
    <div className="flex h-full min-w-[1024px]">
      <SkipLink />
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onSearch={() => setPalette(true)} />
        <GlobalBanners />
        <main id="main" tabIndex={-1} className="min-h-0 flex-1 overflow-auto p-6 outline-none">
          <Outlet />
        </main>
      </div>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </div>
  )
}
