// SCR-F01 MCP 서버 인스턴스 목록 · F02 서버 상세(도구·리소스·프롬프트·헬스·로그·샘플링·등록) · F06 MCP 도구 시험(Inspector)
import { useQuery } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { Fragment, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, CodeBlock, ErrorBox, Field, Gate, Input, Kpi, Kv, Loading, PageHeader, RiskChip, Select, StatusBadge, Tabs, Textarea, TraceLink, cx, useToast } from '@/components/ui'
import { ApiError, api, get, request, type Json } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { fmtAgo, fmtMs, fmtNum, fmtTime, shortId } from '@/lib/format'
import { send, useGet } from '@/lib/query'

const col = createColumnHelper<Json>()

/** 재시작 (SCR-F01 ⑤) — 업스트림 세션·캐시를 비우고 initialize·tools/list 부터 다시 (MSG-002 확인). 취소하면 null. */
async function restartServer(id: string): Promise<Json | null> {
  if (!confirm(`${id} 서버를 재시작할까요? 게이트웨이의 업스트림 세션·캐시를 비우고 initialize·tools/list 부터 다시 합니다 (MSG-002).`)) return null
  return send<Json>('POST', `/api/v1/mcp-servers/${id}/restart`)
}

function RestartButton({ id, onDone, variant = 'secondary' }: { id: string; onDone?: () => void; variant?: 'secondary' | 'primary' }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try {
      const r = await restartServer(id)
      if (!r) return
      toast(r.health === 'healthy' ? 'ok' : 'error', `${id} 재시작 — ${r.health ?? '?'} · 도구 ${r.tools_count ?? r.tools?.length ?? '?'}개${r.error ? ` · ${r.error}` : ''}`)
      onDone?.()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  return <Gate perm="mcp:write">{(ok, why) => <Button size="sm" variant={variant} disabled={!ok} title={why} loading={busy} onClick={run}>재시작</Button>}</Gate>
}

const HEALTH_RANK: Record<string, number> = { unhealthy: 0, degraded: 1, unknown: 2 }

export function McpServers() {
  const nav = useNavigate()
  const q = useGet('/api/v1/mcp-servers', undefined, { refetchInterval: 30_000 })
  const sum = useGet('/api/v1/observe/summary', { window_minutes: 60 }, { refetchInterval: 30_000, retry: false })
  const sess = useGet('/api/v1/gateway/mcp/sessions', { kind: 'client', limit: 500 }, { refetchInterval: 30_000, retry: false })
  const items: Json[] = q.data?.items ?? []
  // 실패 우선 정렬 (SCR-F01 ②) — 같은 상태 안에서는 id 순서 유지
  const rows = useMemo(() => [...items].sort((a, b) => (HEALTH_RANK[a.health] ?? 3) - (HEALTH_RANK[b.health] ?? 3)), [items])
  const failed = items.filter((s) => s.health === 'unhealthy')
  const [sel, setSel] = useState('')
  const bad = failed.find((s) => s.id === sel) ?? failed[0]
  const tool = sum.data?.by_kind?.tool
  const since = Date.now() - 30 * 60_000  // 최근 30분 안에 쓰인 열린 클라이언트 세션
  const openSessions = (sess.data?.items ?? []).filter((x: Json) => !x.closed_at && Date.parse(x.last_used_at) >= since).length
  const columns = [
    col.accessor('name', { header: '서버', cell: ({ row }) => <div><div className="font-medium">{row.original.name}</div><div className="mono text-[11px] text-fg-muted">{row.original.id}</div></div> }),
    col.accessor('transport', { header: '전송', cell: (c) => <Badge>{c.getValue()}</Badge> }),
    col.accessor('health', { header: '헬스', cell: ({ row }) => <span title={row.original.health !== 'healthy' ? row.original.health_detail ?? undefined : undefined}><StatusBadge status={row.original.health} /></span> }),
    col.accessor('tools_count', { header: '도구' }),
    col.accessor('status', { header: '등록', cell: (c) => <StatusBadge status={c.getValue()} /> }),
    col.accessor('version', { header: '버전', cell: (c) => <span className="mono text-xs">{c.getValue() ?? '—'}</span> }),
    col.accessor('auth_type', { header: '인증', cell: (c) => <Badge tone={c.getValue() === 'none' ? 'muted' : 'primary'}>{c.getValue()}</Badge> }),
    col.accessor('gateway_path', { header: '게이트웨이 경로', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
    col.accessor('builtin', { header: '구분', cell: (c) => c.getValue() ? <Badge tone="model">참조</Badge> : <Badge>등록</Badge> }),
    col.accessor('tools_refreshed_at', { header: '도구 갱신', cell: (c) => fmtAgo(c.getValue()) }),
    col.accessor('health_checked_at', { header: '확인', cell: (c) => fmtAgo(c.getValue()) }),
  ]
  return (
    <>
      <PageHeader title="MCP 서버 인스턴스" crumbs={['MCP 서버']} desc="모든 호출은 /mcp/{id} 게이트웨이 경로로만 — 백엔드 직접 접근은 네트워크에서 차단됩니다 (FR-G00)."
        actions={<><Link to="/console/catalog?type=mcp_server"><Button>카탈로그에서 추가</Button></Link><Link to="/console/mcp-servers/new/openapi"><Button>OpenAPI → 도구</Button></Link><Link to="/console/mcp-servers/new/custom"><Button>Custom 템플릿</Button></Link></>} />
      {q.error && <ErrorBox error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <div aria-label="MCP 서버 요약" className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Kpi label="실행 중" value={`${items.filter((s) => s.health === 'healthy').length} / ${items.length}`} sub={`중지 ${items.filter((s) => s.status === 'disabled').length} · 미확인 ${items.filter((s) => !s.health || s.health === 'unknown').length}`} />
          <Kpi label="도구 합계" value={fmtNum(items.reduce((a, s) => a + (s.tools_count ?? 0), 0))} sub="정책 통과 도구만 호출 가능" />
          <Kpi label="MCP 호출(분당)" value={sum.error ? '—' : tool ? (tool.calls / 60).toFixed(1) : '0'} sub={sum.error ? '권한 필요: observe:read' : tool?.p95_latency_ms != null ? `최근 60분 · p95 ${fmtMs(tool.p95_latency_ms)}` : '최근 60분'} />
          <Kpi label="헬스 실패" value={failed.length} tone={failed.length ? 'danger' : 'ok'} sub={failed.length ? failed.map((s) => s.id).slice(0, 3).join(', ') + (failed.length > 3 ? ` 외 ${failed.length - 3}` : '') : '모두 정상'} />
          <Kpi label="활성 세션" value={sess.error ? '—' : openSessions} sub={sess.error ? '권한 필요: gateway:read' : <Link className="text-primary" to="/console/gateway/mcp">최근 30분 · 프록시 세션 →</Link>} />
          <Kpi label="직접 노출" value={items.filter((s) => !s.gateway_path).length} tone="ok" sub="모두 게이트웨이 /mcp/{id} 뒤" />
        </div>
      )}
      {q.isLoading ? <Loading /> : <DataTable data={rows} columns={columns} onRowClick={(r) => nav(`/console/mcp-servers/${r.id}`)} />}
      <p className="mt-2 text-xs text-fg-muted">전송 HTTP = Streamable HTTP · 행 클릭 → 상세(F02) · 등록 승인 대기는 <Link className="text-primary" to="/console/registry/approvals">승인 대기열(E05)</Link>{sum.data?.registry_pending != null ? ` ${sum.data.registry_pending}건` : ''}</p>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {bad ? (
          <Card title={`${bad.id} — 헬스 실패`} actions={failed.length > 1 && (
            <Select aria-label="헬스 실패 서버" className="h-7 w-44 text-xs" value={bad.id} onChange={(e) => setSel(e.target.value)}>{failed.map((s) => <option key={s.id} value={s.id}>{s.id}</option>)}</Select>
          )}>
            <CodeBlock value={`${bad.endpoint ?? bad.transport} → ${bad.health}\n${bad.health_detail ?? '원인 미상'}\n확인 ${fmtTime(bad.health_checked_at, true)} · 도구 갱신 ${bad.tools_refreshed_at ? fmtAgo(bad.tools_refreshed_at) : '없음'}`} maxH="max-h-32" />
            <div className="mt-3 flex flex-wrap justify-end gap-2">
              <Button size="sm" onClick={() => nav(`/console/mcp-servers/${bad.id}?tab=logs`)}>로그</Button>
              <RestartButton id={bad.id} onDone={() => q.refetch()} />
              <Button size="sm" variant="primary" onClick={() => nav(`/console/mcp-servers/${bad.id}/config`)}>설정(F03)</Button>
            </div>
          </Card>
        ) : q.data ? <Card title="헬스"><p className="text-sm text-ok">✓ 모든 서버가 정상입니다.</p></Card> : null}
        <Card title="호스팅 규칙">
          <ul className="space-y-1.5 text-sm">
            <li><span className="text-ok">●</span> 게이트웨이 뒤 배치 — 호출은 /mcp/{'{id}'} 로만, 직접 노출 0 (FR-G00)</li>
            <li><span className="text-ok">●</span> 헬스체크 주기 MCP_HEALTH_INTERVAL_S (기본 60초) — 실패 원인을 표시하고 재시작으로 세션·캐시 초기화</li>
            <li><span className="text-ok">●</span> 기동 시 server.json 자동 등록 (Custom 템플릿 anx_register.py)</li>
            <li><span className="text-warn">●</span> 컨테이너 격리·CPU·메모리 제한은 배포 값(Helm·compose)으로</li>
            <li><span className="text-fg-muted">●</span> stdio 서버는 허용 명령(MCP_STDIO_ALLOWED_COMMANDS)만 — 호스트 사이드카</li>
          </ul>
        </Card>
      </div>
    </>
  )
}

/** 관리 API 경유 JSON-RPC (게이트웨이 경로 그대로 — 정책·승인·소독 적용) */
const inspectCall = (id: string, method: string, params: Json = {}, extra: Json = {}) => send<Json>('POST', `/api/v1/mcp-servers/${id}/inspect`, { method, params, ...extra })

function useInspect(id: string, method: string) {
  return useQuery<Json>({ queryKey: ['mcp-inspect', id, method], queryFn: () => inspectCall(id, method), staleTime: 60_000, retry: false, enabled: !!id })
}

/** MSG-025 — 도구·리소스 응답 소독 결과 */
function SanitizeNote({ result }: { result?: Json | null }) {
  if (!result) return null
  const found: Json[] = result._meta?.['io.agentnexus/sanitized'] ?? []
  if (!found.length) return <p className="mt-2 text-xs text-fg-muted">응답 소독 규칙 적용 — 도구 응답 속 지시문은 실행하지 않습니다 (MSG-025).</p>
  const n = found.reduce((a, f) => a + (f.count ?? 1), 0)
  return (
    <div role="status" className="mt-3 rounded-md border border-warn/50 bg-warn/10 p-3 text-sm">
      응답 소독: 지시문·민감정보로 보이는 내용 {n}건 제거 ({found.map((f) => `${f.kind}:${f.type}`).join(', ')}) — 원문은 감사 저장소에만 (MSG-025)
    </div>
  )
}

type DetailTab = 'tools' | 'resources' | 'prompts' | 'logs' | 'auth' | 'meta'

export function McpServer() {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const q = useGet(`/api/v1/mcp-servers/${id}`)
  const logs = useGet(`/api/v1/mcp-servers/${id}/logs`, { limit: 100 }, { retry: false })
  const resources = useInspect(id, 'resources/list')
  const templates = useInspect(id, 'resources/templates/list')
  const prompts = useInspect(id, 'prompts/list')
  const tab = (sp.get('tab') as DetailTab) || 'tools'
  const setTab = (t: DetailTab) => setSp(t === 'tools' ? {} : { tab: t }, { replace: true })
  const [busy, setBusy] = useState(false)
  const [sampling, setSampling] = useState(false)
  const [read, setRead] = useState<{ key: string; res: Json } | null>(null)
  const refresh = async () => {
    setBusy(true)
    try {
      const r = await send<Json>('POST', `/api/v1/mcp-servers/${id}/refresh`)
      toast(r.health === 'healthy' ? 'ok' : 'error', `도구 ${r.tools_count ?? r.tools?.length ?? '?'}개 · ${r.health}`)
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  const setRule = async (t: Json, patch: Json) => {
    const r = t.rule ?? { allowed: false, risk: 'low', approval_required: false, approver: 'requester' }
    try {
      // PUT 은 규칙 전체를 대체 — 인자 스키마·소독·설명을 함께 보내야 토글 시 지워지지 않는다
      await send('PUT', '/api/v1/gateway/governance/tools', { server_id: id, tool: t.name, allowed: r.allowed, risk: r.risk, approval_required: r.approval_required, approver: r.approver ?? 'requester',
        args_schema: r.args_schema ?? {}, sanitize: r.sanitize ?? true, description: r.description ?? null, ...patch })
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const setPolicy = async (policy: 'ask' | 'deny') => {
    setSampling(true)
    try {
      await send('PUT', `/api/v1/mcp-servers/${id}/sampling`, { policy })
      toast('ok', policy === 'ask' ? '샘플링 요청 — 매번 사용자 동의' : '샘플링 요청 — 항상 거부')
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setSampling(false)
    }
  }
  const setProtocol = async (protocol: string) => {
    try {
      await send('PATCH', `/api/v1/mcp-servers/${id}`, { protocol })
      const r = await send<Json>('POST', `/api/v1/mcp-servers/${id}/refresh`)
      toast(r.health === 'healthy' ? 'ok' : 'error', `업스트림 프로토콜 ${protocol} · 발견 ${r.protocol ?? '—'}`)
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const readIt = async (key: string, method: string, params: Json) => {
    try {
      setRead({ key, res: await inspectCall(id, method, params) })
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  if (q.error) return <ErrorBox error={q.error} />
  if (!q.data) return <Loading />
  const s = q.data
  const resItems: Json[] = resources.data?.result?.resources ?? []
  const tplItems: Json[] = templates.data?.result?.resourceTemplates ?? []
  const promptItems: Json[] = prompts.data?.result?.prompts ?? []
  const logRows: Json[] = logs.data?.logs ?? []
  const events: Json[] = logs.data?.events ?? []
  const policy = s.sampling_policy ?? 'ask'
  const rpcErr = (r: { data?: Json; error: unknown }) => r.error ? <ErrorBox error={r.error} /> : r.data?.error ? <ErrorBox error={{ message: `${r.data.error.code} ${r.data.error.message}` }} /> : null
  const readOut = (key: string) => read?.key === key && (
    <tr className="border-t border-line"><td colSpan={5} className="px-3 py-2">
      {read.res.error ? <ErrorBox error={{ message: `${read.res.error.code} ${read.res.error.message}` }} /> : <CodeBlock value={read.res.result} maxH="max-h-72" />}
      <SanitizeNote result={read.res.result} />
    </td></tr>
  )
  return (
    <>
      <PageHeader title={s.name} crumbs={['MCP 서버', s.id]} desc={<span className="mono">{s.gateway_path} → {s.endpoint ?? s.transport}</span>}
        actions={<><StatusBadge status={s.health} /><Badge tone="mcp">{s.transport}</Badge>{s.version && <Badge>v{s.version}</Badge>}<Button size="sm" onClick={() => nav(`/console/mcp-servers/${id}/config`)}>연결·인증 설정</Button><Button size="sm" onClick={refresh} loading={busy}>도구 새로고침</Button><RestartButton id={id} onDone={() => { q.refetch(); logs.refetch() }} /><Button size="sm" variant="primary" onClick={() => nav(`/console/mcp-servers/${id}/inspect`)}>Inspector</Button></>} />
      {s.health_detail && s.health !== 'healthy' && <div className="mb-3"><ErrorBox error={{ message: s.health_detail }} /></div>}
      <div className="grid gap-4 xl:grid-cols-[1fr_300px]">
        <div className="min-w-0">
          <Tabs tabs={[{ id: 'tools', label: `도구 ${s.tools?.length ?? 0}` }, { id: 'resources', label: `리소스 ${resources.data ? resItems.length + tplItems.length : '…'}` }, { id: 'prompts', label: `프롬프트 ${prompts.data ? promptItems.length : '…'}` },
            { id: 'logs', label: '헬스·로그' }, { id: 'auth', label: '인증·토큰 교환' }, { id: 'meta', label: 'server.json' }]} value={tab} onChange={setTab} />
          {tab === 'tools' && (
            <Card bodyClass="p-0">
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">도구</th><th>설명</th><th>허용</th><th>위험도</th><th>사람 승인</th><th>승인자</th><th /></tr></thead>
                <tbody>
                  {(s.tools ?? []).map((t: Json) => (
                    <tr key={t.name} className="border-t border-line align-top">
                      <td className="px-3 py-2"><div className="mono">{t.name}</div>{t.annotations?.readOnlyHint && <Badge tone="ok">읽기</Badge>}</td>
                      <td className="max-w-sm py-2 text-xs text-fg-muted">{t.description}</td>
                      <Gate perm="policy:write">{(ok, why) => <>
                        <td className="py-2"><input type="checkbox" aria-label={`${t.name} 허용`} disabled={!ok} title={why} checked={!!t.rule?.allowed} onChange={(e) => setRule(t, { allowed: e.target.checked })} /></td>
                        <td className="py-2"><Select aria-label="위험도" disabled={!ok} title={why} value={t.rule?.risk ?? 'low'} onChange={(e) => setRule(t, { risk: e.target.value, approval_required: e.target.value === 'high' || t.rule?.approval_required })} className="h-7 w-24 text-xs"><option>low</option><option>medium</option><option>high</option></Select></td>
                        <td className="py-2"><input type="checkbox" aria-label={`${t.name} 승인 필요`} disabled={!ok} title={why} checked={!!t.rule?.approval_required} onChange={(e) => setRule(t, { approval_required: e.target.checked })} /></td>
                        <td className="py-2"><Select aria-label="승인자" disabled={!ok || !t.rule?.approval_required} value={t.rule?.approver ?? 'requester'} onChange={(e) => setRule(t, { approver: e.target.value })} className="h-7 w-28 text-xs"><option value="requester">요청자</option><option value="approvers">별도 승인자</option></Select></td>
                      </>}</Gate>
                      <td className="py-2 pr-3 text-right"><Link className="text-xs text-primary" to={`/console/mcp-servers/${id}/inspect?tool=${t.name}`}>시험 →</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!s.tools?.length && <p className="p-4 text-sm text-fg-muted">도구가 없습니다 — 새로고침으로 tools/list 를 조회하세요.</p>}
              <p className="border-t border-line px-3 py-2 text-xs text-fg-muted">허용목록 기본 거부 · 규칙이 없는 도구는 호출되지 않습니다 (FR-G07). 고위험은 사람 승인(SCR-C04).</p>
            </Card>
          )}
          {tab === 'resources' && (
            <Card bodyClass="p-0">
              <div className="p-3">{rpcErr(resources)}{rpcErr(templates)}{resources.isLoading && <Loading rows={2} />}</div>
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">URI</th><th>이름</th><th>MIME</th><th>설명</th><th /></tr></thead>
                <tbody>
                  {resItems.map((r) => (
                    <Fragment key={r.uri}>
                      <tr className="border-t border-line align-top">
                        <td className="mono px-3 py-2 text-xs">{r.uri}</td><td className="py-2">{r.name}</td><td className="py-2 text-xs">{r.mimeType ?? '—'}</td>
                        <td className="max-w-xs py-2 text-xs text-fg-muted">{r.description}</td>
                        <td className="py-2 pr-3 text-right"><Button size="sm" variant="ghost" onClick={() => readIt(`r:${r.uri}`, 'resources/read', { uri: r.uri })}>읽기</Button></td>
                      </tr>
                      {readOut(`r:${r.uri}`)}
                    </Fragment>
                  ))}
                  {tplItems.map((r) => (
                    <tr key={r.uriTemplate} className="border-t border-line align-top">
                      <td className="mono px-3 py-2 text-xs">{r.uriTemplate}</td><td className="py-2">{r.name} <Badge>템플릿</Badge></td><td className="py-2 text-xs">{r.mimeType ?? '—'}</td>
                      <td className="max-w-xs py-2 text-xs text-fg-muted">{r.description}</td><td />
                    </tr>
                  ))}
                </tbody>
              </table>
              {resources.data && !resItems.length && !tplItems.length && <p className="p-4 text-sm text-fg-muted">resources/list 결과가 없습니다.</p>}
              <p className="border-t border-line px-3 py-2 text-xs text-fg-muted">resources/list · resources/templates/list (게이트웨이 경유) — 채팅 '/' 메뉴와 Retriever 가 사용합니다 (SCR-C08). 읽기 결과는 소독 규칙 적용.</p>
            </Card>
          )}
          {tab === 'prompts' && (
            <Card bodyClass="p-0">
              <div className="p-3">{rpcErr(prompts)}{prompts.isLoading && <Loading rows={2} />}</div>
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">프롬프트</th><th>설명</th><th>인자</th><th /><th /></tr></thead>
                <tbody>
                  {promptItems.map((p) => (
                    <Fragment key={p.name}>
                      <tr className="border-t border-line align-top">
                        <td className="mono px-3 py-2">{p.name}</td><td className="max-w-sm py-2 text-xs text-fg-muted">{p.description}</td>
                        <td className="py-2 text-xs">{(p.arguments ?? []).map((a: Json) => <span key={a.name} className="mono mr-1">{a.name}{a.required ? '*' : ''}</span>)}{!p.arguments?.length && '—'}</td><td />
                        <td className="py-2 pr-3 text-right"><Button size="sm" variant="ghost" onClick={() => readIt(`p:${p.name}`, 'prompts/get', { name: p.name, arguments: {} })}>가져오기</Button></td>
                      </tr>
                      {readOut(`p:${p.name}`)}
                    </Fragment>
                  ))}
                </tbody>
              </table>
              {prompts.data && !promptItems.length && <p className="p-4 text-sm text-fg-muted">prompts/list 결과가 없습니다.</p>}
              <p className="border-t border-line px-3 py-2 text-xs text-fg-muted">prompts/list (게이트웨이 경유) — 채팅 '/' 메뉴에 노출됩니다 (SCR-C08). 필수 인자(*)가 있으면 Inspector 에서 시험하세요.</p>
            </Card>
          )}
          {tab === 'logs' && (
            <div className="space-y-4">
              <Card title="헬스" actions={<RestartButton id={id} onDone={() => { q.refetch(); logs.refetch() }} />}>
                <Kv rows={[['상태', <StatusBadge status={s.health} />], ['상세', s.health_detail ?? '—'], ['확인', fmtTime(s.health_checked_at, true)], ['도구 갱신', s.tools_refreshed_at ? `${fmtTime(s.tools_refreshed_at, true)} (${fmtAgo(s.tools_refreshed_at)})` : '없음'], ['상태(등록)', <StatusBadge status={s.status} />]]} />
              </Card>
              <Card title={`최근 로그 ${logRows.length}`} bodyClass="p-0">
                {logs.error ? <div className="p-3"><ErrorBox error={logs.error} /></div> : (
                  <table className="w-full text-sm">
                    <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">시각</th><th>레벨</th><th>메시지</th><th>trace</th></tr></thead>
                    <tbody>{logRows.map((l, i) => (
                      <tr key={i} className="border-t border-line align-top">
                        <td className="px-3 py-1.5 text-xs whitespace-nowrap">{fmtTime(l.ts, true)}</td>
                        <td className="py-1.5"><Badge tone={/^(ERROR|CRITICAL)$/i.test(l.level) ? 'danger' : /^WARN/i.test(l.level) ? 'warn' : 'muted'}>{l.level}</Badge></td>
                        <td className="py-1.5 text-xs">{l.message}</td><td className="py-1.5 pr-3"><TraceLink id={l.trace_id} /></td>
                      </tr>
                    ))}</tbody>
                  </table>
                )}
                {logs.data && !logRows.length && <p className="p-4 text-sm text-fg-muted">이 서버에 관한 로그가 없습니다.</p>}
              </Card>
              <Card title={`감사 이벤트 ${events.length}`} bodyClass="p-0">
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">시각</th><th>동작</th><th>주체</th><th>대상</th><th>판정</th></tr></thead>
                  <tbody>{events.map((e, i) => (
                    <tr key={i} className="border-t border-line">
                      <td className="px-3 py-1.5 text-xs whitespace-nowrap">{fmtTime(e.ts, true)}</td><td className="mono py-1.5 text-xs">{e.action}</td>
                      <td className="py-1.5 text-xs">{e.actor}</td><td className="mono py-1.5 text-xs">{e.target}</td><td className="py-1.5">{e.decision ? <Badge>{e.decision}</Badge> : '—'}</td>
                    </tr>
                  ))}</tbody>
                </table>
                {logs.data && !events.length && <p className="p-4 text-sm text-fg-muted">감사 이벤트가 없습니다.</p>}
              </Card>
            </div>
          )}
          {tab === 'auth' && (
            <div className="grid grid-cols-2 gap-4">
              <Card title="MCP Authorization (RFC 9728)"><Kv rows={[['방식', s.auth_type], ['Authorization Server', <span className="mono text-xs">{s.authorization_server || '—'}</span>], ['상세', <CodeBlock value={s.auth ?? {}} maxH="max-h-40" />]]} /></Card>
              <Card title="토큰 교환(RFC 8693) 정책"><Kv rows={[['audience', <span className="mono">mcp:{s.id}</span>], ['허용 스코프', (s.token_exchange?.allowed_scopes ?? []).join(', ') || '—'], ['기본 스코프', (s.token_exchange?.default_scopes ?? []).join(', ') || '—'], ['도구별 스코프', <CodeBlock value={s.token_exchange?.tool_scopes ?? {}} maxH="max-h-40" />]]} /></Card>
            </div>
          )}
          {tab === 'meta' && <CodeBlock value={s.server_json ?? {}} maxH="max-h-[600px]" />}
        </div>
        <aside className="space-y-4">
          <Card title="샘플링 요청">
            <Gate perm="mcp:write">{(ok, why) => (
              <fieldset className="space-y-2 text-sm" disabled={!ok || sampling} title={why}>
                <legend className="sr-only">샘플링 요청 정책</legend>
                <label className="flex items-center gap-2"><input type="radio" name="sampling" checked={policy === 'ask'} onChange={() => setPolicy('ask')} />매번 사용자 동의 (기본)</label>
                <label className="flex items-center gap-2"><input type="radio" name="sampling" checked={policy === 'deny'} onChange={() => setPolicy('deny')} />항상 거부</label>
              </fieldset>
            )}</Gate>
            <p className="mt-2 text-xs text-fg-muted">서버가 모델 호출(sampling)을 요청하면 요청한 사용자 채팅에 동의 카드(MSG-016)를 띄웁니다 · 모델은 라우팅 정책을 따릅니다.</p>
          </Card>
          {s.transport === 'streamable-http' && (
            <Card title="업스트림 프로토콜">
              <Kv rows={[['발견', s.upstream_protocol ? <Badge tone={s.upstream_protocol === '2026-07-28' ? 'mcp' : 'muted'}>{s.upstream_protocol}</Badge> : '— (도구 새로고침 때 발견)'],
                ['호출', (s.protocol === '2026-07-28' || (s.protocol === 'auto' && s.upstream_protocol === '2026-07-28')) ? '세션 없이 요청마다 (stateless)' : 'initialize · 세션']]} />
              <Gate perm="mcp:write">{(ok, why) => (
                <div className="mt-2"><Field label="업스트림 프로토콜 선택">
                  <Select disabled={!ok} title={why} value={s.protocol ?? 'auto'} onChange={(e) => setProtocol(e.target.value)} className="h-8 text-sm">
                    <option value="auto">자동 (server/discover)</option><option value="legacy">2025-11-25 고정 (세션)</option><option value="2026-07-28">2026-07-28 고정</option>
                  </Select>
                </Field></div>
              )}</Gate>
              <p className="mt-2 text-xs text-fg-muted">2026-07-28 업스트림은 InputRequiredResult 로 샘플링·추가 입력을 묻고, 게이트웨이가 같은 동의·중계 경로로 답해 다시 호출합니다 (D14-②).</p>
            </Card>
          )}
          <Card title="헬스·로그" actions={<button type="button" className="text-xs text-primary" onClick={() => setTab('logs')}>전체 →</button>}>
            <ul className="space-y-1 text-sm">
              <li className={s.health === 'healthy' ? 'text-ok' : 'text-danger'}>● readiness {s.health}{s.health_detail ? ` · ${s.health_detail}` : ''}</li>
              <li className="text-xs text-fg-muted">확인 {fmtAgo(s.health_checked_at)} · 도구 갱신 {s.tools_refreshed_at ? fmtAgo(s.tools_refreshed_at) : '없음'}</li>
            </ul>
            {logRows.length > 0 && <CodeBlock className="mt-2" value={logRows.slice(0, 3).map((l) => `${fmtTime(l.ts)} ${l.level} ${l.message}${l.trace_id ? ` · trace ${shortId(l.trace_id)}` : ''}`).join('\n')} maxH="max-h-32" />}
          </Card>
          <Card title="MCP 클라이언트 연결 (호스트 → 게이트웨이)">
            <ul className="space-y-1.5 text-xs">
              <li>원격: Streamable HTTP → <span className="mono">{s.gateway_path}</span> (직접 연결 차단, MSG-005)</li>
              <li>로컬 stdio: 호스트 사이드카만 허용</li>
              <li>세션: Mcp-Session-Id 유지 · 끊기면 자동 재연결 3회 후 안내 (ST-08) — <Link className="text-primary" to="/console/gateway/mcp">프록시 세션</Link></li>
            </ul>
          </Card>
          <Card title="레지스트리 등록">
            <Kv rows={[['server.json', <span className="mono text-xs">{s.server_json?.name ?? '—'}</span>], ['버전', s.version ?? '—'], ['소유자', s.owner ?? '—'], ['상태', <StatusBadge status={s.status} />],
              ['카탈로그', s.catalog_item_id ? <Link className="text-xs text-primary" to={`/console/catalog/${s.catalog_item_id}`}>{shortId(s.catalog_item_id, 6)} →</Link> : '—']]} />
            <p className="mt-2 text-xs text-fg-muted">갱신은 <Link className="text-primary" to="/console/registry/publish">메타데이터 등록(E04)</Link> → <Link className="text-primary" to="/console/registry/approvals">승인(E05)</Link></p>
          </Card>
        </aside>
      </div>
    </>
  )
}

// ───────────────────────── Inspector (SCR-F06)─────────────────────────
function exampleArgs(schema?: Json) {
  const out: Json = {}
  for (const [k, v] of Object.entries<Json>(schema?.properties ?? {})) out[k] = v.default ?? (v.type === 'integer' || v.type === 'number' ? 0 : v.type === 'boolean' ? false : '')
  return JSON.stringify(out, null, 2)
}

type FieldDef = { name: string; kind: 'string' | 'integer' | 'number' | 'boolean' | 'enum' | 'json'; required: boolean; desc?: string; options?: unknown[]; def?: unknown }
type FormVals = Record<string, string | boolean>

/** inputSchema → 폼 필드. anyOf/oneOf 의 null 은 선택 항목으로, 객체·배열은 필드별 JSON 입력으로. */
function fieldsOf(schema?: Json): FieldDef[] {
  const req = new Set<string>(schema?.required ?? [])
  return Object.entries<Json>(schema?.properties ?? {}).map(([name, p]) => {
    const alts: Json[] = p.anyOf ?? p.oneOf ?? [p]
    const b = alts.find((v) => v.type !== 'null') ?? p
    const t = Array.isArray(b.type) ? b.type.find((x: string) => x !== 'null') : b.type
    const options = b.enum ?? p.enum
    const kind: FieldDef['kind'] = options ? 'enum' : ['string', 'integer', 'number', 'boolean'].includes(t) ? t : 'json'
    return { name, kind, required: req.has(name), desc: p.description ?? b.description ?? p.title, options, def: p.default ?? b.default }
  })
}

const toVal = (f: FieldDef, v: unknown): string | boolean => f.kind === 'boolean' ? !!v : v == null ? '' : f.kind === 'json' ? JSON.stringify(v) : String(v)
const initVals = (fs: FieldDef[]): FormVals => Object.fromEntries(fs.map((f) => [f.name, toVal(f, f.def)]))

function buildArgs(fs: FieldDef[], v: FormVals): { args: Json; errors: Record<string, string> } {
  const args: Json = {}
  const errors: Record<string, string> = {}
  for (const f of fs) {
    if (f.kind === 'boolean') {
      args[f.name] = !!v[f.name]
      continue
    }
    const s = String(v[f.name] ?? '').trim()
    if (!s) {
      if (f.required) errors[f.name] = '필수 항목입니다 (MSG-004)'
      continue
    }
    if (f.kind === 'integer') {
      if (/^-?\d+$/.test(s)) args[f.name] = Number(s)
      else errors[f.name] = '정수 형식이 아닙니다 (MSG-004)'
    } else if (f.kind === 'number') {
      if (Number.isFinite(Number(s))) args[f.name] = Number(s)
      else errors[f.name] = '숫자 형식이 아닙니다 (MSG-004)'
    } else if (f.kind === 'json') {
      try {
        args[f.name] = JSON.parse(s)
      } catch {
        errors[f.name] = 'JSON 형식 오류 (MSG-004)'
      }
    } else if (f.kind === 'enum') {
      args[f.name] = (f.options ?? []).find((o) => String(o) === s) ?? s
    } else args[f.name] = s
  }
  return { args, errors }
}

function SchemaForm({ fields, values, errors, onChange }: { fields: FieldDef[]; values: FormVals; errors: Record<string, string>; onChange: (v: FormVals) => void }) {
  if (!fields.length) return <p className="text-xs text-fg-muted">인자가 없는 도구입니다.</p>
  return (
    <div className="space-y-2">
      {fields.map((f) => {
        const set = (x: string | boolean) => onChange({ ...values, [f.name]: x })
        const hint = [f.kind, f.desc].filter(Boolean).join(' · ')
        const label = `${f.name}${f.required ? ' (필수)' : ''}`
        if (f.kind === 'boolean') return (
          <label key={f.name} className="flex items-center gap-2 text-xs"><input type="checkbox" checked={!!values[f.name]} onChange={(e) => set(e.target.checked)} />{label} <span className="text-fg-muted">{hint}</span></label>
        )
        return (
          <Field key={f.name} label={label} hint={hint} error={errors[f.name]}>
            {f.kind === 'enum' ? (
              <Select value={String(values[f.name] ?? '')} onChange={(e) => set(e.target.value)}><option value="">{f.required ? '선택…' : '(미지정)'}</option>{(f.options ?? []).map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}</Select>
            ) : f.kind === 'json' ? (
              <Textarea className="mono h-16 text-xs" value={String(values[f.name] ?? '')} onChange={(e) => set(e.target.value)} />
            ) : (
              <Input className={f.kind !== 'string' ? 'mono' : undefined} inputMode={f.kind === 'string' ? undefined : 'decimal'} value={String(values[f.name] ?? '')} onChange={(e) => set(e.target.value)} />
            )}
          </Field>
        )
      })}
    </div>
  )
}

// 게이트웨이 MCP 엔드포인트(/mcp/{id}) 세션 — initialize · ping · DELETE (Streamable HTTP)
const MCP_VERSION = '2025-11-25'
const MCP_ACCEPT = 'application/json, text/event-stream'
type Sess = { sid: string | null; protocol?: string; server?: string; at: string; reconnects: number }

async function mcpInit(id: string) {
  const res = await request(`/mcp/${id}`, { method: 'POST', headers: { Accept: MCP_ACCEPT },
    body: { jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: MCP_VERSION, capabilities: {}, clientInfo: { name: 'agentnexus-console-inspector', version: '1' } } } })
  const j = await res.json()
  return { sid: res.headers.get('Mcp-Session-Id'), result: (j.result ?? {}) as Json }
}
async function mcpPing(id: string, sess: Sess): Promise<boolean> {
  if (!sess.sid) return false
  try {
    await request(`/mcp/${id}`, { method: 'POST', headers: { Accept: MCP_ACCEPT, 'Mcp-Session-Id': sess.sid, 'MCP-Protocol-Version': sess.protocol ?? MCP_VERSION }, body: { jsonrpc: '2.0', id: 2, method: 'ping' } })
    return true
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return false
    throw e
  }
}
const mcpClose = (id: string, sid: string) => request(`/mcp/${id}`, { method: 'DELETE', headers: { 'Mcp-Session-Id': sid } }).catch(() => undefined)

type Check = { key: string; label: string; level: 'ok' | 'warn' | 'fail'; detail?: string }
const DOT = { ok: 'text-ok', warn: 'text-warn', fail: 'text-danger' }
const RISK_DOT = (t: Json) => !t.allowed ? 'bg-muted' : t.risk === 'high' ? 'bg-danger' : t.risk === 'medium' ? 'bg-warn' : 'bg-ok'

export function Inspector() {
  const { id = '' } = useParams()
  return <InspectorView key={id} id={id} />
}

function InspectorView({ id }: { id: string }) {
  const nav = useNavigate()
  const { me } = useAuth()
  const [sp] = useSearchParams()
  const s = useGet(`/api/v1/mcp-servers/${id}`)
  const servers = useGet('/api/v1/mcp-servers')
  const tools: Json[] = s.data?.tools ?? []
  const [tool, setTool] = useState(sp.get('tool') || '')
  const current = useMemo(() => tools.find((t) => t.name === tool), [tools, tool])
  const fields = useMemo(() => fieldsOf(current?.inputSchema), [current])
  const [mode, setMode] = useState<'form' | 'json'>('form')
  const [form, setForm] = useState<FormVals>({})
  const [formErr, setFormErr] = useState<Record<string, string>>({})
  const [args, setArgs] = useState('')
  const [dc, setDc] = useState('internal')
  const [approval, setApproval] = useState('')
  const [asTest, setAsTest] = useState(false)  // SCR-F06 ⑤ 시험 전용 가상 사용자 — 역할·팀만, 쓰기·위험 도구는 드라이런
  const [testRoles, setTestRoles] = useState('user')
  const [testTeam, setTestTeam] = useState('')
  const [res, setRes] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)
  // 집계 도구 검색 (게이트웨이 /mcp/_all — 여러 서버 도구를 네임스페이스로)
  const [tq, setTq] = useState('')
  const dq = useDeferredValue(tq.trim())
  const agg = useGet('/api/v1/gateway/mcp/tools', { q: dq || undefined }, { retry: false })
  // 세션
  const [sess, setSess] = useState<Sess | null>(null)
  const [sessErr, setSessErr] = useState<unknown>(null)
  const [connecting, setConnecting] = useState(false)
  const sidRef = useRef<string | null>(null)
  const [checks, setChecks] = useState<Check[] | null>(null)
  const [checking, setChecking] = useState(false)

  const pick = useCallback((name: string) => {
    const t = tools.find((x) => x.name === name)
    setTool(name)
    setArgs(exampleArgs(t?.inputSchema))
    setForm(initVals(fieldsOf(t?.inputSchema)))
    setFormErr({})
    setApproval('')
  }, [tools])
  const qTool = sp.get('tool')
  useEffect(() => {  // ?tool= 이 바뀌거나 도구 목록이 처음 도착하면 폼 초기화
    if (qTool && tools.length) pick(qTool)
  }, [qTool, tools.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const connect = useCallback(async (prev: string | null, reconnects: number, attempts = 1) => {
    setConnecting(true)
    setSessErr(null)
    if (prev) await mcpClose(id, prev)
    let last: unknown = null
    for (let i = 0; i < attempts; i++) {
      try {
        const r = await mcpInit(id)
        sidRef.current = r.sid
        setSess({ sid: r.sid, protocol: r.result.protocolVersion, server: r.result.serverInfo?.name, at: new Date().toISOString(), reconnects: reconnects + i })
        setConnecting(false)
        return
      } catch (e) {
        last = e
      }
    }
    sidRef.current = null
    setSess(null)
    setSessErr(last)
    setConnecting(false)
  }, [id])
  useEffect(() => {
    connect(null, 0)
    return () => {
      if (sidRef.current) mcpClose(id, sidRef.current)
    }
  }, [connect, id])
  /** ST-08 — 세션이 끊겼으면(404) 최대 3회 자동 재연결 */
  const ensureSession = async () => {
    if (!sess) return
    if (!(await mcpPing(id, sess).catch(() => true))) await connect(null, sess.reconnects + 1, 3)
  }

  const switchMode = (m: 'form' | 'json') => {
    if (m === mode) return
    if (m === 'json') setArgs(JSON.stringify(buildArgs(fields, form).args, null, 2))
    else {
      try {
        const o = JSON.parse(args || '{}')
        setForm(Object.fromEntries(fields.map((f) => [f.name, toVal(f, o[f.name] ?? (f.kind === 'boolean' ? false : null))])))
      } catch {
        setErr(new ApiError(400, 'invalid_json', 'arguments JSON 형식 오류 — 고친 뒤 폼으로 전환하세요 (MSG-004)'))
        return
      }
    }
    setErr(null)
    setMode(m)
  }

  const call = async (method: string) => {
    setBusy(true)
    setErr(null)
    try {
      let body: Json
      if (method === 'tools/call') {
        let a: Json
        if (mode === 'form') {
          const b = buildArgs(fields, form)
          setFormErr(b.errors)
          if (Object.keys(b.errors).length) return
          a = b.args
        } else {
          try {
            a = JSON.parse(args || exampleArgs(current?.inputSchema))
          } catch {
            setErr(new ApiError(400, 'invalid_json', 'arguments JSON 형식 오류 (MSG-004)'))
            return
          }
        }
        await ensureSession()
        body = { method, params: { name: tool, arguments: a }, data_class: dc, approval_id: approval || undefined,
          ...(asTest ? { test_principal: { roles: testRoles.split(/[\s,]+/).filter(Boolean), team: testTeam || undefined } } : {}) }
      } else body = { method, params: {} }
      const r = await send<Json>('POST', `/api/v1/mcp-servers/${id}/inspect`, body)
      setRes(r)
      const ap = r.error?.data?.approval_id
      if (ap) setApproval(ap)
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }

  const runChecks = async () => {  // 사양 적합성 (SCR-F06 ⑦)
    setChecking(true)
    const out: Check[] = []
    const add = (key: string, label: string, level: Check['level'], detail?: string) => out.push({ key, label, level, detail })
    add('init', 'initialize', sess?.sid ? 'ok' : 'fail', sess ? `${sess.protocol ?? '?'} · 세션 ${shortId(sess.sid)}` : '세션 없음 — 재연결하세요')
    const noSchema = tools.filter((t) => t.inputSchema?.type !== 'object').map((t) => t.name)
    add('tools', 'tools 스키마', tools.length && !noSchema.length ? 'ok' : 'warn', !tools.length ? '도구 없음' : noSchema.length ? `inputSchema 없음: ${noSchema.join(', ')}` : `${tools.length}개 inputSchema(object)`)
    for (const [key, m] of [['resources', 'resources/list'], ['prompts', 'prompts/list']] as const) {
      try {
        const r = await inspectCall(id, m)
        add(key, m, r.error ? 'warn' : 'ok', r.error ? `${r.error.code} ${r.error.message}` : `${(r.result?.[key] ?? []).length}개`)
      } catch (e: any) {
        add(key, m, 'fail', e.message)
      }
    }
    try {
      const r = await inspectCall(id, 'agentnexus/conformance-probe')
      add('jsonrpc', 'JSON-RPC 오류 코드', r.error?.code === -32601 ? 'ok' : 'fail', r.error ? `${r.error.code} ${r.error.message}` : '알 수 없는 메서드에 오류가 없음')
    } catch (e: any) {
      add('jsonrpc', 'JSON-RPC 오류 코드', 'fail', e.message)
    }
    if (sess?.sid) {
      try {
        const alive = await mcpPing(id, sess)
        add('session', '세션 유지 (Mcp-Session-Id)', alive ? 'ok' : 'fail', alive ? 'ping 응답' : '404 — 세션 만료, 재연결 필요')
      } catch (e: any) {
        add('session', '세션 유지 (Mcp-Session-Id)', 'fail', e.message)
      }
    } else add('session', '세션 유지 (Mcp-Session-Id)', 'fail', '세션 없음')
    try {
      const prm = await get(`/.well-known/oauth-protected-resource/mcp/${id}`)
      add('prm', '인가 발견 (RFC 9728)', prm.authorization_servers?.length ? 'ok' : 'fail', prm.resource)
    } catch (e: any) {
      add('prm', '인가 발견 (RFC 9728)', 'fail', e.message)
    }
    try {  // dual-era — stateless 2026-07-28 (D13-④): 세션 없이 server/discover
      const meta = { 'io.modelcontextprotocol/protocolVersion': '2026-07-28', 'io.modelcontextprotocol/clientCapabilities': {} }
      const d = await api<Json>(`/mcp/${id}`, { method: 'POST', body: { jsonrpc: '2.0', id: 'discover', method: 'server/discover', params: { _meta: meta } },
        headers: { Accept: 'application/json, text/event-stream', 'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': 'server/discover' } })
      add('modern', 'MCP 2026-07-28 (server/discover)', d.result?.resultType === 'complete' ? 'ok' : 'fail', (d.result?.supportedVersions ?? []).join(' · '))
    } catch (e: any) {
      add('modern', 'MCP 2026-07-28 (server/discover)', 'fail', e.message)
    }
    add('progress', 'progress 알림', 'warn', '선택 사양 — 게이트웨이가 중계하지 않음')
    setChecks(out)
    setChecking(false)
  }

  const a = s.data?.auth ?? {}
  const authMode = s.data?.auth_type === 'oauth2' ? (a.mode ?? 'exchange') : s.data?.auth_type
  const scope = (tool && a.tool_scopes?.[tool]) || (s.data?.token_exchange?.default_scopes ?? []).join(' ')
  const aggItems: Json[] = agg.data?.items ?? []
  const passed = checks?.filter((c) => c.level === 'ok').length ?? 0
  return (
    <>
      <PageHeader title={`MCP Inspector — ${s.data?.name ?? id}`} crumbs={['MCP 서버', id, 'Inspector']} desc="게이트웨이 경로 그대로 호출 — 정책·승인·토큰 교환·응답 소독이 실제와 같이 적용됩니다." />
      <div className="grid gap-4 xl:grid-cols-[280px_1fr_280px]">
        <div className="space-y-4">
          <Card title="세션">
            <div className="space-y-3">
              <Field label="서버">
                <Select value={id} onChange={(e) => nav(`/console/mcp-servers/${e.target.value}/inspect`)}>
                  {(servers.data?.items ?? [{ id }]).map((x: Json) => <option key={x.id} value={x.id}>{x.id}</option>)}
                </Select>
              </Field>
              <Kv rows={[['경로', <span className="mono text-xs">/mcp/{id}</span>], ['프로토콜', <span className="mono text-xs">{sess?.protocol ?? '—'}</span>],
                ['세션', <span className="mono text-xs" title={sess?.sid ?? undefined}>{sess?.sid ? shortId(sess.sid, 6) : '—'}</span>],
                ['initialize', connecting ? '연결 중…' : sess ? <span className="text-ok">✓ {fmtTime(sess.at)}</span> : <span className="text-danger">✗</span>],
                ['재연결', `${sess?.reconnects ?? 0}회`]]} />
              {sessErr ? <ErrorBox error={sessErr} /> : null}
              <Button size="sm" className="w-full" loading={connecting} onClick={() => connect(sess?.sid ?? null, (sess?.reconnects ?? 0) + 1)}>재연결</Button>
              <p className="text-xs text-fg-muted">Mcp-Session-Id 유지 · 끊기면 호출 전에 자동 재연결 3회 (ST-08)</p>
            </div>
          </Card>
          <Card title="tools/list (집계)" bodyClass="p-3">
            <Input aria-label="도구 검색" placeholder="전체 서버 도구 검색…" value={tq} onChange={(e) => setTq(e.target.value)} />
            {agg.error ? (
              <p className="mt-2 text-xs text-fg-muted">집계 검색은 gateway:read 권한이 필요합니다 — 이 서버 도구만 표시합니다.</p>
            ) : null}
            <ul className="mt-2 max-h-80 space-y-0.5 overflow-auto text-xs">
              {(agg.error ? tools.map((t) => ({ qualified: `${id}.${t.name}`, server_id: id, tool: t.name, allowed: !!t.rule?.allowed, risk: t.rule?.risk })) : aggItems).map((t: Json) => (
                <li key={t.qualified}>
                  <button type="button" onClick={() => t.server_id === id ? pick(t.tool) : nav(`/console/mcp-servers/${t.server_id}/inspect?tool=${encodeURIComponent(t.tool)}`)}
                    className={cx('flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left hover:bg-surface-2', t.server_id === id && t.tool === tool && 'bg-primary-soft text-primary')}>
                    <span className="mono truncate">{t.qualified}</span>
                    <span className={cx('size-2 shrink-0 rounded-full', RISK_DOT(t))} title={t.allowed ? `${t.risk ?? 'low'}${t.approval_required ? ' · 승인 필요' : ''}` : '미허용'} aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
            {agg.data && <p className="mt-2 text-xs text-fg-muted">{aggItems.length}개 · 집계 엔드포인트 <span className="mono">{agg.data.aggregate_endpoint}</span> — 도구명 '&lt;서버&gt;.&lt;도구&gt;'</p>}
          </Card>
        </div>
        <div className="min-w-0 space-y-4">
          <Card title={tool ? `tools/call — ${id}.${tool}` : '요청'}>
            <div className="space-y-3">
              <div className="flex gap-2"><Button size="sm" onClick={() => call('tools/list')} loading={busy}>tools/list</Button><Button size="sm" onClick={() => call('resources/list')}>resources/list</Button><Button size="sm" onClick={() => call('prompts/list')}>prompts/list</Button></div>
              <label className="block text-xs text-fg-muted">도구
                <Select value={tool} onChange={(e) => pick(e.target.value)} className="mt-1">
                  <option value="">선택…</option>{tools.map((t) => <option key={t.name} value={t.name}>{t.name}{t.rule?.risk === 'high' ? ' (고위험)' : ''}</option>)}
                </Select>
              </label>
              {current && <div className="flex gap-1">{current.rule ? <RiskChip risk={current.rule.risk} /> : <Badge tone="danger">미허용 → 거부 예상</Badge>}{current.rule?.approval_required && <Badge tone="warn">승인 필요</Badge>}</div>}
              {current && (
                <div role="group" aria-label="인자 입력 방식" className="flex gap-1 text-xs">
                  <Button size="sm" variant={mode === 'form' ? 'primary' : 'ghost'} aria-pressed={mode === 'form'} onClick={() => switchMode('form')}>스키마 폼</Button>
                  <Button size="sm" variant={mode === 'json' ? 'primary' : 'ghost'} aria-pressed={mode === 'json'} onClick={() => switchMode('json')}>JSON</Button>
                </div>
              )}
              {current && mode === 'form'
                ? <SchemaForm fields={fields} values={form} errors={formErr} onChange={setForm} />
                : <label className="block text-xs text-fg-muted">arguments (JSON)<Textarea className="mono mt-1 h-40 text-xs" value={args || exampleArgs(current?.inputSchema)} onChange={(e) => setArgs(e.target.value)} /></label>}
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-xs text-fg-muted">데이터 등급<Select value={dc} onChange={(e) => setDc(e.target.value)} className="mt-1"><option>public</option><option>internal</option><option>confidential</option></Select></label>
                <label className="block text-xs text-fg-muted">approval_id (승인 후 재호출)<Input className="mono mt-1" value={approval} onChange={(e) => setApproval(e.target.value)} placeholder="apr_…" /></label>
              </div>
              <Button variant="primary" className="w-full" disabled={!tool} loading={busy} onClick={() => call('tools/call')}>tools/call 실행</Button>
            </div>
          </Card>
          <Card title="응답" actions={res && <span className="flex items-center gap-2 text-xs text-fg-muted">{res.error ? <Badge tone="danger">{res.error.code}</Badge> : <Badge tone="ok">ok</Badge>}{fmtMs(res.latency_ms)}<TraceLink id={res.trace_id} /></span>}>
            {err ? <ErrorBox error={err} /> : null}
            {res?.error?.data?.code === 'approval_required' && (
              <div className="mb-3 rounded-md border border-warn/50 bg-warn/10 p-3 text-sm">사람 승인이 필요합니다 — <Link className="text-primary underline" to="/console/gateway/approvals">승인 대기열</Link>에서 <span className="mono">{res.error.data.approval_id}</span> 승인 후 같은 요청을 다시 실행하세요.</div>
            )}
            {res ? <CodeBlock value={res.result ?? res.error ?? res} maxH="max-h-[560px]" /> : <p className="text-sm text-fg-muted">요청을 실행하면 JSON-RPC 응답이 표시됩니다.</p>}
            {res?.dry_run && <div role="status" className="mt-2 rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-sm">드라이런 — 쓰기·위험 도구라 호출하지 않았습니다 · 판정 <span className="mono">{res.decision?.code}</span>{res.decision?.reason ? ` — ${res.decision.reason}` : ''}</div>}
            {res?.test_principal && <p className="mt-1 text-xs text-fg-muted">시험 주체 <span className="mono">{res.test_principal.sub}</span> · 역할 {res.test_principal.roles.join(', ')}{res.test_principal.team ? ` · 팀 ${res.test_principal.team}` : ''} (감사에 실제 호출자로 기록)</p>}
            {res?.request?.method === 'tools/call' && <SanitizeNote result={res.result} />}
          </Card>
        </div>
        <div className="space-y-4">
          <Card title="호출 주체">
            <Kv rows={[['사용자', <span>{me?.name ?? '—'} <span className="mono text-xs text-fg-muted">{me?.sub}</span></span>],
              ['신원 전파', authMode === 'exchange' ? <Badge tone="ok">토큰 교환 OBO (RFC 8693)</Badge> : authMode === 'service' ? <Badge tone="warn">서비스 계정 — 사용자 신원 미전파</Badge> : authMode === 'none' ? <Badge tone="warn">인증 없음 — 신원 미전파</Badge> : <Badge tone="warn">{authMode} 서버 자격 증명 — 신원 미전파</Badge>],
              ['audience', <span className="mono text-xs">mcp:{id}</span>], ['스코프', <span className="mono text-xs">{scope || '—'}</span>]]} />
            <p className="mt-2 text-xs text-fg-muted">시험 호출은 내 토큰으로 실행 — 게이트웨이가 위 audience·스코프로 교환해 업스트림에 전달합니다.</p>
            <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={asTest} onChange={(e) => setAsTest(e.target.checked)} />시험 전용 가상 사용자로 호출</label>
            {asTest && (
              <div className="mt-2 grid grid-cols-2 gap-2">
                <Field label="시험 역할" hint="내 권한 이하만 (쉼표 구분)"><Input className="mono" value={testRoles} onChange={(e) => setTestRoles(e.target.value)} /></Field>
                <Field label="시험 팀"><Input value={testTeam} onChange={(e) => setTestTeam(e.target.value)} placeholder="team-…" /></Field>
                <p className="col-span-2 text-xs text-fg-muted">실제 사용자를 대리하지 않습니다 — 정책·한도는 이 역할·팀으로 판정하고, 쓰기·위험 도구는 호출하지 않고 판정만 보여 줍니다(드라이런).</p>
              </div>
            )}
          </Card>
          <Card title="사양 적합성" actions={<Button size="sm" loading={checking} onClick={runChecks}>점검</Button>}>
            {checks ? (
              <>
                <ul className="space-y-1 text-sm">{checks.map((c) => <li key={c.key} className={DOT[c.level]}>{c.level === 'ok' ? '✓' : c.level === 'warn' ? '!' : '✗'} <span className="text-fg">{c.label}</span>{c.detail && <span className="block pl-4 text-xs text-fg-muted">{c.detail}</span>}</li>)}</ul>
                <p className="mt-2 text-sm font-medium">통과 {passed}/{checks.length}</p>
              </>
            ) : <p className="text-xs text-fg-muted">initialize·tools 스키마·resources·prompts·JSON-RPC 오류 코드·세션·인가(RFC 9728) 를 게이트웨이 경로로 점검합니다.</p>}
          </Card>
        </div>
      </div>
    </>
  )
}
