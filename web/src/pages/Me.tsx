// SCR-B06 내 계정·개인 액세스 토큰(PAT) — IdP 신원·역할·그룹 클레임 · 활성 세션 · 발급 시 1회만 평문 표시(스코프·최대 90일), 이후 마스킹
import { useState } from 'react'
import { Badge, Button, Card, CodeBlock, ErrorBox, Field, Input, Kv, Modal, PageHeader, StatusBadge, cx, useToast } from '@/components/ui'
import { can, useAuth, type Me as MeT } from '@/lib/auth'
import { fmtAgo, fmtTime } from '@/lib/format'
import { send, useGet, useSend } from '@/lib/query'

// PAT 스코프 = 권한 이름 (app/core/auth.py) · gateway:invoke 는 chat:use·models:read·catalog:read 묶음
const SCOPES: [string, string][] = [
  ['gateway:invoke', '게이트웨이 호출 (Model API·MCP·A2A)'], ['catalog:read', '카탈로그 조회'], ['catalog:write', '카탈로그 편집'], ['registry:publish', 'server.json 게시'],
  ['agents:read', '프로파일 조회'], ['agents:write', '프로파일 편집'], ['mcp:read', 'MCP 서버 조회'], ['mcp:write', 'MCP 서버 편집'], ['a2a:read', '원격 에이전트 조회'],
  ['models:read', '모델 조회'], ['observe:read', '트레이스·로그 조회'], ['cost:read', '비용 조회'], ['gateway:read', '게이트웨이 조회'], ['audit:read', '감사 로그 조회'], ['admin:read', '플랫폼 설정 조회'],
]
const scopeOk = (me: MeT, s: string) => (s === 'gateway:invoke' ? ['chat:use', 'models:read', 'catalog:read'].every((p) => can(me, p)) : can(me, s))
const CLIENTS: Record<string, string> = { 'agentnexus-console': '콘솔 (브라우저)', 'nexus-cli': 'CLI (nexus)', demo: 'demo 클라이언트' }
const MAX_TTL = 90 // API le=90
const SESSIONS_SHOWN = 6

export function Me() {
  const { me } = useAuth()
  const toast = useToast()
  const m = me as (MeT & { groups?: string[]; user_id?: string }) | null
  const tokens = useGet('/api/v1/me/tokens')
  const sessions = useGet('/api/v1/me/sessions')
  const detail = useGet(m?.user_id && can(me, 'identity:read') ? `/api/v1/identity/users/${m.user_id}/detail` : null)
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [days, setDays] = useState(30)
  const [scopes, setScopes] = useState<string[]>([])
  const [secret, setSecret] = useState('')
  const create = useSend((b: any) => send('POST', '/api/v1/me/tokens', b), ['/api/v1/me/tokens'])
  const revoke = useSend((id: string) => send('DELETE', `/api/v1/me/tokens/${id}`), ['/api/v1/me/tokens'])
  const revokeOthers = useSend(() => send('POST', '/api/v1/me/sessions/revoke-others'), ['/api/v1/me/sessions'])
  if (!me || !m) return null
  const now = Date.now()
  const list: any[] = tokens.data?.items ?? []
  const isExpired = (t: any) => !!t.expires_at && new Date(t.expires_at).getTime() < now
  const dup = list.some((t) => !t.revoked_at && !isExpired(t) && t.name === name.trim())
  const nameErr = dup ? '같은 이름의 활성 토큰이 있습니다 (MSG-004)' : undefined
  const daysErr = !Number.isInteger(days) || days < 1 || days > MAX_TTL ? `1~${MAX_TTL}일로 입력하세요 (MSG-004)` : undefined
  const sess: any[] = sessions.data?.items ?? []
  const d = detail.data
  const claims = d?.claims && Object.keys(d.claims).length ? d.claims : null
  const startIssue = () => { setOpen(true); setSecret(''); setName(''); setDays(30); setScopes([]) }
  return (
    <>
      <PageHeader title="내 계정·개인 액세스 토큰" crumbs={['개요', '내 계정']} />
      <div className="grid grid-cols-3 gap-4">
        <div className="space-y-4">
          <Card title="계정">
            <div className="mb-3 flex items-center gap-3">
              <span className="grid size-12 place-items-center rounded-full bg-primary-soft text-lg font-semibold text-primary" aria-hidden>{(me.name || '?').slice(0, 1)}</span>
              <div className="min-w-0"><div className="font-semibold">{me.name}</div><div className="truncate text-xs text-fg-muted" title="IdP 제공 · 콘솔에서 수정 불가">{me.email || '—'}</div></div>
            </div>
            <Kv rows={[
              ['IdP', d?.issuer ? <span className="mono text-xs">{d.issuer}</span> : me.sub.startsWith('ext:') ? '외부 IdP (연동)' : '내장 IdP'],
              ['sub', <span className="mono text-xs">{me.sub}</span>], ['팀', me.team || '—'],
              ['역할', <div className="flex flex-wrap gap-1">{me.roles.map((r) => <Badge key={r} tone="primary">{me.role_labels[r] || r}</Badge>)}</div>],
              ['그룹 클레임', m.groups?.length ? <div className="flex flex-wrap gap-1">{m.groups.map((g) => <Badge key={g}>{g}</Badge>)}</div> : <span className="text-fg-muted">없음</span>],
              ...(d ? [['MFA', d.mfa_enabled ? 'IdP 정책 ✓' : '미사용'], ['동기화', d.last_login_at ? `로그인 시 · ${fmtTime(d.last_login_at, true)}` : '—']] as [string, string][] : []),
              ['권한', <span className="text-xs text-fg-muted">{me.permissions.join(', ')}</span>]]} />
            <p className="mt-2 text-xs text-fg-muted">역할은 로그인할 때마다 IdP 그룹 클레임으로 갱신됩니다 (SCR-L03).</p>
            {claims && (
              <details className="mt-2 text-sm">
                <summary className="cursor-pointer text-xs text-primary">IdP 클레임 원문</summary>
                <CodeBlock value={claims} className="mt-1" maxH="max-h-48" />
              </details>
            )}
          </Card>
          <Card title={`활성 세션 ${sess.length}`}>
            {sessions.error && <ErrorBox error={sessions.error} />}
            <ul className="space-y-1.5 text-sm" aria-label="활성 세션">
              {sess.slice(0, SESSIONS_SHOWN).map((s) => (
                <li key={s.id} className="flex items-center gap-2" title={`${s.client_id} · ${s.scope} · 만료 ${fmtTime(s.expires_at, true)}`}>
                  <span className={cx('size-2 shrink-0 rounded-full', s.current ? 'bg-ok' : 'border border-fg-muted')} aria-hidden />
                  <span className={cx('truncate', s.current && 'font-medium')}>{s.current ? '이 브라우저 (가장 최근 세션)' : CLIENTS[s.client_id] ?? s.client_id}</span>
                  <span className="ml-auto shrink-0 text-xs text-fg-muted">{fmtAgo(s.created_at)}</span>
                </li>
              ))}
              {sess.length > SESSIONS_SHOWN && <li className="text-xs text-fg-muted">외 {sess.length - SESSIONS_SHOWN}개 세션</li>}
              {!sess.length && !sessions.isLoading && <li className="text-fg-muted">활성 세션이 없습니다.</li>}
            </ul>
            <Button className="mt-3 w-full" disabled={sess.length < 2} loading={revokeOthers.isPending} title={sess.length < 2 ? '다른 세션이 없습니다' : undefined}
              onClick={() => confirm(`다른 세션 ${sess.length - 1}개를 모두 종료할까요? 해당 브라우저·CLI 는 다시 로그인해야 합니다. (MSG-002)`)
                && revokeOthers.mutate(undefined, { onSuccess: (r: any) => toast('ok', `${r.revoked ?? 0}개 세션을 종료했습니다.`), onError: (e) => toast('error', e.message) })}>
              다른 세션 모두 종료
            </Button>
          </Card>
        </div>
        <Card title="개인 액세스 토큰 (PAT)" className="col-span-2" actions={<Button size="sm" variant="primary" onClick={startIssue}>토큰 발급</Button>}>
          {tokens.error && <ErrorBox error={tokens.error} />}
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-fg-muted"><tr><th className="py-1">이름</th><th>스코프</th><th>접두사</th><th>만료</th><th>마지막 사용</th><th>상태</th><th /></tr></thead>
            <tbody>
              {list.map((t: any) => {
                const exp = isExpired(t)
                return (
                  <tr key={t.id} className={cx('border-t border-line', (exp || t.revoked_at) && 'text-fg-muted')}>
                    <td className="py-1.5">{t.name}</td>
                    <td className="max-w-56"><div className="flex flex-wrap gap-1">{t.scopes?.length ? t.scopes.map((s: string) => <Badge key={s}>{s}</Badge>) : <span className="text-xs text-fg-muted">역할 전체</span>}</div></td>
                    <td className="mono text-xs">{t.prefix}••••</td><td>{fmtTime(t.expires_at, true)}</td><td>{fmtTime(t.last_used_at, true)}</td>
                    <td><StatusBadge status={t.revoked_at ? 'revoked' : exp ? 'expired' : 'active'} /></td>
                    <td className="text-right">{!t.revoked_at && !exp && <Button size="sm" variant="ghost" onClick={() => confirm(`토큰 "${t.name}" 을 폐기할까요? (MSG-002)`) && revoke.mutate(t.id, { onSuccess: () => toast('ok', '폐기했습니다.') })}>폐기</Button>}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-fg-muted">CLI: <code>nexus login --token anx_pat_…</code> · API: <code>Authorization: Bearer anx_pat_…</code> · 토큰 사용은 감사 로그에 기록 · 폐기는 즉시 게이트웨이에 반영</p>
        </Card>
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title="개인 액세스 토큰 발급"
        footer={secret ? <Button onClick={() => setOpen(false)}>닫기</Button> : <>
          <Button onClick={() => setOpen(false)}>취소</Button>
          <Button variant="primary" loading={create.isPending} disabled={!name.trim() || !!nameErr || !!daysErr}
            onClick={() => create.mutate({ name: name.trim(), ttl_days: days, scopes }, { onSuccess: (r: any) => setSecret(r.token) })}>발급</Button></>}>
        {secret ? (
          <div className="space-y-2 text-sm"><p className="text-warn">이 값은 지금만 표시됩니다. 안전한 곳에 보관하세요 (MSG-018).</p><CodeBlock value={secret} />
            <p className="text-xs text-fg-muted">스코프: {scopes.length ? scopes.join(', ') : '역할 전체'} · 만료 {days}일</p></div>
        ) : (
          <div className="space-y-3">
            <Field label="이름" error={nameErr}><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="laptop-cli" autoFocus /></Field>
            <Field label="만료(일)" hint={`최대 ${MAX_TTL}일`} error={daysErr}><Input type="number" min={1} max={MAX_TTL} value={days} onChange={(e) => setDays(Number(e.target.value))} /></Field>
            <fieldset>
              <legend className="mb-1 text-xs font-medium text-fg-muted">스코프 (내 역할 범위 안에서만)</legend>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
                {SCOPES.map(([s, label]) => {
                  const ok = scopeOk(me, s)
                  return (
                    <label key={s} className={cx('flex items-center gap-1.5', !ok && 'cursor-not-allowed opacity-50')} title={ok ? label : `권한 필요: ${s === 'gateway:invoke' ? 'chat:use' : s}`}>
                      <input type="checkbox" disabled={!ok} checked={scopes.includes(s)} onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))} />
                      <span className="mono text-xs">{s}</span>{!ok && <span className="text-xs">(권한 없음)</span>}
                    </label>
                  )
                })}
              </div>
              <p className="mt-1 text-xs text-fg-muted">선택하지 않으면 내 역할 권한 전체가 적용됩니다. 역할이 넓어도 토큰은 선택한 스코프 밖을 거부합니다.</p>
            </fieldset>
            {create.error && <ErrorBox error={create.error} />}
          </div>
        )}
      </Modal>
    </>
  )
}
