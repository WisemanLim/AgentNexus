// SCR-J03 사용자·그룹·역할 · J05 토큰 전파 추적(RFC 8693 교환 기록)
import { createColumnHelper } from '@tanstack/react-table'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { Button, CodeBlock, Drawer, Gate, Kv, Loading, Select, useToast } from '@/components/ui'
import { DataTable } from '@/components/DataTable'
import { Badge, Card, ErrorBox, PageHeader, StatusBadge, Tabs, TraceLink } from '@/components/ui'
import type { Json } from '@/lib/api'
import { fmtAgo, fmtTime } from '@/lib/format'
import { send, useGet } from '@/lib/query'

const col = createColumnHelper<Json>()

const ROLE_IDS = ['admin', 'operator', 'developer', 'security', 'finops', 'user']

// SCR-J03 사용자·역할 + SCR-L03 콘솔 역할·권한(RBAC) — 역할 변경은 다음 토큰부터 반영, IdP 그룹 매핑 사용자는 로그인 때 다시 동기화
const U = '/api/v1/identity/users'
const RBAC_PATH = '/console/admin/rbac'

/** 필수 사유 입력 (MSG-003) — 취소하면 null, 빈 값이면 다시 묻는다. */
function promptReason(msg: string): string | null {
  for (;;) {
    const v = window.prompt(msg)
    if (v === null) return null
    if (v.trim()) return v.trim()
  }
}

const Section = ({ title, actions, children }: { title: ReactNode; actions?: ReactNode; children: ReactNode }) => (
  <section className="border-t border-line pt-3">
    <div className="mb-2 flex items-center justify-between gap-2"><h3 className="text-xs font-semibold text-navy">{title}</h3>{actions}</div>
    {children}
  </section>
)

function UserDrawer({ u, onClose, onSaved }: { u: Json | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const d = useGet(u ? `${U}/${u.id}/detail` : null)
  const [roles, setRoles] = useState<string[]>(u?.roles ?? [])
  const [team, setTeam] = useState(u?.team ?? '')
  const [status, setStatus] = useState(u?.status ?? 'active')
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState<'revoke' | 'resync' | null>(null)
  if (!u) return null
  const det = d.data
  const external = det ? !!det.external : String(u.sub ?? '').startsWith('ext:')
  const sessions: Json[] = det?.sessions ?? []
  const who = u.name || u.username || u.sub
  const save = async () => {
    try {
      await send('PATCH', `${U}/${u.id}`, { roles, team: team || null, status })
      toast('ok', '저장했습니다 — 감사 기록')
      onSaved()
      onClose()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  // ⑦ 세션 강제 종료 — 갱신 토큰 폐기 + 그 전에 발급된 액세스 토큰 거부 (MSG-002 확인 · 사유 필수 MSG-003)
  const revoke = async () => {
    const reason = promptReason(`${who} 의 활성 세션 ${sessions.length}개를 모두 강제 종료할까요? 갱신 토큰이 폐기되고 이미 발급된 액세스 토큰도 거부됩니다 (MSG-002).\n\n강제 종료 사유를 입력하세요 (필수, MSG-003)`)
    if (!reason) return
    setBusy('revoke')
    try {
      const r = await send<Json>('POST', `${U}/${u.id}/sessions/revoke`, { reason })
      setNote(`세션 ${r.revoked}개를 강제 종료했습니다 — 사용자는 채팅·콘솔에서 다시 로그인해야 합니다 (MSG-024). 감사 기록됨.`)
      d.refetch()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(null)
    }
  }
  // ⑤ 재동기화 — 마지막 IdP 클레임 + 현재 그룹→역할 매핑으로 역할을 다시 계산
  const resync = async () => {
    setBusy('resync')
    try {
      const r = await send<Json>('POST', `${U}/${u.id}/resync`)
      const before: string[] = r.before ?? []
      const after: string[] = r.after ?? []
      const changed = before.filter((x) => !after.includes(x)).length + after.filter((x) => !before.includes(x)).length
      setRoles(after)
      setNote(`재동기화 (${r.idp}) — ${before.join(', ') || '없음'} → ${after.join(', ') || '없음'} · 변경 ${changed}건 (MSG-001)`)
      d.refetch()
      onSaved()
    } catch (e: any) {
      toast('error', e.code === 'not_external' ? '외부 IdP 사용자만 재동기화할 수 있습니다 — 로컬 계정은 역할을 직접 편집하세요.' : e.message)
    } finally {
      setBusy(null)
    }
  }
  const claims = det?.claims ?? {}
  const perms: string[] = det?.permissions ?? []
  return (
    <Drawer open onClose={onClose} title={`${who} — 상세 · 역할·상태`} width="w-[520px]">
      <div className="space-y-4 text-sm">
        <div className="text-xs text-fg-muted">{u.email} · {external ? <>외부 IdP <span className="mono">{u.issuer}</span></> : '내장 IdP (로컬 계정)'}</div>
        {d.error ? <ErrorBox error={d.error} /> : null}
        <Kv rows={[['sub', <span className="mono text-xs">{u.sub}</span>], ['그룹', (u.groups ?? []).join(', ') || '—'],
          ['MFA', det ? (det.mfa_enabled ? '사용' : '미사용') : '—'], ['마지막 로그인', fmtAgo(u.last_login_at)],
          ['토큰 기준 시각', det?.tokens_not_before ? <span title="이 시각 이전에 발급된 액세스 토큰은 거부">{fmtTime(det.tokens_not_before, true)}</span> : '—']]} />
        {note && <div role="status" className="rounded-md border border-ok/40 bg-ok/10 px-3 py-2 text-xs">{note}</div>}

        <Section title={`활성 세션 ${det ? sessions.length : ''}`}
          actions={<Gate perm="identity:write">{(ok, why) => <Button size="sm" variant="danger" disabled={!ok || !det} title={why} loading={busy === 'revoke'} onClick={revoke}>세션 강제 종료</Button>}</Gate>}>
          {!det ? <Loading rows={2} /> : sessions.length ? (
            <ul className="space-y-1" aria-label="활성 세션">
              {sessions.map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-2 rounded border border-line px-2 py-1 text-xs">
                  <span><span className="mono">{s.client_id}</span> <span className="text-fg-muted">{s.scope}</span></span>
                  <span className="text-fg-muted" title={`만료 ${fmtTime(s.expires_at, true)}`}>{fmtAgo(s.created_at)} 시작</span>
                </li>
              ))}
            </ul>
          ) : <p className="text-xs text-fg-muted">활성 세션 없음 — 강제 종료하면 이미 발급된 액세스 토큰도 거부됩니다.</p>}
          <p className="mt-2 rounded-md border border-primary/30 bg-primary-soft px-2 py-1.5 text-[11px]">세션이 끝나면 채팅·콘솔에 재로그인 안내가 표시되고, 진행 중 입력은 보존됩니다 (MSG-024).</p>
        </Section>

        <Section title="토큰 클레임 (마지막 IdP 로그인)">
          {Object.keys(claims).length ? <CodeBlock value={claims} maxH="max-h-56" />
            : <p className="text-xs text-fg-muted">{external ? '저장된 클레임이 없습니다 — 다음 IdP 로그인 때 기록됩니다.' : '로컬 계정 — 외부 IdP 클레임이 없습니다.'}</p>}
        </Section>

        <Section title={`실효 권한 ${perms.length || ''}`} actions={<Link className="text-xs text-primary hover:underline" to={RBAC_PATH}>RBAC 권한 행렬에서 보기 →</Link>}>
          <div className="flex flex-wrap gap-1">{perms.map((p) => <Badge key={p}>{p}</Badge>)}</div>
        </Section>

        <Section title="콘솔 역할 (직접 편집)"
          actions={<Gate perm="identity:write">{(ok, why) => <Button size="sm" disabled={!ok || !external || (!!det && !claims.idp)} loading={busy === 'resync'} onClick={resync}
            title={why ?? (!external ? '로컬 계정은 재동기화 대상이 아닙니다 (not_external) — 역할을 직접 편집하세요'
              : det && !claims.idp ? '저장된 IdP 로그인 클레임이 없습니다 — 다음 로그인 후 재동기화할 수 있습니다' : 'IdP 그룹 클레임으로 역할을 지금 다시 계산합니다')}>지금 재동기화</Button>}</Gate>}>
          <fieldset><legend className="sr-only">콘솔 역할</legend>
            {ROLE_IDS.map((r) => <label key={r} className="mr-3 inline-flex items-center gap-1"><input type="checkbox" checked={roles.includes(r)} onChange={(e) => setRoles(e.target.checked ? [...roles, r] : roles.filter((x) => x !== r))} />{r}</label>)}
          </fieldset>
          {external && <p className="mt-2 text-xs text-warn">외부 IdP 사용자는 로그인 때 그룹 매핑으로 역할이 다시 정해집니다 — 바로 반영하려면 재동기화 (SCR-J02).</p>}
          <label className="mt-3 block text-xs text-fg-muted">팀<input className="mt-1 block h-8 w-full rounded-md border border-line bg-surface px-2 text-sm" value={team} onChange={(e) => setTeam(e.target.value)} /></label>
          <label className="mt-2 block text-xs text-fg-muted">상태<Select className="mt-1" value={status} onChange={(e) => setStatus(e.target.value)}><option value="active">active</option><option value="disabled">disabled</option></Select></label>
          <div className="mt-3"><Gate perm="identity:write">{(ok, why) => <Button variant="primary" disabled={!ok || !roles.length} title={why} onClick={save}>저장</Button>}</Gate></div>
        </Section>
      </div>
    </Drawer>
  )
}

export function Users() {
  const users = useGet(U)
  const roles = useGet('/api/v1/identity/roles')
  const [tab, setTab] = useState<'users' | 'roles'>('users')
  const [sel, setSel] = useState<Json | null>(null)
  const columns = [
    col.accessor('name', { header: '이름', cell: ({ row }) => <div><div className="font-medium">{row.original.name}</div><div className="text-[11px] text-fg-muted">{row.original.email}</div></div> }),
    col.accessor('username', { header: '계정', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
    col.accessor('roles', { header: '역할', enableSorting: false, cell: (c) => (c.getValue() ?? []).map((r: string) => <Badge key={r} tone="primary" className="mr-1">{r}</Badge>) }),
    col.accessor('groups', { header: '그룹', enableSorting: false, cell: (c) => <span className="text-xs">{(c.getValue() ?? []).join(', ') || '—'}</span> }),
    col.accessor('team', { header: '팀' }),
    col.accessor('issuer', { header: 'IdP', cell: (c) => <span className="mono text-[11px]">{c.getValue() || '내장'}</span> }),
    col.accessor('status', { header: '상태', cell: (c) => <StatusBadge status={c.getValue()} /> }),
    col.accessor('last_login_at', { header: '마지막 로그인', cell: (c) => fmtAgo(c.getValue()) }),
  ]
  return (
    <>
      <PageHeader title="사용자·그룹·역할" crumbs={['Identity', '사용자']} desc="역할은 IdP 그룹 클레임 매핑으로 로그인마다 동기화됩니다 (SCR-L03 콘솔 RBAC)."
        actions={<Link to={RBAC_PATH}><Button>권한 행렬(RBAC) →</Button></Link>} />
      <Tabs tabs={[{ id: 'users', label: `사용자 ${users.data?.items?.length ?? ''}` }, { id: 'roles', label: '역할·권한' }]} value={tab} onChange={setTab} />
      {tab === 'users' ? (users.error ? <ErrorBox error={users.error} /> : <DataTable data={users.data?.items ?? []} columns={columns} onRowClick={setSel} />) : (
        <div className="grid grid-cols-3 gap-3">
          {(roles.data?.roles ?? []).map((r: Json) => (
            <Card key={r.id} title={<>{r.label} <span className="mono text-xs text-fg-muted">{r.id}</span></>}>
              <div className="flex flex-wrap gap-1">{(r.permissions ?? []).map((p: string) => <Badge key={p}>{p}</Badge>)}</div>
            </Card>
          ))}
        </div>
      )}
      <UserDrawer key={sel?.id} u={sel} onClose={() => setSel(null)} onSaved={() => users.refetch()} />
    </>
  )
}

export function Propagation() {
  const q = useGet('/api/v1/identity/token-exchanges', { limit: 500 }, { refetchInterval: 20_000 })
  const columns = [
    col.accessor('ts', { header: '시각', cell: (c) => fmtTime(c.getValue(), true) }),
    col.accessor('subject', { header: '주체(sub)', cell: ({ row }) => <span className="text-xs">{row.original.subject} <span className="text-fg-muted">{row.original.subject_kind}</span></span> }),
    col.accessor('actor', { header: '행위자(act)', cell: (c) => <span className="mono text-xs">{c.getValue() || '—'}</span> }),
    col.accessor('audience', { header: 'audience', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
    col.accessor('scopes', { header: '스코프', enableSorting: false, cell: (c) => <span className="mono text-xs">{(c.getValue() ?? []).join(' ')}</span> }),
    col.accessor('ttl_seconds', { header: 'TTL', cell: (c) => `${c.getValue()}s` }),
    col.accessor('decision', { header: '결정', cell: (c) => <StatusBadge status={c.getValue() === 'issued' ? 'allow' : 'deny'} /> }),
    col.accessor('reason', { header: '사유', cell: (c) => <span className="text-xs">{c.getValue() || ''}</span> }),
    col.accessor('trace_id', { header: 'trace', cell: (c) => <TraceLink id={c.getValue()} /> }),
  ]
  return (
    <>
      <PageHeader title="토큰 전파 추적" crumbs={['Identity', '토큰 전파']} desc="사용자 신원이 게이트웨이 → MCP·A2A 로 도구별 최소 스코프 토큰(RFC 8693)으로 교환된 기록 (FR-I02)" />
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={q.data?.items ?? []} columns={columns} empty="교환 기록이 없습니다." />
    </>
  )
}
