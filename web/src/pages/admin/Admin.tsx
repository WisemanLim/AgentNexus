// SCR-L01 배포 프로파일(독립 실행) · L02 외부 연동 커넥터 · L03 콘솔 역할·권한(Rbac) · L04 업데이트·사양 버전 · L05 백업·복구 · L06 일반 설정
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Badge, Button, Card, CodeBlock, ErrorBox, Field, Gate, Input, Kpi, Kv, Loading, PageHeader, RiskChip, Select, StatusBadge, Tabs, Textarea, type Tone, cx, useToast } from '@/components/ui'
import { get, type Json } from '@/lib/api'
import { can, useAuth } from '@/lib/auth'
import { fmtTime } from '@/lib/format'
import { queryClient, send, useGet } from '@/lib/query'

type Tab = 'profiles' | 'connectors' | 'updates' | 'backup' | 'settings'

export function Admin() {
  const [tab, setTab] = useState<Tab>('profiles')
  return (
    <>
      <PageHeader title="운영·설정" crumbs={['운영·설정']} />
      <Tabs tabs={[{ id: 'profiles', label: '배포 프로파일' }, { id: 'connectors', label: '외부 연동' }, { id: 'updates', label: '업데이트·사양' }, { id: 'backup', label: '백업·복구' }, { id: 'settings', label: '일반 설정' }]} value={tab} onChange={setTab} />
      {tab === 'profiles' && <Profiles />}
      {tab === 'connectors' && <Connectors />}
      {tab === 'updates' && <Updates />}
      {tab === 'backup' && <Backup />}
      {tab === 'settings' && <Settings />}
    </>
  )
}

const DEPLOY: [string, string, string][] = [['full', '전체 플랫폼', '콘솔·게이트웨이·Host·레지스트리·IdP·관측 + 참조 서버'], ['gateway-only', 'AI 게이트웨이만', '기존 앱의 LLM·MCP·A2A 프록시 (OpenAI 호환 base_url)'],
  ['registry-only', 'MCP 레지스트리만', '발견 전용 /v0/servers · 카탈로그'], ['mcp-servers-only', 'MCP 서버만', '참조 MCP 서버 + 토큰 발급(IdP)'], ['a2a-only', 'A2A 서버만', '플랫폼 오케스트레이션을 A2A 로만 노출']]

function Profiles() {
  const toast = useToast()
  const q = useGet('/api/v1/admin/profiles')
  const [sel, setSel] = useState<string | null>(null)
  const [gap, setGap] = useState<boolean | null>(null)
  const [plan, setPlan] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  const cur = q.data?.current
  const profile = sel ?? cur?.profile ?? 'full'
  const airgap = gap ?? !!cur?.airgap
  useEffect(() => {
    if (!cur) return
    send<Json>('POST', '/api/v1/admin/profiles/plan', { profile, airgap }).then(setPlan).catch((e) => toast('error', e.message))
  }, [profile, airgap, cur?.profile, cur?.airgap]) // eslint-disable-line react-hooks/exhaustive-deps
  if (q.error) return <ErrorBox error={q.error} />
  if (!q.data || !cur) return <Loading />
  const changed = profile !== cur.profile || airgap !== cur.airgap
  const apply = async () => {
    const warn = plan?.active_runs ? `\n실행 중 작업 ${plan.active_runs}건 · 최근 활성 채팅 ${plan.active_sessions}건 — 끝까지 진행되고, 빠지는 구성요소의 새 요청은 거부됩니다.` : ''
    if (!confirm(`배포 프로파일을 ${profile}${airgap ? ' (에어갭)' : ''} 로 적용할까요? 데이터는 지우지 않습니다.${warn} (MSG-002)`)) return
    setBusy(true)
    try {
      const r = await send<Json>('POST', '/api/v1/admin/profiles/apply', { profile, airgap, confirm: true })
      toast('ok', `적용했습니다 — 기동 구성은 다음 명령으로 반영: ${r.commands.preferred}`)
      setSel(null)
      setGap(null)
      q.refetch()
      queryClient.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith('/api/v1/system') })
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-4">
      <Card title="현재 적용">
        <Kv rows={[['프로파일', <span className="flex items-center gap-2"><Badge tone="primary">{cur.profile}</Badge>{cur.airgap && <Badge tone="warn">에어갭</Badge>}<span className="text-xs text-fg-muted">{cur.mode} · {cur.env}</span></span>],
          ['기동 중 구성요소', <div className="flex flex-wrap gap-1">{cur.components.map((c: string) => <Badge key={c} tone="ok">{c}</Badge>)}</div>],
          ['적용', cur.applied_at ? `${fmtTime(cur.applied_at, true)} · ${cur.applied_by}` : '기본값 (적용 이력 없음)']]} />
      </Card>
      <div className="grid grid-cols-5 gap-3" role="radiogroup" aria-label="배포 프로파일">
        {DEPLOY.map(([id, name, desc]) => (
          <button key={id} type="button" role="radio" aria-checked={profile === id} onClick={() => setSel(id)}
            className={cx('rounded-lg border p-3 text-left text-sm', profile === id ? 'border-primary bg-primary-soft' : 'border-line bg-surface hover:border-primary/40')}>
            <div className="font-medium">{name}{cur.profile === id && <span className="ml-1 text-xs text-ok">· 적용 중</span>}</div>
            <div className="mono text-[11px] text-fg-muted">{id}</div>
            <p className="mt-1 text-xs text-fg-muted">{desc}</p>
          </button>
        ))}
      </div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={airgap} onChange={(e) => setGap(e.target.checked)} />에어갭 (외부 SaaS 차단 — 상용 공급자·외부 레지스트리·외부 IdP 비활성, 로컬 모델)</label>
      {plan && (
        <section aria-label="변경 미리보기"><Card title={`변경 미리보기 — ${plan.from.profile}${plan.from.airgap ? '+에어갭' : ''} → ${profile}${airgap ? '+에어갭' : ''}`}>
          <div className="grid grid-cols-3 gap-3 text-sm">
            <div><div className="mb-1 text-xs font-medium text-ok">시작</div>{plan.start.length ? plan.start.map((c: string) => <Badge key={c} tone="ok">{c}</Badge>) : <span className="text-xs text-fg-muted">없음</span>}</div>
            <div><div className="mb-1 text-xs font-medium text-danger">중지</div>{plan.stop.length ? plan.stop.map((c: string) => <Badge key={c} tone="danger">{c}</Badge>) : <span className="text-xs text-fg-muted">없음</span>}</div>
            <div><div className="mb-1 text-xs font-medium">유지</div><div className="flex flex-wrap gap-1">{plan.keep.map((c: string) => <Badge key={c}>{c}</Badge>)}</div></div>
          </div>
          <p className="mt-3 text-xs">💾 {plan.data}</p>
          {plan.airgap_effects?.length > 0 && <ul className="mt-2 list-disc pl-5 text-xs">{plan.airgap_effects.map((x: string) => <li key={x}>{x}</li>)}</ul>}
          {plan.warning && <p role="alert" className="mt-2 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-xs">{plan.warning} (실행 {plan.active_runs} · 채팅 {plan.active_sessions})</p>}
          <div className="mt-3 grid grid-cols-2 gap-3">
            <div><div className="mb-1 text-xs text-fg-muted">Compose</div><CodeBlock value={plan.commands.compose} maxH="max-h-20" /></div>
            <div><div className="mb-1 text-xs text-fg-muted">Helm</div><CodeBlock value={plan.commands.helm} maxH="max-h-20" /></div>
          </div>
          <div className="mt-3 flex justify-end gap-2">
            <Button onClick={() => send<Json>('POST', '/api/v1/admin/profiles/plan', { profile, airgap }).then((r) => { setPlan(r); toast('info', '드라이런 — 계획만 다시 계산했습니다 (변경 없음)') })}>드라이런</Button>
            <Gate perm="admin:write">{(ok, why) => <Button variant="primary" disabled={!ok || !changed} title={why ?? (changed ? undefined : '현재와 같음')} loading={busy} onClick={apply}>적용…</Button>}</Gate>
          </div>
          <p className="mt-2 text-[11px] text-fg-muted">콘솔 적용 = 원하는 상태 저장 + 에어갭 즉시 반영. 컨테이너 구성은 위 명령(또는 GitOps)으로 반영합니다.</p>
        </Card></section>
      )}
    </div>
  )
}

// ───────── L02 외부 연동 커넥터 — 종류 카드·생성/편집 폼·연동 방식·연결 상태 ─────────
const KINDS: [string, string, string][] = [['idp', '기존 IdP', 'Entra ID · Okta (OIDC) — IdP 연동(J01)'], ['api_gateway', 'API 게이트웨이', 'Kong · APISIX — 앞단 라우팅'],
  ['observability', '관측 스택', 'Grafana · Jaeger (OTLP)'], ['registry', '외부 MCP 레지스트리', '공식 레지스트리 미러'], ['a2a', 'A2A 에이전트', '원격 에이전트 카드']]
const PRODUCTS: Record<string, string[]> = { idp: ['entra', 'okta', 'keycloak', 'oidc'], api_gateway: ['kong', 'apisix'], observability: ['grafana', 'jaeger', 'otel'] }
type CForm = { id?: string; kind: string; name: string; url: string; product: string; health_path: string; auth: string; mode: string; sync_routes: boolean; secret: string
  cred?: boolean; status?: string; last_tested_at?: string; result?: Json }
const blankConnector = (kind: string): CForm => ({ kind, name: '', url: '', product: PRODUCTS[kind]?.[0] ?? '', health_path: '', auth: 'none', mode: 'front', sync_routes: true, secret: '' })
const toForm = (c: Json): CForm => ({ id: c.id, kind: c.kind, name: c.name, url: c.config?.url ?? '', product: c.config?.product ?? '', health_path: c.config?.health_path ?? '',
  auth: c.config?.auth ?? 'none', mode: c.config?.mode ?? 'front', sync_routes: !!c.config?.sync_routes, secret: '', cred: !!c.credential_configured, status: c.status, last_tested_at: c.last_tested_at })

function Connectors() {
  const toast = useToast()
  const q = useGet('/api/v1/admin/connectors')
  const prof = useGet('/api/v1/admin/profiles')
  const [f, setF] = useState<CForm | null>(null)
  const [busy, setBusy] = useState('')
  const [gwCfg, setGwCfg] = useState<Json | null>(null)
  const items: Json[] = q.data?.items ?? []
  const open = (next: CForm | null) => { setF(next); setGwCfg(null) }
  const test = async (id: string) => {
    setBusy(`t:${id}`)
    try {
      const r = await send<Json>('POST', `/api/v1/admin/connectors/${id}/test`)
      toast(r.status === 'ok' ? 'ok' : 'error', `${r.status} ${r.result?.status ?? r.result?.error ?? ''}`)
      setF((x) => (x?.id === id ? { ...x, status: r.status, last_tested_at: r.last_tested_at, result: r.result } : x))
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy('')
    }
  }
  const save = async () => {
    if (!f) return
    if (!f.name.trim() || !f.url.trim()) return toast('error', '이름과 연결 URL 은 필수입니다 (MSG-003).')
    const config: Json = { url: f.url.trim(), product: f.product, health_path: f.health_path.trim(), auth: f.auth, ...(f.kind === 'api_gateway' ? { mode: f.mode, sync_routes: f.sync_routes } : {}) }
    setBusy('save')
    try {
      const r = f.id ? await send<Json>('PATCH', `/api/v1/admin/connectors/${f.id}`, { name: f.name.trim(), config })
        : await send<Json>('POST', '/api/v1/admin/connectors', { kind: f.kind, name: f.name.trim(), config, ...(f.secret ? { secret: f.secret } : {}) })
      toast('ok', `저장했습니다 — ${r.name} (MSG-001)`)
      setF({ ...f, id: r.id, secret: '', cred: f.cred || !!f.secret, status: r.status })
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy('')
    }
  }
  const genCfg = async () => {
    try {
      setGwCfg(await get<Json>('/api/v1/admin/connectors/api-gateway/config', { product: f?.product === 'apisix' ? 'apisix' : 'kong' }))
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const pname = f?.product === 'apisix' ? 'APISIX' : 'Kong'
  const cardCls = 'rounded-lg border border-line bg-surface p-3 text-left text-sm hover:border-primary/40'
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        {KINDS.map(([id, name, desc]) => {
          const cs = items.filter((c) => c.kind === id)
          const bad = cs.filter((c) => c.status === 'error').length
          const body = <><div className="font-medium">{name}</div><p className="text-xs text-fg-muted">{desc}</p>
            <div className="mt-2">{!cs.length ? <Badge>미연결</Badge> : bad ? <Badge tone="warn">⚠ 오류 {bad}/{cs.length}</Badge> : <Badge tone="ok">✓ 연결 {cs.length}</Badge>}</div></>
          return id === 'idp'
            ? <Link key={id} to="/console/identity/idp" className={cardCls}>{body}</Link>
            : <button key={id} type="button" className={cx(cardCls, f?.kind === id && !f.id && 'border-primary')} onClick={() => open(blankConnector(id))}>{body}</button>
        })}
        <button type="button" className="rounded-lg border border-dashed border-line p-3 text-left text-sm text-primary hover:border-primary" onClick={() => open(blankConnector('api_gateway'))}>
          <div className="font-medium">+ 커넥터 추가</div><p className="text-xs text-fg-muted">APISIX · 기타 OIDC · OTLP</p></button>
      </div>
      <Card title="외부 연동 커넥터 (관측·API 게이트웨이·IdP·시크릿)" bodyClass="p-0">
        {q.error && <ErrorBox error={q.error} />}
        <table className="w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">이름</th><th>종류</th><th>대상</th><th>자격증명</th><th>상태</th><th>확인</th><th /></tr></thead>
          <tbody>{items.map((c: Json) => (
            <tr key={c.id} className={cx('border-t border-line', f?.id === c.id && 'bg-primary-soft')}><td className="px-3 py-2">{c.name}</td><td><Badge>{c.kind}</Badge>{c.config?.mode === 'behind' && <span className="ml-1 text-xs text-fg-muted">레거시→MCP</span>}</td><td className="mono text-xs">{c.config?.url ?? '—'}</td>
              <td>{c.credential_configured ? '시크릿 저장소' : '—'}</td><td><StatusBadge status={c.status} /></td><td className="text-xs">{fmtTime(c.last_tested_at, true)}</td>
              <td className="space-x-1 pr-3 text-right"><Button size="sm" variant="ghost" onClick={() => open(toForm(c))}>편집</Button><Button size="sm" loading={busy === `t:${c.id}`} onClick={() => test(c.id)}>연결 시험</Button></td></tr>))}</tbody></table>
        {!items.length && <p className="p-4 text-sm text-fg-muted">커넥터가 없습니다 — 위의 종류 카드나 「+ 커넥터 추가」로 등록하세요.</p>}
      </Card>
      {f && (
        <section aria-label="커넥터 설정" className="grid grid-cols-3 gap-4">
          <Card className="col-span-2" title={`${KINDS.find((k) => k[0] === f.kind)?.[1] ?? f.kind} 커넥터 — ${f.id ? '편집' : '추가'}`} actions={<Button size="sm" variant="ghost" onClick={() => open(null)}>닫기</Button>}>
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <Field label="종류"><Select value={f.kind} disabled={!!f.id} onChange={(e) => setF({ ...blankConnector(e.target.value), name: f.name, url: f.url })}>{KINDS.map(([id, n]) => <option key={id} value={id}>{n}</option>)}</Select></Field>
                <Field label="이름"><Input value={f.name} placeholder="예: 사내 Kong" onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
              </div>
              {f.kind === 'api_gateway' && (
                <div role="radiogroup" aria-label="연동 방식" className="space-y-1 text-sm">
                  <div className="text-xs font-medium text-fg-muted">연동 방식</div>
                  {[['front', `${pname} 앞단 → AgentNexus 게이트웨이 upstream (권장)`], ['behind', `${pname} 뒤 레거시 API 를 MCP 서버(F04)로 노출`]].map(([v, l]) => (
                    <label key={v} className="flex items-center gap-2"><input type="radio" name="connector-mode" checked={f.mode === v} onChange={() => setF({ ...f, mode: v })} />{l}</label>))}
                </div>
              )}
              <div className="grid grid-cols-[1fr_180px] gap-3">
                <Field label={f.kind === 'api_gateway' ? 'Admin API URL' : '연결 URL'}><Input value={f.url} placeholder="https://kong-admin.example:8444" onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>
                <Field label="인증"><Select value={f.auth} onChange={(e) => setF({ ...f, auth: e.target.value })}><option value="none">없음</option><option value="token">토큰(Bearer)</option><option value="mtls">mTLS 인증서</option></Select></Field>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {PRODUCTS[f.kind] && <Field label="제품"><Select value={f.product} onChange={(e) => setF({ ...f, product: e.target.value })}>{PRODUCTS[f.kind].map((p) => <option key={p}>{p}</option>)}</Select></Field>}
                <Field label="헬스 경로" hint="연결 시험 시 URL 뒤에 붙입니다 (예: /status)"><Input value={f.health_path} onChange={(e) => setF({ ...f, health_path: e.target.value })} /></Field>
              </div>
              {f.id
                ? <p className="text-xs text-fg-muted">자격증명: {f.cred ? '시크릿 저장소에 저장됨' : '없음'} — 교체는 <Link to="/console/gateway/secrets" className="text-primary hover:underline">시크릿 저장소</Link>에서 (MSG-018)</p>
                : <Field label="자격증명 (선택)" hint="시크릿 저장소에만 저장 — 화면·로그에 평문으로 남지 않습니다 (MSG-018)"><Input type="password" autoComplete="new-password" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} /></Field>}
              {f.kind === 'api_gateway' && f.mode === 'front' && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.sync_routes} onChange={(e) => setF({ ...f, sync_routes: e.target.checked })} />라우트 동기화: /v1/chat/* · /mcp/* · /a2a/*</label>}
              {f.kind === 'api_gateway' && f.mode === 'behind' && <p className="text-xs">레거시 API 는 <Link to="/console/mcp-servers/new/openapi" className="text-primary hover:underline">Internal API 서버 생성(F04)</Link>에서 OpenAPI 로 MCP 서버를 만듭니다 — 호출은 게이트웨이를 경유합니다.</p>}
              <div className="flex items-center gap-2 pt-1">
                <Gate perm="admin:write">{(ok, why) => <Button disabled={!ok || !f.id} title={why ?? (f.id ? undefined : '저장 후 시험할 수 있습니다')} loading={busy === `t:${f.id}`} onClick={() => f.id && test(f.id)}>연결 시험</Button>}</Gate>
                <Gate perm="admin:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} loading={busy === 'save'} onClick={save}>저장</Button>}</Gate>
                {f.kind === 'api_gateway' && f.mode === 'front' && <Button variant="ghost" onClick={genCfg}>{pname} 설정 생성</Button>}
              </div>
              {gwCfg && <div><p className="mb-1 text-xs text-fg-muted">{gwCfg.note}</p><CodeBlock value={gwCfg.config} maxH="max-h-64" /></div>}
            </div>
          </Card>
          <div className="space-y-4">
            <Card title="연결 상태">
              <Kv rows={[['상태', <StatusBadge status={f.status ?? 'untested'} />], ['마지막 시험', fmtTime(f.last_tested_at, true)],
                ['결과', f.result ? (f.result.error ?? `HTTP ${f.result.status}`) : '—'], ['대상', <span className="mono text-xs">{f.url || '—'}{f.health_path}</span>]]} />
            </Card>
            {prof.data?.current?.airgap && <p role="note" className="rounded-md border border-primary/40 bg-primary-soft px-3 py-2 text-xs">에어갭: 외부 레지스트리·SaaS 커넥터는 비활성입니다 — 사내 엔드포인트만 연결하세요 (ST-09).</p>}
          </div>
        </section>
      )}
    </div>
  )
}

// ───────── L04 업데이트·사양 버전 — 릴리스·서명·마이그레이션·SBOM 변경·업그레이드 순서 ─────────
const OP: Record<string, [Tone, string]> = { '+': ['ok', '추가'], '-': ['danger', '제거'], '~': ['warn', '변경'] }

function Updates() {
  const toast = useToast()
  const q = useGet('/api/v1/admin/updates')
  const plan = useGet('/api/v1/admin/updates/plan')
  const sbom = useGet('/v1/catalog/sbom', { format: 'summary' })
  const [all, setAll] = useState(false)
  const [busy, setBusy] = useState(false)
  if (q.error) return <ErrorBox error={q.error} />
  if (!q.data) return <Loading />
  const u = q.data
  const p = plan.data
  const lic = new Map<string, Json>((sbom.data?.components ?? []).map((c: Json) => [c.name, c]))
  const diff: Json[] = p?.sbom_diff ?? []
  const risky = diff.filter((d) => d.op !== '-' && lic.get(d.name)?.risk === 'high')
  const baseline = async () => {
    if (!confirm('현재 SBOM 을 새 기준으로 저장할까요? 이후 변경분은 이 기준과 비교합니다. (MSG-002)')) return
    setBusy(true)
    try {
      const r = await send<Json>('POST', '/api/v1/admin/updates/sbom-baseline')
      toast('ok', `SBOM 기준을 갱신했습니다 — 구성요소 ${r.components}개 (MSG-001)`)
      plan.refetch()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-4">
      {p && <p role="status" className="rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-sm">ℹ {p.target_version !== p.current_version ? `새 릴리스 v${p.target_version} 사용 가능` : `최신 릴리스 v${p.current_version} 사용 중`} — 고정한 사양 버전은 자동 변경되지 않습니다.</p>}
      <div className="grid grid-cols-2 gap-4">
        <Card title="플랫폼 릴리스" actions={<Button size="sm" variant="ghost" loading={plan.isFetching} onClick={() => plan.refetch()}>업그레이드 계획 새로 계산</Button>}>
          {plan.error ? <ErrorBox error={plan.error} /> : !p ? <Loading rows={2} /> : (
            <Kv rows={[['현재', `v${p.current_version}`], ['대상', `v${p.target_version}`],
              ['서명', <span className="flex items-center gap-2"><Badge tone={p.signature?.configured ? 'ok' : 'warn'}>{p.signature?.configured ? 'cosign ✓' : '미설정'}</Badge><span className="text-xs text-fg-muted">{p.signature?.detail}</span></span>],
              ['DB 마이그레이션', <span className="flex flex-wrap items-center gap-1"><span className="mono text-xs">{p.migration ?? '—'} → head {p.head}</span>
                {p.pending_migrations?.length ? p.pending_migrations.map((m: string) => <Badge key={m} tone="warn">{m}</Badge>) : <Badge tone="ok">대기 없음</Badge>}</span>]]} />
          )}
        </Card>
        <Card title="사양 버전 고정"><Kv rows={[['AgentNexus', u.version], ['DB 마이그레이션', u.migration], ...Object.entries(u.protocols ?? {}).map(([k, v]: [string, any]) => [k.toUpperCase(), typeof v === 'string' ? v : `${v.current} (지원 ${(v.supported ?? []).join(', ')})`] as [string, string]), ['정책', u.policy]]} /></Card>
      </div>
      {p && (
        <Card title={`SBOM 변경 (CycloneDX) — 기준 ${p.sbom_baseline_at ? fmtTime(p.sbom_baseline_at, true) : '없음 · 전체가 추가로 표시됨'}`} bodyClass="p-0"
          actions={<Gate perm="admin:write">{(ok, why) => <Button size="sm" disabled={!ok} title={why} loading={busy} onClick={baseline}>SBOM 기준 갱신</Button>}</Gate>}>
          <p className="px-4 py-2 text-xs text-fg-muted">추가 {diff.filter((d) => d.op === '+').length} · 제거 {diff.filter((d) => d.op === '-').length} · 변경 {diff.filter((d) => d.op === '~').length}
            {' · '}<Link to="/console/catalog/sbom" className="text-primary hover:underline">전체 SBOM(E08)</Link></p>
          {diff.length ? (
            <table className="w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">변경</th><th>구성요소</th><th>이전</th><th>이후</th><th>라이선스</th><th>위험 등급</th></tr></thead>
              <tbody>{(all ? diff : diff.slice(0, 20)).map((d) => { const l = lic.get(d.name); return (
                <tr key={`${d.op}${d.name}`} className="border-t border-line"><td className="px-3 py-1.5"><Badge tone={OP[d.op]?.[0]}>{OP[d.op]?.[1] ?? d.op}</Badge></td><td>{d.name}</td>
                  <td className="mono text-xs">{d.from ?? '—'}</td><td className="mono text-xs">{d.to ?? '—'}</td><td className="text-xs">{l?.license ?? '—'}</td><td>{l?.risk ? <RiskChip risk={l.risk} /> : '—'}</td></tr>) })}</tbody></table>
          ) : <p className="px-4 pb-3 text-sm text-fg-muted">기준 대비 변경 없음</p>}
          {diff.length > 20 && <div className="border-t border-line px-4 py-2"><Button size="sm" variant="ghost" onClick={() => setAll(!all)}>{all ? '20개만 보기' : `전체 ${diff.length}개 보기`}</Button></div>}
        </Card>
      )}
      {p && (
        <div className="grid grid-cols-2 gap-4">
          <Card title="업그레이드 순서"><ol className="list-decimal space-y-1 pl-5 text-sm">{(p.steps ?? []).map((s: string) => <li key={s}>{s}</li>)}</ol></Card>
          <div className="space-y-2 text-xs">
            {!p.signature?.configured && <p role="alert" className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2">서명 검증 준비 안 됨 — 이미지 서명 검증 실패 시 업그레이드 불가 (MSG-020)</p>}
            {risky.length > 0 && <p role="alert" className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2">라이선스 고위험 구성요소 {risky.length}개 ({risky.slice(0, 3).map((d) => d.name).join(', ')}{risky.length > 3 ? ' …' : ''}) — 분리 배포 또는 빌드에서 제외 후 업그레이드 (MSG-023)</p>}
            {p.pending_migrations?.length > 0 && <p className="rounded-md border border-warn/50 bg-warn/10 px-3 py-2">대기 마이그레이션 {p.pending_migrations.length}건 — 백업 후 make db-migrate</p>}
            {p.signature?.configured && !risky.length && !p.pending_migrations?.length && <p className="rounded-md border border-ok/40 bg-ok/10 px-3 py-2">업그레이드 차단 요인 없음</p>}
          </div>
        </div>
      )}
      <Card title="의존성"><Kv rows={Object.entries(u.dependencies ?? {}).map(([k, v]) => [k, <span className="mono text-xs">{String(v)}</span>])} /></Card>
    </div>
  )
}

// ───────── L05 백업·복구 — 상태 타일·예약 일정·백업 목록·격리 복구 시험·실제 복구(RESTORE 입력) ─────────
const fmtBytes = (n?: number) => (n == null ? '—' : n < 1024 ? `${n}B` : n < 1048576 ? `${(n / 1024).toFixed(1)}KB` : `${(n / 1048576).toFixed(1)}MB`)
const INTERVAL: Record<string, string> = { off: '예약 없음', daily: '매일', weekly: '매주' }
const epoch = (s?: number) => (s ? new Date(s * 1000).toISOString() : null)
const saveJson = (data: unknown, name: string) => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: 'application/json' }))
  Object.assign(document.createElement('a'), { href: url, download: name }).click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function Backup() {
  const toast = useToast()
  const list = useGet('/api/v1/admin/backups')
  const [bk, setBk] = useState<Json | null>(null)
  const [secrets, setSecrets] = useState(false)
  const [busy, setBusy] = useState('')
  const [file, setFile] = useState<Json | null>(null)
  const [src, setSrc] = useState('')
  const [rt, setRt] = useState<Json | null>(null)
  const [sched, setSched] = useState<Json | null>(null)
  const items: Json[] = list.data?.items ?? []
  const sch: Json = sched ?? list.data?.schedule ?? { interval: 'off', keep: 7 }
  const last = items[0]
  const nextAt = sch.interval === 'off' ? null : last ? epoch(last.created_at + (sch.interval === 'daily' ? 86400 : 604800)) : null
  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key)
    try {
      await fn()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy('')
    }
  }
  const create = () => run('b', async () => {
    const r = await send<Json>('POST', '/api/v1/admin/backup', { include_secrets: secrets })
    setBk(r)
    saveJson(r, `agentnexus-backup-${Date.now()}.json`)
    toast('ok', '서명된 구성 백업을 내려받았습니다.')
  })
  const runNow = () => {
    if (!confirm(`지금 서명 백업을 만들어 서버 백업 저장소에 남길까요? 보관 개수(${sch.keep ?? 7})를 넘는 오래된 백업은 지워집니다. (MSG-002)`)) return
    run('now', async () => {
      const r = await send<Json>('POST', '/api/v1/admin/backups/run')
      toast('ok', `백업했습니다 — ${r.name} (MSG-001)`)
      list.refetch()
    })
  }
  const saveSched = () => {
    const keep = Number(sch.keep)
    if (!Number.isInteger(keep) || keep < 1) return toast('error', '보관 개수는 1 이상의 정수입니다 (MSG-004).')
    run('s', async () => {
      await send<Json>('PUT', '/api/v1/admin/settings', { 'backup.schedule': { interval: sch.interval, keep } })
      toast('ok', '백업 일정을 저장했습니다 (MSG-001)')
      setSched(null)
      list.refetch()
    })
  }
  const download = (name: string) => run(`d:${name}`, async () => saveJson(await send<Json>('GET', `/api/v1/admin/backups/${name}`), name))
  const pick = (name: string) => run(`p:${name}`, async () => {
    setFile(await send<Json>('GET', `/api/v1/admin/backups/${name}`))
    setSrc(name)
    setRt(null)
    toast('info', `${name} 을 복구 대상으로 골랐습니다 — 먼저 복구 시험을 실행하세요.`)
  })
  const restoreTest = () => file && run('t', async () => {
    const r = await send<Json>('POST', '/api/v1/admin/restore/test', { bundle: file.bundle, signature: file.signature })
    setRt(r)
    toast(r.ok ? 'ok' : 'error', r.ok ? '격리 복구 시험 통과 — 운영 DB 는 바뀌지 않았습니다.' : `복구 시험 실패 (${r.stage}) ${r.detail ?? (r.mismatch ?? []).join(', ')}`)
  })
  const restore = () => {
    if (!file) return
    const warn = !rt ? '격리 복구 시험을 아직 실행하지 않았습니다. ' : !rt.ok ? '격리 복구 시험이 실패했습니다. ' : ''
    const typed = window.prompt(`${warn}실제 복구는 운영 데이터(같은 ID 항목)를 백업 내용으로 덮어씁니다. 서버가 직전 상태를 백업 저장소에 먼저 남깁니다(-prerestore). 계속하려면 RESTORE 를 입력하세요. (MSG-002)`)
    if (typed === null) return
    if (typed.trim() !== 'RESTORE') return toast('error', 'RESTORE 를 정확히 입력해야 복구합니다.')
    run('r', async () => {
      const r = await send<Json>('POST', '/api/v1/admin/restore', { bundle: file.bundle, signature: file.signature, confirm: 'RESTORE' })
      toast('ok', `복구 ${Object.values<number>(r.restored).reduce((a, b) => a + b, 0)} rows — 직전 상태 ${r.snapshot} (MSG-001)`)
      list.refetch()
    })
  }
  const lrt = list.data?.last_restore_test as Json | null | undefined
  const tables = rt ? Object.keys({ ...(rt.expected ?? {}), ...(rt.tables ?? {}) }) : []
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-3">
        <Kpi label="마지막 백업" value={last ? fmtTime(epoch(last.created_at), true) : '없음'} sub={last ? `${last.name} · ${fmtBytes(last.size)}` : '서버 백업 저장소 비어 있음'} tone={last ? 'ok' : 'warn'} />
        <Kpi label="다음 예약 백업" value={sch.interval === 'off' ? '꺼짐' : nextAt ? fmtTime(nextAt, true) : '다음 점검 시'} sub={INTERVAL[sch.interval] ?? sch.interval} />
        <Kpi label="보관" value={`${sch.keep ?? 7}개`} sub={`현재 ${items.length}개`} />
        <Kpi label="마지막 복구 시험" value={rt ? (rt.ok ? '통과 ✓' : '실패') : lrt ? (lrt.ok ? '통과 ✓' : '실패') : '—'}
          sub={rt ? `${src || '업로드 파일'} · ${rt.stage}` : lrt ? `${fmtTime(lrt.at, true)} · ${lrt.by}` : '아직 실행 안 함'}
          tone={(rt ?? lrt) ? ((rt ?? lrt)!.ok ? 'ok' : 'danger') : undefined} />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Card title="백업 대상·일정">
          <div className="grid grid-cols-2 gap-3">
            <Field label="예약 주기"><Select value={sch.interval} onChange={(e) => setSched({ ...sch, interval: e.target.value })}><option value="off">꺼짐</option><option value="daily">매일</option><option value="weekly">매주</option></Select></Field>
            <Field label="보관 개수" hint="넘으면 오래된 것부터 삭제"><Input type="number" min={1} value={sch.keep ?? 7} onChange={(e) => setSched({ ...sch, keep: e.target.value })} /></Field>
          </div>
          <p className="mt-2 text-xs text-fg-muted">대상: 카탈로그·정책·IdP·서버·에이전트·설정·커넥터 구성 (서명 번들). DB 전체는 pg_dump · 감사 로그는 해시 체인(K09).</p>
          <Gate perm="admin:write">{(ok, why) => <Button className="mt-3" disabled={!ok || !sched} title={why} loading={busy === 's'} onClick={saveSched}>일정 저장</Button>}</Gate>
        </Card>
        <Card title="구성 백업 (서명 RS256)">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={secrets} onChange={(e) => setSecrets(e.target.checked)} />시크릿 포함(암호문 그대로)</label>
          <Gate perm="admin:write">{(ok, why) => <Button className="mt-3" variant="primary" disabled={!ok} title={why} onClick={create} loading={busy === 'b'}>백업 생성·다운로드</Button>}</Gate>
          {bk && <Kv rows={[['테이블', Object.keys(bk.bundle.tables).length], ['생성', fmtTime(bk.bundle.created_at, true)]]} />}
          <p className="mt-3 text-xs text-fg-muted">DB 전체 백업은 운영 DB 도구(pg_dump)로 · 에어갭 번들은 <code>nexus bundle export</code>.</p>
        </Card>
      </div>
      <Card title="백업 목록" bodyClass="p-0" actions={<Gate perm="admin:write">{(ok, why) => <Button size="sm" variant="primary" disabled={!ok} title={why} loading={busy === 'now'} onClick={runNow}>지금 백업</Button>}</Gate>}>
        {list.error && <ErrorBox error={list.error} />}
        <table className="w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">백업</th><th>시각</th><th>크기</th><th>형식</th><th /></tr></thead>
          <tbody>{items.map((b) => (
            <tr key={b.name} className={cx('border-t border-line', src === b.name && 'bg-primary-soft')}><td className="mono px-3 py-1.5 text-xs">{b.name}</td><td className="text-xs">{fmtTime(epoch(b.created_at), true)}</td><td className="text-xs">{fmtBytes(b.size)}</td>
              <td className="text-xs text-fg-muted">서명 번들(RS256)</td>
              <td className="space-x-1 pr-3 text-right"><Gate perm="admin:write">{(ok, why) => <>
                <Button size="sm" variant="ghost" disabled={!ok} title={why} loading={busy === `d:${b.name}`} onClick={() => download(b.name)}>다운로드</Button>
                <Button size="sm" disabled={!ok} title={why} loading={busy === `p:${b.name}`} onClick={() => pick(b.name)}>복구 대상으로</Button></>}</Gate></td></tr>))}</tbody></table>
        {!items.length && <p className="p-4 text-sm text-fg-muted">서버에 저장된 백업이 없습니다 — 「지금 백업」 또는 예약 주기를 켜세요.</p>}
      </Card>
      <div className="grid grid-cols-2 gap-4">
        <Card title="복구 시험(격리 환경) → 실제 복구">
          <input type="file" accept="application/json" aria-label="백업 파일" onChange={async (e) => { const f = e.target.files?.[0]; if (f) { try { setFile(JSON.parse(await f.text())); setSrc(''); setRt(null) } catch { toast('error', '백업 파일 형식 오류') } } }} />
          {file && <p className="mt-2 text-xs">{src ? <span className="mono">{src}</span> : '업로드 파일'} · 테이블 {Object.keys(file.bundle?.tables ?? {}).length}개 · 서명 {file.signature ? '있음' : '없음'}</p>}
          <div className="mt-3 flex gap-2">
            <Gate perm="admin:write">{(ok, why) => <Button variant="primary" disabled={!ok || !file} title={why} onClick={restoreTest} loading={busy === 't'}>복구 시험 실행</Button>}</Gate>
            <Gate perm="admin:write">{(ok, why) => <Button variant="danger" disabled={!ok || !file} title={why} onClick={restore} loading={busy === 'r'}>실제 복구…</Button>}</Gate>
          </div>
          <p className="mt-3 text-xs text-fg-muted">복구 시험은 임시 DB 에만 복원합니다(운영 DB 무변경). 서명·SHA-256 이 일치하지 않으면 복원을 거부합니다 (MSG-020).</p>
        </Card>
        <div className="space-y-3">
          {rt ? (
            <section aria-label="복구 시험 결과" className="rounded-lg border border-line bg-surface p-4 text-sm">
              <div className="mb-2 flex items-center gap-2"><Badge tone={rt.ok ? 'ok' : 'danger'}>{rt.ok ? '통과' : '실패'}</Badge><span className="text-xs text-fg-muted">단계 {rt.stage}{rt.version ? ` · 백업 v${rt.version}` : ''}{rt.created_at ? ` · ${fmtTime(rt.created_at, true)}` : ''}</span></div>
              {rt.detail && <p role="alert" className="mb-2 text-xs text-danger">{rt.detail}</p>}
              {rt.stage === 'complete' && <>
                <ul className="mb-2 space-y-0.5 text-xs">
                  <li><span className="text-ok">●</span> 서명·SHA-256 검증 <span className="mono text-fg-muted">{String(rt.sha256 ?? '').slice(0, 12)}…</span></li>
                  <li><span className={rt.errors?.length ? 'text-danger' : 'text-ok'}>●</span> 임시 DB 복원 {Object.keys(rt.tables ?? {}).length}개 테이블{rt.errors?.length ? ` · 오류 ${rt.errors.join(', ')}` : ''}</li>
                  <li><span className={rt.mismatch?.length ? 'text-danger' : 'text-ok'}>●</span> 행 수 일치{rt.mismatch?.length ? ` — 불일치 ${rt.mismatch.join(', ')}` : ''}</li>
                  <li><span className="text-ok">●</span> 임시 DB 정리</li>
                </ul>
                <div className="max-h-48 overflow-auto"><table className="w-full text-xs"><thead className="text-left text-fg-muted"><tr><th>테이블</th><th className="text-right">기대</th><th className="text-right">복원</th></tr></thead>
                  <tbody>{tables.map((t) => <tr key={t} className={cx(rt.mismatch?.includes(t) && 'text-danger')}><td className="mono">{t}</td><td className="text-right">{rt.expected?.[t] ?? '—'}</td><td className="text-right">{rt.tables?.[t] ?? '—'}</td></tr>)}</tbody></table></div>
              </>}
            </section>
          ) : <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-xs text-fg-muted">복구 대상을 고른 뒤 「복구 시험 실행」으로 서명·행 수·무결성을 먼저 확인하세요.</p>}
          <p role="note" className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs">실제 복구는 현재 데이터를 덮어씁니다 — RESTORE 입력 확인(MSG-002) · 직전 구성 자동 내려받기.</p>
        </div>
      </div>
    </div>
  )
}

// ───────── L06 일반 설정 — 언어·시간대·세션/IP/토큰·보존·PII 규칙·알림 채널·변경 이력 ─────────
const ZONES: string[] = (() => {
  try {
    return Intl.supportedValuesOf('timeZone')
  } catch {
    return ['Asia/Seoul', 'Asia/Tokyo', 'Asia/Singapore', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles']
  }
})()
const SAVE_KEYS = ['ui.language', 'gateway.default_data_class', 'ui.timezone', 'retention', 'auth.access_token_ttl_min', 'auth.session_ttl_hours', 'auth.ip_allowlist',
  'secrets.rotation_days', 'guards.pii_rules', 'notify.channels']
const CIDR = /^(\d{1,3}\.){3}\d{1,3}(\/\d{1,2})?$|^[0-9a-f]*:[0-9a-f:]*(\/\d{1,3})?$/i
const parseIps = (t: string) => t.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean)

function Settings() {
  const toast = useToast()
  const q = useGet('/api/v1/admin/settings')
  const [s, setS] = useState<Json | null>(null)
  const [ipText, setIpText] = useState('')
  const [sample, setSample] = useState('연락처 010-1234-5678 · kim@example.com')
  const [pii, setPii] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!q.data) return
    setS(q.data)
    setIpText((q.data['auth.ip_allowlist'] ?? []).join('\n'))
  }, [q.data])
  const save = async () => {
    const next: Json = { ...s!, 'auth.ip_allowlist': parseIps(ipText) }
    const body = Object.fromEntries(SAVE_KEYS.filter((k) => JSON.stringify(next[k]) !== JSON.stringify(q.data![k])).map((k) => [k, next[k]]))
    if (!Object.keys(body).length) return toast('info', '변경 사항이 없습니다.')
    const bad = next['auth.ip_allowlist'].filter((n: string) => !CIDR.test(n))
    if (bad.length) return toast('error', `CIDR 형식 오류: ${bad.join(', ')} (MSG-004)`)
    if ((next['guards.pii_rules'] ?? []).some((r: Json) => !String(r.pattern ?? '').trim())) return toast('error', '패턴이 비어 있는 PII 규칙이 있습니다 (MSG-004).')
    if (body['auth.ip_allowlist'] && next['auth.ip_allowlist'].length
      && !confirm(`IP 허용 목록을 ${next['auth.ip_allowlist'].join(', ')} 로 바꿉니다. 목록 밖에서 접속한 사용자(본인 포함)는 로그인·API 가 즉시 막힙니다 — 현재 접속 IP 가 포함되는지 확인하세요. (MSG-002)`)) return
    setBusy(true)
    try {
      queryClient.setQueryData(['/api/v1/admin/settings', {}], await send<Json>('PUT', '/api/v1/admin/settings', body))
      queryClient.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith('/api/v1/admin/settings/history') })
      toast('ok', `저장했습니다 — ${Object.keys(body).join(', ')}${body['auth.access_token_ttl_min'] || body['auth.session_ttl_hours'] ? ' · 다음 토큰부터 적용' : ''} (MSG-001)`)
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  if (q.error) return <ErrorBox error={q.error} />
  if (!s) return <Loading />
  const rules: Json[] = s['guards.pii_rules'] ?? []
  const setRule = (i: number, patch: Json) => setS({ ...s, 'guards.pii_rules': rules.map((r, j) => (j === i ? { ...r, ...patch } : r)) })
  const testPii = async () => {
    try {
      setPii(await send<Json>('POST', '/api/v1/admin/settings/pii-test', { text: sample, rules }))
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const num = (k: string) => (e: { target: { value: string } }) => setS({ ...s, [k]: Number(e.target.value) })
  return (
    <div className="grid grid-cols-2 gap-4">
      <Card title="언어·시간대">
        <div className="space-y-3">
          <Field label="기본 언어"><Select value={s['ui.language']} onChange={(e) => setS({ ...s, 'ui.language': e.target.value })}><option value="ko">한국어</option><option value="en">English</option></Select></Field>
          <Field label="시간대" hint="콘솔 표시·API 응답 형식 공통"><Select value={s['ui.timezone']} onChange={(e) => setS({ ...s, 'ui.timezone': e.target.value })}>{[...new Set([s['ui.timezone'], 'Asia/Seoul', 'UTC', ...ZONES])].map((z) => <option key={z}>{z}</option>)}</Select></Field>
          <Field label="기본 데이터 등급" hint="헤더가 없는 요청에 적용"><Select value={s['gateway.default_data_class']} onChange={(e) => setS({ ...s, 'gateway.default_data_class': e.target.value })}><option>public</option><option>internal</option><option>confidential</option></Select></Field>
        </div>
      </Card>
      <Card title="보존 기간(일) — PII 최소 보관">
        <div className="grid grid-cols-2 gap-3">
          {Object.entries<number>(s.retention ?? {}).map(([k, v]) => (
            <Field key={k} label={k}><Input type="number" min={1} value={v} onChange={(e) => setS({ ...s, retention: { ...s.retention, [k]: Number(e.target.value) } })} /></Field>
          ))}
        </div>
      </Card>
      <Card title="콘솔 세션·접근">
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <Field label="액세스 토큰 수명(분)" hint="5–60"><Input type="number" min={5} max={60} value={s['auth.access_token_ttl_min']} onChange={num('auth.access_token_ttl_min')} /></Field>
            <Field label="세션 수명(시간)" hint="1–720 · 갱신 토큰"><Input type="number" min={1} max={720} value={s['auth.session_ttl_hours']} onChange={num('auth.session_ttl_hours')} /></Field>
            <Field label="시크릿 교체 주기(일)" hint="7–365 · D-day 경고"><Input type="number" min={7} max={365} value={s['secrets.rotation_days']} onChange={num('secrets.rotation_days')} /></Field>
          </div>
          <Field label="콘솔 IP 허용 목록 (CIDR, 줄마다 하나)" hint="비우면 제한 없음 · 콘솔·REST·CLI 동일 적용">
            <Textarea rows={3} className="mono text-xs" value={ipText} placeholder={'10.0.0.0/8\n192.168.0.0/16'} onChange={(e) => setIpText(e.target.value)} />
          </Field>
          <p role="note" className="rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-xs">⚠ 잘못된 허용 목록은 본인을 포함한 모든 사용자를 잠급니다 — 현재 접속 IP 대역을 반드시 포함하세요.</p>
          <p className="text-xs text-fg-muted">만료 시 재로그인 안내(MSG-024) · 변경은 다음 토큰부터 적용됩니다.</p>
        </div>
      </Card>
      <Card title="PII 마스킹 규칙" actions={<Button size="sm" onClick={() => setS({ ...s, 'guards.pii_rules': [...rules, { name: '', pattern: '', enabled: true }] })}>+ 규칙</Button>}>
        <p className="mb-2 text-xs text-fg-muted">내장 규칙(주민등록번호·전화·이메일·카드·시크릿)은 항상 적용됩니다. 아래는 조직 정의 규칙 — 수집 단계에서 마스킹해 원문 PII 를 저장하지 않습니다.</p>
        <table className="w-full text-sm"><thead className="text-left text-xs text-fg-muted"><tr><th>규칙 이름</th><th>정규식</th><th>적용</th><th /></tr></thead>
          <tbody>{rules.map((r, i) => (
            <tr key={i}><td className="pr-2 py-1"><Input aria-label={`규칙 ${i + 1} 이름`} value={r.name ?? ''} placeholder="사번" onChange={(e) => setRule(i, { name: e.target.value })} /></td>
              <td className="pr-2"><Input aria-label={`규칙 ${i + 1} 정규식`} className="mono text-xs" value={r.pattern ?? ''} placeholder={'HG-\\d{6}'} onChange={(e) => setRule(i, { pattern: e.target.value })} /></td>
              <td><input type="checkbox" aria-label={`규칙 ${i + 1} 적용`} checked={r.enabled !== false} onChange={(e) => setRule(i, { enabled: e.target.checked })} /></td>
              <td className="text-right"><Button size="sm" variant="ghost" aria-label={`규칙 ${i + 1} 삭제`} onClick={() => setS({ ...s, 'guards.pii_rules': rules.filter((_, j) => j !== i) })}>✕</Button></td></tr>))}</tbody></table>
        {!rules.length && <p className="py-2 text-xs text-fg-muted">조직 정의 규칙 없음</p>}
        <div className="mt-3 space-y-2">
          <Field label="샘플 텍스트"><Textarea rows={2} value={sample} onChange={(e) => setSample(e.target.value)} /></Field>
          <Button size="sm" onClick={testPii} disabled={!sample.trim()}>샘플로 시험</Button>
          {pii && <section aria-label="마스킹 결과"><CodeBlock value={pii.text} maxH="max-h-24" />
            <div className="mt-1 flex flex-wrap gap-1">{pii.findings.length ? pii.findings.map((x: Json) => <Badge key={x.type} tone="warn">{x.type} ×{x.count}</Badge>) : <span className="text-xs text-fg-muted">탐지 없음</span>}</div></section>}
        </div>
      </Card>
      <Card title="알림 채널">
        <Field label="Slack 웹훅 URL" hint="경고 이상 알림을 보냅니다 · https:// 만 · 에어갭이면 보내지 않음">
          <Input type="url" placeholder="https://hooks.slack.com/services/…" value={s['notify.channels']?.slack_webhook ?? ''}
            onChange={(e) => { const ch: Json = { ...(s['notify.channels'] ?? {}), slack_webhook: e.target.value }; if (!e.target.value) delete ch.slack_webhook; setS({ ...s, 'notify.channels': ch }) }} />
        </Field>
        <p className="mt-2 text-xs text-fg-muted">사용자별 분류 끄기는 알림 센터의 수신 설정에서.</p>
      </Card>
      <div className="flex items-end justify-end gap-2">
        <Button variant="ghost" onClick={() => { setS(q.data!); setIpText((q.data!['auth.ip_allowlist'] ?? []).join('\n')) }}>편집 취소</Button>
        <Gate perm="admin:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} loading={busy} onClick={save}>저장</Button>}</Gate>
      </div>
      <div className="col-span-2"><SettingsHistory /></div>
    </div>
  )
}

function SettingsHistory() {
  const toast = useToast()
  const [key, setKey] = useState('')
  const q = useGet('/api/v1/admin/settings/history', { key, limit: 50 })
  const revert = async (h: Json) => {
    if (!confirm(`${h.key} 를 이 변경 직전 값으로 되돌릴까요? (MSG-002)`)) return
    try {
      await send<Json>('POST', '/api/v1/admin/settings/revert', { history_id: h.id })
      toast('ok', `${h.key} 를 되돌렸습니다 (MSG-001)`)
      queryClient.invalidateQueries({ predicate: (x) => ['/api/v1/admin', '/api/v1/identity/rbac'].some((p) => String(x.queryKey[0]).startsWith(p)) })
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  return (
    <Card title="변경 이력·되돌리기" bodyClass="p-0" actions={
      <Select aria-label="설정 키" className="w-56" value={key} onChange={(e) => setKey(e.target.value)}>
        <option value="">전체 설정</option>{[...SAVE_KEYS, 'backup.schedule', 'rbac.overrides', 'sbom.baseline', 'gateway.security', 'registry.require_signature'].map((k) => <option key={k}>{k}</option>)}
      </Select>}>
      {q.error && <ErrorBox error={q.error} />}
      <table className="w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">시각</th><th>설정</th><th>변경 전 값</th><th>변경자</th><th /></tr></thead>
        <tbody>{(q.data?.items ?? []).map((h: Json) => { const v = h.value == null ? '(없음 — 기본값)' : JSON.stringify(h.value); return (
          <tr key={h.id} className="border-t border-line"><td className="px-3 py-1.5 text-xs">{fmtTime(h.ts, true)}</td><td className="mono text-xs">{h.key}</td>
            <td className="mono max-w-md truncate text-xs" title={v}>{v}</td><td className="text-xs">{h.changed_by}</td>
            <td className="pr-3 text-right"><Gate perm="admin:write">{(ok, why) => <Button size="sm" variant="ghost" disabled={!ok} title={why} onClick={() => revert(h)}>되돌리기</Button>}</Gate></td></tr>) })}</tbody></table>
      {!q.data?.items?.length && <p className="p-4 text-sm text-fg-muted">변경 이력이 없습니다.</p>}
      <p className="border-t border-line px-4 py-2 text-xs text-fg-muted">저장할 때마다 직전 값이 기록됩니다 — 되돌리기는 그 값으로 다시 저장하고 감사에 남깁니다.</p>
    </Card>
  )
}

// ───────── L03 콘솔 역할·권한(RBAC) — /console/admin/rbac ─────────
const ROLE_ORDER = ['admin', 'operator', 'developer', 'security', 'finops', 'user']
const rank = (id: string) => (ROLE_ORDER.includes(id) ? ROLE_ORDER.indexOf(id) : ROLE_ORDER.length)
const AREA: Record<string, string> = { catalog: '카탈로그', registry: '레지스트리', gateway: '게이트웨이', policy: '정책', limits: '한도', approvals: '승인', identity: 'Identity', tokens: '토큰',
  audit: '감사', observe: '관측', cost: '비용', admin: '운영·설정', agents: '에이전트', a2a: 'A2A', mcp: 'MCP 서버', models: '모델', chat: '채팅' }
const area = (p: string) => p.split(':')[0]
const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x))

export function Rbac() {
  const toast = useToast()
  const { me } = useAuth()
  const writable = can(me, 'identity:write')
  const q = useGet('/api/v1/identity/rbac')
  const idps = useGet('/api/v1/identity/idps')
  const [draft, setDraft] = useState<Record<string, string[]>>({})
  const [reason, setReason] = useState('')
  const [focus, setFocus] = useState('operator')
  const [busy, setBusy] = useState(false)
  const header = <PageHeader title="콘솔 역할·권한(RBAC)" crumbs={['운영·설정', '역할·권한']} desc="역할별 권한 행렬 — 같은 권한이 REST API·CLI 토큰 스코프에도 적용됩니다." />
  if (q.error) return <>{header}<ErrorBox error={q.error} /></>
  if (!q.data) return <>{header}<Loading /></>
  const perms: string[] = q.data.permissions
  const roles: Json[] = [...q.data.roles].sort((a, b) => rank(a.id) - rank(b.id))
  const eff = (r: Json): string[] => draft[r.id] ?? r.effective
  const changed = roles.filter((r) => !r.admin && draft[r.id] && !sameSet(draft[r.id], r.effective))
  const toggle = (r: Json, p: string, on: boolean) => setDraft({ ...draft, [r.id]: on ? [...eff(r), p] : eff(r).filter((x) => x !== p) })
  const save = async () => {
    if (!reason.trim()) return toast('error', '변경 사유를 입력하세요 (MSG-003).')
    const overrides = Object.fromEntries(roles.filter((r) => !r.admin).map((r) => {
      const e = eff(r)
      return [r.id, { add: e.filter((x) => !r.base.includes(x)), remove: r.base.filter((x: string) => !e.includes(x)) }]
    }))
    if (!confirm(`${changed.map((r) => r.label).join(', ')} 역할의 권한을 변경할까요? 역할 변경은 다음 토큰부터 반영됩니다. (MSG-002)`)) return
    setBusy(true)
    try {
      queryClient.setQueryData(['/api/v1/identity/rbac', {}], await send<Json>('PUT', '/api/v1/identity/rbac', { overrides, reason: reason.trim() }))
      setDraft({})
      setReason('')
      toast('ok', '권한을 저장했습니다 — 역할 변경은 다음 토큰부터 반영 (MSG-001)')
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  const fr = roles.find((r) => r.id === focus) ?? roles[0]
  return (
    <>
      {header}
      <div className="grid grid-cols-[1fr_300px] gap-4">
        <div className="space-y-4">
          <Card title="권한 매트릭스" bodyClass="p-0">
            <div className="max-h-[560px] overflow-auto">
              <table className="w-full text-sm" aria-label="권한 매트릭스">
                <thead className="sticky top-0 bg-surface-2 text-xs text-fg-muted"><tr><th className="px-3 py-2 text-left">권한 영역</th>
                  {roles.map((r) => <th key={r.id} className="px-2 py-2"><button type="button" aria-pressed={focus === r.id} onClick={() => setFocus(r.id)} className={cx('hover:text-primary', focus === r.id && 'font-semibold text-primary')}>{r.label}</button>
                    {r.admin && <div className="text-[10px] font-normal">읽기 전용</div>}</th>)}</tr></thead>
                <tbody>{perms.map((p) => (
                  <tr key={p} className="border-t border-line"><th scope="row" className="px-3 py-1 text-left font-normal"><span className="text-xs text-fg-muted">{AREA[area(p)] ?? area(p)} · </span><span className="mono text-xs">{p}</span></th>
                    {roles.map((r) => {
                      const on = eff(r).includes(p)
                      const dirty = on !== r.effective.includes(p)
                      const ov = on !== r.base.includes(p)
                      return <td key={r.id} className={cx('text-center', dirty && 'bg-warn/15')} title={r.admin ? '관리자는 모든 권한 (재정의 불가)' : ov ? (on ? '재정의: 추가' : '재정의: 제거') : '기본 권한'}>
                        <input type="checkbox" aria-label={`${r.label} ${p}`} checked={on} disabled={r.admin || !writable} onChange={(e) => toggle(r, p, e.target.checked)} />
                        {ov && !r.admin && <span className="ml-0.5 text-[10px] text-primary">{on ? '+' : '−'}</span>}</td>
                    })}</tr>))}</tbody>
              </table>
            </div>
            <div className="flex items-end gap-2 border-t border-line px-4 py-3">
              <div className="flex-1"><Field label="변경 사유 (필수, MSG-003)"><Input value={reason} disabled={!writable} placeholder="예: FinOps 팀 예산 한도 편집 위임" onChange={(e) => setReason(e.target.value)} /></Field></div>
              <Button variant="ghost" disabled={!Object.keys(draft).length} onClick={() => setDraft({})}>편집 취소</Button>
              <Gate perm="identity:write">{(ok, why) => <Button variant="primary" disabled={!ok || !changed.length || !reason.trim()} title={why ?? (changed.length ? (reason.trim() ? undefined : '사유를 입력하세요') : '변경 없음')} loading={busy} onClick={save}>권한 저장{changed.length ? ` (${changed.length})` : ''}</Button>}</Gate>
            </div>
            <p className="px-4 pb-3 text-xs text-fg-muted">역할 변경은 다음 토큰부터 반영됩니다 — 변경은 감사에 기록됩니다 (MSG-001). 관리자 역할은 항상 모든 권한을 가집니다. + / − 는 기본값 대비 재정의.</p>
          </Card>
          <RbacMappings ext={(idps.data?.items ?? []).filter((i: Json) => i.kind !== 'builtin')} error={idps.error} refetch={idps.refetch} roles={roles} writable={writable} />
        </div>
        <div className="space-y-4">
          {fr && <Card title={`${fr.label} — 스코프`}>
            <div className="flex flex-wrap gap-1">{eff(fr).slice().sort().map((p) => <Badge key={p} tone={fr.base.includes(p) ? 'muted' : 'primary'}><span className="mono">{p}</span></Badge>)}</div>
            {!fr.admin && removedFrom(fr, eff(fr)).length > 0 && <p className="mt-2 text-xs text-fg-muted">기본에서 제거: <span className="mono">{removedFrom(fr, eff(fr)).join(', ')}</span></p>}
            <p className="mt-2 text-xs text-fg-muted">콘솔·REST·CLI 토큰에 같은 스코프가 적용됩니다 (B06).</p>
          </Card>}
          <RbacSimulator roles={roles} perms={perms} idps={(idps.data?.items ?? []).filter((i: Json) => i.kind !== 'builtin')} />
          <p role="note" className="rounded-md border border-primary/40 bg-primary-soft px-3 py-2 text-xs">변경은 감사에 기록되고, 다음 로그인·토큰 갱신부터 반영됩니다 (MSG-001 · FR-O05).</p>
        </div>
      </div>
    </>
  )
}
const removedFrom = (r: Json, e: string[]) => (r.base as string[]).filter((x) => !e.includes(x))

function RbacMappings({ ext, error, refetch, roles, writable }: { ext: Json[]; error: unknown; refetch: () => unknown; roles: Json[]; writable: boolean }) {
  const toast = useToast()
  const [idp, setIdp] = useState('')
  const [group, setGroup] = useState('')
  const [role, setRole] = useState('developer')
  const [busy, setBusy] = useState(false)
  const rows = ext.flatMap((i) => Object.entries<string>(i.role_mapping ?? {}).map(([g, r]) => ({ idp: i, group: g, role: r })))
  const label = (id: string) => roles.find((r) => r.id === id)?.label ?? id
  const target = ext.find((i) => i.id === (idp || ext[0]?.id))
  const seen: string[] = target?.last_test?.groups ?? []
  const add = async () => {
    if (!target) return
    if (!group.trim()) return toast('error', '그룹 클레임 값을 입력하세요 (MSG-003).')
    if (/\s/.test(group.trim())) return toast('error', '그룹 값에 공백을 넣을 수 없습니다 (MSG-004).')
    setBusy(true)
    try {
      await send<Json>('PATCH', `/api/v1/identity/idps/${target.id}`, { role_mapping: { ...(target.role_mapping ?? {}), [group.trim()]: role } })
      toast('ok', `매핑을 추가했습니다 — ${group.trim()} → ${label(role)} (다음 로그인부터 반영, MSG-001)`)
      setGroup('')
      refetch()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card title="IdP 그룹 → 콘솔 역할 매핑" bodyClass="p-0" actions={<Link to="/console/identity/idp" className="text-xs text-primary hover:underline">IdP 연동에서 편집 →</Link>}>
      {!!error && <ErrorBox error={error} />}
      <table className="w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">IdP</th><th>그룹 클레임</th><th>콘솔 역할</th></tr></thead>
        <tbody>{rows.map((m) => (
          <tr key={`${m.idp.id}:${m.group}`} className="border-t border-line"><td className="px-3 py-1.5">{m.idp.name}</td><td className="mono text-xs">{m.idp.groups_claim ?? 'groups'}: {m.group}</td><td><Badge tone="primary">{label(m.role)}</Badge></td></tr>))}</tbody></table>
      {!rows.length && <p className="p-4 text-sm text-fg-muted">{ext.length ? '그룹 매핑이 없습니다.' : '외부 IdP 가 없습니다 — 내장 IdP 사용자는 역할을 직접 지정합니다.'}</p>}
      {ext.length > 0 && (
        <div className="flex flex-wrap items-end gap-2 border-t border-line px-4 py-3">
          <Field label="IdP"><Select value={target?.id ?? ''} onChange={(e) => setIdp(e.target.value)}>{ext.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}</Select></Field>
          <Field label="그룹 클레임 값"><Input value={group} placeholder="platform-ops" onChange={(e) => setGroup(e.target.value)} /></Field>
          <Field label="콘솔 역할"><Select value={role} onChange={(e) => setRole(e.target.value)}>{roles.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}</Select></Field>
          <Button variant="primary" disabled={!writable} title={writable ? undefined : '권한 필요: identity:write'} loading={busy} onClick={add}>+ 매핑</Button>
          {group.trim() && seen.length > 0 && !seen.includes(group.trim()) && <p className="w-full text-xs text-warn">시험 로그인에서 본 그룹({seen.join(', ')})에 없습니다 — 철자를 확인하세요.</p>}
        </div>
      )}
      <p className="border-t border-line px-4 py-2 text-xs text-fg-muted">미매핑 그룹 = 최종 사용자(채팅만) · 여러 그룹 = 역할 합집합 · 로그인마다 클레임으로 역할 산출.</p>
    </Card>
  )
}

function RbacSimulator({ roles, perms, idps }: { roles: Json[]; perms: string[]; idps: Json[] }) {
  const toast = useToast()
  const [mode, setMode] = useState<'roles' | 'groups'>('roles')
  const [sel, setSel] = useState<string[]>(['developer'])
  const [groups, setGroups] = useState('')
  const [idp, setIdp] = useState('')
  const [perm, setPerm] = useState('')
  const [busy, setBusy] = useState(false)
  const [r, setR] = useState<Json | null>(null)
  const run = async () => {
    const body = mode === 'roles' ? { roles: sel, perm: perm || null } : { groups: groups.split(/[\s,]+/).filter(Boolean), idp_id: idp || null, perm: perm || null }
    setBusy(true)
    try {
      setR(await send<Json>('POST', '/api/v1/identity/rbac/simulate', body))
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  const areas = [...new Set(perms.map(area))]
  const level = (a: string): [string, string] => {
    const ps: string[] = r?.permissions ?? []
    if (ps.some((p) => area(p) === a && !p.endsWith(':read'))) return ['text-ok', '허용']
    if (ps.includes(`${a}:read`)) return ['text-warn', '조회만']
    return ['text-danger', '거부']
  }
  return (
    <Card title="권한 시뮬레이터">
      <div className="space-y-2 text-sm">
        <div role="radiogroup" aria-label="시뮬레이션 기준" className="flex gap-3">
          <label className="flex items-center gap-1"><input type="radio" name="sim-mode" checked={mode === 'roles'} onChange={() => setMode('roles')} />역할로</label>
          <label className="flex items-center gap-1"><input type="radio" name="sim-mode" checked={mode === 'groups'} onChange={() => setMode('groups')} />IdP 그룹으로</label>
        </div>
        {mode === 'roles' ? (
          <div className="flex flex-wrap gap-x-3 gap-y-1">{roles.map((x) => (
            <label key={x.id} className="flex items-center gap-1 text-xs"><input type="checkbox" checked={sel.includes(x.id)} onChange={(e) => setSel(e.target.checked ? [...sel, x.id] : sel.filter((y) => y !== x.id))} />{x.label}</label>))}</div>
        ) : (
          <>
            <Field label="그룹 (쉼표 구분)"><Input value={groups} placeholder="anx-developers" onChange={(e) => setGroups(e.target.value)} /></Field>
            <Field label="IdP"><Select value={idp} onChange={(e) => setIdp(e.target.value)}><option value="">전체 IdP</option>{idps.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}</Select></Field>
          </>
        )}
        <Field label="확인할 권한"><Select value={perm} onChange={(e) => setPerm(e.target.value)}><option value="">(선택 안 함)</option>{perms.map((p) => <option key={p}>{p}</option>)}</Select></Field>
        <Button variant="primary" size="sm" loading={busy} onClick={run}>시뮬레이션</Button>
        {r && (
          <section aria-label="시뮬레이션 결과" className="space-y-2 border-t border-line pt-2">
            {r.allowed != null && <div className="flex items-center gap-2"><Badge tone={r.allowed ? 'ok' : 'danger'}>{r.allowed ? '허용 ✓' : '거부 ✗'}</Badge><span className="mono text-xs">{perm}</span></div>}
            <div className="flex flex-wrap items-center gap-1 text-xs">역할 {r.roles.map((x: string) => <Badge key={x}>{roles.find((y) => y.id === x)?.label ?? x}</Badge>)}</div>
            {r.via?.length > 0 && <ul className="text-xs text-fg-muted">{r.via.map((v: Json) => <li key={`${v.idp}:${v.group}`}>{v.group} → {v.role} ({v.idp})</li>)}</ul>}
            {mode === 'groups' && !r.via?.length && <p className="text-xs text-fg-muted">매핑된 그룹 없음 → 최종 사용자</p>}
            <ul className="grid grid-cols-2 gap-x-2 text-xs">{areas.map((a) => { const [c, l] = level(a); return <li key={a}><span className={c}>●</span> {AREA[a] ?? a} {l}</li> })}</ul>
            <p className="text-[11px] text-fg-muted">저장된 권한 기준 — 편집 중인 변경은 저장 후 반영됩니다.</p>
          </section>
        )}
      </div>
    </Card>
  )
}
