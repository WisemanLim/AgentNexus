// SCR-B03 전역 검색·명령 팔레트(⌘K) — 범위 탭(전체·결과 유형·명령) · 권한 범위 결과만(/api/v1/search) · 명령(권한 없으면 회색, ST-04) · 빠른 이동
import { Command } from 'cmdk'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router'
import { can, useAuth } from '@/lib/auth'
import { useT } from '@/lib/i18n'
import { useGet } from '@/lib/query'
import { NAV } from './Shell'
import { TypeBadge, cx } from './ui'

const GROUPS: Record<string, string> = { catalog: '카탈로그', tools: 'MCP 도구', agents: '에이전트 프로파일', a2a: '원격 에이전트', sessions: '대화', users: '사용자', policies: '정책', traces: '트레이스' }
// trace_id 형식이면 trace 결과를 먼저 (SCR-B03 ①)
const ORDER = ['traces', 'catalog', 'tools', 'agents', 'a2a', 'policies', 'sessions', 'users']

// 명령 (SCR-B03 ⑥) — 역할 권한 없는 명령은 숨기지 않고 회색 + 사유
const COMMANDS: { id: string; label: string; scr: string; to: string; perm?: string }[] = [
  { id: 'use', label: '즉시 사용 — 카탈로그에서 골라 바로 쓰기', scr: 'E03', to: '/console/catalog', perm: 'catalog:read' },
  { id: 'publish', label: 'server.json 등록', scr: 'E04', to: '/console/registry/publish', perm: 'registry:publish' },
  { id: 'profile', label: '새 프로파일 (템플릿에서 만들기)', scr: 'D01', to: '/console/agents', perm: 'agents:write' },
  { id: 'simulate', label: '정책 시뮬레이터 (정책 선택 → 시뮬레이터 탭)', scr: 'H08', to: '/console/gateway/policies', perm: 'gateway:read' },
  { id: 'mcp-openapi', label: '새 MCP 서버 — Internal API(OpenAPI)', scr: 'F04', to: '/console/mcp-servers/new/openapi', perm: 'mcp:write' },
  { id: 'a2a-register', label: '원격 에이전트 등록 (Agent Card)', scr: 'G02', to: '/console/a2a?register=1', perm: 'a2a:write' },
  { id: 'approvals', label: '사람 승인 대기열', scr: 'H12', to: '/console/gateway/approvals', perm: 'approvals:decide' },
  { id: 'reg-approvals', label: '등록 승인 대기열', scr: 'E05', to: '/console/registry/approvals', perm: 'catalog:approve' },
  { id: 'traces', label: '트레이스 탐색', scr: 'K02', to: '/console/observe/traces', perm: 'observe:read' },
  { id: 'pat', label: '개인 액세스 토큰 발급', scr: 'B06', to: '/console/me' },
  { id: 'settings', label: '플랫폼 설정', scr: 'L06', to: '/console/admin', perm: 'admin:read' },
]

const itemCls = 'flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm text-fg data-[selected=true]:bg-primary-soft data-[disabled=true]:cursor-not-allowed data-[disabled=true]:opacity-50'
const groupCls = 'mb-1 text-xs text-fg-muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1'

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate()
  const { me } = useAuth()
  const { t } = useT()
  const input = useRef<HTMLInputElement>(null)
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [scope, setScope] = useState('all')
  const [sel, setSel] = useState('')
  useEffect(() => {
    const h = setTimeout(() => setDebounced(q.trim()), 150)
    return () => clearTimeout(h)
  }, [q])
  useEffect(() => {
    if (!open) {
      setQ('')
      setScope('all')
    }
  }, [open])
  const searching = open && debounced.length >= 2
  const res = useGet('/api/v1/search', { q: debounced }, { enabled: searching })
  const group = scope !== 'all' && scope !== 'commands' ? scope : null
  const scoped = useGet('/api/v1/search', { q: debounced, limit: 20 }, { enabled: searching && !!group }) // 탭별 최대 20건
  const go = (to: string) => {
    onClose()
    nav(to)
  }
  const results: Record<string, any[]> = res.data?.results ?? {}
  const present = ORDER.filter((g) => results[g]?.length).concat(Object.keys(results).filter((g) => !ORDER.includes(g) && results[g]?.length))
  const tabs = ['all', ...present, ...(group && !present.includes(group) ? [group] : []), 'commands']
  const shown: [string, any[]][] = group ? [[group, (scoped.data?.results ?? results)[group] ?? []]] : scope === 'all' ? present.map((g) => [g, results[g]]) : []
  const ql = q.trim().toLowerCase()
  const cmds = scope === 'all' || scope === 'commands' ? COMMANDS.filter((c) => !ql || c.label.toLowerCase().includes(ql)) : []
  const cmdTo = new Set(cmds.filter((c) => can(me, c.perm)).map((c) => c.to))
  const navs = scope === 'all' || scope === 'commands'
    ? NAV.filter((n) => can(me, n.perm)).flatMap((n) => n.sub?.length ? n.sub.filter((s) => can(me, s.perm || n.perm)).map((s) => ({ to: s.to, label: `${t(n.key)} › ${s.label}` })) : [{ to: n.to, label: t(n.key) }])
      .filter((x) => !cmdTo.has(x.to) && (!ql || x.label.toLowerCase().includes(ql)))
    : []
  const links = new Map<string, string>()
  shown.forEach(([g, items]) => items.forEach((it) => links.set(`${g}:${it.id}`, it.link)))
  cmds.forEach((c) => can(me, c.perm) && links.set(`cmd:${c.id}`, c.to))
  navs.forEach((x) => links.set(`nav:${x.to}`, x.to))
  const fetching = res.isFetching || (!!group && scoped.isFetching)
  const noResults = searching && !fetching && !shown.some(([, v]) => v?.length) && scope !== 'commands'
  const onKeyDown = (e: KeyboardEvent) => { // ⌘↵ 새 탭
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && links.get(sel)) {
      e.preventDefault()
      window.open(links.get(sel), '_blank', 'noopener')
    }
  }
  const pick = (s: string) => {
    setScope(s)
    input.current?.focus()
  }
  return (
    <Command.Dialog open={open} onOpenChange={(o) => !o && onClose()} label="전역 검색" shouldFilter={false} value={sel} onValueChange={setSel} onKeyDown={onKeyDown}
      overlayClassName="fixed inset-0 z-50 bg-black/40"
      contentClassName="fixed top-[15vh] left-1/2 z-50 w-[640px] max-w-[92vw] -translate-x-1/2 overflow-hidden rounded-xl border border-line bg-surface shadow-2xl">
      <Command.Input ref={input} value={q} onValueChange={setQ} placeholder={`${t('top.search')} (2자 이상)`} className="h-12 w-full border-b border-line bg-transparent px-4 text-sm outline-none" />
      <div role="tablist" aria-label="검색 범위" className="flex gap-1 overflow-x-auto border-b border-line px-2">
        {tabs.map((s) => (
          <button key={s} type="button" role="tab" aria-selected={scope === s} onClick={() => pick(s)}
            className={cx('-mb-px shrink-0 border-b-2 px-3 py-1.5 text-xs', scope === s ? 'border-primary font-semibold text-primary' : 'border-transparent text-fg-muted hover:text-fg')}>
            {s === 'all' ? '전체' : s === 'commands' ? '명령' : GROUPS[s] ?? s}
          </button>
        ))}
      </div>
      <Command.List className="max-h-[55vh] overflow-auto p-2">
        {noResults && (
          <div className="p-4 text-sm text-fg-muted">결과 없음 — 권한 밖 항목은 표시되지 않습니다.
            {can(me, 'catalog:read') && <button type="button" className="ml-2 text-primary hover:underline" onClick={() => go(`/console/catalog?q=${encodeURIComponent(debounced)}`)}>카탈로그 열기 →</button>}
          </div>
        )}
        {shown.filter(([, v]) => v?.length).map(([g, items]) => (
          <Command.Group key={g} heading={GROUPS[g] ?? g} className={groupCls}>
            {items.map((it) => (
              <Command.Item key={`${g}:${it.id}`} value={`${g}:${it.id}`} onSelect={() => go(it.link)} className={itemCls}>
                <TypeBadge type={it.type} /><span className="truncate">{it.title}</span><span className="ml-auto mono text-[11px] text-fg-muted">{it.id}</span>
              </Command.Item>
            ))}
          </Command.Group>
        ))}
        {cmds.length > 0 && (
          <Command.Group heading="명령" className={groupCls}>
            {cmds.map((c) => {
              const ok = can(me, c.perm)
              return (
                <Command.Item key={c.id} value={`cmd:${c.id}`} disabled={!ok} onSelect={() => go(c.to)} className={itemCls} title={ok ? undefined : `${t('common.noperm')}: ${c.perm}`}>
                  <span aria-hidden className="text-fg-muted">›</span><span className="truncate">{c.label}</span>
                  <span className="ml-auto text-[11px] text-fg-muted">{ok ? c.scr : `권한 필요: ${c.perm}`}</span>
                </Command.Item>
              )
            })}
          </Command.Group>
        )}
        {navs.length > 0 && (
          <Command.Group heading="이동" className="text-xs text-fg-muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1">
            {navs.map((x) => (
              <Command.Item key={x.to} value={`nav:${x.to}`} onSelect={() => go(x.to)} className="cursor-pointer rounded px-2 py-1.5 text-sm text-fg data-[selected=true]:bg-primary-soft">{x.label}</Command.Item>
            ))}
          </Command.Group>
        )}
      </Command.List>
      <div className="border-t border-line px-4 py-1.5 text-[11px] text-fg-muted">↑↓ 이동 · ↵ 열기 · ⌘↵ 새 탭 · esc 닫기 · 권한 없는 명령은 회색</div>
    </Command.Dialog>
  )
}
