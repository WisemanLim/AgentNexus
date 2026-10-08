// SCR-E01 통합 카탈로그 · E02 항목 상세 · E03 즉시 사용 위저드 · E05 등록 승인 대기열 · E07 버전·변경 이력
import { createColumnHelper } from '@tanstack/react-table'
import { useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, CodeBlock, Drawer, Empty, ErrorBox, Field, Gate, Input, Kv, Loading, Modal, PageHeader, RiskChip, Select, StatusBadge, Stepper, Tabs, Textarea, type Tone, TypeBadge, askReason, cx, useToast } from '@/components/ui'
import type { Json } from '@/lib/api'
import { fmtAgo, fmtTime } from '@/lib/format'
import { send, useGet } from '@/lib/query'

const col = createColumnHelper<Json>()
const TYPES = [['', '전체'], ['mcp_server', 'MCP 서버'], ['a2a_agent', 'A2A 에이전트'], ['model', '모델'], ['agent_profile', '프로파일']] as const
const LICENSE: Record<string, Tone> = { low: 'ok', medium: 'warn', high: 'danger', unknown: 'muted' }
const TRUST: Record<string, Tone> = { official: 'ok', partner: 'primary', community: 'warn', external: 'muted' }
export const TrustTag = ({ trust }: { trust?: string | null }) => trust ? <Badge tone={TRUST[trust] ?? 'muted'}>{trust}</Badge> : <span className="text-xs text-fg-muted">—</span>
const FILTERS = {
  status: ['active', 'approved', 'pending', 'deprecated'],
  auth: ['oauth2', 'api_key', 'bearer', 'none'],
  trust: ['official', 'partner', 'community', 'external'],
} as const
const FILTER_LABEL = { status: '상태', auth: '인증 방식', trust: '신뢰 등급' }

export function Catalog() {
  const nav = useNavigate()
  const [sp, setSp] = useSearchParams()
  const type = sp.get('type') || ''
  const [text, setText] = useState(sp.get('q') || '')
  useEffect(() => {  // 150ms 디바운스 — 검색어는 URL 에 보존
    const t = setTimeout(() => {
      if ((sp.get('q') || '') === text) return
      const next = new URLSearchParams(sp)
      if (text) next.set('q', text)
      else next.delete('q')
      setSp(next, { replace: true })
    }, 150)
    return () => clearTimeout(t)
  }, [text, sp, setSp])
  const picked = (k: keyof typeof FILTERS) => (sp.get(k) || '').split(',').filter(Boolean)
  const toggle = (k: keyof typeof FILTERS, v: string) => {
    const cur = picked(k)
    const nxt = cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]
    const p = new URLSearchParams(sp)
    if (nxt.length) p.set(k, nxt.join(','))
    else p.delete(k)
    setSp(p, { replace: true })
  }
  const filtered = Object.keys(FILTERS).some((k) => sp.get(k))
  const q = useGet('/v1/catalog', { type: type || undefined, q: sp.get('q') || undefined, status: sp.get('status') || undefined,
    auth: sp.get('auth') || undefined, trust: sp.get('trust') || undefined, limit: 500 })
  const exposures = useGet('/v1/catalog/exposures', undefined, { refetchInterval: 30_000 })
  const columns = [
    col.accessor('type', { header: '유형', cell: (c) => <TypeBadge type={c.getValue()} /> }),
    col.accessor('display_name', { header: '이름', cell: ({ row }) => <div><div className="font-medium">{row.original.display_name}</div>
      <div className="mono text-[11px] text-fg-muted">{row.original.name}</div>
      {row.original.match && <div className="text-[11px] text-primary">{row.original.match}</div>}</div> }),
    col.accessor('version', { header: '버전' }),
    col.accessor('status', { header: '상태', cell: (c) => <StatusBadge status={c.getValue()} /> }),
    col.accessor('auth_scheme', { header: '인증', cell: (c) => <span className="text-xs">{c.getValue() || 'none'}</span> }),
    col.accessor('trust', { header: '신뢰', cell: (c) => <TrustTag trust={c.getValue()} /> }),
    col.accessor('license', { header: '라이선스', cell: ({ row }) => <Badge tone={LICENSE[row.original.license_risk] ?? 'muted'}>{row.original.license || '미상'}</Badge> }),
    col.accessor('instance_id', { header: '등록', cell: (c) => c.getValue() ? <Badge tone="ok">사용 중</Badge> : '—' }),
    col.accessor('updated_at', { header: '갱신', cell: (c) => fmtAgo(c.getValue()) }),
  ]
  const total = Object.values(q.data?.counts ?? {}).reduce((a: number, b) => a + (b as number), 0)
  return (
    <>
      <PageHeader title="통합 카탈로그" crumbs={['카탈로그']} desc={`MCP 서버·원격 A2A 에이전트(Agent Card)·모델·에이전트 프로파일을 한 목록에서 검색 — ${total}개 항목`}
        actions={<><Button onClick={() => nav('/console/catalog/sbom')}>SBOM·라이선스</Button>
          <Gate perm="a2a:write">{(ok, why) => <Button disabled={!ok} title={why} onClick={() => nav('/console/a2a?register=1')}>+ Agent Card 등록</Button>}</Gate>
          <Gate perm="registry:publish">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => nav('/console/registry/publish')}>+ server.json 등록</Button>}</Gate></>} />
      <div className="mb-3">
        <Input aria-label="카탈로그 검색" value={text} onChange={(e) => setText(e.target.value)} placeholder="이름·태그·도구·스킬·보안 스킴 — 예: jira tag:이슈 auth:oauth2 trust:partner" />
      </div>
      <Tabs tabs={TYPES.map(([id, l]) => ({ id, label: <>{l} {id && q.data?.counts ? <span className="text-xs text-fg-muted">{q.data.counts[id]}</span> : null}</> }))} value={type}
        onChange={(v) => { const p = new URLSearchParams(sp); if (v) p.set('type', v); else p.delete('type'); setSp(p) }} />
      <div className="grid grid-cols-[200px_1fr_260px] gap-4">
        <aside aria-label="필터" className="space-y-4 text-sm">
          {(Object.keys(FILTERS) as (keyof typeof FILTERS)[]).map((k) => (
            <fieldset key={k}>
              <legend className="mb-1 text-xs font-semibold text-fg-muted">{FILTER_LABEL[k]}</legend>
              {FILTERS[k].map((v) => <label key={v} className="flex items-center gap-2 py-0.5"><input type="checkbox" checked={picked(k).includes(v)} onChange={() => toggle(k, v)} />{v}</label>)}
            </fieldset>
          ))}
          {filtered && <Button size="sm" onClick={() => { const p = new URLSearchParams(sp); Object.keys(FILTERS).forEach((k) => p.delete(k)); setSp(p) }}>필터 초기화</Button>}
        </aside>
        <div className="min-w-0">
          {q.error && <ErrorBox error={q.error} onRetry={() => q.refetch()} />}
          {q.isLoading ? <Loading /> : (q.data?.items ?? []).length === 0
            ? <Empty action={<Button onClick={() => nav('/console/registry/publish')}>server.json 등록</Button>}>조건에 맞는 항목이 없습니다 (ST-02) — 검색어·필터를 바꾸거나 새로 등록하세요.</Empty>
            : <DataTable data={q.data?.items ?? []} columns={columns} onRowClick={(r) => nav(`/console/catalog/${r.id}`)} />}
        </div>
        <Card title="등록 → 노출 현황">
          <ul className="space-y-2 text-xs">
            {(exposures.data?.items ?? []).map((e: Json) => (
              <li key={`${e.item_id}@${e.version}`} className="flex flex-col">
                <span className="flex items-center gap-1"><TypeBadge type={e.type} /><span className="font-medium">{e.name}</span><span className="text-fg-muted">{e.version}</span></span>
                {e.status === 'pending' ? <span className="text-warn">승인 대기</span>
                  : <span className="text-ok">승인 {fmtTime(e.decided_at)} · 카탈로그 노출 {e.exposed_after_s != null ? `+${e.exposed_after_s}s` : '즉시'} (≤60s)</span>}
              </li>
            ))}
            {!exposures.data?.items?.length && <li className="text-fg-muted">최근 등록이 없습니다.</li>}
          </ul>
        </Card>
      </div>
    </>
  )
}

export function CatalogItem() {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const [sp, setSp] = useSearchParams()
  const q = useGet(`/v1/catalog/${id}`)
  const versions = useGet(`/v1/catalog/${id}/versions`)
  const usage = useGet(`/v1/catalog/${id}/usage`)
  const tab = (sp.get('tab') || 'info') as 'info' | 'meta' | 'tools' | 'versions' | 'usage' | 'history'
  const [wizard, setWizard] = useState(false)
  if (q.error) return <ErrorBox error={q.error} />
  if (!q.data) return <Loading />
  const i = q.data
  const pending = (versions.data?.items ?? []).filter((v: Json) => v.status === 'pending')
  const meta = i.metadata ?? {}
  const card = meta.card ?? meta
  const tools: string[] = i.type === 'mcp_server' ? (meta._meta?.tools ?? []) : (card.skills ?? []).map((s: Json) => s.id)
  const gateway = i.type === 'mcp_server' ? `/mcp/${i.instance_id ?? '{id}'}` : i.type === 'a2a_agent' ? `/a2a/${i.instance_id ?? '{id}'}` : i.type === 'agent_profile' ? `/a2a/platform/${i.name}` : '/v1/chat/completions'
  return (
    <>
      <PageHeader title={i.display_name} crumbs={['카탈로그', i.type]} desc={i.description}
        actions={<>
          <TypeBadge type={i.type} /><StatusBadge status={i.status} /><TrustTag trust={i.trust} /><Badge>v{i.version}</Badge>
          <Badge tone={LICENSE[i.license_risk] ?? 'muted'}>{i.license || '미상'}</Badge>
          {pending.length > 0 && <Gate perm="catalog:approve">{(ok, why) => <Button size="sm" disabled={!ok} title={why} onClick={() => nav('/console/registry/approvals')}>v{pending[0].version} 심사 →</Button>}</Gate>}
          <Button size="sm" onClick={() => setSp({ tab: 'versions' })}>버전 이력</Button>
          {['approved', 'active'].includes(i.status) && ['mcp_server', 'a2a_agent'].includes(i.type) && <Gate perm="catalog:write">{(ok, why) => <Button size="sm" variant="primary" disabled={!ok} title={why || undefined} onClick={() => setWizard(true)}>즉시 사용</Button>}</Gate>}
          {i.instance_id && i.type === 'mcp_server' && <Button size="sm" onClick={() => nav(`/console/mcp-servers/${i.instance_id}`)}>인스턴스 →</Button>}
          {i.instance_id && i.type === 'a2a_agent' && <Button size="sm" onClick={() => nav(`/console/a2a/${i.instance_id}`)}>인스턴스 →</Button>}
        </>} />
      {usage.data?.pending_version && <div role="status" className="mb-3 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm">새 버전 {usage.data.pending_version} 이 승인 대기 중입니다 — 현재 사용 버전은 {i.pinned_version ? `${i.pinned_version}(고정)` : i.version}.</div>}
      <Tabs tabs={[{ id: 'info', label: '개요' }, { id: 'meta', label: i.type === 'mcp_server' ? 'server.json' : i.type === 'model' ? '공급자 메타데이터' : 'agent-card.json' },
        { id: 'tools', label: `${i.type === 'mcp_server' ? '도구' : '스킬'} ${tools.length}` }, { id: 'versions', label: `버전 ${versions.data?.items?.length ?? ''}` },
        { id: 'usage', label: `사용처 ${usage.data?.items?.length ?? ''}` }, { id: 'history', label: '변경 이력' }]} value={tab} onChange={(v) => setSp(v === 'info' ? {} : { tab: v })} />
      {tab === 'info' && (
        <div className="grid grid-cols-2 gap-4">
          <Card title="메타데이터 (CatalogItem)"><Kv rows={[['ID', <span className="mono text-xs">{i.id}</span>], ['이름', <span className="mono">{i.name}</span>], ['유형', i.type], ['버전', <>{i.version}{i.pinned_version && <Badge tone="primary" className="ml-1">고정 {i.pinned_version}</Badge>}</>],
            ['상태', <StatusBadge status={i.status} />], ['출처', i.source], ['소유자', i.owner || '—'], ['인증', i.auth_scheme || 'none'], ['신뢰', <TrustTag trust={i.trust} />],
            ['라이선스', <Badge tone={LICENSE[i.license_risk] ?? 'muted'}>{i.license || '미상'} · 위험 {i.license_risk}</Badge>], ['서명', i.signature_verified ? '검증됨' : '미서명'],
            ['태그', <div className="flex flex-wrap gap-1">{(i.tags ?? []).map((t: string) => <Badge key={t}>{t}</Badge>)}</div>], ['갱신', fmtTime(i.updated_at, true)]]} /></Card>
          <Card title="Discovery only">
            <p className="text-sm">레지스트리는 메타데이터만 제공합니다 — 실행 호출은 게이트웨이 <span className="mono">{gateway}</span> 로만 갑니다.</p>
            {i.type === 'mcp_server' && <p className="mt-2 text-xs text-fg-muted">발견: <span className="mono">GET /v0/servers/{encodeURIComponent(i.name)}</span></p>}
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-fg-muted">
              <li>승인 전(pending)에는 /v0/servers·채팅에 노출되지 않습니다.</li>
              <li>즉시 사용 = 등록 → 인증(토큰 교환) → 정책 기본값(읽기 허용·쓰기 명시 허용·고위험 사람 승인) → 60초 노출 확인.</li>
            </ul>
          </Card>
        </div>
      )}
      {tab === 'meta' && <CodeBlock value={meta} maxH="max-h-[600px]" />}
      {tab === 'tools' && <Card bodyClass="p-0"><table className="w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">{i.type === 'mcp_server' ? '도구' : '스킬'}</th><th>위험도(승인 시 확정)</th></tr></thead>
        <tbody>{tools.map((t) => <tr key={t} className="border-t border-line"><td className="mono px-3 py-1.5 text-xs">{t}</td><td><RiskChip risk={meta._anx_review?.tool_risks?.[t]} /></td></tr>)}</tbody></table></Card>}
      {tab === 'versions' && <Versions item={i} versions={versions.data?.items ?? []} current={versions.data?.current} onChange={() => { q.refetch(); versions.refetch(); usage.refetch() }} />}
      {tab === 'usage' && <Usage rows={usage.data?.items ?? []} />}
      {tab === 'history' && <History id={id} />}
      <InstantUseWizard open={wizard} item={i} onClose={() => setWizard(false)} onDone={() => q.refetch()} />
    </>
  )
}

function Usage({ rows }: { rows: Json[] }) {
  const nav = useNavigate()
  return (
    <Card title="이 항목을 쓰는 에이전트 프로파일" bodyClass="p-0">
      <table className="w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">프로파일</th><th>상태</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.id} className="cursor-pointer border-t border-line hover:bg-surface-2" onClick={() => nav(`/console/agents/${r.id}/graph`)}><td className="px-3 py-1.5">{r.name}</td><td><StatusBadge status={r.status} /></td></tr>)}
          {!rows.length && <tr><td colSpan={2} className="px-3 py-6 text-center text-fg-muted">사용하는 프로파일이 없습니다.</td></tr>}</tbody></table>
      {rows.length > 0 && <p className="border-t border-line px-3 py-2 text-xs text-warn">비활성·삭제하면 위 {rows.length}개 프로파일에서 빠집니다 (MSG-002).</p>}
    </Card>
  )
}

function History({ id }: { id: string }) {
  const q = useGet(`/v1/catalog/${id}/history`)
  return (
    <Card title="변경 이력 — 불변 감사 로그" bodyClass="p-0">
      {q.error && <ErrorBox error={q.error} />}
      <table className="w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">시각</th><th>동작</th><th>대상</th><th>주체</th><th>해시</th></tr></thead>
        <tbody>{(q.data?.items ?? []).map((e: Json) => <tr key={e.seq} className="border-t border-line"><td className="px-3 py-1.5 text-xs">{fmtTime(e.ts, true)}</td><td><Badge>{e.action.replace('registry.', '')}</Badge></td>
          <td className="mono text-xs">{e.target}</td><td className="text-xs">{e.actor}</td><td className="mono text-[11px] text-fg-muted">{e.hash}</td></tr>)}</tbody></table>
    </Card>
  )
}

// SCR-E07 — 두 버전 diff(재승인 필요 표시) · 버전 고정 · 롤백(확인)
function Versions({ item, versions, current, onChange }: { item: Json; versions: Json[]; current?: string; onChange: () => void }) {
  const toast = useToast()
  const approved = versions.filter((v) => ['approved', 'superseded'].includes(v.status))
  const [from, setFrom] = useState(versions[1]?.version ?? '')
  const [to, setTo] = useState(versions[0]?.version ?? '')
  const [diff, setDiff] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const compare = async () => {
    setErr(null)
    try {
      setDiff(await send<Json>('GET', `/v1/catalog/${item.id}/diff?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`))
    } catch (e) {
      setErr(e)
    }
  }
  const pin = async (version: string | null) => {
    try {
      const r = await send<Json>('PUT', `/v1/catalog/${item.id}/pin`, { version })
      toast('ok', version ? `${version} 고정 — 프로파일 ${r.affected_profiles}개` : '최신 승인 버전 자동 사용')
      onChange()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const rollback = async (version: string) => {
    if (!confirm(`${version} 으로 되돌립니다 — 이 항목을 쓰는 프로파일이 바로 그 버전을 씁니다 (MSG-002). 계속할까요?`)) return
    try {
      const r = await send<Json>('POST', `/v1/catalog/${item.id}/rollback`, { version, confirm: true })
      toast('ok', `${version} 으로 롤백 — 영향 프로파일 ${r.affected_profiles}개`)
      onChange()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  return (
    <div className="grid grid-cols-[1fr_380px] gap-4">
      <Card bodyClass="p-0">
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">버전</th><th>상태</th><th>서명</th><th>게시자</th><th>결정</th><th>메모</th><th /></tr></thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.id} className="border-t border-line">
                <td className="px-3 py-2">{v.version}{v.version === current && <Badge tone="primary" className="ml-1">현재</Badge>}{v.version === item.pinned_version && <Badge tone="warn" className="ml-1">고정</Badge>}</td>
                <td><StatusBadge status={v.status} /></td><td>{v.signature_verified ? '✓' : '—'}</td><td>{v.created_by}</td>
                <td className="text-xs">{v.decided_by ? `${v.decided_by} · ${fmtTime(v.decided_at, true)}` : '—'}</td><td className="text-xs">{v.note || ''}</td>
                <td className="space-x-1 pr-2 text-right">{['approved', 'superseded'].includes(v.status) && <Gate perm="catalog:approve">{(ok, why) => <>
                  {v.version !== item.pinned_version && <Button size="sm" disabled={!ok} title={why} onClick={() => pin(v.version)}>고정</Button>}
                  {v.version !== current && <Button size="sm" disabled={!ok} title={why} onClick={() => rollback(v.version)}>롤백</Button>}</>}</Gate>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {item.pinned_version && <div className="border-t border-line px-3 py-2 text-xs"><Gate perm="catalog:approve">{(ok) => <Button size="sm" disabled={!ok} onClick={() => pin(null)}>고정 해제 — 최신 승인 버전 자동</Button>}</Gate></div>}
      </Card>
      <Card title="버전 비교">
        <div className="mb-3 grid grid-cols-[1fr_auto_1fr_auto] items-end gap-2">
          <Select aria-label="이전 버전" value={from} onChange={(e) => setFrom(e.target.value)}>{versions.map((v) => <option key={v.id}>{v.version}</option>)}</Select>
          <span className="pb-2 text-fg-muted">→</span>
          <Select aria-label="새 버전" value={to} onChange={(e) => setTo(e.target.value)}>{versions.map((v) => <option key={v.id}>{v.version}</option>)}</Select>
          <Button onClick={compare} disabled={!from || !to || from === to}>비교</Button>
        </div>
        {err ? <ErrorBox error={err} /> : null}
        {diff && <DiffList diff={diff} />}
        {!diff && <p className="text-xs text-fg-muted">도구·스코프·엔드포인트·라이선스 변경을 +/−/~ 로 보여 줍니다. 승인된 버전 {approved.length}개 중에서 고정·롤백할 수 있습니다.</p>}
      </Card>
    </div>
  )
}

function DiffList({ diff }: { diff: Json }) {
  return (
    <div className="space-y-2">
      {diff.reapproval && <div role="alert" className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-xs">새 고위험 도구가 추가됐습니다 — 재승인이 필요합니다.</div>}
      <ul className="space-y-1 text-sm">
        {(diff.changes ?? []).map((c: Json, n: number) => (
          <li key={n} className="flex items-center gap-2"><span className={cx('mono w-4 text-center font-bold', c.op === '+' ? 'text-ok' : c.op === '-' ? 'text-danger' : 'text-warn')}>{c.op}</span>
            <span className="text-xs text-fg-muted">{c.path}</span><span className="mono text-xs">{c.value}</span>{c.risk === 'high' && <RiskChip risk="high" />}</li>
        ))}
        {!diff.changes?.length && <li className="text-fg-muted">변경 없음</li>}
      </ul>
      <p className="text-xs text-fg-muted">변경 {diff.count ?? 0} · 위험 {diff.risky ?? 0}</p>
    </div>
  )
}

const TEMPLATES = [['', '붙이지 않음'], ['tpl-qa', '사내 Q&A'], ['tpl-issue-report', '이슈 보고 자동화'], ['tpl-incident', '장애 분석 위임']] as const
const TTL = [[300, '5분'], [600, '10분'], [900, '15분']] as const

// SCR-E03 즉시 사용 — ① 선택(등록·템플릿) ② 인증 설정(시험 교환) ③ 정책 기본값(Rego) ④ 노출 확인(60초)
function InstantUseWizard({ open, item, onClose, onDone }: { open: boolean; item: Json; onClose: () => void; onDone: () => void }) {
  const [stepNo, setStep] = useState(0)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState<unknown>(null)
  const [reg, setReg] = useState<Json | null>(null)
  const [template, setTemplate] = useState('')
  const [scopes, setScopes] = useState('')
  const [ttl, setTtl] = useState(300)
  const [auth, setAuth] = useState<Json | null>(null)
  const [tools, setTools] = useState<Record<string, Json>>({})
  const [subjectType, setSubjectType] = useState('any')
  const [subject, setSubject] = useState('*')
  const [routing, setRouting] = useState('domain-local')
  const [perMin, setPerMin] = useState(60)
  const [plan, setPlan] = useState<Json | null>(null)
  const [applied, setApplied] = useState<Json | null>(null)
  const [exposure, setExposure] = useState<Json | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const registered = !!(reg || item.instance_id)
  const run = async (key: string, fn: () => Promise<void>) => {
    setErr(null)
    setBusy(key)
    try {
      await fn()
    } catch (e) {
      setErr(e)
    } finally {
      setBusy('')
    }
  }
  const register = () => run('reg', async () => {
    setReg(await send<Json>('POST', `/v1/catalog/${item.id}/register`, {}))
    onDone()
  })
  const testExchange = () => run('auth', async () => {
    setAuth(null)
    setAuth(await send<Json>('POST', `/v1/catalog/${item.id}/instant-use/auth`, { scopes: scopes.split(/[\s,]+/).filter(Boolean), ttl_seconds: ttl }))
  })
  const policyBody = (dry: boolean) => ({ subject_type: subjectType, subject: subjectType === 'any' ? '*' : subject, data_class_routing: routing ? { internal: routing } : {},
    calls_per_min: perMin || null, tools, profile_id: template || null, dry_run: dry })
  const preview = () => run('preview', async () => setPlan(await send<Json>('POST', `/v1/catalog/${item.id}/instant-use/policy`, policyBody(true))))
  const apply = () => run('apply', async () => {
    setApplied(await send<Json>('POST', `/v1/catalog/${item.id}/instant-use/policy`, policyBody(false)))
    setStep(3)
    onDone()
  })
  useEffect(() => {  // ③ 기본값을 바꿀 때마다 미리보기(dry-run) 갱신 — 저장하지 않는다
    if (!open || stepNo !== 2) return
    const t = setTimeout(preview, 250)
    return () => clearTimeout(t)
  }, [open, stepNo, tools, subjectType, subject, routing, perMin])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {  // ④ 적용 후 60초 동안 3초마다 노출 확인
    if (stepNo !== 3 || !applied) return
    const t0 = Date.now()
    let stop = false
    const tick = async () => {
      if (stop) return
      setElapsed(Math.round((Date.now() - t0) / 1000))
      try {
        const r = await send<Json>('GET', `/v1/catalog/${item.id}/exposure`)
        setExposure(r)
        if (r.exposed) return
      } catch (e) {
        setErr(e)
      }
      if (Date.now() - t0 < 60_000) setTimeout(tick, 3000)
    }
    tick()
    return () => { stop = true }
  }, [stepNo, applied, item.id])
  const close = () => {
    setStep(0); setReg(null); setAuth(null); setPlan(null); setApplied(null); setExposure(null); setErr(null); setTools({})
    onClose()
  }
  const setTool = (name: string, k: string, v: boolean) => setTools({ ...tools, [name]: { ...(tools[name] ?? {}), [k]: v } })
  const steps = ['선택', '인증 설정', '정책 기본값', '노출 확인']
  const footer = stepNo === 0 ? <Button variant="primary" disabled={!registered} title={registered ? undefined : '먼저 등록하세요'} onClick={() => setStep(1)}>다음</Button>
    : stepNo === 1 ? <><Button onClick={() => setStep(0)}>이전</Button><Button variant="primary" disabled={!auth?.ok} title={auth?.ok ? undefined : '시험 교환이 성공해야 진행합니다 (MSG-019)'} onClick={() => setStep(2)}>다음</Button></>
      : stepNo === 2 ? <><Button onClick={() => setStep(1)}>이전</Button><Button variant="primary" loading={busy === 'apply'} onClick={apply}>적용 → 노출 확인</Button></>
        : <Button onClick={close}>닫기</Button>
  return (
    <Modal open={open} onClose={close} title={`즉시 사용 — ${item.display_name}`} wide footer={footer}>
      <Stepper steps={steps} current={stepNo} />
      {err ? <div className="mb-3"><ErrorBox error={err} /></div> : null}
      {stepNo === 0 && (
        <div className="space-y-4 text-sm">
          <Kv rows={[['항목', <><TypeBadge type={item.type} /> {item.display_name} · v{item.version}</>], ['인증', item.auth_scheme || 'none'], ['라이선스', item.license || '미상'],
            ['등록', registered ? <Badge tone="ok">등록됨 — {(reg?.instance ?? {}).id ?? item.instance_id}</Badge> : <Button size="sm" variant="primary" loading={busy === 'reg'} onClick={register}>등록 (인스턴스 생성)</Button>]]} />
          {reg?.instance?.refresh && <p className="text-xs text-fg-muted">도구 {reg.instance.refresh.tools?.length ?? 0}개 · 헬스 {reg.instance.refresh.health} · {reg.instance.refresh.latency_ms}ms</p>}
          <fieldset>
            <legend className="mb-1 text-xs font-semibold text-fg-muted">사전 템플릿에 붙이기 (선택)</legend>
            {TEMPLATES.map(([v, l]) => <label key={v} className="mr-4 inline-flex items-center gap-1"><input type="radio" name="tpl" checked={template === v} onChange={() => setTemplate(v)} />{l}</label>)}
          </fieldset>
        </div>
      )}
      {stepNo === 1 && (
        <div className="space-y-3 text-sm">
          <p className="text-fg-muted">사용자 토큰을 게이트웨이가 검증한 뒤 RFC 8693 토큰 교환으로 대상별 최소 스코프·짧은 수명 토큰을 발급합니다.</p>
          <div className="grid grid-cols-3 gap-3">
            <Field label="audience"><Input className="mono" readOnly value={`${item.type === 'mcp_server' ? 'mcp' : 'a2a'}:${(reg?.instance ?? {}).id ?? item.instance_id ?? ''}`} /></Field>
            <Field label="스코프 (공백·쉼표)" hint="비우면 교환 정책 기본값(읽기)"><Input className="mono" value={scopes} onChange={(e) => { setScopes(e.target.value); setAuth(null) }} placeholder="issues:read" /></Field>
            <Field label="토큰 수명"><Select value={ttl} onChange={(e) => { setTtl(Number(e.target.value)); setAuth(null) }}>{TTL.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
          </div>
          <Button loading={busy === 'auth'} onClick={testExchange}>시험 교환</Button>
          {auth?.ok && <div role="status" className="rounded-md border border-ok/40 bg-ok/5 px-3 py-2">✓ 시험 교환 성공 — {auth.subject} · {auth.audience} · 스코프 {auth.scopes.join(' ')} · {Math.round(auth.expires_in / 60)}분 · 신원이 백엔드까지 전파·감사
            {auth.warnings?.map((w: string) => <div key={w} className="text-xs text-warn">{w}</div>)}</div>}
        </div>
      )}
      {stepNo === 2 && (
        <div className="space-y-3 text-sm">
          {(plan?.tools ?? []).length > 0 && (
            <table className="w-full text-sm"><thead className="text-left text-xs text-fg-muted"><tr><th>도구</th><th>위험도</th><th>허용</th><th>사람 승인</th></tr></thead>
              <tbody>{plan!.tools.map((t: Json) => <tr key={t.tool} className="border-t border-line">
                <td className="mono py-1 text-xs">{t.tool}</td><td><RiskChip risk={t.risk} /></td>
                <td><input type="checkbox" aria-label={`${t.tool} 허용`} checked={tools[t.tool]?.allowed ?? t.allowed} onChange={(e) => setTool(t.tool, 'allowed', e.target.checked)} /></td>
                <td><input type="checkbox" aria-label={`${t.tool} 사람 승인`} checked={tools[t.tool]?.approval_required ?? t.approval_required} disabled={t.risk === 'high'} onChange={(e) => setTool(t.tool, 'approval_required', e.target.checked)} /></td></tr>)}</tbody></table>
          )}
          {plan && <p className="text-xs text-fg-muted">읽기 {plan.summary.read_allowed} 허용 · 쓰기 {plan.summary.write_blocked} 차단(명시 허용 필요) · 고위험 {plan.summary.approval} 사람 승인 — 미등록 도구 호출은 거부(MSG-006)</p>}
          <div className="grid grid-cols-4 gap-3">
            <Field label="적용 주체"><Select value={subjectType} onChange={(e) => setSubjectType(e.target.value)}>{['any', 'group', 'team', 'role', 'user', 'agent'].map((x) => <option key={x}>{x}</option>)}</Select></Field>
            <Field label="주체 값"><Input disabled={subjectType === 'any'} value={subjectType === 'any' ? '*' : subject} onChange={(e) => setSubject(e.target.value)} placeholder="anx-developers" /></Field>
            <Field label="internal 데이터 → 모델" hint="MSG-011"><Select value={routing} onChange={(e) => setRouting(e.target.value)}><option value="domain-local">로컬만 (domain-local)</option><option value="">제한 없음</option></Select></Field>
            <Field label="호출 한도(회/분·사용자)"><Input type="number" min={1} value={perMin} onChange={(e) => setPerMin(Number(e.target.value))} /></Field>
          </div>
          <div className="flex items-center gap-2"><Button size="sm" loading={busy === 'preview'} onClick={preview}>정책 미리보기 갱신</Button>{plan && <span className="text-xs text-fg-muted">policy {plan.summary.policy} v{plan.summary.version} (Rego) · 저장 시 버전 증가·감사 기록 · 상세 편집은 정책 편집기(H08)</span>}</div>
          {plan && <CodeBlock value={plan.rego} maxH="max-h-56" />}
        </div>
      )}
      {stepNo === 3 && (
        <div className="space-y-3 text-sm">
          {exposure?.exposed
            ? <div role="status" className="rounded-md border border-ok/40 bg-ok/5 px-3 py-2">✓ 노출 확인 — 호스트 도구 목록·콘솔 카탈로그에 보입니다 (MSG-009) · 적용 후 {exposure.elapsed_s ?? elapsed}초{exposure.within_slo ? ' (≤60s)' : ''}</div>
            : elapsed >= 60 ? <div role="alert" className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2">60초 안에 노출되지 않았습니다 — 아래 점검에서 원인을 확인하세요 (trace 는 관측 › 트레이스).</div>
              : <p className="text-fg-muted">노출 확인 중… {elapsed}초 / 60초</p>}
          <ul className="space-y-1">{(exposure?.checks ?? []).map((c: Json) => <li key={c.key}>{c.ok ? '✓' : '✗'} {c.detail}</li>)}</ul>
          {applied && <Kv rows={[['정책', <span className="mono">{applied.summary.policy} v{applied.summary.version}</span>], ['템플릿', applied.attached_profile ?? '—'], ['적용 시각', fmtTime(applied.applied_at, true)]]} />}
        </div>
      )}
    </Modal>
  )
}

const QSTATUS = [['pending', '대기'], ['approved', '승인'], ['rejected', '반려'], ['expired', '만료']] as const
const CHECK_TONE: Record<string, string> = { ok: 'text-ok', warn: 'text-warn', fail: 'text-danger' }

export function RegistryApprovals() {
  const toast = useToast()
  const [status, setStatus] = useState<string>('pending')
  const q = useGet('/api/v1/registry/approvals', { status }, { refetchInterval: 20_000 })
  const [sel, setSel] = useState<Json | null>(null)
  const reject = async (r: Json) => {
    const note = askReason(`${r.item.name}@${r.version}`)
    if (note === null) return
    try {
      await send('POST', `/v1/catalog/${r.item.id}/reject`, { version: r.version, note })
      toast('ok', `${r.item.name}@${r.version} 거부`)
      setSel(null)
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const columns = [
    col.display({ id: 'name', header: '항목', cell: ({ row }) => <div><div className="font-medium">{row.original.item.display_name}</div><div className="mono text-[11px] text-fg-muted">{row.original.item.name}</div></div> }),
    col.display({ id: 'type', header: '유형', cell: ({ row }) => <><TypeBadge type={row.original.item.type} />{row.original.review?.kind === 'a2a_card' && <Badge className="ml-1">카드 게시</Badge>}</> }),
    col.accessor('version', { header: '버전', cell: ({ row }) => <>{row.original.version}{row.original.review?.previous_version && <span className="text-xs text-fg-muted"> ← {row.original.review.previous_version}</span>}</> }),
    col.accessor('created_by', { header: '요청자' }),
    col.display({ id: 'checks', header: '점검', cell: ({ row }) => {
      const ck: Json[] = row.original.review?.checks ?? []
      const fail = ck.filter((c) => c.level === 'fail').length
      return !row.original.review ? '—' : fail ? <Badge tone="danger">실패 {fail}</Badge> : <Badge tone="ok">통과 {ck.length}</Badge>
    } }),
    col.accessor('signature_verified', { header: '서명', cell: (c) => c.getValue() ? <Badge tone="ok">검증됨</Badge> : <Badge tone="warn">미서명</Badge> }),
    col.display({ id: 'lic', header: '라이선스', cell: ({ row }) => <Badge tone={LICENSE[row.original.item.license_risk] ?? 'muted'}>{row.original.item.license || '미상'}</Badge> }),
    col.accessor('created_at', { header: status === 'pending' ? '대기' : '요청', cell: ({ row }) => <span title={row.original.review ? `만료 ${fmtTime(row.original.review.expires_at, true)}` : undefined}>{fmtAgo(row.original.created_at)}</span> }),
    ...(status === 'pending' ? [col.display({ id: 'act', header: '', cell: ({ row }) => <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
      <Button size="sm" onClick={() => reject(row.original)}>거부</Button>
      <Button size="sm" variant="primary" onClick={() => setSel(row.original)}>심사</Button></div> })] : [
      col.accessor('decided_by', { header: '결정', cell: ({ row }) => <span className="text-xs">{row.original.decided_by ?? '—'} · {fmtTime(row.original.decided_at, true)}</span> }),
      col.accessor('note', { header: '메모', cell: (c) => <span className="text-xs">{c.getValue() || ''}</span> })]),
  ]
  return (
    <>
      <PageHeader title="등록 승인 대기열" crumbs={['카탈로그', '승인 대기열']} desc="MCP 서버(server.json)·원격 A2A 에이전트(Agent Card)·플랫폼 Agent Card 게시가 한 대기열로 들어옵니다. 점검 실패는 승인할 수 없고(MSG-007), 72시간이 지나면 자동 반려됩니다." />
      <Tabs tabs={QSTATUS.map(([id, l]) => ({ id, label: <>{l} <span className="text-xs text-fg-muted">{q.data?.counts?.[id] ?? ''}</span></> }))} value={status} onChange={setStatus} />
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={q.data?.items ?? []} columns={columns} onRowClick={status === 'pending' ? setSel : undefined} empty={status === 'pending' ? '승인 대기 중인 요청이 없습니다.' : '해당 상태의 요청이 없습니다.'} />
      {sel && <ReviewDrawer row={sel} onClose={() => setSel(null)} onReject={() => reject(sel)} onDone={() => { setSel(null); q.refetch() }} />}
    </>
  )
}

// SCR-E05 심사 패널 — ③ 점검 ④ 스코프 ⑤ 도구 위험도 확정 ⑥ 신뢰 등급 ⑦ 허용목록 · 이전 버전 diff
function ReviewDrawer({ row, onClose, onReject, onDone }: { row: Json; onClose: () => void; onReject: () => void; onDone: () => void }) {
  const toast = useToast()
  const rv = row.review ?? {}
  const [risks, setRisks] = useState<Record<string, string>>(Object.fromEntries((rv.tools ?? []).map((t: Json) => [t.name, t.risk])))
  const [trust, setTrust] = useState(row.item.type === 'a2a_agent' ? 'community' : row.item.source === 'builtin' ? 'official' : 'community')
  const [group, setGroup] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [showMeta, setShowMeta] = useState(false)
  const approve = async () => {
    setBusy(true)
    try {
      const r = await send<Json>('POST', `/v1/catalog/${row.item.id}/approve`, { version: row.version, note: note || '대기열 심사 승인', trust,
        tool_risks: row.item.type === 'mcp_server' ? risks : undefined, allowlist_group: group || undefined })
      toast('ok', `${row.item.name}@${row.version} 승인 — ${(r.effects ?? []).join(' · ') || '발견 가능'}`)
      onDone()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  const fails = (rv.checks ?? []).filter((c: Json) => c.level === 'fail')
  return (
    <Drawer open onClose={onClose} title={`심사 — ${row.item.display_name} ${row.version}`} width="w-[600px]">
      <div className="space-y-4 text-sm">
        <Kv rows={[['유형', <TypeBadge type={row.item.type} />], ['요청자', row.created_by], ['대기', `${fmtAgo(row.created_at)} · 만료 ${fmtTime(rv.expires_at, true)}`], ['이전 버전', rv.previous_version ?? '없음 (신규)']]} />
        <section><h3 className="mb-1 font-semibold">점검</h3>
          <ul className="space-y-0.5">{(rv.checks ?? []).map((c: Json) => <li key={c.key} className={CHECK_TONE[c.level]}>{c.level === 'ok' ? '✓' : c.level === 'warn' ? '!' : '✗'} <span className="text-fg">{c.key}</span> <span className="text-xs text-fg-muted">{c.detail}</span></li>)}</ul></section>
        {(rv.scopes ?? []).length > 0 && <section><h3 className="mb-1 font-semibold">요청 스코프</h3>
          <div className="flex flex-wrap gap-1">{rv.scopes.map((s: Json) => <Badge key={s.scope} tone={s.kind === 'admin' ? 'danger' : s.kind === 'write' ? 'warn' : 'ok'}>{s.scope} · {s.kind}</Badge>)}</div></section>}
        {rv.diff && <section><h3 className="mb-1 font-semibold">{rv.previous_version} → {row.version} 변경</h3><DiffList diff={rv.diff} /></section>}
        {(rv.tools ?? []).length > 0 && <section><h3 className="mb-1 font-semibold">{row.item.type === 'mcp_server' ? '도구 위험도 — 자동 분류, 승인자가 확정 (고위험 = 사람 승인)' : '스킬'}</h3>
          <table className="w-full"><tbody>{rv.tools.map((t: Json) => <tr key={t.name} className="border-t border-line"><td className="mono py-1 text-xs">{t.name}</td>
            <td className="w-32">{row.item.type === 'mcp_server' ? <Select aria-label={`${t.name} 위험도`} value={risks[t.name]} onChange={(e) => setRisks({ ...risks, [t.name]: e.target.value })}><option value="low">low · 읽기</option><option value="medium">medium · 쓰기</option><option value="high">high · 고위험</option></Select> : <RiskChip risk={t.risk} />}</td></tr>)}</tbody></table></section>}
        <div className="grid grid-cols-2 gap-3">
          <Field label="신뢰 등급" hint={row.item.type === 'a2a_agent' ? '서명 없는 카드에 partner 이상은 메모(사유) 필요' : undefined}><Select value={trust} onChange={(e) => setTrust(e.target.value)}>{Object.keys(TRUST).map((t) => <option key={t}>{t}</option>)}</Select></Field>
          <Field label="허용목록 그룹 (선택)" hint="비우면 호출 기본 거부 유지"><Input value={group} onChange={(e) => setGroup(e.target.value)} placeholder="anx-developers" /></Field>
        </div>
        <Field label="메모"><Textarea className="h-16" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <Button size="sm" variant="ghost" onClick={() => setShowMeta(!showMeta)}>{showMeta ? '원본 접기' : '원본 메타데이터 보기'}</Button>
        {showMeta && <CodeBlock value={row.metadata ?? {}} maxH="max-h-72" />}
        {fails.length > 0 && <div role="alert" className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-xs">점검 실패 {fails.length}건 — 승인할 수 없습니다 (MSG-007). 게시자가 수정해 새 버전을 올려야 합니다.</div>}
        <div className="flex justify-end gap-2 border-t border-line pt-3">
          <Button onClick={onReject}>거부</Button>
          <Gate perm="catalog:approve">{(ok, why) => <Button variant="primary" loading={busy} disabled={!ok || fails.length > 0} title={fails.length ? '점검 실패 (MSG-007)' : why} onClick={approve}>승인</Button>}</Gate>
        </div>
      </div>
    </Drawer>
  )
}
