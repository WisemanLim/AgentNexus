// SCR-H01 게이트웨이 개요 · H02 모델 라우팅 규칙 · H04 라우팅 결정 로그 · H07 정책 목록·게시 심사 · H08 정책 편집기(초안·회귀 시험·2인 승인)·시뮬레이터 · H09 한도·예산 · H12 사람 승인 대기열
import { createColumnHelper } from '@tanstack/react-table'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { ExtendTime } from '@/components/ExtendTime'
import { Badge, Button, Card, CodeBlock, Drawer, ErrorBox, Field, Gate, Input, Kpi, Kv, Modal, PageHeader, RiskChip, Select, StatusBadge, Stepper, Tabs, Textarea, TraceLink, askReason, cx, useToast } from '@/components/ui'
import { can, useAuth } from '@/lib/auth'
import { request, type Json } from '@/lib/api'
import { fmtAgo, fmtMs, fmtNum, fmtPct, fmtTime, fmtUsd } from '@/lib/format'
import { send, useGet } from '@/lib/query'

const col = createColumnHelper<Json>()
const P = '/api/v1/gateway'

const BYPASS_LABEL: Record<string, string> = {
  provider_keys: '공급자 키는 게이트웨이 시크릿 저장소에만', mcp_via_gateway: 'MCP 호출은 게이트웨이 경유', mcp_identity: '원격 MCP 는 위임·서비스 자격으로만',
  stdio: 'stdio 서버는 허용 명령만', a2a_via_gateway: 'A2A 위임은 게이트웨이 경유', network: 'egress 기본 거부 · 게이트웨이만 외부 허용', otel: 'OTel 관측 전송',
}

export function GatewayOverview() {
  const sum = useGet('/api/v1/observe/summary', { window_minutes: 60 }, { refetchInterval: 15_000 })
  const sec = useGet(`${P}/security`)
  const rules = useGet(`${P}/routing/rules`)
  const blocks = useGet(`${P}/governance/blocks`, { limit: 100 }, { refetchInterval: 30_000 })
  const pipe = useGet('/api/v1/observe/pipeline', undefined, { refetchInterval: 30_000 })
  const [chk, setChk] = useState<Json | null>(null)
  const [chkErr, setChkErr] = useState<unknown>(null)
  const [checking, setChecking] = useState(false)
  const ran = useRef(false)
  const check = async (audited = true) => {
    setChecking(true)
    setChkErr(null)
    try {
      setChk(await send<Json>(audited ? 'POST' : 'GET', `${P}/bypass-check`)) // 진입 시 GET(감사 없음) · 「지금 점검」 POST(감사)
    } catch (e) {
      setChkErr(e)
    } finally {
      setChecking(false)
    }
  }
  useEffect(() => {
    if (ran.current) return
    ran.current = true
    check(false) // 열 때 현황 조회 — 카운터(401/429/5xx)도 이 응답에서
  }, [])
  const s = sum.data
  const c = chk?.counts ?? {}
  const h = chk?.http_since_start ?? {}
  const failed = (chk?.items ?? []).filter((i: Json) => !i.ok)
  const cutoff = Date.now() - 86_400_000
  const recent = (blocks.data?.items ?? []).filter((b: Json) => new Date(b.ts).getTime() >= cutoff)
  const otel = (chk?.items ?? []).find((i: Json) => i.key === 'otel')
  const pd = pipe.data
  return (
    <>
      <PageHeader title="게이트웨이 개요 (단일 진입)" crumbs={['AI 게이트웨이']} desc="모든 LLM·MCP·A2A 호출은 이 게이트웨이를 거칩니다 — 정책·한도·가드·토큰 교환·관측이 한 지점에서 적용됩니다." />
      {sum.error && <ErrorBox error={sum.error} />}
      {s && (
        <div className="mb-4 grid grid-cols-4 gap-3">
          {(['model', 'tool', 'remote_agent'] as const).map((k) => {
            const b = s.by_kind?.[k] ?? {}
            return <Kpi key={k} label={{ model: 'Unified Model API', tool: 'MCP 프록시', remote_agent: 'A2A 프록시' }[k]} value={fmtNum(b.calls ?? 0)} sub={`p95 ${fmtMs(b.p95_latency_ms)} · 오류 ${fmtPct(b.error_rate ?? 0)}`} tone={(b.error_rate ?? 0) > 0.05 ? 'danger' : undefined} />
          })}
          <Kpi label="폴백 비율(1h)" value={fmtPct(s.fallback_rate)} sub={`승인 대기 ${s.approvals_pending}`} tone={s.fallback_rate > 0.3 ? 'warn' : undefined} />
        </div>
      )}
      <div role="group" className="mb-4 grid grid-cols-5 gap-3" aria-label="오류·차단 카운터">
        <Kpi label="차단(24h)" value={fmtNum(c.blocked_24h)} sub="감사 로그 거부 결정" tone={c.blocked_24h > 0 ? 'warn' : undefined} />
        <Kpi label="401 미인증" value={fmtNum(h['401'])} sub="토큰 없음·만료 · 프로세스 시작 이후" tone={h['401'] > 0 ? 'warn' : undefined} />
        <Kpi label="403 정책 거부(1h)" value={fmtNum(c.denied_1h)} sub="정책·허용목록" />
        <Kpi label="429 한도 초과(1h)" value={fmtNum(c.rate_limited_1h)} sub="Rate limit·예산" tone={c.rate_limited_1h > 0 ? 'warn' : undefined} />
        <Kpi label="5xx·오류(1h)" value={fmtNum(c.errors_1h)} sub="업스트림·내부 오류" tone={c.errors_1h > 0 ? 'danger' : undefined} />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-4">
          <Card title="모델 라우팅 규칙 (SCR-H02)" actions={<><Link to="/console/gateway/routing/models" className="text-xs text-primary">편집 →</Link><Link to="/console/gateway/routing/log" className="text-xs text-primary">결정 로그 →</Link></>}>
            <table className="w-full text-sm"><thead className="text-left text-xs text-fg-muted"><tr><th>우선</th><th>이름</th><th>조건</th><th>전략</th><th>대상 → 폴백</th></tr></thead>
              <tbody>{(rules.data?.items ?? []).map((r: Json) => (
                <tr key={r.id} className="border-t border-line"><td className="py-1.5">{r.priority}</td><td>{r.name}{!r.enabled && <Badge className="ml-1">중지</Badge>}</td>
                  <td className="text-xs">{r.match_model ?? '*'}{r.match_data_class ? ` · ${r.match_data_class}` : ''}</td><td><Badge tone="model">{r.strategy}</Badge></td>
                  <td className="mono text-xs">{(r.targets ?? []).map((t: Json) => t.model ?? t).join(', ')} → {(r.fallbacks ?? []).join(', ') || '—'}</td></tr>))}</tbody></table>
          </Card>
          <Card title={`차단된 우회 시도 (24h) ${recent.length}`} actions={<Link to="/console/audit" className="text-xs text-primary">감사 로그 →</Link>}>
            {blocks.error ? <ErrorBox error={blocks.error} /> : recent.length ? (
              <ul className="divide-y divide-line text-sm">
                {recent.slice(0, 8).map((b: Json, i: number) => (
                  <li key={i} className="flex items-center gap-2 py-1.5">
                    <span className="w-16 shrink-0 text-xs tabular-nums text-fg-muted" title={fmtTime(b.ts, true)}>{fmtTime(b.ts)}</span>
                    <Badge>{b.action}</Badge>
                    <span className="mono truncate text-xs">{b.actor}{b.target ? ` → ${b.target}` : ''}</span>
                    {b.reason && <Badge tone="danger">{b.reason}</Badge>}
                    <span className="ml-auto shrink-0"><TraceLink id={b.trace_id} /></span>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-fg-muted">최근 24시간 차단된 시도가 없습니다.</p>}
          </Card>
          <Card title="OTel 전송 상태 (관측 계층)" actions={<Link to="/console/observe/pipeline" className="text-xs text-primary">파이프라인 →</Link>}>
            {pipe.error ? <ErrorBox error={pipe.error} /> : (
              <Kv rows={[
                ['게이트웨이 스팬 내보내기', otel ? <Badge tone={otel.ok ? 'ok' : 'danger'} title={otel.detail}>{otel.ok ? '정상' : '실패'}</Badge> : <span className="text-fg-muted">점검 대기</span>],
                ['내장 저장소', pd ? `${fmtNum(pd.exporters?.builtin_store?.exported)}건 전송 · 실패 ${fmtNum(pd.exporters?.builtin_store?.failed ?? 0)}` : '—'],
                ['OTLP 내보내기', pd?.exporters?.otlp?.configured ? <span className="mono text-xs">{pd.exporters.otlp.endpoint}</span> : <Badge>미구성</Badge>],
                ['수신 (OTLP)', pd?.receiver ? `${fmtNum(pd.receiver.ingested)}건 · 최근 ${fmtAgo(pd.receiver.last_ingest_at)}` : '—'],
                ['스팬(1h)', Object.entries(pd?.spans_last_hour ?? {}).map(([k, v]) => `${k} ${fmtNum(v as number)}`).join(' · ') || '—'],
              ]} />
            )}
          </Card>
        </div>
        <div className="space-y-4">
          <Card title="우회 차단 점검" actions={<Button size="sm" variant="primary" loading={checking} onClick={() => check()}>지금 점검</Button>}>
            {chkErr ? <div className="mb-2"><ErrorBox error={chkErr} /></div> : null}
            {chk && !chk.ok && <div role="alert" className="mb-2 rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-sm text-danger">우회 점검 실패 — {failed.length}개 항목이 단일 진입을 보장하지 못합니다 (MSG-005)</div>}
            <ul className="space-y-1.5 text-sm" aria-label="우회 점검 항목">
              {(chk?.items ?? []).map((i: Json) => (
                <li key={i.key} className="flex items-start gap-2">
                  <span aria-hidden className={cx('mt-1.5 h-2 w-2 shrink-0 rounded-full', i.ok ? 'bg-ok' : 'bg-danger')} />
                  <div><div className={cx(!i.ok && 'font-medium text-danger')}>{BYPASS_LABEL[i.key] ?? i.key} {i.ok ? '✓' : '✗'}</div><div className="text-xs text-fg-muted">{i.detail}</div></div>
                </li>
              ))}
            </ul>
            <div className="mt-2 text-xs text-fg-muted">{chk ? `마지막 점검 ${fmtTime(chk.checked_at, true)} · ${chk.ok ? '모두 통과' : `실패 ${failed.length}건`}` : checking ? '점검 중…' : '아직 점검하지 않았습니다.'}</div>
          </Card>
          <Card title="Security — 인증·가드 (SCR-H05)">
            {sec.error ? <ErrorBox error={sec.error} /> : <Kv rows={Object.entries(sec.data?.settings ?? sec.data ?? {}).map(([k, v]) => [k, typeof v === 'object' ? <CodeBlock value={v} maxH="max-h-24" /> : <Badge tone={v === true ? 'ok' : v === false ? 'muted' : 'primary'}>{String(v)}</Badge>])} />}
          </Card>
        </div>
      </div>
    </>
  )
}

const RL_PERIODS: [string, string, number][] = [['15m', '최근 15분', 15], ['1h', '최근 1시간', 60], ['24h', '최근 24시간', 1440], ['7d', '최근 7일', 10080], ['all', '전체 기간', 0]]
const RL_KEYS = ['period', 'kind', 'status', 'fallback', 'subject', 'model', 'data_class', 'trace_id'] as const
type RlKey = (typeof RL_KEYS)[number]

// 텍스트 필터 — Enter·포커스 이탈 시 URL 에 반영 (입력마다 재조회하지 않음)
function FilterInput({ label, value, onCommit, className }: { label: string; value: string; onCommit: (v: string) => void; className?: string }) {
  const commit = (v: string) => v.trim() !== value && onCommit(v.trim())
  return <Input key={value} aria-label={label} placeholder={label} defaultValue={value} className={cx('w-40', className)}
    onBlur={(e) => commit(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && commit(e.currentTarget.value)} />
}

export function RoutingLog() {
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const f = Object.fromEntries(RL_KEYS.map((k) => [k, sp.get(k) ?? ''])) as Record<RlKey, string>
  const period = f.period || '24h'
  const minutes = RL_PERIODS.find((p) => p[0] === period)?.[2] ?? 1440
  const setF = (k: RlKey, v: string) => {
    const n = new URLSearchParams(sp)
    if (v) n.set(k, v)
    else n.delete(k)
    setSp(n, { replace: true })
  }
  const query = { kind: f.kind || undefined, status: f.status || undefined, fallback: f.fallback ? true : undefined, subject: f.subject || undefined,
    q: f.model || undefined, trace_id: f.trace_id || undefined, data_class: f.data_class || undefined, window_minutes: minutes || undefined }
  const q = useGet(`${P}/routing/decisions`, { ...query, limit: 500 }, { refetchInterval: 15_000 })
  const [sel, setSel] = useState<Json | null>(null)
  const [exporting, setExporting] = useState(false)
  // 기간·데이터 등급은 서버 필터가 없을 때도 맞도록 클라이언트에서 한 번 더 거른다
  const keep = (r: Json) => (!minutes || new Date(r.ts).getTime() >= Date.now() - minutes * 60_000)
    && (!f.data_class || (r.data_class !== undefined ? r.data_class === f.data_class : new RegExp(`data_class=${f.data_class}\\b|데이터 분류\\(${f.data_class}\\)`).test(r.reason ?? '')))
  const rows: Json[] = (q.data?.items ?? []).filter(keep)
  const exportJsonl = async () => {
    setExporting(true)
    try {
      const res = await request(`${P}/routing/decisions`, { query: { ...query, format: 'jsonl', limit: 10000 } })
      const lines = (await res.text()).split('\n').filter((l) => {
        try {
          return !!l.trim() && keep(JSON.parse(l))
        } catch {
          return false
        }
      })
      const url = URL.createObjectURL(new Blob([lines.join('\n') + '\n'], { type: 'application/x-ndjson' }))
      Object.assign(document.createElement('a'), { href: url, download: `routing-decisions-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.jsonl` }).click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      toast('ok', `JSONL ${fmtNum(lines.length)}건을 내보냈습니다.`)
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setExporting(false)
    }
  }
  const active = RL_KEYS.some((k) => f[k])
  const toolbar = <>
    <Select aria-label="기간" className="w-32" value={period} onChange={(e) => setF('period', e.target.value === '24h' ? '' : e.target.value)}>
      {RL_PERIODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </Select>
    <Select aria-label="유형" className="w-28" value={f.kind} onChange={(e) => setF('kind', e.target.value)}>
      <option value="">유형: 전체</option><option value="model">모델</option><option value="mcp">MCP</option><option value="a2a">A2A</option>
    </Select>
    <Select aria-label="결과" className="w-32" value={f.status} onChange={(e) => setF('status', e.target.value)}>
      <option value="">결과: 전체</option>{['ok', 'error', 'tool_error', 'no_match'].map((s) => <option key={s} value={s}>{s}</option>)}
    </Select>
    <Select aria-label="데이터 등급" className="w-36" value={f.data_class} onChange={(e) => setF('data_class', e.target.value)}>
      <option value="">데이터 등급: 전체</option>{['public', 'internal', 'confidential'].map((s) => <option key={s} value={s}>{s}</option>)}
    </Select>
    <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={!!f.fallback} onChange={(e) => setF('fallback', e.target.checked ? '1' : '')} />폴백만</label>
    <FilterInput label="주체(sub)" value={f.subject} onCommit={(v) => setF('subject', v)} />
    <FilterInput label="모델·대상" value={f.model} onCommit={(v) => setF('model', v)} />
    <FilterInput label="trace_id" value={f.trace_id} onCommit={(v) => setF('trace_id', v)} className="mono w-44" />
    {active && <Button size="sm" variant="ghost" onClick={() => setSp({}, { replace: true })}>필터 초기화</Button>}
  </>
  const columns = [
    col.accessor('ts', { header: '시각', cell: (c) => fmtTime(c.getValue(), true) }),
    col.accessor('kind', { header: '종류', cell: (c) => <Badge tone="model">{c.getValue()}</Badge> }),
    col.accessor('requested', { header: '요청', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
    col.accessor('chosen', { header: '선택', cell: (c) => <span className="mono text-xs">{c.getValue() ?? '—'}</span> }),
    col.accessor('fallback_used', { header: '폴백', cell: (c) => c.getValue() ? <Badge tone="warn">폴백</Badge> : '—' }),
    col.accessor('reason', { header: '사유', cell: (c) => <span className="text-xs">{c.getValue()}</span> }),
    col.accessor('status', { header: '결과', cell: (c) => <StatusBadge status={c.getValue()} /> }),
    col.accessor('subject', { header: '주체' }),
    col.accessor('trace_id', { header: 'trace', cell: (c) => <span onClick={(e) => e.stopPropagation()}><TraceLink id={c.getValue()} /></span> }),
  ]
  return (
    <>
      <PageHeader title="라우팅 결정 로그" crumbs={['AI 게이트웨이', '라우팅 결정']} desc="요청 모델 → 데이터 등급·정책·회로 차단·전략으로 선택된 공급자 (FR-G01·O02)"
        actions={<Button loading={exporting} onClick={exportJsonl} title="현재 필터 결과를 최대 10,000건까지 JSONL 로 내려받습니다">내보내기 (JSONL)</Button>} />
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={rows} columns={columns} onRowClick={setSel} toolbar={toolbar} empty="조건에 맞는 라우팅 결정이 없습니다." />
      <Drawer open={!!sel} onClose={() => setSel(null)} title="라우팅 결정"><CodeBlock value={sel ?? {}} maxH="max-h-[80vh]" /></Drawer>
    </>
  )
}

export function Policies() {
  const nav = useNavigate()
  const [sp, setSp] = useSearchParams()
  const tab = (sp.get('tab') || 'list') as 'list' | 'review' | 'tests'
  const q = useGet(`${P}/policies`, undefined, { refetchInterval: 30_000 })
  const pending = useGet(`${P}/policies/drafts`, { status: 'pending' }, { refetchInterval: 30_000 })
  const es = q.data?.engine_status
  const nPending = pending.data?.items?.length ?? 0
  const columns = [
    col.accessor('priority', { header: '우선' }),
    col.accessor('name', { header: '이름', cell: (c) => <span className="font-medium">{c.getValue()}</span> }),
    col.accessor('effect', { header: '효과', cell: (c) => <StatusBadge status={c.getValue()} /> }),
    col.display({ id: 'subj', header: '주체', cell: ({ row }) => <span className="text-xs">{row.original.subject_type}:{row.original.subject}</span> }),
    col.display({ id: 'tgt', header: '대상', cell: ({ row }) => <span className="mono text-xs">{row.original.target_type}:{row.original.target}</span> }),
    col.accessor('approval_required', { header: '승인', cell: (c) => c.getValue() ? <Badge tone="warn">필요</Badge> : '—' }),
    col.accessor('data_class_routing', { header: '데이터 등급 라우팅', enableSorting: false, cell: (c) => <span className="mono text-xs">{Object.entries(c.getValue() ?? {}).map(([k, v]) => `${k}→${v}`).join(', ') || '—'}</span> }),
    col.accessor('decisions_24h', { header: '24h 결정', enableSorting: false, cell: (c) => <span className="text-xs tabular-nums"><span className="text-ok">허용 {fmtNum(c.getValue()?.allow ?? 0)}</span> · <span className="text-danger">거부 {fmtNum(c.getValue()?.deny ?? 0)}</span></span> }),
    col.accessor('enabled', { header: '사용', cell: (c) => <StatusBadge status={c.getValue() ? 'enabled' : 'disabled'} /> }),
    col.accessor('version', { header: '버전', cell: ({ row }) => <>v{row.original.version}{row.original.pending_review && <Badge tone="warn" className="ml-1">게시 대기</Badge>}</> }),
  ]
  return (
    <>
      <PageHeader title="정책 목록·버전" crumbs={['AI 게이트웨이', '정책']}
        desc={<>엔진 <Badge tone={es ? (es.synced ? 'ok' : 'danger') : 'muted'} title={es?.bundle}>{es?.engine ?? q.data?.engine ?? '—'}</Badge> {es?.detail} · 우선순위 순 평가 · deny 우선 · 기본 거부 · 변경은 초안 → 회귀 시험 → 작성자 외 승인</>}
        actions={<Gate perm="policy:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => nav('/console/gateway/policies/new')}>+ 정책 초안</Button>}</Gate>} />
      <Tabs tabs={[{ id: 'list', label: '정책' }, { id: 'review', label: <>게시 심사 {nPending > 0 && <Badge tone="warn">{nPending}</Badge>}</> }, { id: 'tests', label: '회귀 시험 케이스' }]}
        value={tab} onChange={(v) => setSp(v === 'list' ? {} : { tab: v })} />
      {tab === 'list' && <>
        {q.error && <ErrorBox error={q.error} />}
        <DataTable data={q.data?.items ?? []} columns={columns} onRowClick={(r) => nav(`/console/gateway/policies/${r.id}`)} />
      </>}
      {tab === 'review' && <DraftQueue onChange={() => { pending.refetch(); q.refetch() }} />}
      {tab === 'tests' && <PolicyTests />}
    </>
  )
}

const testSummary = (r: Json | undefined) => !r?.tested_at ? <span className="text-xs text-fg-muted">미시험</span>
  : r.passed ? <Badge tone="ok">통과 {r.cases?.length ?? 0}건</Badge> : <Badge tone="danger">실패 {(r.cases ?? []).filter((c: Json) => !c.pass).length}건</Badge>

function DraftQueue({ onChange }: { onChange: () => void }) {
  const nav = useNavigate()
  const toast = useToast()
  const { me } = useAuth()
  const [status, setStatus] = useState('pending')
  const q = useGet(`${P}/policies/drafts`, { status })
  const decide = async (d: Json, action: 'approve' | 'reject') => {
    const note = action === 'reject' ? askReason(d.policy?.name) : window.prompt('승인 메모 (선택)', '') ?? null
    if (note === null) return
    try {
      await send('POST', `${P}/policies/drafts/${d.id}/${action}`, { note })
      toast('ok', action === 'approve' ? `게시했습니다 — ${d.policy?.name}` : '반려했습니다 — 작성자에게 사유가 전달됩니다')
      q.refetch()
      onChange()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const columns = [
    col.display({ id: 'p', header: '정책', cell: ({ row }) => <div><div className="font-medium">{row.original.policy?.name}</div><div className="mono text-[11px] text-fg-muted">{row.original.policy_id ?? '새 정책'}{row.original.base_version ? ` · v${row.original.base_version} 기준` : ''}</div></div> }),
    col.display({ id: 'chg', header: '변경', cell: ({ row }) => row.original.delete ? <Badge tone="danger">삭제</Badge> : <span className="text-xs">{row.original.diff?.length ?? 0}개 필드</span> }),
    col.accessor('test_result', { header: '회귀 시험', enableSorting: false, cell: (c) => <div className="flex flex-col gap-0.5">{testSummary(c.getValue())}{c.getValue()?.replay && <span className="text-[11px] text-fg-muted">최근 요청 {c.getValue().replay.checked}건 중 판정 변화 {c.getValue().replay.changed}</span>}</div> }),
    col.accessor('reason', { header: '사유', cell: (c) => <span className="text-xs">{c.getValue() || '—'}</span> }),
    col.accessor('created_by', { header: '작성자' }),
    col.accessor('created_at', { header: '작성', cell: (c) => fmtAgo(c.getValue()) }),
    col.accessor('status', { header: '상태', cell: (c) => <StatusBadge status={c.getValue()} /> }),
    col.display({ id: 'act', header: '', cell: ({ row }) => row.original.status !== 'pending' ? null : (
      <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
        <Gate perm="policy:write">{(ok, why) => {
          const self = row.original.created_by === me?.sub
          const t = self ? '작성자는 자신의 변경을 승인할 수 없습니다 (2인 검토)' : why
          return <><Button size="sm" disabled={!ok || self} title={t} onClick={() => decide(row.original, 'reject')}>반려</Button>
            <Button size="sm" variant="primary" disabled={!ok || self} title={t} onClick={() => decide(row.original, 'approve')}>승인·게시</Button></>
        }}</Gate>
      </div>) }),
  ]
  return (
    <>
      <div className="mb-3 flex items-center gap-2 text-sm">
        <span className="text-fg-muted">상태</span>
        <Select className="w-40" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="초안 상태">
          {[['pending', '게시 대기'], ['draft', '초안'], ['approved', '게시됨'], ['rejected', '반려']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
        <span className="text-xs text-fg-muted">회귀 시험을 통과한 초안만 게시 요청할 수 있고, 작성자가 아닌 정책 권한자가 승인해야 적용됩니다.</span>
      </div>
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={q.data?.items ?? []} columns={columns} onRowClick={(d) => nav(`/console/gateway/policies/${d.policy_id ?? 'new'}?draft=${d.id}`)} empty="해당 상태의 정책 초안이 없습니다." />
    </>
  )
}

const TBLANK = { name: '', kind: 'tool', target: '', roles: 'user', groups: '', data_class: 'internal', expect: 'deny' }

function PolicyTests() {
  const toast = useToast()
  const q = useGet(`${P}/policy-tests`)
  const [f, setF] = useState(TBLANK)
  const set = (k: keyof typeof TBLANK, v: string) => setF({ ...f, [k]: v })
  const add = async () => {
    try {
      const split = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean)
      await send('POST', `${P}/policy-tests`, { name: f.name, expect: f.expect, input: { kind: f.kind, target: f.target, data_class: f.data_class, sub: 'policy-test', roles: split(f.roles), groups: split(f.groups) } })
      setF(TBLANK)
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const remove = async (t: Json) => {
    if (!confirm(`시험 케이스 "${t.name}" 을 삭제할까요?`)) return
    await send('DELETE', `${P}/policy-tests/${t.id}`).catch((e) => toast('error', e.message))
    q.refetch()
  }
  return (
    <div className="grid grid-cols-[360px_1fr] gap-4">
      <Card title="케이스 추가">
        <div className="space-y-2">
          <Field label="이름"><Input value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="일반 사용자는 Jira 상태 전환 불가" /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="종류"><Select value={f.kind} onChange={(e) => set('kind', e.target.value)}><option>tool</option><option>model</option><option>agent</option></Select></Field>
            <Field label="데이터 등급"><Select value={f.data_class} onChange={(e) => set('data_class', e.target.value)}><option>public</option><option>internal</option><option>confidential</option></Select></Field>
          </div>
          <Field label="대상"><Input className="mono" value={f.target} onChange={(e) => set('target', e.target.value)} placeholder="jira.transition_issue" /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="역할(쉼표)"><Input value={f.roles} onChange={(e) => set('roles', e.target.value)} /></Field>
            <Field label="그룹(쉼표)"><Input value={f.groups} onChange={(e) => set('groups', e.target.value)} /></Field>
          </div>
          <Field label="기대 판정"><Select value={f.expect} onChange={(e) => set('expect', e.target.value)}><option value="allow">허용</option><option value="deny">거부</option></Select></Field>
          <Gate perm="policy:write">{(ok, why) => <Button variant="primary" className="w-full" disabled={!ok || !f.name || !f.target} title={why} onClick={add}>추가</Button>}</Gate>
        </div>
      </Card>
      <Card title={`회귀 시험 케이스 ${q.data?.items?.length ?? ''}`} bodyClass="p-0">
        {q.error && <ErrorBox error={q.error} />}
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">이름</th><th>입력</th><th>기대</th><th>작성자</th><th /></tr></thead>
          <tbody>
            {(q.data?.items ?? []).map((t: Json) => (
              <tr key={t.id} className="border-t border-line">
                <td className="px-3 py-2">{t.name}</td>
                <td className="mono text-xs">{t.input.kind}:{t.input.target} · {(t.input.roles ?? []).join(',') || '—'}{t.input.groups?.length ? ` · ${t.input.groups.join(',')}` : ''} · {t.input.data_class}</td>
                <td><StatusBadge status={t.expect} /></td><td className="text-xs">{t.created_by}</td>
                <td className="pr-2 text-right"><Gate perm="policy:write">{(ok) => <Button size="sm" variant="ghost" disabled={!ok} onClick={() => remove(t)}>삭제</Button>}</Gate></td>
              </tr>
            ))}
            {!q.data?.items?.length && <tr><td colSpan={5} className="px-3 py-6 text-center text-fg-muted">케이스가 없습니다 — 게시 전에 꼭 지켜야 할 판정을 등록하세요.</td></tr>}
          </tbody>
        </table>
      </Card>
    </div>
  )
}

const BLANK = { name: '', description: '', subject_type: 'any', subject: '*', target_type: 'tool', target: '*', effect: 'allow', conditions: {}, args_schema: {}, data_class_routing: {}, approval_required: false, priority: 100, enabled: true }
const POLICY_FIELDS = Object.keys(BLANK)
const fmtVal = (v: unknown) => v === undefined || v === null ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v)

function DiffTable({ rows }: { rows: Json[] }) {
  if (!rows?.length) return <p className="text-sm text-fg-muted">변경 없음</p>
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs text-fg-muted"><tr><th className="py-1">필드</th><th>이전</th><th>이후</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.field} className="border-t border-line align-top"><td className="py-1.5 font-medium">{r.field}</td>
        <td className="mono text-xs text-danger"><del>{fmtVal(r.from)}</del></td><td className="mono text-xs text-ok">{fmtVal(r.to)}</td></tr>)}</tbody>
    </table>
  )
}

function TestResult({ r }: { r?: Json }) {
  if (!r?.tested_at) return <p className="text-sm text-fg-muted">초안을 저장한 뒤 「회귀 시험」을 실행하세요 — 등록된 케이스와 최근 24시간 실제 요청을 이 초안으로 다시 평가합니다.</p>
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">{testSummary(r)}<span className="text-xs text-fg-muted">시험 {fmtTime(r.tested_at, true)}</span></div>
      <Card title="시험 케이스" bodyClass="p-0">
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">케이스</th><th>기대</th><th>결과</th><th>사유</th></tr></thead>
          <tbody>{(r.cases ?? []).map((c: Json) => <tr key={c.id} className={cx('border-t border-line', !c.pass && 'bg-danger/5')}>
            <td className="px-3 py-2">{c.pass ? '✓' : '✗'} {c.name}</td><td><StatusBadge status={c.expect} /></td><td><StatusBadge status={c.got} /></td><td className="text-xs">{c.reason}</td></tr>)}
            {!r.cases?.length && <tr><td colSpan={4} className="px-3 py-4 text-center text-xs text-fg-muted">등록된 케이스 없음 — 정책 목록 › 회귀 시험 케이스</td></tr>}
          </tbody>
        </table>
      </Card>
      <Card title={`최근 요청 재평가 — ${r.replay?.checked ?? 0}건 중 판정 변화 ${r.replay?.changed ?? 0}건`}>
        {r.replay?.samples?.length ? (
          <table className="w-full text-sm"><thead className="text-left text-xs text-fg-muted"><tr><th>대상</th><th>주체</th><th>등급</th><th>변화</th></tr></thead>
            <tbody>{r.replay.samples.map((s: Json, i: number) => <tr key={i} className="border-t border-line"><td className="mono py-1 text-xs">{s.kind}:{s.target}</td><td className="text-xs">{s.sub}</td><td className="text-xs">{s.data_class}</td>
              <td><StatusBadge status={s.before} /> → <StatusBadge status={s.after} /></td></tr>)}</tbody></table>
        ) : <p className="text-sm text-fg-muted">게시해도 최근 요청의 허용·거부가 바뀌지 않습니다.</p>}
      </Card>
    </div>
  )
}

export function PolicyEditor() {
  const { id = 'new' } = useParams()
  const [sp, setSp] = useSearchParams()
  const draftId = sp.get('draft')
  const nav = useNavigate()
  const toast = useToast()
  const { me } = useAuth()
  const isNew = id === 'new'
  const list = useGet(isNew ? null : `${P}/policies`)
  const versions = useGet(isNew ? null : `${P}/policies/${id}/versions`)
  const draft = useGet(draftId ? `${P}/policies/drafts/${draftId}` : null)
  const [f, setF] = useState<Json>(BLANK)
  const [json, setJson] = useState({ conditions: '{}', args_schema: '{}', data_class_routing: '{}' })
  const [err, setErr] = useState<unknown>(null)
  const [tab, setTab] = useState<'edit' | 'sim' | 'test' | 'rego' | 'diff' | 'ver'>('edit')
  const [busy, setBusy] = useState('')
  const [rego, setRego] = useState('')
  const [verDiff, setVerDiff] = useState<Json | null>(null)
  const current = list.data?.items?.find((x: Json) => x.id === id)
  const d = draft.data
  const load = (p: Json) => {
    setF({ ...BLANK, ...p })
    setJson({ conditions: JSON.stringify(p.conditions ?? {}, null, 2), args_schema: JSON.stringify(p.args_schema ?? {}, null, 2), data_class_routing: JSON.stringify(p.data_class_routing ?? {}, null, 2) })
  }
  useEffect(() => {
    if (d) load(d.policy)
    else if (current) load(current)
  }, [d, current])
  const body = () => {
    const b: Json = { ...f, conditions: JSON.parse(json.conditions || '{}'), args_schema: JSON.parse(json.args_schema || '{}'), data_class_routing: JSON.parse(json.data_class_routing || '{}') }
    return Object.fromEntries(Object.entries(b).filter(([k]) => POLICY_FIELDS.includes(k)))
  }
  const run = async (key: string, fn: () => Promise<void>) => {
    setErr(null)
    setBusy(key)
    try {
      await fn()
    } catch (e) {
      setErr(e instanceof SyntaxError ? { message: `JSON 형식 오류: ${e.message}` } : e)
    } finally {
      setBusy('')
    }
  }
  const editable = !d || ['draft', 'rejected'].includes(d.status)
  const saveDraft = () => run('draft', async () => {
    const b = body()
    const r = d && editable ? await send<Json>('PUT', `${P}/policies/drafts/${d.id}`, b) : await send<Json>('POST', `${P}/policies/drafts`, { policy_id: isNew ? null : id, policy: b })
    setSp({ draft: r.id }, { replace: true })
    await draft.refetch()
    toast('ok', '초안 저장 — 회귀 시험을 실행하세요')
  })
  const test = () => run('test', async () => {
    await send('POST', `${P}/policies/drafts/${d!.id}/test`)
    await draft.refetch()
    setTab('test')
  })
  const submit = () => run('submit', async () => {
    const reason = window.prompt('게시 사유 (필수, MSG-003) — 승인자에게 전달되고 감사에 남습니다')
    if (reason === null) return
    if (!reason.trim()) throw { message: '게시 사유를 입력하세요 (MSG-003).' }
    await send('POST', `${P}/policies/drafts/${d!.id}/submit`, { reason })
    await draft.refetch()
    toast('ok', '게시 요청 — 작성자가 아닌 정책 권한자가 승인하면 적용됩니다')
  })
  const decide = (action: 'approve' | 'reject') => run(action, async () => {
    const note = action === 'reject' ? askReason(f.name) : window.prompt('승인 메모 (선택)', '') ?? null
    if (note === null) return
    const r = await send<Json>('POST', `${P}/policies/drafts/${d!.id}/${action}`, { note })
    toast('ok', action === 'approve' ? '게시했습니다 — 새 버전이 적용됐습니다' : '반려했습니다')
    if (action === 'approve' && r.policy_id && isNew) nav(`/console/gateway/policies/${r.policy_id}?draft=${r.id}`, { replace: true })
    await draft.refetch()
    list.refetch()
    versions.refetch()
  })
  const requestDelete = () => run('delete', async () => {
    if (!confirm(`정책 "${f.name}" 삭제를 게시 요청 초안으로 만듭니다 — 시험·승인 후 삭제됩니다 (MSG-002). 계속할까요?`)) return
    const r = await send<Json>('POST', `${P}/policies/drafts`, { policy_id: id, delete: true })
    setSp({ draft: r.id }, { replace: true })
    await draft.refetch()
  })
  const breakGlass = () => run('direct', async () => {
    if (!confirm('관리자 비상 경로 — 2인 검토 없이 즉시 적용하고 감사에 break_glass 로 기록합니다. 계속할까요?')) return
    const r = await send<Json>(isNew ? 'POST' : 'PUT', isNew ? `${P}/policies` : `${P}/policies/${id}`, body())
    toast('ok', `즉시 적용 — v${r.version} (비상 경로 감사 기록)`)
    if (isNew) nav(`/console/gateway/policies/${r.id}`, { replace: true })
    else { list.refetch(); versions.refetch() }
  })
  const showRego = () => run('rego', async () => {
    setRego((await send<Json>('POST', `${P}/policies/rego`, { policy: body() })).rego)
    setTab('rego')
  })
  const compare = (v: number) => run(`cmp${v}`, async () => setVerDiff(await send<Json>('GET', `${P}/policies/${id}/diff?from=${v}`)))
  const rollback = (v: number) => run(`rb${v}`, async () => {
    const reason = window.prompt(`v${v} 로 되돌립니다 — 사유 (필수, MSG-003)`)
    if (reason === null) return
    if (!confirm(`v${v} 의 규칙을 새 버전으로 즉시 복원합니다 (MSG-002). 계속할까요?`)) return
    const r = await send<Json>('POST', `${P}/policies/${id}/rollback`, { version: v, reason, confirm: true })
    toast('ok', `v${v} 로 되돌렸습니다 — 현재 v${r.version}`)
    list.refetch()
    versions.refetch()
  })
  const set = (k: string, v: unknown) => setF({ ...f, [k]: v })
  const passed = !!d?.test_result?.passed && !!d?.test_result?.tested_at
  const stage = !d ? 0 : d.status === 'approved' ? 4 : d.status === 'pending' ? 3 : passed ? 2 : 1
  const self = d?.created_by === me?.sub
  const admin = me?.roles?.includes('admin')
  return (
    <>
      <PageHeader title={isNew && !d?.policy_id ? '새 정책' : f.name || id} crumbs={['AI 게이트웨이', '정책', isNew ? 'new' : `v${current?.version ?? ''}`]}
        desc={d ? <>초안 <span className="mono">{d.id}</span> · <StatusBadge status={d.status} /> · 작성 {d.created_by}{d.reason ? ` · 사유: ${d.reason}` : ''}</> : '정책 변경은 초안으로 저장 → 회귀 시험 → 게시 요청(사유) → 작성자 외 승인 순서로 적용됩니다.'}
        actions={<Gate perm="policy:write">{(ok, why) => <>
          {!isNew && !d?.delete && <Button disabled={!ok} title={why} loading={busy === 'delete'} onClick={requestDelete}>삭제 요청</Button>}
          {admin && <Button variant="danger" disabled={!f.name} loading={busy === 'direct'} title="관리자 비상 경로 — 감사에 break_glass 로 기록" onClick={breakGlass}>즉시 적용(비상)</Button>}
          <Button disabled={!ok || !f.name || !editable} title={editable ? why : '게시 요청 중인 초안은 고칠 수 없습니다'} loading={busy === 'draft'} onClick={saveDraft}>초안 저장</Button>
          <Button disabled={!ok || !d || !editable} title={!d ? '먼저 초안을 저장하세요' : why} loading={busy === 'test'} onClick={test}>회귀 시험</Button>
          {d?.status === 'pending'
            ? <><Button disabled={!ok || self} title={self ? '작성자는 자신의 변경을 승인할 수 없습니다 (2인 검토)' : why} loading={busy === 'reject'} onClick={() => decide('reject')}>반려</Button>
              <Button variant="primary" disabled={!ok || self} title={self ? '작성자는 자신의 변경을 승인할 수 없습니다 (2인 검토)' : why} loading={busy === 'approve'} onClick={() => decide('approve')}>승인·게시</Button></>
            : <Button variant="primary" disabled={!ok || !passed || !editable} title={!passed ? '회귀 시험을 통과해야 게시 요청할 수 있습니다' : why} loading={busy === 'submit'} onClick={submit}>게시 요청</Button>}
        </>}</Gate>} />
      <Stepper steps={['초안', '회귀 시험', '게시 요청', '작성자 외 승인', '적용']} current={stage} />
      {d && d.base_version != null && d.current_version != null && d.base_version !== d.current_version && d.status !== 'approved' && (
        <div role="alert" className="mb-3 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-sm">초안 작성(v{d.base_version}) 이후 정책이 v{d.current_version} 으로 바뀌었습니다 — 새 초안으로 다시 시험하세요.</div>
      )}
      {d?.delete && <div role="alert" className="mb-3 rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-sm">삭제 요청 초안 — 승인되면 이 정책이 삭제됩니다.</div>}
      <Tabs tabs={[{ id: 'edit', label: '편집' }, { id: 'sim', label: '시뮬레이터' }, { id: 'test', label: <>회귀 시험 {d?.test_result?.tested_at ? testSummary(d.test_result) : null}</> }, { id: 'rego', label: 'Rego' },
        ...(d ? [{ id: 'diff' as const, label: `변경 비교 ${d.diff?.length ?? 0}` }] : []), ...(isNew ? [] : [{ id: 'ver' as const, label: '버전' }])]} value={tab} onChange={(v) => v === 'rego' ? showRego() : setTab(v)} />
      {err ? <div className="mb-3"><ErrorBox error={err} /></div> : null}
      {tab === 'edit' && (
        <fieldset disabled={!editable} className="grid grid-cols-2 gap-4">
          <Card title="규칙">
            <div className="grid grid-cols-2 gap-3">
              <Field label="이름"><Input value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
              <Field label="우선순위(낮을수록 먼저)"><Input type="number" value={f.priority} onChange={(e) => set('priority', Number(e.target.value))} /></Field>
              <Field label="주체 유형"><Select value={f.subject_type} onChange={(e) => set('subject_type', e.target.value)}>{['any', 'user', 'group', 'role', 'team', 'agent'].map((x) => <option key={x}>{x}</option>)}</Select></Field>
              <Field label="주체"><Input value={f.subject} onChange={(e) => set('subject', e.target.value)} /></Field>
              <Field label="대상 유형"><Select value={f.target_type} onChange={(e) => set('target_type', e.target.value)}>{['model', 'tool', 'agent'].map((x) => <option key={x}>{x}</option>)}</Select></Field>
              <Field label="대상" hint="* · jira.* · category:commercial · risk:high"><Input className="mono" value={f.target} onChange={(e) => set('target', e.target.value)} /></Field>
              <Field label="효과"><Select value={f.effect} onChange={(e) => set('effect', e.target.value)}><option>allow</option><option>deny</option></Select></Field>
              <div className="flex flex-col justify-end gap-1 text-sm">
                <label className="flex items-center gap-2"><input type="checkbox" checked={f.approval_required} onChange={(e) => set('approval_required', e.target.checked)} />사람 승인 필요</label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={f.enabled} onChange={(e) => set('enabled', e.target.checked)} />사용</label>
              </div>
            </div>
            <Field label="설명"><Textarea className="mt-1 h-16" value={f.description ?? ''} onChange={(e) => set('description', e.target.value)} /></Field>
          </Card>
          <Card title="조건·인자 스키마·데이터 등급 라우팅 (JSON)">
            {(['conditions', 'args_schema', 'data_class_routing'] as const).map((k) => (
              <Field key={k} label={{ conditions: '조건 — {"data_class":["internal"]}', args_schema: 'args_schema (JSON Schema 2020-12)', data_class_routing: '데이터 등급 → 모델 — {"internal":"domain-local"}' }[k]}>
                <Textarea className="mono mb-3 h-24 text-xs" value={json[k]} onChange={(e) => setJson({ ...json, [k]: e.target.value })} />
              </Field>
            ))}
          </Card>
        </fieldset>
      )}
      {tab === 'sim' && <Simulator defaults={{ kind: f.target_type, target: f.target === '*' ? '' : f.target }} />}
      {tab === 'test' && <TestResult r={d?.test_result} />}
      {tab === 'rego' && <Card title="Rego 미리보기 — OPA 번들과 같은 의미 (내장 엔진은 같은 규칙을 직접 평가)"><CodeBlock value={rego || d?.rego || ''} maxH="max-h-[520px]" /></Card>}
      {tab === 'diff' && d && <Card title={`게시 시 바뀌는 필드 — 현재 ${d.current_version ? `v${d.current_version}` : '(새 정책)'} → 초안`}><DiffTable rows={d.diff ?? []} /></Card>}
      {tab === 'ver' && (
        <div className="grid grid-cols-[1fr_380px] gap-4">
          <Card bodyClass="p-0"><table className="w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">버전</th><th>변경자</th><th>시각</th><th>스냅샷</th><th /></tr></thead>
            <tbody>{(versions.data?.items ?? []).map((v: Json) => <tr key={v.version} className="border-t border-line align-top">
              <td className="px-3 py-2">v{v.version}{v.version === current?.version && <Badge tone="primary" className="ml-1">현재</Badge>}</td><td>{v.changed_by}</td><td>{fmtTime(v.created_at, true)}</td>
              <td className="py-2"><CodeBlock value={v.snapshot} maxH="max-h-32" /></td>
              <td className="space-y-1 px-2 py-2 text-right">{v.version !== current?.version && <>
                <Button size="sm" loading={busy === `cmp${v.version}`} onClick={() => compare(v.version)}>현재와 비교</Button>
                <Gate perm="policy:write">{(ok, why) => <Button size="sm" disabled={!ok} title={why} loading={busy === `rb${v.version}`} onClick={() => rollback(v.version)}>되돌리기</Button>}</Gate></>}</td>
            </tr>)}</tbody></table></Card>
          <Card title={verDiff ? `v${verDiff.from} → 현재` : '버전 비교'}>{verDiff ? <DiffTable rows={verDiff.changes} /> : <p className="text-sm text-fg-muted">「현재와 비교」로 바뀐 필드를 봅니다. 되돌리기는 사유·확인 후 그 버전을 새 버전으로 복원합니다.</p>}</Card>
        </div>
      )}
    </>
  )
}

function Simulator({ defaults }: { defaults: { kind: string; target: string } }) {
  const [kind, setKind] = useState(defaults.kind)
  const [target, setTarget] = useState(defaults.target || 'jira.transition_issue')
  const [dc, setDc] = useState('internal')
  const [roles, setRoles] = useState('user')
  const [args, setArgs] = useState('{}')
  const [res, setRes] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const run = async () => {
    setErr(null)
    try {
      setRes(await send<Json>('POST', `${P}/policies/simulate`, { kind, target, data_class: dc, args: JSON.parse(args || '{}'), subject: roles ? { sub: 'sim-user', roles: roles.split(',').map((r) => r.trim()) } : {} }))
    } catch (e) {
      setErr(e)
    }
  }
  const d = res?.decision
  return (
    <div className="grid grid-cols-[380px_1fr] gap-4">
      <Card title="입력">
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <Field label="종류"><Select value={kind} onChange={(e) => setKind(e.target.value)}><option>model</option><option>tool</option><option>agent</option></Select></Field>
            <Field label="데이터 등급"><Select value={dc} onChange={(e) => setDc(e.target.value)}><option>public</option><option>internal</option><option>confidential</option></Select></Field>
          </div>
          <Field label="대상"><Input className="mono" value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
          <Field label="주체 역할(쉼표) — 비우면 나"><Input value={roles} onChange={(e) => setRoles(e.target.value)} /></Field>
          <Field label="인자(JSON)"><Textarea className="mono h-24 text-xs" value={args} onChange={(e) => setArgs(e.target.value)} /></Field>
          <Button variant="primary" className="w-full" onClick={run}>평가</Button>
        </div>
      </Card>
      <Card title="결정">
        {err ? <ErrorBox error={err} /> : null}
        {d ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-lg font-semibold">{d.allow ? <Badge tone="ok" className="text-sm">허용</Badge> : <Badge tone="danger" className="text-sm">거부</Badge>}{d.require_approval && <Badge tone="warn">사람 승인</Badge>}{d.reroute && <Badge tone="model">재라우팅 → {d.reroute}</Badge>}</div>
            <Kv rows={[['사유', d.reason], ['정책', d.policy_name ? `${d.policy_name} v${d.policy_version}` : '기본 거부'], ['엔진', d.engine], ['일치', (d.matched ?? []).map((m: Json) => m.name ?? m).join(', ') || '—']]} />
            <CodeBlock value={res} maxH="max-h-72" />
          </div>
        ) : <p className="text-sm text-fg-muted">게시 전에 정책 결과를 미리 확인합니다 (deny 우선 · 인자 제한 위반 · 기본 거부).</p>}
      </Card>
    </div>
  )
}

const LBLANK = { name: '', scope_type: 'team', scope: '', target_type: 'any', target: '*', rps: '', burst: '', tokens_per_min: '', budget_usd: '', period: 'month', alert_pct: 80, on_exceed: 'block', fallback_model: '' }

const LTABS = [['all', '전체'], ['user', '사용자'], ['team', '팀'], ['agent', '에이전트'], ['model', '모델'], ['tool', '도구']] as const
type LTab = (typeof LTABS)[number][0]
const inLTab = (r: Json, t: LTab) => t === 'all' || (t === 'user' || t === 'team' ? r.scope_type === t
  : t === 'agent' ? r.scope_type === 'agent' || r.target_type === 'agent' : r.target_type === t)
const ON_EXCEED: Record<string, string> = { block: '차단(429)', fallback: '저비용 폴백', warn: '경고만' }

// 429 미리보기 (SCR-H09 ⑧) — 버킷을 쓰지 않고 적용 한도·사용량·차단 여부와 사용자에게 보일 응답
function LimitPreview({ limits }: { limits: Json[] }) {
  const [f, setF] = useState({ kind: 'model', target: '', sub: '', roles: 'user', team: '', agent: '' })
  const [res, setRes] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof f, v: string) => setF({ ...f, [k]: v })
  const run = async () => {
    setErr(null)
    setBusy(true)
    try {
      const subject: Json = { sub: f.sub || 'preview-user', roles: f.roles.split(',').map((r) => r.trim()).filter(Boolean) }
      if (f.team) subject.team = f.team
      if (f.agent) subject.agent = f.agent
      setRes({ ...(await send<Json>('POST', `${P}/limits/preview`, { kind: f.kind, target: f.target, subject })), kind: f.kind, who: subject.sub })
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }
  const rows: Json[] = res?.limits ?? []
  const overBudget = (r: Json) => r.budget_usd != null && (r.spend_usd ?? 0) >= r.budget_usd
  const over = (r: Json) => overBudget(r) || (!!r.tokens_per_min && (r.tokens_used ?? 0) >= r.tokens_per_min)
  const block = rows.find((r) => r.would_block || (over(r) && (r.on_exceed === 'block' || (r.on_exceed === 'fallback' && res?.kind !== 'model'))))
  const fb = !block ? rows.find((r) => over(r) && r.on_exceed === 'fallback') : undefined
  const fbModel = fb && limits.find((l) => l.id === fb.id)?.fallback_model
  const warns = rows.filter((r) => over(r) && r.on_exceed === 'warn')
  const verdict = (r: Json) => !over(r) && !r.would_block ? <Badge tone="ok">여유</Badge>
    : r.on_exceed === 'warn' ? <Badge tone="warn">경고만</Badge> : r.on_exceed === 'fallback' && res?.kind === 'model' ? <Badge tone="model">폴백</Badge> : <Badge tone="danger">차단</Badge>
  const response = !res ? '' : block
    ? `429 Too Many Requests\nRetry-After: ${overBudget(block) ? 3600 : 60}\nMSG-012 ${res.who} — ${block.name} 한도 초과. 잠시 후 다시 시도하세요.`
    : fb ? `200 OK — 저비용 폴백 → ${fbModel ?? '폴백 모델'}\nMSG-015 ${fb.name} 한도 초과로 ${fbModel ?? '폴백 모델'} 이(가) 응답합니다.`
      : `200 OK${warns.length ? `\n경고만 — ${warns.map((w) => w.name).join(', ')} 초과 (알림·감사 기록, 차단 안 함)` : `\n적용 한도 ${rows.length}개 이내`}`
  return (
    <div className="mt-4 grid grid-cols-[360px_1fr] gap-4">
      <Card title="429 미리보기 — 입력">
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <Field label="종류"><Select value={f.kind} onChange={(e) => set('kind', e.target.value)}><option>model</option><option>tool</option><option>agent</option></Select></Field>
            <Field label="대상"><Input className="mono" value={f.target} onChange={(e) => set('target', e.target.value)} placeholder="smart-commercial" /></Field>
            <Field label="주체 sub"><Input value={f.sub} onChange={(e) => set('sub', e.target.value)} placeholder="usr_user" /></Field>
            <Field label="주체 역할(쉼표)"><Input value={f.roles} onChange={(e) => set('roles', e.target.value)} /></Field>
            <Field label="팀"><Input value={f.team} onChange={(e) => set('team', e.target.value)} placeholder="team-biz" /></Field>
            <Field label="에이전트"><Input value={f.agent} onChange={(e) => set('agent', e.target.value)} placeholder="sre-analyst" /></Field>
          </div>
          <Button variant="primary" className="w-full" loading={busy} disabled={!f.target} onClick={run}>429 미리보기</Button>
        </div>
      </Card>
      <Card title="사용자에게 보이는 응답">
        {err ? <ErrorBox error={err} /> : null}
        {res ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">{block ? <Badge tone="danger">429 차단</Badge> : fb ? <Badge tone="model">폴백</Badge> : warns.length ? <Badge tone="warn">경고만</Badge> : <Badge tone="ok">통과</Badge>}
              <span className="text-xs text-fg-muted">rps 한도는 실제 요청이 버킷을 소비할 때 판정됩니다 (미리보기는 버킷을 쓰지 않음).</span></div>
            <CodeBlock value={response} maxH="max-h-32" />
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-fg-muted"><tr><th className="py-1">적용 한도</th><th>초과 시</th><th>RPS</th><th>토큰/분</th><th>예산</th><th>판정</th></tr></thead>
              <tbody>
                {rows.map((r) => <tr key={r.id} className="border-t border-line"><td className="py-1.5">{r.name}</td><td className="text-xs">{ON_EXCEED[r.on_exceed] ?? r.on_exceed}</td><td className="text-xs">{r.rps ?? '—'}</td>
                  <td className="text-xs tabular-nums">{r.tokens_per_min ? `${fmtNum(r.tokens_used ?? 0)} / ${fmtNum(r.tokens_per_min)}` : '—'}</td>
                  <td className="text-xs tabular-nums">{r.budget_usd != null ? `${fmtUsd(r.spend_usd ?? 0)} / ${fmtUsd(r.budget_usd)}` : '—'}</td><td>{verdict(r)}</td></tr>)}
                {!rows.length && <tr><td colSpan={6} className="py-3 text-center text-xs text-fg-muted">이 주체·대상에 적용되는 한도가 없습니다.</td></tr>}
              </tbody>
            </table>
          </div>
        ) : <p className="text-sm text-fg-muted">주체·대상을 넣고 「429 미리보기」를 누르면 적용 한도와 초과 시 사용자에게 보일 응답(MSG-012·Retry-After)을 확인합니다.</p>}
      </Card>
    </div>
  )
}

export function Limits() {
  const toast = useToast()
  const q = useGet(`${P}/limits`)
  const st = useGet(`${P}/limits/status`, undefined, { refetchInterval: 30_000 })
  const [edit, setEdit] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [tab, setTab] = useState<LTab>('all')
  const items: Json[] = q.data?.items ?? []
  const status: Record<string, Json[]> = {}
  for (const s of st.data?.items ?? []) (status[s.limit_id] ??= []).push(s)
  const save = async () => {
    setErr(null)
    const num = (v: unknown) => (v === '' || v == null ? null : Number(v))
    const body: Json = { ...edit, rps: num(edit!.rps), burst: num(edit!.burst), tokens_per_min: num(edit!.tokens_per_min), budget_usd: num(edit!.budget_usd), fallback_model: edit!.fallback_model || null, scope: edit!.scope || '*' }
    delete body.id
    try {
      await send(edit!.id ? 'PUT' : 'POST', edit!.id ? `${P}/limits/${edit!.id}` : `${P}/limits`, body)
      setEdit(null)
      q.refetch()
      st.refetch()
      toast('ok', '저장했습니다.')
    } catch (e) {
      setErr(e)
    }
  }
  const columns = [
    col.accessor('name', { header: '이름', cell: (c) => <span className="font-medium">{c.getValue()}</span> }),
    col.display({ id: 'scope', header: '범위', cell: ({ row }) => <span className="text-xs">{row.original.scope_type}:{row.original.scope}</span> }),
    col.display({ id: 'tgt', header: '대상', cell: ({ row }) => <span className="mono text-xs">{row.original.target_type}:{row.original.target}</span> }),
    col.display({ id: 'rate', header: 'RPS·토큰/분', cell: ({ row }) => <span className="text-xs">{row.original.rps ?? '—'}{row.original.burst ? ` (burst ${row.original.burst})` : ''} · {row.original.tokens_per_min ?? '—'}</span> }),
    col.display({ id: 'budget', header: '예산', cell: ({ row }) => {
      const r = row.original
      if (r.budget_usd == null) return '—'
      const used = status[r.id]?.[0]
      const pct = used?.pct ?? 0
      return <div className="w-40"><div className="flex justify-between text-xs"><span>{fmtUsd(used?.used_usd ?? 0)} / {fmtUsd(r.budget_usd)}</span><span>{pct}%</span></div>
        <div className="mt-0.5 h-1.5 rounded bg-surface-2"><div className={`h-1.5 rounded ${pct >= 100 ? 'bg-danger' : pct >= r.alert_pct ? 'bg-warn' : 'bg-ok'}`} style={{ width: `${Math.min(pct, 100)}%` }} /></div></div>
    } }),
    col.accessor('on_exceed', { header: '초과 시', cell: ({ row }) => row.original.on_exceed === 'fallback' ? <Badge tone="model">폴백 → {row.original.fallback_model}</Badge>
      : row.original.on_exceed === 'warn' ? <Badge tone="warn" title="차단하지 않고 알림·감사 기록">경고만</Badge> : <Badge tone="danger">차단(429)</Badge> }),
    col.accessor('enabled', { header: '사용', cell: (c) => <StatusBadge status={c.getValue() ? 'enabled' : 'disabled'} /> }),
  ]
  return (
    <>
      <PageHeader title="Rate limits·예산 한도" crumbs={['AI 게이트웨이', '한도']} desc={<>토큰 버킷 · 백엔드 <Badge tone={q.data?.backend?.ok ? 'ok' : 'danger'}>{q.data?.backend?.backend ?? '—'}</Badge> · 예산 경보는 알림 센터로</>}
        actions={<Gate perm="limits:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => setEdit({ ...LBLANK })}>+ 한도</Button>}</Gate>} />
      <Tabs tabs={LTABS.map(([id, l]) => ({ id, label: <>{l} <span className="text-xs text-fg-muted">{items.filter((r) => inLTab(r, id)).length}</span></> }))} value={tab} onChange={setTab} />
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={items.filter((r) => inLTab(r, tab))} columns={columns} onRowClick={(r) => setEdit({ ...LBLANK, ...Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v ?? ''])) })} empty="이 대상 유형의 한도가 없습니다." />
      <LimitPreview limits={items} />
      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? `한도 편집 — ${edit.name}` : '새 한도'} wide footer={<><Button onClick={() => setEdit(null)}>취소</Button><Button variant="primary" onClick={save} disabled={!edit?.name}>저장</Button></>}>
        {edit && (
          <div className="grid grid-cols-3 gap-3">
            <Field label="이름"><Input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="범위 유형"><Select value={edit.scope_type} onChange={(e) => setEdit({ ...edit, scope_type: e.target.value })}>{['any', 'user', 'team', 'agent', 'client_key'].map((x) => <option key={x}>{x}</option>)}</Select></Field>
            <Field label="범위 값" hint="* · per-subject · team-biz"><Input value={edit.scope} onChange={(e) => setEdit({ ...edit, scope: e.target.value })} /></Field>
            <Field label="대상 유형"><Select value={edit.target_type} onChange={(e) => setEdit({ ...edit, target_type: e.target.value })}>{['any', 'model', 'tool', 'agent'].map((x) => <option key={x}>{x}</option>)}</Select></Field>
            <Field label="대상"><Input value={edit.target} onChange={(e) => setEdit({ ...edit, target: e.target.value })} /></Field>
            <Field label="RPS"><Input type="number" min={0} step="0.1" value={edit.rps} onChange={(e) => setEdit({ ...edit, rps: e.target.value })} /></Field>
            <Field label="burst"><Input type="number" min={1} value={edit.burst} onChange={(e) => setEdit({ ...edit, burst: e.target.value })} /></Field>
            <Field label="토큰/분"><Input type="number" min={1} value={edit.tokens_per_min} onChange={(e) => setEdit({ ...edit, tokens_per_min: e.target.value })} /></Field>
            <Field label="예산 USD" hint="정수 > 0 · 상위 한도 이하"><Input type="number" min={0} step="0.01" value={edit.budget_usd} onChange={(e) => setEdit({ ...edit, budget_usd: e.target.value })} /></Field>
            <Field label="기간"><Select value={edit.period} onChange={(e) => setEdit({ ...edit, period: e.target.value })}><option value="day">일</option><option value="month">월</option></Select></Field>
            <Field label="경보 %"><Input type="number" min={1} max={100} value={edit.alert_pct} onChange={(e) => setEdit({ ...edit, alert_pct: Number(e.target.value) })} /></Field>
            <Field label="초과 시"><Select value={edit.on_exceed} onChange={(e) => setEdit({ ...edit, on_exceed: e.target.value })}><option value="block">차단</option><option value="fallback">저가 모델 폴백</option><option value="warn">경고만</option></Select></Field>
            {edit.on_exceed === 'warn' && <p className="col-span-3 text-xs text-fg-muted">경고만 — 초과해도 차단하지 않고 알림 센터·감사 로그에 기록합니다.</p>}
            {edit.on_exceed === 'fallback' && <Field label="폴백 모델"><Input value={edit.fallback_model} onChange={(e) => setEdit({ ...edit, fallback_model: e.target.value })} /></Field>}
            {err ? <div className="col-span-3"><ErrorBox error={err} /></div> : null}
          </div>
        )}
      </Modal>
    </>
  )
}

const APPROVER_ROLES = ['admin', 'operator', 'security'] // approvals:decide 보유 역할 — 위임 대상 제안용(최종 검증은 서버)
const URGENT_MS = 180_000 // 기한 ≤3분 임박 강조 (SCR-H12 ②)

function Deadline({ a, now }: { a: Json; now: number }) {
  if (!a.expires_at) return <span className="text-fg-muted">—</span>
  if (a.status !== 'pending') return <span className="text-xs">{fmtTime(a.expires_at)}</span>
  const left = new Date(a.expires_at).getTime() - now
  const urgent = left <= URGENT_MS
  return <span className={cx('text-xs tabular-nums', urgent && 'font-semibold text-danger')} title={fmtTime(a.expires_at, true)}>
    {fmtTime(a.expires_at)} · {left <= 0 ? '기한 지남' : `${Math.ceil(left / 60_000)}분 남음`}{urgent && left > 0 ? ' ⚠' : ''}</span>
}

export function Approvals() {
  const toast = useToast()
  const { me } = useAuth()
  const [status, setStatus] = useState('pending')
  const q = useGet('/api/v1/approvals', { status: status === 'delegated' ? 'pending' : status || undefined, limit: 500 }, { refetchInterval: 10_000 })
  const users = useGet(can(me, 'identity:read') ? '/api/v1/identity/users' : null)
  const [sel, setSel] = useState<Json | null>(null)
  const [note, setNote] = useState('')
  const [to, setTo] = useState('')
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15_000)
    return () => clearInterval(t)
  }, [])
  const userList: Json[] = users.data?.items ?? []
  const uname = (sub?: string | null) => userList.find((u) => u.sub === sub)?.username ?? sub
  const all: Json[] = q.data?.items ?? []
  const items = status === 'delegated' ? all.filter((a) => a.delegated_to) : all
  const urgent = all.filter((a) => a.status === 'pending' && a.expires_at && new Date(a.expires_at).getTime() - now <= URGENT_MS).length
  const close = () => {
    setSel(null)
    setNote('')
    setTo('')
  }
  const decide = async (a: Json, action: 'approve' | 'reject') => {
    if (action === 'reject' && !note.trim()) return toast('error', '거부 사유를 입력하세요 (MSG-003)')
    if (action === 'reject' && !confirm(`${a.target} 요청을 거부할까요? (MSG-002)`)) return
    try {
      await send('POST', `/api/v1/approvals/${a.id}/${action}`, { note: note || undefined })
      toast('ok', `${a.target} ${action === 'approve' ? '승인' : '거부'}`)
      close()
      q.refetch()
    } catch (e: any) {
      toast('error', e.code === 'reason_required' ? '거부 사유를 입력하세요 (MSG-003)' : e.message)
    }
  }
  const delegate = async (a: Json) => {
    const target = to.trim()
    if (!target) return toast('error', '위임할 승인자를 입력하세요')
    if (!note.trim()) return toast('error', '위임 사유를 입력하세요 (MSG-003)')
    const u = userList.find((x) => x.username === target || x.sub === target)
    if (target === a.requester || u?.sub === a.requester) return toast('error', '요청자 본인에게는 위임할 수 없습니다 (직무 분리)')
    try {
      const r = await send<Json>('POST', `/api/v1/approvals/${a.id}/delegate`, { to: target, note: note.trim() })
      toast('ok', `${a.target} → ${uname(r.delegated_to) ?? target} 위임 — 기한은 그대로 유지됩니다`)
      close()
      q.refetch()
    } catch (e: any) {
      toast('error', e.code === 'delegate_forbidden' ? '요청자 본인에게는 위임할 수 없습니다 (직무 분리)' : e.message)
    }
  }
  const columns = [
    col.accessor('created_at', { header: '요청', cell: (c) => fmtAgo(c.getValue()) }),
    col.accessor('kind', { header: '종류', cell: (c) => <Badge tone={c.getValue() === 'tool_call' ? 'muted' : 'a2a'}>{({ tool_call: '도구 실행', sampling: '샘플링 동의', elicitation: '추가 입력', registry: '등록' } as Record<string, string>)[c.getValue()] ?? c.getValue()}</Badge> }),
    col.accessor('target', { header: '대상', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
    col.accessor('risk', { header: '위험도', cell: (c) => <RiskChip risk={c.getValue()} /> }),
    col.accessor('requester', { header: '요청자' }),
    col.accessor('approver', { header: '승인자', cell: (c) => c.getValue() === 'approvers' ? <Badge tone="primary">별도 승인자</Badge> : '요청자' }),
    col.accessor('delegated_to', { header: '위임', cell: (c) => c.getValue() ? <Badge tone="primary" title={c.getValue()}>→ {uname(c.getValue())}</Badge> : '—' }),
    col.accessor('status', { header: '상태', cell: (c) => <StatusBadge status={c.getValue()} /> }),
    col.accessor('expires_at', { header: '만료', cell: ({ row }) => <Deadline a={row.original} now={now} /> }),
    col.accessor('run_id', { header: 'run', cell: (c) => <span className="mono text-[11px]">{c.getValue()?.slice(0, 12) ?? '—'}</span> }),
  ]
  return (
    <>
      <PageHeader title="사람 승인 대기열" crumbs={['AI 게이트웨이', '사람 승인']}
        desc={<>고위험 도구·직무 분리 승인 — 기한 초과 시 자동 거부, 결정·인자 해시는 감사 로그에 기록 (FR-G07){urgent > 0 && <Badge tone="danger" className="ml-2">기한 임박(≤3분) {urgent}건</Badge>}</>}
        actions={<Select aria-label="상태" value={status} onChange={(e) => setStatus(e.target.value)} className="w-36"><option value="pending">대기</option><option value="delegated">위임됨</option><option value="approved">승인됨</option><option value="rejected">거부됨</option><option value="">전체</option></Select>} />
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={items} columns={columns} onRowClick={setSel} empty="대기 중인 승인이 없습니다." />
      <Drawer open={!!sel} onClose={close} title="승인 카드" width="w-[520px]">
        {sel && (
          <div className="space-y-3 text-sm">
            <div className="flex items-center gap-2"><span className="mono font-semibold">{sel.target}</span><RiskChip risk={sel.risk} /><StatusBadge status={sel.status} /></div>
            <Kv rows={[['요청자', sel.requester], ['사유', sel.reason || '—'], ['정책', sel.policy_ref || '—'], ['세션', sel.session_id || '—'], ['run', sel.run_id || '—'], ['만료', <Deadline a={sel} now={now} />],
              ['위임', sel.delegated_to ? `→ ${uname(sel.delegated_to)}` : '—'], ['결정', sel.decided_by ? `${sel.decided_by} · ${sel.decision_note ?? ''}` : '—']]} />
            <div className="text-xs text-fg-muted">인자</div><CodeBlock value={sel.args} maxH="max-h-56" />
            {sel.result?.routing && <Kv rows={[['샘플링 모델(게이트웨이 라우팅)', <span className="mono">{sel.result.routing.model ?? sel.result.routing.error}</span>], ['데이터 등급', sel.result.routing.data_class]]} />}
            {sel.kind === 'elicitation' && sel.status === 'pending' && <p className="rounded-md border border-line px-3 py-2 text-xs text-fg-muted">추가 입력 요청은 요청자가 채팅의 입력 카드에서 내용과 함께 응답합니다 (거부만 여기서 가능).</p>}
            {sel.status === 'pending' && (
              <Gate perm="approvals:decide">{(ok, why) => <>
                <Field label="사유 — 거부·위임 시 필수 (MSG-003)"><Input placeholder="승인 메모는 선택" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
                <div className="flex items-end gap-2">
                  <div className="flex-1"><Field label="위임 대상 승인자"><Input list="approval-delegates" placeholder="사용자명 (요청자 제외)" value={to} disabled={!ok} title={why} onChange={(e) => setTo(e.target.value)} /></Field></div>
                  <datalist id="approval-delegates">
                    {userList.filter((u) => u.status === 'active' && u.username && u.sub !== sel.requester && u.sub !== me?.sub && (u.roles ?? []).some((r: string) => APPROVER_ROLES.includes(r)))
                      .map((u) => <option key={u.sub} value={u.username}>{u.name}</option>)}
                  </datalist>
                  <Button disabled={!ok} title={why ?? '다른 승인자에게 넘깁니다 — 기한은 유지'} onClick={() => delegate(sel)}>위임</Button>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-2">{sel.expires_at && <ExtendTime id={sel.id} extensions={sel.extensions} max={sel.max_extensions} step={sel.extend_step_s}
                  left={Math.max(0, Math.round((new Date(sel.expires_at).getTime() - now) / 1000))} onExtended={(r) => { setSel({ ...sel, ...r }); q.refetch() }} />}<span className="flex-1" />
                  <Button disabled={!ok && sel.approver === 'approvers'} title={why} onClick={() => decide(sel, 'reject')}>거부</Button><Button variant="primary" disabled={(!ok && sel.approver === 'approvers') || sel.kind === 'elicitation'} title={sel.kind === 'elicitation' ? '채팅에서 입력해 응답' : why} onClick={() => decide(sel, 'approve')}>승인</Button></div>
              </>}</Gate>
            )}
          </div>
        )}
      </Drawer>
    </>
  )
}
