// SCR-B02 운영 개요 — KPI(QPS·추가 지연·오류율·비용) · 프로토콜별 호출 추이 · 승인 대기 · 경보·이벤트 · 팀별 비용 · 구성요소 요약
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Link, useSearchParams } from 'react-router'
import { Badge, Card, ErrorBox, Kpi, Loading, PageHeader, RiskChip, Select } from '@/components/ui'
import { can, useAuth } from '@/lib/auth'
import { fmtAgo, fmtMs, fmtNum, fmtPct, fmtTime, fmtUsd } from '@/lib/format'
import { useGet } from '@/lib/query'

const WINDOWS = [[15, '최근 15분'], [60, '최근 1시간'], [1440, '최근 24시간'], [10080, '최근 7일']] as const

export function Overview() {
  const { me } = useAuth()
  const [sp, setSp] = useSearchParams()
  const win = Number(sp.get('w') || 60)
  const sum = useGet('/api/v1/observe/summary', { window_minutes: win }, { refetchInterval: 15_000 })
  const ts = useGet('/api/v1/observe/timeseries', { window_minutes: Math.max(win, 5), buckets: 12 }, { refetchInterval: 15_000 })
  const approvals = useGet('/api/v1/approvals', { status: 'pending' })
  const reg = useGet('/api/v1/registry/approvals', undefined, { enabled: can(me, 'catalog:approve') })
  const notes = useGet('/api/v1/notifications')
  const s = sum.data
  const series = (ts.data?.items ?? []).map((r: any) => ({ ...r, label: fmtTime(r.t).slice(0, 5) }))
  const p95 = s?.by_kind?.model?.p95_latency_ms
  const gwP95: number | null | undefined = s?.gateway_overhead_p95_ms
  const ftP95: number | null | undefined = s?.first_token_p95_ms
  const budgets: any[] = s?.team_budgets ?? []
  const topBudget = budgets[0]
  const toolPending: number = approvals.data?.items?.length ?? 0
  const regPending: number = s?.registry_pending ?? 0
  const maxTeam = Math.max(...(s?.cost_by_team ?? []).map((t: any) => t.cost_usd), 0.000001)
  return (
    <>
      <PageHeader title={`운영 개요 — ${WINDOWS.find((w) => w[0] === win)?.[1] ?? `${win}분`}`} crumbs={['개요']}
        actions={<Select aria-label="기간" value={win} onChange={(e) => setSp({ w: e.target.value })} className="w-36">{WINDOWS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>} />
      {sum.error && <ErrorBox error={sum.error} onRetry={() => sum.refetch()} />}
      {!s && !sum.error && <Loading rows={4} />}
      {s && (
        <div className="space-y-4">
          <div className="grid grid-cols-5 gap-3">
            <Kpi label="요청 QPS" value={s.qps.toFixed(2)} sub={`LLM ${s.by_kind?.model?.calls ?? 0} · MCP ${s.by_kind?.tool?.calls ?? 0} · A2A ${s.by_kind?.remote_agent?.calls ?? 0}`} />
            <Kpi label="게이트웨이 추가 지연 p95" value={fmtMs(gwP95)} tone={gwP95 == null ? undefined : gwP95 <= 30 ? 'ok' : 'warn'} sub="목표 ≤ 30ms (게이트웨이 스팬 − 업스트림)" />
            <Kpi label="오류율" value={fmtPct(s.error_rate)} tone={s.error_rate > 0.05 ? 'danger' : s.error_rate > 0.01 ? 'warn' : 'ok'} sub={`폴백 ${fmtPct(s.fallback_rate)}`} />
            <Kpi label="첫 토큰 p95" value={fmtMs(ftP95)} tone={ftP95 == null ? undefined : ftP95 <= 2000 ? 'ok' : 'warn'} sub={`목표 ≤ 2s (NFR-P1) · 모델 호출 p95 ${fmtMs(p95)}`} />
            <Kpi label="비용" value={fmtUsd(s.cost_usd)} tone={!topBudget ? undefined : topBudget.pct >= 100 ? 'danger' : topBudget.pct >= 80 ? 'warn' : undefined}
              sub={topBudget ? `팀 예산 최대 ${topBudget.pct}% (${topBudget.team}) · 토큰 ${fmtNum(s.tokens)}` : `토큰 ${fmtNum(s.tokens)}${s.top_models?.[0] ? ` · 최대 ${s.top_models[0].model}` : ''}`} />
          </div>
          <div className="grid grid-cols-3 gap-4">
            <Card title="프로토콜별 호출 추이" className="col-span-2">
              <div className="h-64" aria-label="프로토콜별 호출 차트">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={series}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                    <XAxis dataKey="label" fontSize={11} stroke="var(--fg-muted)" />
                    <YAxis allowDecimals={false} fontSize={11} stroke="var(--fg-muted)" />
                    <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--line)', fontSize: 12 }} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Bar dataKey="model" name="LLM" stackId="a" fill="#0C8599" isAnimationActive={false} />
                    <Bar dataKey="tool" name="MCP" stackId="a" fill="#3F51E5" isAnimationActive={false} />
                    <Bar dataKey="remote_agent" name="A2A" stackId="a" fill="#7048E8" isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <Link to="/console/observe/traces" className="text-xs text-primary hover:underline">트레이스 탐색 →</Link>
            </Card>
            <Card title={`승인 대기 ${toolPending + regPending}`} actions={<Link to="/console/gateway/approvals" className="text-xs text-primary">전체 보기 →</Link>}>
              <ul className="space-y-2">
                {(approvals.data?.items ?? []).slice(0, 3).map((a: any) => (
                  <li key={a.id} className="rounded-md border border-warn/40 bg-warn/5 p-2 text-sm">
                    <div className="flex items-center gap-2"><span className="mono truncate">{a.target}</span><RiskChip risk={a.risk} /></div>
                    <div className="text-xs text-fg-muted">{a.requester} · {fmtAgo(a.created_at)}</div>
                  </li>
                ))}
                {(reg.data?.items ?? []).slice(0, 2).map((r: any) => (
                  <li key={r.version_id ?? r.item?.id} className="rounded-md border border-primary/30 bg-primary-soft/40 p-2 text-sm">
                    <div className="flex items-center gap-2"><Badge tone="primary">등록</Badge><span className="truncate">{r.item?.display_name || r.item?.name}</span><span className="ml-auto text-xs text-fg-muted">{r.version}</span></div>
                    <div className="text-xs text-fg-muted">{r.created_by} · {fmtAgo(r.created_at)}</div>
                  </li>
                ))}
                {regPending > 0 && (
                  <li><Link to="/console/registry/approvals" className="flex items-center gap-2 rounded-md border border-line px-2 py-1.5 text-sm hover:bg-surface-2">
                    <span>등록 승인 대기 {regPending}건</span><span className="ml-auto text-xs text-primary">등록 승인 대기열 →</span>
                  </Link></li>
                )}
                {!toolPending && !regPending && <li className="text-sm text-fg-muted">대기 중인 승인이 없습니다.</li>}
              </ul>
            </Card>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Card title="경보·이벤트">
              <ul className="space-y-1.5 text-sm">
                {(notes.data?.items ?? []).slice(0, 6).map((n: any) => (
                  <li key={n.id} className="flex items-center gap-2">
                    <span className={`size-2 rounded-full ${n.severity === 'danger' || n.severity === 'error' ? 'bg-danger' : n.severity?.startsWith('warn') ? 'bg-warn' : 'bg-ok'}`} aria-hidden />
                    <span className="truncate">{n.title}</span><span className="ml-auto text-xs text-fg-muted">{fmtAgo(n.ts)}</span>
                  </li>
                ))}
                {!notes.data?.items?.length && <li className="text-fg-muted">이벤트 없음</li>}
              </ul>
            </Card>
            <Card title="팀별 비용·구성요소">
              {budgets.length > 0 ? (
                <ul className="space-y-2 text-sm" aria-label="팀 예산 소진율">
                  {budgets.map((b: any) => (
                    <li key={`${b.team}:${b.name}`} className="grid grid-cols-[120px_1fr_80px] items-center gap-2" title={`${b.name} · ${fmtUsd(b.used_usd)} / ${fmtUsd(b.budget_usd)}`}>
                      <span className="truncate">{b.team}</span>
                      <span className="h-2 rounded bg-surface-2"><span className={`block h-2 rounded ${b.pct >= 100 ? 'bg-danger' : b.pct >= 80 ? 'bg-warn' : 'bg-primary'}`} style={{ width: `${Math.min(b.pct, 100)}%` }} /></span>
                      <span className="text-right tabular-nums text-xs">{b.pct}%</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <>
                  <ul className="space-y-2 text-sm">
                    {(s.cost_by_team ?? []).map((t: any) => (
                      <li key={t.team} className="grid grid-cols-[120px_1fr_80px] items-center gap-2">
                        <span className="truncate">{t.team === '-' ? '(미지정)' : t.team}</span>
                        <span className="h-2 rounded bg-surface-2"><span className="block h-2 rounded bg-primary" style={{ width: `${(t.cost_usd / maxTeam) * 100}%` }} /></span>
                        <span className="text-right tabular-nums text-xs">{fmtUsd(t.cost_usd)}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-xs text-fg-muted">팀 예산이 없어 비용만 표시합니다 — <Link to="/console/gateway/limits" className="text-primary">Rate limits·예산</Link>에서 팀 예산을 설정하면 소진율(80%·100%)을 보여 줍니다.</p>
                </>
              )}
              <Link to="/console/observe/cost" className="mt-2 inline-block text-xs text-primary hover:underline">Usage & Cost →</Link>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
                <span>MCP 서버 {s.mcp_servers?.healthy}/{s.mcp_servers?.total} 정상</span>
                <Badge tone={s.rate_limit_backend?.ok ? 'ok' : 'danger'}>rate-limit {s.rate_limit_backend?.backend}</Badge>
                {s.providers?.circuit_open?.length > 0 && <Badge tone="warn">회로 차단 {s.providers.circuit_open.join(', ')}</Badge>}
                <Link to="/console/system" className="ml-auto text-xs text-primary">상세 → SCR-B05</Link>
              </div>
            </Card>
          </div>
        </div>
      )}
    </>
  )
}
