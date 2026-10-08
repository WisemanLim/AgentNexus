// SCR-D01 프로파일 목록(필터·행 동작·연결 점검) · D07 그래프 정의(YAML/ADK·동기·게시본 비교·코드만 저장) · D08 테스트 실행(드라이런·버전·노드별·점검·게시) · D09 MCP Host 세션
import { createColumnHelper } from '@tanstack/react-table'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, CodeBlock, Drawer, ErrorBox, Field, Gate, Input, Kpi, Kv, Loading, Modal, PageHeader, Select, StatusBadge, Tabs, TraceLink, TypeBadge, cx, useToast } from '@/components/ui'
import type { Json } from '@/lib/api'
import { fmtAgo, fmtMs, fmtTime, fmtUsd } from '@/lib/format'
import { send, useGet } from '@/lib/query'
import { ProfileTabs } from './AgentGraph'

const col = createColumnHelper<Json>()
const ST_TABS = [['', '전체'], ['published', '게시'], ['draft', '초안'], ['archived', '보관함']] as const

export function Agents() {
  const nav = useNavigate()
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const status = sp.get('status') || ''
  const q = useGet('/api/v1/agents', { status: status || undefined, team: sp.get('team') || undefined, q: sp.get('q') || undefined })
  const all = useGet('/api/v1/agents')
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [tpl, setTpl] = useState('tpl-qa')
  const [health, setHealth] = useState<Json | null>(null)
  const items: Json[] = q.data?.items ?? []
  const teams = [...new Set((all.data?.items ?? []).map((p: Json) => p.team).filter(Boolean))] as string[]
  const setF = (k: string, v: string) => { const p = new URLSearchParams(sp); if (v) p.set(k, v); else p.delete(k); setSp(p, { replace: true }) }
  const act = async (fn: () => Promise<unknown>, msg: string) => {
    try {
      await fn()
      toast('ok', msg)
      q.refetch()
      all.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const create = async () => {
    try {
      const r = await send<Json>('POST', '/api/v1/agents', { name, from_template: tpl })
      nav(`/console/agents/${r.id}/graph`)
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const check = async (p: Json) => setHealth({ p, ...(await send<Json>('GET', `/api/v1/agents/${p.id}/health`)) })
  const columns = [
    col.accessor('name', { header: '이름', cell: (c) => <span className="font-medium">{c.getValue()}</span> }),
    col.accessor('id', { header: 'ID', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
    col.accessor('template', { header: '유형', cell: (c) => c.getValue() ? <Badge tone="profile">템플릿</Badge> : <TypeBadge type="profile" /> }),
    col.accessor('status', { header: '상태', cell: (c) => <StatusBadge status={c.getValue()} /> }),
    col.accessor('version', { header: '버전', cell: (c) => `v${c.getValue()}` }),
    col.accessor('team', { header: '팀', cell: (c) => <span className="text-xs">{c.getValue() || '—'}</span> }),
    col.accessor('nodes', { header: '노드', enableSorting: false, cell: (c) => <span className="text-xs">{(c.getValue() ?? []).join(' → ')}</span> }),
    col.display({ id: 'conn', header: '연결', cell: ({ row }) => <span className="text-xs text-fg-muted">MCP {row.original.tools?.length ?? 0} · A2A {row.original.remote_agents?.length ?? 0}</span> }),
    col.accessor('a2a_exposed', { header: 'A2A 노출', cell: (c) => c.getValue() ? <Badge tone="a2a">노출</Badge> : '—' }),
    col.accessor('updated_at', { header: '갱신', cell: (c) => fmtAgo(c.getValue()) }),
    col.display({ id: 'act', header: '', cell: ({ row }) => { const p = row.original; return (
      <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
        <Button size="sm" onClick={() => check(p).catch((e) => toast('error', e.message))}>연결 점검</Button>
        <Gate perm="agents:write">{(ok, why) => p.status === 'archived'
          ? <Button size="sm" disabled={!ok} title={why} onClick={() => act(() => send('POST', `/api/v1/agents/${p.id}/restore`), '복원했습니다 (초안)')}>복원</Button>
          : <>
            <Button size="sm" disabled={!ok} title={why} onClick={() => { const n = window.prompt('복제본 이름', `${p.name} 복제`); if (n) act(() => send('POST', `/api/v1/agents/${p.id}/clone`, { name: n }), '복제했습니다') }}>복제</Button>
            {!p.template && <Button size="sm" disabled={!ok} title={why} onClick={() => confirm(`"${p.name}" 을 보관할까요? 채팅에서 빠지고 A2A 노출도 멈춥니다 (MSG-002).`) && act(() => send('DELETE', `/api/v1/agents/${p.id}`), '보관했습니다')}>보관</Button>}
          </>}</Gate>
      </div>) } }),
  ]
  return (
    <>
      <PageHeader title="에이전트 프로파일" crumbs={['에이전트']} desc="Planner → Retriever·Summarize·Executor·원격 A2A 로 구성된 오케스트레이션 그래프. 게시하면 채팅에 노출됩니다."
        actions={<Gate perm="agents:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => setOpen(true)}>+ 템플릿에서 만들기</Button>}</Gate>} />
      <Tabs tabs={ST_TABS.map(([id, l]) => ({ id, label: l }))} value={status} onChange={(v) => setF('status', v)} />
      <div className="mb-3 flex flex-wrap gap-2">
        <Input aria-label="프로파일 검색" className="w-64" placeholder="이름·ID 검색" defaultValue={sp.get('q') || ''} onKeyDown={(e) => e.key === 'Enter' && setF('q', (e.target as HTMLInputElement).value)} />
        <Select aria-label="팀" className="w-44" value={sp.get('team') || ''} onChange={(e) => setF('team', e.target.value)}><option value="">팀: 전체</option>{teams.map((t) => <option key={t}>{t}</option>)}</Select>
      </div>
      {q.error && <ErrorBox error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading ? <Loading /> : <DataTable data={items} columns={columns} onRowClick={(r) => r.status !== 'archived' && nav(`/console/agents/${r.id}/graph`)} empty={status === 'archived' ? '보관한 프로파일이 없습니다.' : '프로파일이 없습니다 — 템플릿에서 만들기'} />}
      <Modal open={open} onClose={() => setOpen(false)} title="새 프로파일 (템플릿 복제)" footer={<><Button onClick={() => setOpen(false)}>취소</Button><Button variant="primary" disabled={name.trim().length < 2} onClick={create}>만들기</Button></>}>
        <div className="space-y-3">
          <Field label="이름"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="결제팀 장애 분석" autoFocus /></Field>
          <Field label="템플릿"><Select value={tpl} onChange={(e) => setTpl(e.target.value)}>{(all.data?.items ?? []).filter((i: Json) => i.template).map((i: Json) => <option key={i.id} value={i.id}>{i.name}</option>)}</Select></Field>
        </div>
      </Modal>
      <Drawer open={!!health} onClose={() => setHealth(null)} title={`연결 점검 — ${health?.p?.name ?? ''}`}>
        {health && <><p className={cx('mb-2 text-sm', health.ok ? 'text-ok' : 'text-danger')}>{health.ok ? '✓ 모든 연결 정상' : '일부 연결에 문제가 있습니다 — 실행 시 해당 단계가 실패합니다.'}</p>
          <ul className="space-y-1 text-sm">{health.items.map((i: Json) => <li key={`${i.kind}:${i.id}`} className={i.ok ? '' : 'text-danger'}>{i.ok ? '✓' : '✗'} <TypeBadge type={i.kind === 'a2a' ? 'a2a' : i.kind === 'model' ? 'model' : 'mcp'} /> <span className="mono">{i.id}</span> <span className="text-xs text-fg-muted">{i.detail}</span></li>)}</ul></>}
      </Drawer>
    </>
  )
}

export function AgentCode() {
  const { id = '' } = useParams()
  const toast = useToast()
  const prof = useGet(`/api/v1/agents/${id}`)
  const [fmt, setFmt] = useState<'yaml' | 'adk'>('yaml')
  const adk = useGet(fmt === 'adk' ? `/api/v1/agents/${id}/code` : null, { format: 'adk' })
  const [text, setText] = useState('')
  const [check, setCheck] = useState<Json | null>(null)
  const [diff, setDiff] = useState<Json | null>(null)
  const [busy, setBusy] = useState(false)
  const area = useRef<HTMLTextAreaElement>(null)
  const loaded = useRef(false)
  useEffect(() => { if (prof.data && !loaded.current) { setText(prof.data.draft_code ?? prof.data.yaml); loaded.current = true } }, [prof.data])
  useEffect(() => {  // 입력 0.6초 뒤 검증 — 오류 줄·경고·그래프 미리보기
    if (!loaded.current || fmt !== 'yaml') return
    const t = setTimeout(() => { send<Json>('POST', `/api/v1/agents/${id}/validate`, { yaml: text }).then(setCheck).catch(() => {}) }, 600)
    return () => clearTimeout(t)
  }, [text, id, fmt])
  if (prof.error) return <ErrorBox error={prof.error} />
  if (!prof.data) return <Loading />
  const p = prof.data
  const errors: Json[] = check?.errors ?? []
  const warnings: Json[] = check?.warnings ?? []
  const lines = text.split('\n')
  const bad = new Set(errors.map((e) => e.line).filter(Boolean))
  const warn = new Set(warnings.map((e) => e.line).filter(Boolean))
  const jump = (line?: number) => {
    if (!line || !area.current) return
    const pos = lines.slice(0, line - 1).join('\n').length + (line > 1 ? 1 : 0)
    area.current.focus()
    area.current.setSelectionRange(pos, pos + (lines[line - 1]?.length ?? 0))
  }
  const save = async () => {
    setBusy(true)
    try {
      const codeOnly = errors.length > 0
      if (codeOnly && !confirm(`검증 오류 ${errors.length}건 — 그래프에는 반영하지 않고 코드만 저장합니다 (MSG-001). 게시는 오류 0 일 때만 가능합니다.`)) return
      const r = await send<Json>('PUT', `/api/v1/agents/${id}/graph`, { yaml: text, allow_invalid: codeOnly })
      toast(codeOnly ? 'info' : 'ok', codeOnly ? '코드만 저장했습니다 (그래프 반영 보류)' : '저장했습니다 — 그래프(D02)에 반영됨')
      if (!codeOnly) setText(r.yaml)
      prof.refetch()
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  const revert = async () => {
    if (!confirm('코드만 저장된 변경을 버리고 저장된 그래프로 되돌릴까요? (MSG-002)')) return
    const r = await send<Json>('POST', `/api/v1/agents/${id}/code/revert`)
    setText(r.yaml)
    prof.refetch()
  }
  const sync = p.sync && text === p.yaml
  return (
    <>
      <PageHeader title={p.name} crumbs={['에이전트', id, '코드']} actions={<>
        <Select aria-label="정의 형식" className="w-44" value={fmt} onChange={(e) => setFmt(e.target.value as 'yaml' | 'adk')}><option value="yaml">LangGraph YAML</option><option value="adk">ADK (Python) 보기</option></Select>
        <Badge tone={sync ? 'ok' : 'warn'}>{sync ? '그래프 ↔ 코드 동기 ✓' : p.draft_code != null ? '코드만 저장됨 (그래프 반영 보류)' : '저장 안 된 변경'}</Badge>
        {p.draft_code != null && <Button size="sm" onClick={revert}>되돌리기</Button>}
        <Button size="sm" onClick={() => send<Json>('GET', `/api/v1/agents/${id}/diff`).then(setDiff).catch((e) => toast('error', e.message))}>게시본 비교</Button>
        <Button size="sm" variant="primary" disabled={p.template || fmt !== 'yaml'} title={p.template ? '템플릿은 복제 후 편집' : fmt !== 'yaml' ? 'ADK 보기는 읽기 전용' : undefined} onClick={save} loading={busy}>저장</Button></>} />
      <ProfileTabs id={id} active="code" />
      <div className="grid grid-cols-[1fr_340px] gap-4">
        {fmt === 'yaml' ? (
          <div className="flex overflow-hidden rounded-lg border border-line bg-[#111827] text-[#e5e7eb]">
            <pre aria-hidden className="mono select-none border-r border-white/10 px-2 py-2 text-right text-[#9ca3af]">{lines.map((_, i) => <div key={i} className={bad.has(i + 1) ? 'bg-danger/40 text-white' : warn.has(i + 1) ? 'bg-warn/30 text-white' : ''}>{i + 1}</div>)}</pre>
            <textarea ref={area} aria-label="그래프 YAML" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} readOnly={p.template}
              className="mono h-[560px] flex-1 resize-none bg-transparent p-2 leading-[19.5px] outline-none" style={{ fontSize: 13 }} />
          </div>
        ) : (
          <div className="space-y-2">
            {(adk.data?.warnings ?? []).map((w: string) => <p key={w} className="rounded-md border border-warn/40 bg-warn/10 px-3 py-1.5 text-xs">⚠ {w}</p>)}
            {adk.isLoading ? <Loading /> : <CodeBlock value={adk.data?.code ?? ''} maxH="max-h-[560px]" />}
            <p className="text-xs text-fg-muted">ADK 보기는 같은 그래프를 Google ADK 에이전트 코드로 변환한 읽기 전용 미리보기입니다. 편집은 YAML 에서 합니다.</p>
          </div>
        )}
        <div className="space-y-3">
          <Card title={`검증 — ${errors.length ? `오류 ${errors.length}` : '스키마 ✓'} · 경고 ${warnings.length}`}>
            {!errors.length && !warnings.length ? <p className="text-sm text-fg-muted">스키마·미정의 노드·미허용 도구(MSG-006)·노드 설정을 검사합니다.</p> : (
              <ul className="space-y-1 text-sm">
                {errors.map((e, i) => <li key={`e${i}`}><button type="button" className="text-left text-danger hover:underline" onClick={() => jump(e.line)}>✗ {e.line ? `${e.line}행 · ` : ''}<span className="mono">{e.path}</span> — {e.message}</button></li>)}
                {warnings.map((e, i) => <li key={`w${i}`}><button type="button" className="text-left text-[#a06d00] hover:underline dark:text-warn" onClick={() => jump(e.line)}>! {e.line ? `${e.line}행 · ` : ''}{e.message}</button></li>)}
              </ul>
            )}
            {errors.length > 0 && <p className="mt-2 text-xs text-fg-muted">오류가 있으면 코드만 저장(draft)하고 그래프 반영은 보류합니다. 게시는 오류 0 일 때만.</p>}
          </Card>
          <Card title="그래프 미리보기">
            {check?.canvas ? (
              <ol className="space-y-1 text-xs">{check.canvas.nodes.map((n: Json) => {
                const deps = check.canvas.edges.filter((e: Json) => e.to === n.id).map((e: Json) => e.from)
                return <li key={n.id} className={cx('rounded border px-2 py-1', n.error ? 'border-danger bg-danger/5 text-danger' : 'border-line')}>
                  <span className="font-medium">{n.id}</span> <span className="text-fg-muted">{n.type} · {n.label}</span>{deps.length > 0 && <span className="text-fg-muted"> ← {deps.join(', ')}</span>}
                  {n.join && <Badge className="ml-1">⋈ {n.join}</Badge>}{n.when && <Badge tone="warn" className="ml-1">조건</Badge>}{n.error && <Badge tone="danger" className="ml-1">오류 · 반영 보류</Badge>}</li>
              })}</ol>
            ) : <p className="text-xs text-fg-muted">YAML 구문 오류가 있으면 미리보기를 그릴 수 없습니다.</p>}
          </Card>
        </div>
      </div>
      <Drawer open={!!diff} onClose={() => setDiff(null)} title={`게시본 v${diff?.base_version ?? '—'} 대비 변경`} width="w-[520px]">
        {diff && (diff.changes.length ? <ul className="space-y-1 text-sm">{diff.changes.map((c: Json, i: number) => (
          <li key={i} className="flex gap-2"><span className={cx('mono w-4 font-bold', c.op === '+' ? 'text-ok' : c.op === '-' ? 'text-danger' : 'text-warn')}>{c.op}</span>
            <span className="mono text-xs">{c.path}</span><span className="text-xs text-fg-muted">{c.value ?? `${JSON.stringify(c.from)} → ${JSON.stringify(c.to)}`}</span></li>))}</ul>
          : <p className="text-sm text-fg-muted">게시본과 차이가 없습니다.</p>)}
      </Drawer>
    </>
  )
}

export function AgentTest() {
  const { id = '' } = useParams()
  const toast = useToast()
  const prof = useGet(`/api/v1/agents/${id}`)
  const versions = useGet(`/api/v1/agents/${id}/versions`)
  const history = useGet(`/api/v1/agents/${id}/test-runs`)
  const checks = useGet(`/api/v1/agents/${id}/checks`)
  const [req, setReq] = useState('')
  const [dc, setDc] = useState('')
  const [ver, setVer] = useState('')
  const [dry, setDry] = useState(true)
  const [res, setRes] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [node, setNode] = useState<Json | null>(null)
  const [cmp, setCmp] = useState('')
  const run = async () => {
    setBusy(true)
    setErr(null)
    try {
      setRes(await send<Json>('POST', `/api/v1/agents/${id}/test`, { request: req, data_class: dc || undefined, timeout_s: 90, dry_run: dry, version: ver ? Number(ver) : undefined }))
      history.refetch()
      checks.refetch()
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }
  const publish = async () => {
    try {
      const r = await send<Json>('POST', `/api/v1/agents/${id}/publish`, { note: '테스트 통과 후 게시' })
      toast('ok', `v${r.version} 게시 — 60초 안에 채팅에 노출됩니다`)
      prof.refetch()
      checks.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const r = res?.run
  const meta: Json = r?.meta ?? {}
  const steps: Record<string, Json> = Object.fromEntries((r?.steps ?? []).map((s: Json) => [s.name, s]))
  const prev = (history.data?.items ?? []).find((h: Json) => h.id === cmp)
  const prevNodes: Record<string, Json> = Object.fromEntries((prev?.nodes ?? []).map((n: Json) => [n.step, n]))
  return (
    <>
      <PageHeader title={prof.data?.name ?? id} crumbs={['에이전트', id, '테스트']} />
      <ProfileTabs id={id} active="test" />
      <div className="grid grid-cols-[1fr_320px] gap-4">
        <div className="space-y-4">
          <Card title="테스트 입력 — 실행은 내 권한(OBO)으로만">
            <div className="flex flex-wrap gap-2">
              <Input aria-label="테스트 요청" className="min-w-[280px] flex-1" value={req} onChange={(e) => setReq(e.target.value)} placeholder="예: orders-api 장애 원인 분석" onKeyDown={(e) => e.key === 'Enter' && req && run()} />
              <Select aria-label="버전" value={ver} onChange={(e) => setVer(e.target.value)} className="w-36"><option value="">현재 초안</option>{(versions.data?.items ?? []).map((v: Json) => <option key={v.version} value={v.version}>v{v.version} 게시본</option>)}</Select>
              <Select aria-label="데이터 등급" value={dc} onChange={(e) => setDc(e.target.value)} className="w-32"><option value="">기본 등급</option><option>public</option><option>internal</option><option>confidential</option></Select>
              <label className="flex items-center gap-1 text-sm" title="쓰기 도구는 호출하지 않고 인자만 기록합니다"><input type="checkbox" checked={dry} onChange={(e) => setDry(e.target.checked)} />쓰기 도구 드라이런</label>
              <Button variant="primary" onClick={run} disabled={!req.trim()} loading={busy}>실행</Button>
            </div>
          </Card>
          {err ? <ErrorBox error={err} /> : null}
          {r && (
            <>
              <Card title={<span className="flex flex-wrap items-center gap-2">실행 <StatusBadge status={r.status} />{r.dry_run && <Badge tone="warn">드라이런</Badge>}<span className="text-xs font-normal text-fg-muted">v{r.profile_version} · {fmtMs(meta.latency_ms)} · {fmtUsd(meta.cost)}{meta.first_token_ms != null ? ` · 첫 토큰 ${fmtMs(meta.first_token_ms)}` : ''}</span></span>} actions={<TraceLink id={r.trace_id} />}>
                <p className="text-sm whitespace-pre-wrap">{r.result || r.error || '(결과 없음)'}</p>
                {r.citations?.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{r.citations.map((c: Json) => <Badge key={c.n} tone="primary">[{c.n}] {c.title}</Badge>)}</div>}
                <p className="mt-2 text-xs text-fg-muted">인용 {meta.sources?.cited ?? 0} · 미사용 근거 {meta.sources?.unused ?? 0} · 응답 소독 {meta.sanitized ?? 0}건 · {meta.local_only ? '로컬 모델만' : `모델 ${(meta.models ?? []).join(', ') || '—'}`} (MSG-025)</p>
                {(meta.dry_run_calls ?? []).map((d: Json) => <p key={d.step} className="mt-1 rounded bg-warn/10 px-2 py-1 text-xs">[드라이런] {d.tool} → 실행하지 않음 (승인 필요 도구) · 인자 기록됨</p>)}
              </Card>
              <Card title="노드별 결과" bodyClass="p-0" actions={<Select aria-label="이전 실행과 비교" className="h-7 w-56 text-xs" value={cmp} onChange={(e) => setCmp(e.target.value)}>
                <option value="">이전 실행과 비교…</option>{(history.data?.items ?? []).filter((h: Json) => h.id !== res?.run_id).map((h: Json) => <option key={h.id} value={h.id}>{fmtTime(h.created_at)} · v{h.version} · {h.status}</option>)}</Select>}>
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-left text-xs text-fg-muted"><tr><th className="px-3 py-2">노드</th><th>지연</th><th>비용</th><th>상태</th>{prev && <th>이전</th>}</tr></thead>
                  <tbody>{(meta.nodes ?? []).map((n: Json) => (
                    <tr key={n.step} className={cx('cursor-pointer border-t border-line hover:bg-surface-2', n.status === 'failed' && 'bg-danger/5')} onClick={() => setNode(steps[n.step])}>
                      <td className="px-3 py-1.5">{n.step} · {n.node} <span className="mono text-xs text-fg-muted">{n.tool || n.agent || ''}</span>{n.models?.length ? <span className="text-xs text-fg-muted"> ({n.models.join(', ')})</span> : null}</td>
                      <td>{fmtMs(n.latency_ms)}</td><td>{fmtUsd(n.cost)}</td><td>{n.dry_run ? <Badge tone="warn">드라이런</Badge> : <StatusBadge status={n.status} />}{n.attempts > 1 && <span className="ml-1 text-xs text-warn">시도 {n.attempts}</span>}</td>
                      {prev && <td className="text-xs">{prevNodes[n.step] ? <>{fmtMs(prevNodes[n.step].latency_ms)} · <StatusBadge status={prevNodes[n.step].status} /></> : '—'}</td>}
                    </tr>))}</tbody>
                </table>
              </Card>
            </>
          )}
        </div>
        <div className="space-y-4">
          <Card title="게시 전 점검">
            {checks.isLoading ? <Loading /> : <ul className="space-y-1 text-sm">{(checks.data?.items ?? []).map((c: Json) => <li key={c.key} className={c.ok ? 'text-ok' : 'text-danger'}>{c.ok ? '✓' : '✗'} <span className="text-fg">{c.detail}</span></li>)}</ul>}
            <Gate perm="agents:write">{(ok, why) => <Button className="mt-3 w-full" variant="primary" disabled={!ok || !checks.data?.publishable} title={!checks.data?.publishable ? '점검을 모두 통과해야 게시할 수 있습니다' : why} onClick={publish}>v{(checks.data?.version ?? 0) + 1} 게시</Button>}</Gate>
            <p className="mt-2 text-xs text-fg-muted">게시 후 60초 안에 채팅·카탈로그에 노출됩니다.</p>
          </Card>
          <Card title="이전 실행">
            <ul className="space-y-1 text-xs">{(history.data?.items ?? []).slice(0, 8).map((h: Json) => <li key={h.id} className="flex items-center gap-2"><StatusBadge status={h.status} /><span>v{h.version}</span>{h.dry_run && <Badge tone="warn">드라이런</Badge>}<span className="text-fg-muted">{fmtMs(h.latency_ms)} · {fmtAgo(h.created_at)}</span></li>)}
              {!history.data?.items?.length && <li className="text-fg-muted">아직 없습니다.</li>}</ul>
          </Card>
        </div>
      </div>
      <Drawer open={!!node} onClose={() => setNode(null)} title={`노드 입출력 — ${node?.name ?? ''}`} width="w-[560px]">
        {node && <div className="space-y-3 text-sm">
          <Kv rows={[['노드', node.node], ['도구·에이전트', <span className="mono text-xs">{node.tool || node.agent || '—'}</span>], ['상태', <StatusBadge status={node.status} />], ['시도', node.attempts], ['오류', node.error || '—']]} />
          <div><div className="mb-1 text-xs font-medium text-fg-muted">입력 (PII 마스킹 · 시크릿 가림, MSG-021)</div><CodeBlock value={node.args ?? {}} maxH="max-h-48" /></div>
          <div><div className="mb-1 text-xs font-medium text-fg-muted">출력</div><CodeBlock value={node.output ?? {}} maxH="max-h-80" /></div>
        </div>}
      </Drawer>
    </>
  )
}

export function HostSessions() {
  const toast = useToast()
  const [state, setState] = useState('')
  const q = useGet('/api/v1/host/sessions', { state: state || undefined }, { refetchInterval: 15_000 })
  const [sel, setSel] = useState<Json | null>(null)
  const k: Json = q.data?.kpi ?? {}
  const act = async (fn: () => Promise<unknown>, msg: string) => {
    try {
      await fn()
      toast('ok', msg)
      setSel(null)
      q.refetch()
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const columns = [
    col.accessor('short', { header: '세션', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }),
    col.accessor('server', { header: 'MCP 서버', cell: (c) => <span className="mono">{c.getValue() === '_all' ? '집계(_all)' : `mcp/${c.getValue()}`}</span> }),
    col.accessor('transport', { header: '전송' }),
    col.accessor('tools', { header: '도구', cell: (c) => c.getValue() ?? '—' }),
    col.accessor('state', { header: '상태', cell: ({ row }) => row.original.state === 'reconnecting' ? <Badge tone="warn">↻ 재연결 {row.original.reconnects}/5</Badge> : <StatusBadge status={row.original.state === 'open' ? 'active' : 'closed'} /> }),
    col.accessor('principal', { header: '주체' }),
    col.accessor('profile_id', { header: '프로파일', cell: ({ row }) => <>{row.original.profile_id ?? '—'}{row.original.test && <Badge className="ml-1">테스트</Badge>}</> }),
    col.accessor('created_at', { header: '시작', cell: (c) => fmtTime(c.getValue(), true) }),
  ]
  return (
    <>
      <PageHeader title="MCP Host 세션·클라이언트" crumbs={['에이전트', 'Host 세션']} desc="플랫폼 Host 가 게이트웨이 경유로 맺은 MCP 클라이언트·업스트림 세션 — 원격 MCP 는 게이트웨이 경유가 강제됩니다." />
      {q.error && <ErrorBox error={q.error} />}
      <div className="mb-4 grid grid-cols-4 gap-3">
        <Kpi label="활성 세션" value={k.active ?? 0} sub={`테스트 ${k.tests ?? 0}`} /><Kpi label="연결 서버" value={k.servers ?? 0} />
        <Kpi label="재연결 중" value={k.reconnecting ?? 0} tone={k.reconnecting ? 'warn' : undefined} /><Kpi label="동의 대기" value={k.consents ?? 0} tone={k.consents ? 'warn' : undefined} />
      </div>
      <div className="mb-3 flex gap-2"><Select aria-label="상태" className="w-40" value={state} onChange={(e) => setState(e.target.value)}><option value="">상태: 전체</option><option value="open">연결</option><option value="reconnecting">재연결 중</option><option value="closed">종료</option></Select></div>
      <DataTable data={q.data?.items ?? []} columns={columns} onRowClick={setSel} empty="세션이 없습니다." />
      {(q.data?.pending_consents?.length ?? 0) > 0 && (
        <Card title="동의 대기 (SCR-C08)" className="mt-4">
          <ul className="space-y-1 text-sm">{q.data!.pending_consents.map((c: Json) => <li key={c.id} className="flex items-center gap-2"><Badge tone="warn">{c.kind}</Badge><span className="mono">{c.target ?? '—'}</span><span className="text-xs text-fg-muted">{fmtAgo(c.created_at)}</span></li>)}</ul>
        </Card>
      )}
      <Drawer open={!!sel} onClose={() => setSel(null)} title={`세션 ${sel?.short ?? ''}`} width="w-[480px]">
        {sel && <div className="space-y-3 text-sm">
          <Kv rows={[['프로토콜', `MCP ${sel.protocol_version ?? '—'}`], ['전송', sel.transport], ['경유', <span className="mono text-xs">gw…/mcp/{sel.server}</span>], ['사용자', sel.principal],
            ['프로파일', sel.profile_id ?? '—'], ['실행', sel.run_id ? <span className="mono text-xs">{sel.run_id}</span> : '—'], ['재연결', `${sel.reconnects}회`], ['마지막 사용', fmtAgo(sel.last_used_at)]]} />
          <div><div className="mb-1 text-xs font-medium text-fg-muted">capabilities (클라이언트 선언)</div>
            <div className="flex flex-wrap gap-1">{Object.keys(sel.capabilities ?? {}).length ? Object.keys(sel.capabilities).map((c) => <Badge key={c}>{c} ✓</Badge>) : <span className="text-xs text-fg-muted">선언 없음 — 차단 도구는 에이전트에 노출되지 않습니다</span>}</div></div>
          <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-xs">동의 경계: 샘플링은 사용자 동의(C08)·roots 노출 없음·elicitation 은 확인 후 응답.</p>
          <div><div className="mb-1 text-xs font-medium text-fg-muted">최근 이벤트</div>
            <ol className="space-y-0.5 text-xs">{(sel.events ?? []).map((e: Json, i: number) => <li key={i}>{fmtTime(e.at, true)} · <Badge>{e.type}</Badge> {e.by ? `· ${e.by}` : ''}</li>)}{!sel.events?.length && <li className="text-fg-muted">없음</li>}</ol></div>
          {sel.state !== 'closed' && <Gate perm="mcp:write">{(ok, why) => <div className="flex gap-2">
            <Button disabled={!ok} title={why} onClick={() => act(() => send('POST', `/api/v1/host/sessions/${sel.id}/reconnect`), '재연결 — 다음 호출에서 세션을 다시 맺습니다')}>재연결</Button>
            <Button variant="danger" disabled={!ok} title={why} onClick={() => confirm('세션을 종료할까요? 진행 중 호출 이후 요청은 거부됩니다 (MSG-002).') && act(() => send('DELETE', `/api/v1/host/sessions/${sel.id}`), '세션을 종료했습니다')}>세션 종료</Button></div>}</Gate>}
        </div>}
      </Drawer>
    </>
  )
}
