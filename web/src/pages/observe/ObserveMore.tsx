// SCR-K01 관측 파이프라인 상태 · K05 메트릭 대시보드·알림 규칙 (Prometheus /metrics · Grafana 대시보드 · 경보)
// · K07 예산·비용 알림 · K08 세션 리플레이 · K10 규제 리포트 내보내기(4단계)
import { createColumnHelper } from '@tanstack/react-table'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, CodeBlock, Empty, ErrorBox, Field, Gate, Input, Kpi, Kv, Loading, Masked, Modal, PageHeader, RiskChip, Select, StatusBadge, Stepper, TraceLink, cx, useToast, type Tone } from '@/components/ui'
import { request, type Json } from '@/lib/api'
import { can, useAuth } from '@/lib/auth'
import { fmtAgo, fmtMs, fmtNum, fmtPct, fmtTime, fmtUsd } from '@/lib/format'
import { send, useGet } from '@/lib/query'
import { LineChart, MiniBars, download } from './Observe'

const col = createColumnHelper<Json>()

const p95 = (v: number[]) => {
  if (!v.length) return null
  const s = [...v].sort((a, b) => a - b)
  return s[Math.min(s.length - 1, Math.round(0.95 * (s.length - 1)))]
}

export function Pipeline() {
  const q = useGet('/api/v1/observe/pipeline', undefined, { refetchInterval: 15_000 })
  const [hours, setHours] = useState(1)
  const ins = useGet('/api/v1/observe/instrumentation', { hours }, { refetchInterval: 15_000 })
  const [prom, setProm] = useState<'ok' | 'fail' | null>(null)
  useEffect(() => {
    request('/metrics', { headers: { Accept: 'text/plain' } }).then(() => setProm('ok'), () => setProm('fail'))
  }, [])
  const p = q.data
  const i = ins.data
  const cov: number | null = i?.coverage_pct ?? null
  const ingestAge = p?.receiver.last_ingest_at ? (Date.now() - new Date(p.receiver.last_ingest_at).getTime()) / 60_000 : null
  const backends: [string, Tone, string, string][] = p ? [
    ['내장 저장소(DB) · 트레이스', p.exporters.builtin_store.failed ? 'warn' : 'ok', p.exporters.builtin_store.failed ? '저하' : '정상', p.exporters.builtin_store.failed ? `실패 ${fmtNum(p.exporters.builtin_store.failed)} — 큐 버퍼 후 재전송 (ST-08)` : `내보냄 ${fmtNum(p.exporters.builtin_store.exported)}`],
    ['OTLP 수신기 (다른 컨테이너)', ingestAge == null ? 'muted' : ingestAge > 10 ? 'warn' : 'ok', ingestAge == null ? '수신 없음' : ingestAge > 10 ? '끊김 의심' : '정상', ingestAge == null ? '수신 기록 없음' : `마지막 ${fmtAgo(p.receiver.last_ingest_at)}`],
    ['외부 OTLP Collector', p.exporters.otlp.configured ? 'ok' : 'muted', p.exporters.otlp.configured ? '설정됨' : '미설정', p.exporters.otlp.endpoint ?? '커넥터(L02)에서 OTLP 내보내기 설정'],
    ['Prometheus /metrics', prom === 'ok' ? 'ok' : prom === 'fail' ? 'danger' : 'muted', prom === 'ok' ? '정상' : prom === 'fail' ? '응답 없음' : '확인 중', '멀티프로세스 집계'],
    ['Grafana 대시보드', 'muted', '분리 배포', 'AGPL · 코드 미포함 (MSG-023)'],
  ] : []
  return (
    <>
      <PageHeader title="관측 파이프라인 상태" crumbs={['관측·감사', '파이프라인']} desc={p?.semconv}
        actions={<Select aria-label="계측 기간" value={hours} onChange={(e) => setHours(Number(e.target.value))} className="w-28"><option value={1}>1시간</option><option value={24}>24시간</option><option value={168}>7일</option></Select>} />
      {q.error && <ErrorBox error={q.error} />}
      {ins.error && <ErrorBox error={ins.error} />}
      {p && (
        <>
          <div className="mb-4 grid grid-cols-5 gap-3">
            <Kpi label="계측률 (trace 가 붙은 실행)" value={cov == null ? '—' : `${cov}%`} tone={cov == null ? undefined : cov >= 100 ? 'ok' : cov >= 90 ? 'warn' : 'danger'}
              sub={i ? (i.runs ? `실행 ${fmtNum(i.traced_runs)}/${fmtNum(i.runs)} · 목표 100%` : `최근 ${hours}시간 실행 없음`) : undefined} />
            <Kpi label="내장 저장소 내보냄" value={fmtNum(p.exporters.builtin_store.exported)} sub={`실패 ${p.exporters.builtin_store.failed}`} tone={p.exporters.builtin_store.failed ? 'warn' : 'ok'} />
            <Kpi label="OTLP 수신(타 컨테이너)" value={fmtNum(p.receiver.ingested)} sub={`마지막 ${fmtAgo(p.receiver.last_ingest_at)}`} />
            <Kpi label="외부 OTLP 내보내기" value={p.exporters.otlp.configured ? '설정됨' : '없음'} sub={p.exporters.otlp.endpoint ?? 'OTLP_ENDPOINT 미설정'} />
            <Kpi label="로그(1시간)" value={fmtNum(p.logs_last_hour)} />
          </div>
          <div className="mb-4 grid grid-cols-2 gap-4">
            <Card title={`구성요소별 계측 (최근 ${hours}시간)`} bodyClass="p-0">
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-4 py-2">구성요소</th><th className="text-right">스팬</th><th className="text-right">스팬/분</th><th className="pr-4 text-right">trace</th></tr></thead>
                <tbody>{(i?.components ?? []).map((c: Json) => (
                  <tr key={c.service} className="border-t border-line"><td className="mono px-4 py-1.5 text-xs">{c.service}</td><td className="text-right tabular-nums">{fmtNum(c.spans)}</td>
                    <td className="text-right tabular-nums">{(c.spans / (hours * 60)).toFixed(1)}</td><td className="pr-4 text-right tabular-nums">{fmtNum(c.traces)}</td></tr>
                ))}</tbody>
              </table>
              {!i?.components?.length && <p className="px-4 py-3 text-xs text-danger">기간 내 스팬을 보낸 구성요소가 없습니다 — 계측 0% (헬스 복구 후 재계산)</p>}
              <p className="border-t border-line px-4 py-2 text-xs text-fg-muted">목표: 모든 실행에 trace_id (OTel 계측 100%) · 계측률 = trace 가 붙은 실행 / 전체 실행</p>
            </Card>
            <Card title="백엔드 상태" bodyClass="p-0">
              <ul className="divide-y divide-line text-sm">{backends.map(([name, tone, label, note]) => (
                <li key={name} className="flex items-center justify-between gap-3 px-4 py-2"><span>{name}</span><span className="flex items-center gap-2 text-right text-xs text-fg-muted">{note}<Badge tone={tone}>{label}</Badge></span></li>
              ))}</ul>
              <p className="border-t border-line px-4 py-2 text-xs"><Link to="/console/observe/metrics" className="text-primary hover:underline">게이트웨이 메트릭·Grafana 대시보드 JSON → 메트릭(K05)</Link></p>
            </Card>
          </div>
          <Card title="흐름">
            <Kv rows={[['앱 내부', 'OTel SDK → 내장 저장소(DB) · 선택 OTLP Collector'], ['다른 컨테이너', <span>OTLP/HTTP → <span className="mono">{p.receiver.endpoint}</span> (토큰)</span>],
              ['trace 전파', 'W3C traceparent — 게이트웨이 → MCP 서버 → 원격 에이전트 단일 trace_id'], ['조회', '트레이스(K02·K03) · 로그(K04) · 메트릭(K05)']]} />
          </Card>
        </>
      )}
    </>
  )
}

const BLANK = { name: '', metric: 'error_rate', op: '>', threshold: 0.05, window_s: 300, severity: 'warn' }
const WINDOWS: [number, string][] = [[15, '15분'], [60, '1시간'], [1440, '24시간'], [10080, '7일']]
const A2A_STATES = ['submitted', 'working', 'input-required', 'completed', 'failed', 'canceled']
const A2A_BAR: Record<string, string> = { completed: 'bg-ok', failed: 'bg-danger', 'input-required': 'bg-warn', working: 'bg-primary', submitted: 'bg-a2a', canceled: 'bg-muted' }
const normState = (s: string) => s.replace(/^TASK_STATE_/, '').toLowerCase().replace(/_/g, '-')

export function Metrics() {
  const toast = useToast()
  const nav = useNavigate()
  const [sp, setSp] = useSearchParams()
  const w = Number(sp.get('w') || 60)
  const alerts = useGet('/api/v1/observe/alerts')
  const sum = useGet('/api/v1/observe/summary', { window_minutes: w }, { refetchInterval: 30_000 })
  const ts = useGet('/api/v1/observe/timeseries', { window_minutes: w, buckets: 12 }, { refetchInterval: 30_000 })
  const hasP95 = (ts.data?.items ?? []).some((r: Json) => 'p95_latency_ms' in r)
  // 구간별 p95 — 시계열에 p95 가 없으면 trace 지연으로 대신 계산
  const traces = useGet(ts.data && !hasP95 ? '/api/v1/traces' : null, { since_minutes: w, limit: 500 })
  const a2a = useGet('/api/v1/observe/a2a', { hours: Math.max(1, Math.ceil(w / 60)) }, { refetchInterval: 30_000 })
  const [lines, setLines] = useState<string[]>([])
  const [mErr, setMErr] = useState<unknown>(null)
  const [edit, setEdit] = useState<Json | null>(null)
  const [dash, setDash] = useState<Json | null>(null)
  const load = () => request('/metrics', { headers: { Accept: 'text/plain' } }).then((r) => r.text()).then((t) => {
    setLines(t.split('\n').filter((l) => l.startsWith('anx_') && !l.includes('_created')).slice(0, 80))
    setMErr(null)
  }).catch(setMErr)
  useEffect(() => { load() }, [])
  const save = async () => {
    try {
      await send('POST', '/api/v1/observe/alerts', { ...edit, threshold: Number(edit!.threshold), window_s: Number(edit!.window_s) })
      setEdit(null)
      alerts.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const evaluate = async () => {
    try {
      const r = await send<Json>('POST', '/api/v1/observe/alerts/evaluate')
      toast('info', `발생 ${r.fired?.length ?? 0}건 — 알림 센터 확인`)
      alerts.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const grafana = async () => {
    try {
      setDash(await send<Json>('GET', '/api/v1/admin/connectors/grafana/dashboard'))
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const s = sum.data
  const bucketMs = (ts.data?.bucket_seconds ?? (w * 60) / 12) * 1000
  const tsItems: Json[] = ts.data?.items ?? []
  const points = useMemo(() => {
    if (hasP95) return tsItems.map((r) => ({ t: r.t, v: r.p95_latency_ms ?? null }))
    if (!traces.data || !tsItems.length) return []
    const start = new Date(tsItems[0].t).getTime()
    const b: number[][] = tsItems.map(() => [])
    for (const t of traces.data.items ?? []) {
      if (/^GET \/(health|healthz|metrics)/.test(t.root ?? '') || t.duration_ms == null) continue
      const i = Math.floor((new Date(t.start).getTime() - start) / bucketMs)
      if (i >= 0 && i < b.length) b[i].push(t.duration_ms)
    }
    return tsItems.map((r, i) => ({ t: r.t, v: p95(b[i]) }))
  }, [hasP95, tsItems, traces.data, bucketMs])
  const p95Rule = (alerts.data?.items ?? []).find((a: Json) => a.metric === 'p95_latency_ms' && a.enabled)
  const openTraces = (i: number) => {
    const t0 = new Date(tsItems[i].t).getTime()
    nav(`/console/observe/traces?since=${w}&from=${t0}&to=${t0 + bucketMs}`)
  }
  const kind = (k: string) => s?.by_kind?.[k]?.calls ?? 0
  const states: Record<string, number> = {}
  for (const [k, n] of Object.entries<number>(a2a.data?.states ?? {})) states[normState(k)] = (states[normState(k)] ?? 0) + n
  const stateKeys = [...A2A_STATES, ...Object.keys(states).filter((k) => !A2A_STATES.includes(k))]
  const stateMax = Math.max(1, ...Object.values(states))
  return (
    <>
      <PageHeader title="메트릭 대시보드·알림 규칙" crumbs={['관측·감사', '메트릭']} desc="Prometheus /metrics (멀티프로세스 집계) · 경보는 알림 센터로 (FR-O03·O04)"
        actions={<><Select aria-label="기간" value={w} onChange={(e) => setSp({ w: e.target.value })} className="w-28">{WINDOWS.map(([m, l]) => <option key={m} value={m}>최근 {l}</option>)}</Select>
          <Button onClick={grafana}>Grafana 대시보드 JSON</Button><Button onClick={evaluate}>지금 평가</Button>
          <Gate perm="admin:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => setEdit({ ...BLANK })}>+ 알림 규칙</Button>}</Gate></>} />
      {sum.error && <ErrorBox error={sum.error} />}
      <div className="mb-4 grid grid-cols-5 gap-3">
        <Kpi label="요청 QPS" value={s ? s.qps.toFixed(2) : '—'} sub={s && `LLM ${fmtNum(kind('model'))} · MCP ${fmtNum(kind('tool'))} · A2A ${fmtNum(kind('remote_agent'))}`} />
        <Kpi label="게이트웨이 지연 p95" value={fmtMs(s?.gateway_overhead_p95_ms)} sub="목표 ≤ 30ms" tone={s?.gateway_overhead_p95_ms == null ? undefined : s.gateway_overhead_p95_ms <= 30 ? 'ok' : 'warn'} />
        <Kpi label="오류율" value={fmtPct(s?.error_rate)} tone={s == null ? undefined : s.error_rate > 0.05 ? 'danger' : s.error_rate > 0.01 ? 'warn' : 'ok'} sub={s && `오류 ${fmtNum(Object.values<Json>(s.by_kind ?? {}).reduce((a, k) => a + k.errors, 0))}건`} />
        <Kpi label="토큰/분" value={s ? fmtNum(Math.round(s.tokens / w)) : '—'} />
        <Kpi label="도구 호출/분" value={s ? (kind('tool') / w).toFixed(1) : '—'} />
      </div>
      <div className="mb-4 grid grid-cols-2 gap-4">
        <Card title={`지연 p95 (ms)${p95Rule ? ` · 경보 임계 ${p95Rule.threshold}ms (점선)` : ''}`}>
          {ts.error ? <ErrorBox error={ts.error} /> : points.length ? (
            <LineChart points={points} label="구간별 지연 p95" target={p95Rule?.threshold} fmt={fmtMs} onPick={openTraces} />
          ) : <Loading />}
          <p className="mt-2 text-xs text-fg-muted">{hasP95 ? '게이트웨이 호출 지연' : `trace 지연 (최근 ${fmtNum(traces.data?.items?.length ?? 0)}건 기준)`} · 구간 클릭 → 트레이스 탐색(K02) 해당 시간대</p>
          <div className="mt-3 text-xs font-medium text-fg-muted">프로토콜별 호출 추이 (적색 = 오류 포함)</div>
          <MiniBars height={48} label="프로토콜별 호출 추이" data={tsItems.map((r) => ({ key: r.t, value: r.model + r.tool + r.remote_agent, alert: r.errors > 0, tip: `${fmtTime(r.t, w > 1440)} · LLM ${r.model} · MCP ${r.tool} · A2A ${r.remote_agent}${r.errors ? ` · 오류 ${r.errors}` : ''}` }))} />
        </Card>
        <Card title="A2A Task 상태 분포" actions={<Link to="/console/a2a/tasks" className="text-xs text-primary hover:underline">Task 목록(G05) →</Link>}>
          {a2a.error ? <ErrorBox error={a2a.error} /> : (
            <>
              <ul className="space-y-1.5 text-xs" aria-label="A2A Task 상태">{stateKeys.map((k) => (
                <li key={k} className="grid grid-cols-[110px_1fr_48px] items-center gap-2">
                  <span>{k}</span>
                  <span className="h-2 rounded bg-surface-2"><span className={cx('block h-2 rounded', A2A_BAR[k] ?? 'bg-muted')} style={{ width: `${((states[k] ?? 0) / stateMax) * 100}%` }} /></span>
                  <span className="text-right tabular-nums">{fmtNum(states[k] ?? 0)}</span>
                </li>
              ))}</ul>
              <div className="mt-3 flex flex-wrap gap-3 text-xs text-fg-muted"><span>위임 호출 {fmtNum(a2a.data?.calls)}</span><span>오류 {fmtNum(a2a.data?.errors)}</span><span>p95 {fmtMs(a2a.data?.p95_latency_ms)}</span></div>
              {(a2a.data?.agents?.length ?? 0) > 0 && (
                <table className="mt-2 w-full text-xs"><thead className="text-left text-fg-muted"><tr><th className="py-1">에이전트</th><th className="text-right">호출</th><th className="text-right">오류</th><th className="text-right">p95</th></tr></thead>
                  <tbody>{a2a.data!.agents.map((g: Json) => <tr key={g.agent} className="border-t border-line"><td className="mono py-1">{g.agent}</td><td className="text-right tabular-nums">{fmtNum(g.calls)}</td><td className="text-right tabular-nums">{fmtNum(g.errors)}</td><td className="text-right tabular-nums">{fmtMs(g.p95_latency_ms)}</td></tr>)}</tbody></table>
              )}
            </>
          )}
        </Card>
      </div>
      <Card title="알림 규칙" className="mb-4" bodyClass="p-0">
        {alerts.error && <ErrorBox error={alerts.error} />}
        <DataTable data={alerts.data?.items ?? []} filter={false} empty="규칙이 없습니다 — 오류율·p95 지연·비용·폴백·승인 대기에 임계값을 둡니다." columns={[
          col.accessor('name', { header: '이름' }), col.display({ id: 'cond', header: '조건', cell: ({ row }) => <span className="mono text-xs">{row.original.metric} {row.original.op} {row.original.threshold}</span> }),
          col.accessor('window_s', { header: '창', cell: (c) => `${c.getValue()}s` }), col.accessor('severity', { header: '심각도', cell: (c) => <Badge tone={c.getValue() === 'danger' ? 'danger' : c.getValue() === 'warn' ? 'warn' : 'primary'}>{c.getValue()}</Badge> }),
          col.accessor('last_fired_at', { header: '최근 발생', cell: (c) => fmtAgo(c.getValue()) }),
          col.accessor('enabled', { header: '사용', cell: (c) => <StatusBadge status={c.getValue() ? 'enabled' : 'disabled'} /> }),
        ]} />
      </Card>
      <Card title="/metrics (anx_* 시계열)" actions={<Button size="sm" onClick={load}>새로고침</Button>}>
        {mErr ? <ErrorBox error={mErr} /> : <CodeBlock value={lines.join('\n') || '(아직 수집된 시계열이 없습니다)'} maxH="max-h-[480px]" />}
        <p className="mt-2 text-xs text-fg-muted">스크레이프: <code>Authorization: Bearer METRICS_TOKEN</code> 또는 observe:read 토큰 · Helm ServiceMonitor 지원</p>
      </Card>
      <Modal open={!!edit} onClose={() => setEdit(null)} title="알림 규칙" footer={<><Button onClick={() => setEdit(null)}>취소</Button><Button variant="primary" disabled={!edit?.name} onClick={save}>저장</Button></>}>
        {edit && <div className="grid grid-cols-2 gap-3">
          <Field label="이름"><Input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
          <Field label="지표"><Select value={edit.metric} onChange={(e) => setEdit({ ...edit, metric: e.target.value })}>{['error_rate', 'p95_latency_ms', 'cost_usd', 'qps', 'fallback_rate', 'approvals_pending'].map((m) => <option key={m}>{m}</option>)}</Select></Field>
          <Field label="비교"><Select value={edit.op} onChange={(e) => setEdit({ ...edit, op: e.target.value })}>{['>', '>=', '<', '<='].map((o) => <option key={o}>{o}</option>)}</Select></Field>
          <Field label="임계값"><Input type="number" step="any" value={edit.threshold} onChange={(e) => setEdit({ ...edit, threshold: e.target.value })} /></Field>
          <Field label="창(초)"><Input type="number" min={60} value={edit.window_s} onChange={(e) => setEdit({ ...edit, window_s: e.target.value })} /></Field>
          <Field label="심각도"><Select value={edit.severity} onChange={(e) => setEdit({ ...edit, severity: e.target.value })}><option>info</option><option>warn</option><option>danger</option></Select></Field>
        </div>}
      </Modal>
      <Modal open={!!dash} onClose={() => setDash(null)} title="Grafana 대시보드 (가져오기용 JSON)" wide><CodeBlock value={dash ?? {}} /></Modal>
    </>
  )
}

// ───────────────────────── SCR-K07 예산·비용 알림 ─────────────────────────
const EXCEED: Record<string, [Tone, string]> = { warn: ['warn', '알림만'], fallback: ['model', '저비용 폴백'], block: ['danger', '차단(429)'] }
const LBLANK: Json = { name: '', scope_type: 'team', scope: '', target_type: 'any', target: '*', rps: null, burst: null, tokens_per_min: null, budget_usd: '', period: 'month', alert_pct: 80, on_exceed: 'warn', fallback_model: '', enabled: true }
const exceedLabel = (l: Json) => (l.on_exceed === 'fallback' && l.fallback_model ? `저비용 폴백 → ${l.fallback_model}` : EXCEED[l.on_exceed]?.[1] ?? l.on_exceed)

export function Budgets() {
  const toast = useToast()
  const { me } = useAuth()
  const limits = useGet('/api/v1/gateway/limits')
  const status = useGet('/api/v1/gateway/limits/status')
  const summary = useGet('/api/v1/observe/summary', { window_minutes: 60 })
  const settings = useGet(can(me, 'admin:read') ? '/api/v1/admin/settings' : null, undefined, { retry: false })
  const models = useGet(can(me, 'models:read') ? '/api/v1/models' : null)
  const [edit, setEdit] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  // 한도별 사용액 — limits/status(주체 지정 한도) 우선, 없으면 운영 요약의 팀 예산
  const rows = (limits.data?.items ?? []).filter((l: Json) => l.budget_usd != null).map((l: Json) => {
    const st = (status.data?.items ?? []).filter((x: Json) => x.limit_id === l.id)
    const usage: Json[] = st.length ? st : (summary.data?.team_budgets ?? []).filter((b: Json) => b.name === l.name).map((b: Json) => ({ ...b, scope: b.team }))
    return { ...l, usage }
  })
  const hot = rows.flatMap((r: Json) => r.usage.filter((u: Json) => u.pct != null && u.pct >= r.alert_pct).map((u: Json) => ({ r, u })))
  const save = async () => {
    const e = edit!
    if (!e.name.trim() || String(e.budget_usd).trim() === '') return toast('error', '이름과 월 예산은 필수입니다 (MSG-003)')
    if (!(Number(e.budget_usd) >= 0) || !(Number(e.alert_pct) >= 1 && Number(e.alert_pct) <= 100)) return toast('error', '예산은 0 이상, 임계 알림은 1~100% 입니다 (MSG-004)')
    if (e.on_exceed === 'fallback' && !e.fallback_model) return toast('error', '저비용 폴백에는 폴백 모델이 필요합니다 (MSG-003)')
    const { id, usage: _u, ...rest } = e
    const body = { ...rest, scope: rest.scope.trim() || '*', budget_usd: Number(rest.budget_usd), alert_pct: Number(rest.alert_pct), fallback_model: rest.on_exceed === 'fallback' ? rest.fallback_model : null }
    setBusy(true)
    try {
      await send(id ? 'PUT' : 'POST', id ? `/api/v1/gateway/limits/${id}` : '/api/v1/gateway/limits', body)
      toast('ok', '저장되었습니다 — 게이트웨이 한도에 즉시 반영 · 감사 기록 (MSG-001)')
      setEdit(null)
      limits.refetch()
      status.refetch()
    } catch (err: any) {
      toast('error', err.message)
    } finally {
      setBusy(false)
    }
  }
  const ch: Json | undefined = settings.data?.['notify.channels']
  const set = (k: string, v: unknown) => setEdit({ ...edit!, [k]: v })
  return (
    <>
      <PageHeader title="예산·비용 알림" crumbs={['관측·감사', '예산']} desc="팀·사용자·모델 단위 예산과 임계 알림 — 초과 시 게이트웨이 Rate limits 와 연동해 알림만·저비용 폴백·차단(429) 중 하나로 동작합니다. 한도는 범위별로 격리됩니다."
        actions={<Gate perm="limits:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => setEdit({ ...LBLANK })}>+ 예산 추가</Button>}</Gate>} />
      {hot.map(({ r, u }: Json) => (
        <div key={r.id + u.scope} role="alert" className={cx('mb-2 rounded-md border px-3 py-2 text-sm', u.pct >= 100 ? 'border-danger bg-danger/10 text-danger' : 'border-warn/50 bg-warn/10')}>
          ⚠ {r.name} ({u.scope}) 예산 {u.pct}% 도달 — {u.pct >= 100 ? '초과: ' : '100% 도달 시 '}{exceedLabel(r)} (MSG-013)
        </div>
      ))}
      {(limits.error || status.error) && <ErrorBox error={limits.error || status.error} />}
      <Card title="예산" className="mb-4" bodyClass="p-0">
        {limits.isLoading ? <div className="p-4"><Loading /></div> : !rows.length ? <Empty>예산 한도가 없습니다 — + 예산 추가로 월 예산과 임계 알림을 둡니다.</Empty> : (
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-4 py-2">이름</th><th>범위</th><th>예산</th><th>사용</th><th className="w-48">사용률</th><th>임계 알림</th><th>초과 시 동작</th><th className="pr-4">상태</th></tr></thead>
            <tbody>{rows.map((r: Json) => (
              <tr key={r.id} className={cx('border-t border-line', can(me, 'limits:write') && 'cursor-pointer hover:bg-surface-2')} onClick={() => can(me, 'limits:write') && setEdit({ ...r, fallback_model: r.fallback_model ?? '' })}>
                <td className="px-4 py-2">{r.name}</td>
                <td><span className="mono text-xs">{r.scope_type}:{r.scope}</span>{r.target_type !== 'any' && <span className="ml-1 text-xs text-fg-muted">· {r.target_type} {r.target}</span>}</td>
                <td className="tabular-nums">{fmtUsd(r.budget_usd)} <span className="text-xs text-fg-muted">/{r.period === 'day' ? '일' : '월'}</span></td>
                <td className="tabular-nums">{r.usage.length ? r.usage.map((u: Json) => <div key={u.scope}>{fmtUsd(u.used_usd)}{r.usage.length > 1 && <span className="ml-1 text-xs text-fg-muted">{u.scope}</span>}</div>) : '—'}</td>
                <td>{r.usage.length ? r.usage.map((u: Json) => (
                  <div key={u.scope} className="flex items-center gap-2">
                    <span className="h-2 flex-1 rounded bg-surface-2"><span className={cx('block h-2 rounded', u.pct >= 100 ? 'bg-danger' : u.pct >= r.alert_pct ? 'bg-warn' : 'bg-ok')} style={{ width: `${Math.min(u.pct ?? 0, 100)}%` }} /></span>
                    <span className="w-14 text-right text-xs tabular-nums">{u.pct ?? '—'}%{u.pct >= r.alert_pct ? ' ⚠' : ''}</span>
                  </div>
                )) : <span className="text-xs text-fg-muted">주체별 집계 — 한도 미리보기(H09)</span>}</td>
                <td className="text-xs">{r.alert_pct}% · 100%</td>
                <td><Badge tone={EXCEED[r.on_exceed]?.[0] ?? 'muted'}>{exceedLabel(r)}</Badge></td>
                <td className="pr-4"><StatusBadge status={r.enabled ? 'enabled' : 'disabled'} /></td>
              </tr>
            ))}</tbody>
          </table>
        )}
        <p className="border-t border-line px-4 py-2 text-xs text-fg-muted">80% 주황 · 100% 적색 · 행 클릭으로 편집 (limits:write) · 저장 시 게이트웨이 한도 즉시 반영·감사 기록</p>
      </Card>
      <div className="grid grid-cols-2 gap-4">
        <Card title="알림 채널">
          <Kv rows={[
            ['콘솔 알림 센터', <StatusBadge status="enabled" />],
            ['Slack 웹훅', !settings.data ? <span className="text-fg-muted">—</span> : ch?.slack_webhook ? <Masked value={ch.slack_webhook} /> : <span className="text-fg-muted">미설정</span>],
            ...Object.keys(ch ?? {}).filter((k) => k !== 'slack_webhook').map((k): [string, ReactNode] => [k, <Badge tone="ok">설정됨</Badge>]),
          ]} />
          <p className="mt-2 text-xs text-fg-muted">
            {can(me, 'admin:read') ? '경고·위험 알림만 외부 채널로 보냅니다 (에어갭이면 보내지 않음).' : '채널 설정은 admin:read 권한으로 볼 수 있습니다 — 여기서는 읽기 전용입니다.'}
          </p>
          {can(me, 'admin:write') && <Link to="/console/admin" className="mt-1 inline-block text-xs text-primary hover:underline">채널 변경 → 일반 설정 (L06)</Link>}
        </Card>
        <Card title="게이트웨이 한도 연동">
          <Kv rows={[
            ['연동 정책', <Link to="/console/gateway/limits" className="text-primary hover:underline">Rate limits (H09) →</Link>],
            ['카운터 백엔드', limits.data?.backend ? <span className="flex items-center gap-2"><span className="mono text-xs">{limits.data.backend.backend}</span><StatusBadge status={limits.data.backend.ok ? 'healthy' : 'unhealthy'} /></span> : '—'],
            ['격리 범위', '범위(팀·사용자·에이전트) 단위 — 다른 팀에 영향 없음'],
          ]} />
          <div className="mt-3 space-y-2 text-xs">
            <div className="rounded-md border border-model/40 bg-model/5 px-3 py-2">폴백 시 사용자 안내: "예산 초과로 저비용 모델로 전환" (MSG-015)</div>
            <div className="rounded-md border border-danger/30 bg-danger/5 px-3 py-2 text-danger">차단 시: 429 한도 초과 (MSG-012) — 채팅에 재시도 가능 시각 표시</div>
          </div>
        </Card>
      </div>
      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? `예산 편집 — ${edit.name}` : '예산 추가'}
        footer={<><span className="mr-auto text-xs text-fg-muted">변경은 감사 기록 · 즉시 반영</span><Button onClick={() => setEdit(null)}>취소</Button><Button variant="primary" loading={busy} onClick={save}>저장</Button></>}>
        {edit && <div className="grid grid-cols-2 gap-3">
          <Field label="이름"><Input value={edit.name} onChange={(e) => set('name', e.target.value)} /></Field>
          <Field label="범위 유형"><Select value={edit.scope_type} onChange={(e) => set('scope_type', e.target.value)}>{['team', 'user', 'agent', 'client_key', 'any'].map((t) => <option key={t}>{t}</option>)}</Select></Field>
          <Field label="범위 값" hint="팀 ID·사용자 sub 등 (* = 전체)"><Input value={edit.scope} onChange={(e) => set('scope', e.target.value)} placeholder="team-biz" /></Field>
          <Field label="예산 (USD)"><Input type="number" min={0} step="any" value={edit.budget_usd ?? ''} onChange={(e) => set('budget_usd', e.target.value)} /></Field>
          <Field label="기간"><Select value={edit.period} onChange={(e) => set('period', e.target.value)}><option value="month">월</option><option value="day">일</option></Select></Field>
          <Field label="임계 알림 (%)" hint="도달 시 알림 센터·채널 · 100% 는 항상 알림"><Input type="number" min={1} max={100} value={edit.alert_pct} onChange={(e) => set('alert_pct', e.target.value)} /></Field>
          <fieldset className="col-span-2">
            <legend className="mb-1 text-xs font-medium text-fg-muted">초과 시 동작 (게이트웨이 Rate limits 연동)</legend>
            <div className="flex flex-wrap gap-4 text-sm">{(['warn', 'fallback', 'block'] as const).map((k) => (
              <label key={k} className="flex items-center gap-1.5"><input type="radio" name="on_exceed" checked={edit.on_exceed === k} onChange={() => set('on_exceed', k)} />{EXCEED[k][1]}</label>
            ))}</div>
          </fieldset>
          {edit.on_exceed === 'fallback' && (
            <Field label="폴백 모델"><Select value={edit.fallback_model} onChange={(e) => set('fallback_model', e.target.value)}><option value="">선택…</option>{(models.data?.items ?? []).filter((m: Json) => m.enabled).map((m: Json) => <option key={m.id}>{m.id}</option>)}</Select></Field>
          )}
        </div>}
      </Modal>
    </>
  )
}

// ───────────────────────── SCR-K08 세션 리플레이 ─────────────────────────
const isBad = (e: Json) => !!e.error || ['failed', 'error', 'deny', 'denied', 'rejected'].includes(String(e.status ?? e.decision ?? '').toLowerCase())
  || (String(e.kind).startsWith('call.') && e.status && e.status !== 'ok')

function stepLabel(e: Json): string {
  const k: string = e.kind ?? ''
  if (k === 'message.user') return '사용자 프롬프트'
  if (k === 'message.assistant') return '응답'
  if (k.startsWith('message.')) return `메시지 · ${k.slice(8)}`
  if (k.startsWith('step.')) return `계획 단계 ${e.step ?? ''} · ${k.slice(5)}${e.tool ? ` — ${e.tool}` : ''}`
  if (k === 'call.model') return `모델 호출 · ${e.target}`
  if (k === 'call.tool') return `도구 호출 · ${e.target}`
  if (k === 'call.remote_agent') return `A2A 위임 · ${e.target}`
  if (k.startsWith('call.')) return `${k.slice(5)} 호출 · ${e.target}`
  if (k.startsWith('routing.')) return `라우팅(${k.slice(8)}) · ${e.requested} → ${e.chosen}`
  if (k === 'approval') return `승인 · ${e.target}`
  if (k === 'token.exchange') return `토큰 교환 · ${e.audience}`
  if (k === 'a2a.task') return `A2A Task · ${e.agent}`
  if (k === 'audit') return `감사 · ${e.action}`
  return k
}

function dotOf(e: Json) {
  const k: string = e.kind ?? ''
  if (isBad(e)) return 'bg-danger'
  if (k.startsWith('message.')) return 'bg-primary'
  if (k === 'call.tool') return 'bg-mcp'
  if (k === 'call.remote_agent' || k === 'a2a.task') return 'bg-a2a'
  if (k.startsWith('step.') || k === 'call.model') return 'bg-model'
  if (k === 'approval') return 'bg-warn'
  if (k === 'token.exchange') return 'bg-profile'
  return 'bg-muted'
}

function stepIO(e: Json): [unknown, unknown] {
  const k: string = e.kind ?? ''
  if (k === 'message.user') return [e.content, null]
  if (k.startsWith('message.')) return [null, e.content]
  if (k.startsWith('step.')) return [e.args, e.output_preview ?? (e.error ? { error: e.error } : null)]
  if (k.startsWith('call.')) return [{ target: e.target, agent: e.agent, tokens_in: e.tokens_in }, { status: e.status, tokens_out: e.tokens_out, cost_usd: e.cost, latency_ms: e.latency_ms }]
  if (k.startsWith('routing.')) return [{ requested: e.requested }, { chosen: e.chosen, fallback: e.fallback }]
  if (k === 'approval') return [e.args, { status: e.status, decided_by: e.decided_by }]
  if (k === 'token.exchange') return [{ audience: e.audience, scopes: e.scopes, subject_kind: e.subject_kind }, { decision: e.decision }]
  if (k === 'a2a.task') return [{ agent: e.agent, task: e.task }, { state: e.state, artifacts: e.artifacts }]
  return [null, e]
}

function evidence(e: Json): [string, ReactNode][] {
  const rows: [string, ReactNode][] = []
  for (const p of e.policy ?? []) {
    const bits = [p.code, p.policy, p.data_class && `data_class=${p.data_class}`, p.risk && `risk=${p.risk}`].filter(Boolean).join(' · ')
    rows.push(['정책 결정', <span className="mono text-xs">{bits || JSON.stringify(p)}</span>])
    for (const n of p.notes ?? []) rows.push(['근거', <span className="text-xs">{n}</span>])
    if (p.findings?.length) rows.push(['소독·탐지', <span className="text-xs">{p.findings.length}건 (응답에서 제거 · MSG-025)</span>])
  }
  if (e.reason) rows.push(['라우팅 근거', <span className="text-xs">{e.reason}</span>])
  if (e.decision) rows.push(['결정', <StatusBadge status={e.decision} />])
  if (e.kind === 'approval') rows.push(['승인', <span><StatusBadge status={e.status} /> {e.decided_by && <span className="text-xs text-fg-muted">by {e.decided_by}</span>}</span>])
  if (e.attempts > 1) rows.push(['시도', `${e.attempts}회`])
  if (e.error) rows.push(['오류', <span className="text-xs text-danger">{typeof e.error === 'string' ? e.error : JSON.stringify(e.error)}</span>])
  if (e.seq) rows.push(['감사', <span className="mono text-xs">#{e.seq} · {String(e.hash ?? '').slice(0, 12)}</span>])
  return rows
}

export function Replay() {
  const { sid = '' } = useParams()
  const toast = useToast()
  const q = useGet(sid ? `/api/v1/audit/replay/${sid}` : null)
  const tl: Json[] = q.data?.timeline ?? []
  const n = tl.length
  const [idx, setIdx] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!playing) return
    if (idx >= n - 1) { setPlaying(false); return }
    const t = setTimeout(() => setIdx((i) => Math.min(i + 1, n - 1)), 1500 / speed)
    return () => clearTimeout(t)
  }, [playing, idx, n, speed])
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest('input,select,textarea')) return
      if (e.key === 'ArrowRight') setIdx((i) => Math.min(i + 1, Math.max(n - 1, 0)))
      if (e.key === 'ArrowLeft') setIdx((i) => Math.max(i - 1, 0))
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [n])
  if (q.error) return <><PageHeader title={`세션 리플레이 — ${sid}`} crumbs={['관측·감사', '세션 리플레이']} /><ErrorBox error={q.error} /></>
  if (!q.data) return <Loading rows={8} />
  const runs: Json[] = q.data.runs ?? []
  const runOf = (id?: string) => runs.find((r) => r.id === id)
  const cur = tl[Math.min(idx, n - 1)]
  const curRun = runOf(cur?.run_id) ?? runs[0]
  const [input, output] = cur ? stepIO(cur) : [null, null]
  const ev = cur ? evidence(cur) : []
  const profiles = [...new Set(runs.map((r) => `${r.profile_id} v${r.profile_version}`))]
  const rerun = async () => {
    if (!curRun || !window.confirm(`실행 ${curRun.id} 을(를) 같은 요청·같은 그래프 스냅샷으로 다시 실행합니다 (테스트 실행 · 감사 기록). 계속할까요? (MSG-002)`)) return
    setBusy(true)
    try {
      const r = await send<Json>('POST', `/api/v1/audit/replay/${curRun.id}/rerun`)
      toast('ok', `재실행 시작 — ${r.run_id} (테스트 실행)`)
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <PageHeader title={`세션 리플레이 — ${sid}`} crumbs={['관측·감사', '세션 리플레이']} desc="프롬프트·계획·정책 결정·도구 호출·승인을 시간 순서로 재생합니다 — 기록만 보여 주며 도구를 다시 실행하지 않습니다 (FR-O05)."
        actions={curRun && <Button variant="ghost" loading={busy} onClick={rerun} title="같은 요청으로 테스트 실행을 새로 만듭니다">같은 요청으로 재실행 (test run)</Button>} />
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <Badge tone="primary">{q.data.user}</Badge>
        {profiles.map((p) => <Badge key={p} tone="profile">{p}</Badge>)}
        {n > 0 && <Badge>{fmtTime(tl[0].ts, true)} → {fmtTime(tl[n - 1].ts)}</Badge>}
        {runs.filter((r) => r.trace_id).slice(0, 3).map((r) => <TraceLink key={r.id} id={r.trace_id} />)}
        <Badge tone="ok">읽기 전용 재생 · 도구 재실행 없음</Badge>
      </div>
      {!n ? <Empty>재생할 기록이 없습니다.</Empty> : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Button size="sm" aria-label="처음" onClick={() => setIdx(0)}>⏮</Button>
            <Button size="sm" aria-label="이전" onClick={() => setIdx(Math.max(idx - 1, 0))}>◀</Button>
            <Button size="sm" variant="primary" aria-label={playing ? '일시정지' : '재생'} onClick={() => { if (idx >= n - 1) setIdx(0); setPlaying(!playing) }}>{playing ? '⏸' : '▶'}</Button>
            <Button size="sm" aria-label="다음" onClick={() => setIdx(Math.min(idx + 1, n - 1))}>▶▶</Button>
            <input type="range" aria-label="진행 위치" min={0} max={n - 1} value={idx} onChange={(e) => setIdx(Number(e.target.value))} className="mx-2 min-w-40 flex-1 accent-[#3f51e5]" />
            <span className="text-xs tabular-nums text-fg-muted">{idx + 1}/{n}</span>
            <Select aria-label="속도" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="w-20"><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option></Select>
            <span className="text-xs text-fg-muted">키보드 ← →</span>
          </div>
          <div className="grid grid-cols-[minmax(260px,2fr)_3fr] gap-4">
            <Card title="단계 타임라인" bodyClass="max-h-[640px] overflow-auto p-2">
              <ol aria-label="단계 타임라인" className="space-y-1">{tl.map((e, i) => (
                <li key={i}>
                  <button type="button" aria-current={i === idx ? 'step' : undefined} onClick={() => { setIdx(i); setPlaying(false) }}
                    className={cx('flex w-full items-start gap-2 rounded-md border px-2 py-1.5 text-left text-xs', i === idx ? 'border-primary bg-primary-soft' : 'border-transparent hover:bg-surface-2', i > idx && 'opacity-60')}>
                    <span className={cx('mt-1 size-2 shrink-0 rounded-full', dotOf(e))} aria-hidden />
                    <span className="min-w-0"><span className="block truncate font-medium">{i + 1}. {stepLabel(e)}</span><span className="text-fg-muted">{fmtTime(e.ts)}</span></span>
                  </button>
                </li>
              ))}</ol>
            </Card>
            {cur && (
              <Card title={`${idx + 1}. ${stepLabel(cur)}`} className="border-primary/50">
                <div className="space-y-3 text-sm">
                  <div className="flex flex-wrap gap-2">
                    <Badge>{cur.kind}</Badge>{cur.risk && <RiskChip risk={cur.risk} />}
                    {(cur.status || cur.state) && <StatusBadge status={cur.status ?? cur.state} />}
                    {cur.fallback && <Badge tone="warn">폴백 (MSG-015)</Badge>}
                    <span className="text-xs text-fg-muted">{fmtTime(cur.ts, true)}</span>
                  </div>
                  {input != null && input !== '' && <><div className="text-xs font-medium text-fg-muted">입력</div><CodeBlock value={input} maxH="max-h-56" /></>}
                  {output != null && output !== '' && <><div className="text-xs font-medium text-fg-muted">출력</div><CodeBlock value={output} maxH="max-h-56" /></>}
                  <div className="text-xs font-medium text-fg-muted">정책 결정 근거</div>
                  {ev.length ? <Kv rows={ev} /> : <p className="text-xs text-fg-muted">이 단계에 기록된 정책 결정이 없습니다.</p>}
                  <div className="rounded-md border border-ok/40 bg-ok/5 px-3 py-2 text-xs">원문 이메일·전화번호는 마스킹된 기록 그대로 표시합니다 (MSG-021) — 해제는 로그 검색(K04)과 같은 사유·감사 절차</div>
                  <div className="flex flex-wrap gap-2">
                    <Link to="/console/audit" className="rounded-md border border-line px-3 py-1.5 text-xs hover:bg-surface-2">감사 로그 (K09) →</Link>
                    {curRun?.trace_id && <Link to={`/console/observe/traces/${curRun.trace_id}`} className="rounded-md border border-line px-3 py-1.5 text-xs hover:bg-surface-2">trace 워터폴 (K03) →</Link>}
                  </div>
                </div>
              </Card>
            )}
          </div>
        </>
      )}
    </>
  )
}

// ───────────────────────── SCR-K10 규제 리포트 내보내기 (4단계) ─────────────────────────
const STEPS = ['템플릿', '기간', '범위·PII', '미리보기·생성']
const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

export function ReportWizard() {
  const nav = useNavigate()
  const toast = useToast()
  const tpl = useGet('/api/v1/audit/reports/templates')
  const verify = useGet('/api/v1/audit/verify')
  const prev = useGet('/api/v1/audit/reports')
  const [step, setStep] = useState(0)
  const [f, setF] = useState(() => {
    const now = new Date()
    return { template: 'pipa_access', since: ymd(new Date(now.getFullYear(), now.getMonth(), 1)), until: ymd(now), team: '', user: '', actions: '', pii: true }
  })
  const [preview, setPreview] = useState<Json | null>(null)
  const [created, setCreated] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)
  const templates: Json[] = tpl.data?.items ?? []
  const t = templates.find((x) => x.id === f.template)
  const set = (k: keyof typeof f, v: string | boolean) => { setF({ ...f, [k]: v }); setPreview(null); setCreated(null) }
  const periodErr = !f.since || !f.until ? '시작·끝은 필수입니다 (MSG-003)'
    : f.until < f.since ? '끝은 시작 이후여야 합니다 (MSG-004)'
      : (new Date(f.until).getTime() - new Date(f.since).getTime()) / 86400_000 > 366 ? '기간은 최대 1년입니다 (MSG-004)' : ''
  const body = () => ({
    template: f.template, since: new Date(`${f.since}T00:00:00`).toISOString(), until: new Date(`${f.until}T23:59:59.999`).toISOString(),
    team: f.team.trim() || null, user: f.user.trim() || null, actions: f.actions.split(',').map((s) => s.trim()).filter(Boolean), pii: f.pii,
  })
  const call = async (path: string, then: (r: Json) => void) => {
    setBusy(true)
    setErr(null)
    try {
      then(await send<Json>('POST', path, body()))
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }
  const next = () => {
    if (step === 1 && periodErr) return toast('error', periodErr)
    if (step === 2) call('/api/v1/audit/reports/preview', setPreview)
    setStep(step + 1)
  }
  const create = () => call('/api/v1/audit/reports', (r) => {
    setCreated(r)
    prev.refetch()
    toast('ok', `리포트 ${r.id} — ${fmtNum(r.count)}건 · 서명 생성 (MSG-001)`)
  })
  const togglePii = (on: boolean) => {
    if (!on && !window.confirm('PII 마스킹을 끄면 행위자·대상이 원문으로 포함되며 감사에 기록됩니다 — 보안 책임자 승인(ST-06) 후 진행하세요. 계속할까요? (MSG-002)')) return
    set('pii', on)
  }
  const dl = (id: string, fmt: 'md' | 'csv' | 'jsonl') => download(`/api/v1/audit/reports/${id}/download?format=${fmt}`, `${id}.${fmt}`).catch((e) => toast('error', e.message))
  const chainBad = verify.data && !verify.data.valid
  return (
    <>
      <PageHeader title="규제 리포트 내보내기" crumbs={['관측·감사', '감사 로그', '규제 리포트']} desc="템플릿·기간·범위를 고르고 미리 본 뒤, 해시 체인 검증 결과를 포함해 콘솔 서명 키(RS256)로 서명합니다 (FR-O05)." />
      <Stepper steps={STEPS} current={step} />
      {tpl.error && <ErrorBox error={tpl.error} />}
      <div className="grid grid-cols-[3fr_2fr] gap-4">
        <Card title={`${step + 1}. ${STEPS[step]}`} className="border-primary/50">
          {step === 0 && (
            <fieldset className="space-y-2">
              <legend className="sr-only">템플릿</legend>
              {templates.map((x) => (
                <label key={x.id} className={cx('flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm', f.template === x.id ? 'border-primary bg-primary-soft' : 'border-line hover:bg-surface-2')}>
                  <input type="radio" name="template" className="mt-1" checked={f.template === x.id} onChange={() => set('template', x.id)} />
                  <span><span className="font-medium">{x.label}</span>
                    <span className="block text-xs text-fg-muted">{x.actions?.length ? `행위: ${x.actions.join(' · ')}` : '모든 행위'}{x.decision ? ` · 결정=${x.decision}` : ''}</span></span>
                </label>
              ))}
            </fieldset>
          )}
          {step === 1 && (
            <div className="grid grid-cols-2 gap-3">
              <Field label="시작"><Input type="date" value={f.since} onChange={(e) => set('since', e.target.value)} /></Field>
              <Field label="끝 (최대 1년)" error={periodErr || undefined}><Input type="date" value={f.until} onChange={(e) => set('until', e.target.value)} /></Field>
            </div>
          )}
          {step === 2 && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <Field label="팀" hint="비우면 전체 팀"><Input value={f.team} onChange={(e) => set('team', e.target.value)} placeholder="team-biz" /></Field>
                <Field label="사용자(행위자)" hint="지정 시 팀보다 우선"><Input value={f.user} onChange={(e) => set('user', e.target.value)} placeholder="usr_…" /></Field>
              </div>
              <Field label="행위(action) 접두사" hint={`쉼표로 구분 · 비우면 템플릿 기본값${t?.actions?.length ? ` (${t.actions.join(', ')})` : ' (모든 행위)'}`}>
                <Input value={f.actions} onChange={(e) => set('actions', e.target.value)} placeholder="auth.login, gateway." />
              </Field>
              <div className="text-xs text-fg-muted">결정 필터: {t?.decision ? <Badge tone="danger">{t.decision}</Badge> : '전체'} (템플릿 기준)</div>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.pii} onChange={(e) => togglePii(e.target.checked)} />PII 마스킹 유지 (해제는 보안 책임자 승인 · 감사 기록)</label>
            </div>
          )}
          {step === 3 && (
            <div className="space-y-3">
              {!!err && <ErrorBox error={err} />}
              {busy && !preview && <Loading />}
              {chainBad && <div role="alert" className="rounded-md border border-danger bg-danger/10 px-3 py-2 text-sm text-danger">⚠ 감사 체인 손상 — seq {verify.data!.broken_at} · 손상 구간이 있으면 리포트를 생성할 수 없습니다 (MSG-022)</div>}
              {preview && (
                <>
                  <div className="flex flex-wrap items-center gap-2 text-sm"><strong>예상 이벤트 {fmtNum(preview.count)}건</strong>
                    {verify.data?.valid && <Badge tone="ok">체인 검증 정상 · seq {verify.data.last_seq}</Badge>}<Badge tone={f.pii ? 'ok' : 'warn'}>PII 마스킹 {f.pii ? '켬' : '끔'}</Badge></div>
                  <ul aria-label="행위별 분포" className="space-y-1 text-xs">{(preview.by_action ?? []).map((a: Json) => (
                    <li key={a.action} className="grid grid-cols-[200px_1fr_56px] items-center gap-2"><span className="mono truncate">{a.action}</span>
                      <span className="h-1.5 rounded bg-surface-2"><span className="block h-1.5 rounded bg-primary" style={{ width: `${(a.count / Math.max(preview.count, 1)) * 100}%` }} /></span><span className="text-right tabular-nums">{fmtNum(a.count)}</span></li>
                  ))}</ul>
                  <div className="max-h-64 overflow-auto rounded-md border border-line">
                    <table className="w-full text-xs"><thead className="sticky top-0 bg-surface-2 text-left text-fg-muted"><tr>{['seq', '시각', '행위자', 'action', '대상', '결정'].map((h) => <th key={h} className="px-2 py-1">{h}</th>)}</tr></thead>
                      <tbody>{(preview.rows ?? []).map((r: Json) => <tr key={r.seq} className="border-t border-line"><td className="px-2 py-1">{r.seq}</td><td>{fmtTime(r.ts, true)}</td><td>{r.actor}</td><td className="mono">{r.action}</td><td className="mono">{r.target || '—'}</td><td>{r.decision ?? '—'}</td></tr>)}</tbody></table>
                  </div>
                  <p className="text-xs text-fg-muted">처음 20행 미리보기 · 생성 시 전체 건수를 서명합니다</p>
                </>
              )}
              {created && (
                <div role="status" className="space-y-2 rounded-md border border-ok/40 bg-ok/5 px-3 py-2 text-sm">
                  <div>✔ 리포트 <span className="mono">{created.id}</span> — {fmtNum(created.count)}건 · 체인 {created.chain?.valid ? '정상' : '손상'} · SHA-256 <span className="mono text-xs">{created.sha256?.slice(0, 16)}…</span></div>
                  <div className="flex flex-wrap gap-2"><Button size="sm" onClick={() => dl(created.id, 'md')}>Markdown (인쇄 → PDF)</Button><Button size="sm" onClick={() => dl(created.id, 'csv')}>CSV</Button><Button size="sm" onClick={() => dl(created.id, 'jsonl')}>JSONL (서명 원본)</Button></div>
                </div>
              )}
            </div>
          )}
        </Card>
        <div className="space-y-4">
          <Card title="리포트 항목">
            <Kv rows={[['템플릿', t?.label ?? f.template], ['기간', `${f.since || '—'} ~ ${f.until || '—'}`], ['범위', [f.team && `팀 ${f.team}`, f.user && `사용자 ${f.user}`].filter(Boolean).join(' · ') || '전체'],
              ['행위', f.actions || (t?.actions?.length ? t.actions.join(', ') : '모든 행위')], ['PII 마스킹', f.pii ? '켬' : '끔'], ['포함', '요약·건수 · 접근 기록 · 결정 · 해시 체인 검증 결과']]} />
          </Card>
          <Card title="이전 리포트" bodyClass="p-0">
            {prev.error ? <div className="p-3"><ErrorBox error={prev.error} /></div> : (
              <ul className="divide-y divide-line text-xs">{(prev.data?.items ?? []).slice(0, 6).map((r: Json) => (
                <li key={r.id} className="flex items-center justify-between gap-2 px-4 py-2"><span><span className="mono">{r.id}</span> <span className="text-fg-muted">{fmtTime(r.created_at, true)} · {fmtNum(r.event_count)}건</span></span>
                  <span className="flex items-center gap-1">{r.chain_valid ? <Badge tone="ok">서명 ✓</Badge> : <Badge tone="danger">손상</Badge>}<Button size="sm" variant="ghost" onClick={() => dl(r.id, 'jsonl')}>받기</Button></span></li>
              ))}</ul>
            )}
            <p className="border-t border-line px-4 py-2 text-xs text-fg-muted">서명: 콘솔 서명 키 · SHA-256 첨부 · MD·CSV·JSONL 형식</p>
          </Card>
        </div>
      </div>
      <div className="mt-4 flex items-center justify-end gap-2">
        <span className="mr-auto text-xs text-fg-muted">생성은 감사에 기록됩니다 (audit.report.create)</span>
        <Button onClick={() => nav('/console/audit')}>취소</Button>
        {step > 0 && <Button onClick={() => setStep(step - 1)}>이전</Button>}
        {step < 3 ? <Button variant="primary" onClick={next} disabled={!templates.length}>다음 →</Button> : (
          <Gate perm="audit:read">{(ok, why) => <Button variant="primary" loading={busy && !!preview} disabled={!ok || !preview || !!chainBad || !!created} title={why ?? (chainBad ? 'MSG-022 체인 손상' : undefined)} onClick={create}>서명 리포트 생성</Button>}</Gate>
        )}
      </div>
    </>
  )
}

// D15-④ SLO·오류 예산 — 데이터 경로(모델·MCP·A2A) 가용성·첫 응답 지연 SLO · 이동 창 예산 잔량 · 다중 창 소진율 · 경보 상태
const SLO_STATE: Record<string, { label: string; tone: Tone }> = {
  ok: { label: '정상', tone: 'ok' }, ticket: { label: '주의 — 티켓', tone: 'warn' }, page: { label: '긴급 — 호출', tone: 'danger' }, exhausted: { label: '예산 소진', tone: 'danger' },
}
const pctOf = (v: number | null | undefined, d = 3) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)

export function Slos() {
  const toast = useToast()
  const q = useGet('/api/v1/observe/slos', undefined, { refetchInterval: 30_000 })
  const [edit, setEdit] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  const items: Json[] = q.data?.items ?? []
  const alerting: Json = q.data?.alerting ?? {}
  const evaluate = async () => {
    try {
      const r = await send<Json>('POST', '/api/v1/observe/slos/evaluate')
      toast('ok', r.changed?.length ? `상태 변경 ${r.changed.length}건 — 알림을 보냈습니다` : '상태 변화 없음')
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const save = async () => {
    const e = edit!
    if (!String(e.reason ?? '').trim()) return toast('error', '변경 사유를 입력하세요 (MSG-003)')
    const target = Number(e.target_pct) / 100
    if (!(target >= 0.9 && target <= 0.99999)) return toast('error', '목표는 90% ~ 99.999% 입니다 (MSG-004)')
    setBusy(true)
    try {
      await send('PATCH', `/api/v1/observe/slos/${e.id}`, { target, window_days: Number(e.window_days), enabled: e.enabled, reason: e.reason.trim(),
        ...(e.kind === 'latency' ? { threshold_ms: Number(e.threshold_ms) } : {}) })
      toast('ok', '저장되었습니다 — 감사 기록 (MSG-001)')
      setEdit(null)
      q.refetch()
    } catch (err: any) {
      toast('error', err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <PageHeader title="SLO·오류 예산" crumbs={['관측·감사', 'SLO']}
        desc="모델 API·MCP·A2A 요청의 가용성(5xx 아님)과 첫 응답 지연을 이동 창으로 집계합니다. 오류 예산 = (1 − 목표) × 요청 수 · 소진율 = 창 안 오류율 ÷ (1 − 목표)."
        actions={<Button onClick={evaluate}>지금 평가</Button>} />
      {items.filter((s) => s.enabled && s.state !== 'ok').map((s) => (
        <div key={s.id} role="alert" className={cx('mb-2 rounded-md border px-3 py-2 text-sm', SLO_STATE[s.state]?.tone === 'danger' ? 'border-danger bg-danger/10 text-danger' : 'border-warn/50 bg-warn/10')}>
          ⚠ {s.name} — {SLO_STATE[s.state]?.label} · 1시간 소진율 {s.burn['1h'].burn_rate} · 예산 잔량 {pctOf(s.budget.remaining, 1)}
        </div>
      ))}
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading && <Loading />}
      <div className="grid gap-4 xl:grid-cols-2">
        {items.map((s) => {
          const st = SLO_STATE[s.state] ?? SLO_STATE.ok
          const rem = s.budget.remaining as number
          return (
            <Card key={s.id} title={s.name} actions={<>{!s.enabled && <Badge>꺼짐</Badge>}<Badge tone={st.tone}>{st.label}</Badge></>}>
              <div className="grid grid-cols-3 gap-3">
                <Kpi label={`SLI (${s.window_days}일)`} value={pctOf(s.sli)} sub={`목표 ${pctOf(s.target)}`} tone={s.sli != null && s.sli < s.target ? 'danger' : 'ok'} />
                <Kpi label="오류 예산 잔량" value={pctOf(rem, 1)} sub={`${fmtNum(s.budget.consumed)} / ${fmtNum(s.budget.allowed)}건`} tone={rem <= 0 ? 'danger' : rem < 0.25 ? 'warn' : 'ok'} />
                <Kpi label="요청" value={fmtNum(s.total)} sub={`${s.kind === 'latency' ? `첫 응답 ≤ ${s.threshold_ms}ms` : '5xx 아님'} · ${s.scope === 'all' ? '전체' : s.scope}`} />
              </div>
              <div className="mt-3" aria-hidden><span className="block h-2 rounded bg-surface-2"><span className={cx('block h-2 rounded', rem <= 0 ? 'bg-danger' : rem < 0.25 ? 'bg-warn' : 'bg-ok')} style={{ width: `${Math.max(0, Math.min(rem, 1)) * 100}%` }} /></span></div>
              <table className="mt-3 w-full text-xs">
                <caption className="sr-only">창별 소진율</caption>
                <thead className="text-left text-fg-muted"><tr><th scope="col" className="py-1">창</th><th scope="col">요청</th><th scope="col">나쁜 요청</th><th scope="col">소진율</th></tr></thead>
                <tbody>{['5m', '30m', '1h', '6h'].map((w) => {
                  const b = s.burn[w]
                  const lim = w === '5m' || w === '1h' ? alerting.page_burn : alerting.ticket_burn
                  return <tr key={w} className="border-t border-line"><td className="py-1">{w}</td><td className="tabular-nums">{fmtNum(b.total)}</td><td className="tabular-nums">{fmtNum(b.bad)}</td>
                    <td className={cx('tabular-nums', b.burn_rate >= lim && 'font-semibold text-danger')}>{b.burn_rate}× <span className="text-fg-muted">(경보 {lim}×)</span></td></tr>
                })}</tbody>
              </table>
              <div className="mt-3"><MiniBars label={`${s.name} 일별 나쁜 요청 (${s.window_days}일)`} height={56}
                data={s.series.map((d: Json) => ({ key: d.day, value: d.bad, tip: `${d.day} · 나쁜 ${fmtNum(d.bad)} / ${fmtNum(d.total)} · SLI ${pctOf(d.sli)}`, alert: d.sli != null && d.sli < s.target }))} /></div>
              <div className="mt-2 flex items-center justify-between text-xs text-fg-muted">
                <span>{s.alert_since ? `상태 ${fmtAgo(s.alert_since)}부터` : '경보 기록 없음'} · 수정 {s.updated_by ?? '—'}</span>
                <Gate perm="admin:write">{(ok, why) => <Button size="sm" disabled={!ok} title={why} onClick={() => setEdit({ ...s, target_pct: (s.target * 100).toFixed(3), reason: '' })}>목표 수정</Button>}</Gate>
              </div>
            </Card>
          )
        })}
      </div>
      <p className="mt-3 text-xs text-fg-muted">경보: 1시간·5분 소진율이 모두 {alerting.page_burn ?? 14.4}× 이상이면 긴급, 6시간·30분이 모두 {alerting.ticket_burn ?? 6}× 이상이면 주의 — 짧은 창 요청이 {alerting.min_events ?? 10}건 미만이면 내지 않습니다. 상태가 바뀔 때 운영자 알림.</p>
      {edit && (
        <Modal open onClose={() => setEdit(null)} title={`SLO 수정 — ${edit.name}`}
          footer={<><Button onClick={() => setEdit(null)}>취소</Button><Button variant="primary" loading={busy} onClick={save}>저장</Button></>}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="목표 (%)"><Input type="number" step="0.001" value={edit.target_pct} onChange={(e) => setEdit({ ...edit, target_pct: e.target.value })} /></Field>
            <Field label="이동 창 (일)"><Input type="number" min={7} max={90} value={edit.window_days} onChange={(e) => setEdit({ ...edit, window_days: e.target.value })} /></Field>
            {edit.kind === 'latency' && <Field label="첫 응답 기준 (ms)"><Select value={edit.threshold_ms} onChange={(e) => setEdit({ ...edit, threshold_ms: e.target.value })}>{(q.data?.thresholds ?? []).map((b: number) => <option key={b} value={b}>{b}</option>)}</Select></Field>}
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!edit.enabled} onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })} />경보 사용</label>
            <div className="col-span-2"><Field label="변경 사유 (필수 · 감사)"><Input value={edit.reason} onChange={(e) => setEdit({ ...edit, reason: e.target.value })} /></Field></div>
          </div>
        </Modal>
      )}
    </>
  )
}
