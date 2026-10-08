// SCR-E04 MCP 서버 메타데이터 등록(server.json 4단계) · E06 레지스트리 소스·미러 · E08 SBOM·라이선스 리포트
import { createColumnHelper } from '@tanstack/react-table'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, Drawer, ErrorBox, Field, Gate, Input, Kpi, Kv, Modal, PageHeader, RiskChip, Select, StatusBadge, Stepper, Textarea, useToast } from '@/components/ui'
import type { Json } from '@/lib/api'
import { fmtAgo, fmtTime } from '@/lib/format'
import { send, useGet } from '@/lib/query'

const col = createColumnHelper<Json>()
const SAMPLE = `{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.agentnexus.custom/orders-tool",
  "title": "Orders Tool",
  "description": "주문 조회 도구",
  "version": "0.1.0",
  "remotes": [{ "type": "streamable-http", "url": "http://orders-tool:8080/orders-tool/mcp" }],
  "_meta": { "auth": { "type": "oauth2", "tool_scopes": { "lookup": "orders:read" } }, "tools": ["lookup"], "license": "MIT" }
}`

export function Publish() {
  const nav = useNavigate()
  const [step, setStep] = useState(0)
  const [text, setText] = useState(SAMPLE)
  const [check, setCheck] = useState<Json | null>(null)
  const [res, setRes] = useState<Json | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [url, setUrl] = useState('')
  const [saved, setSaved] = useState<string | null>(null)
  const [restored, setRestored] = useState(false)
  const draft = useGet('/api/v1/registry/draft')
  const doc = useMemo(() => { try { return JSON.parse(text) } catch { return null } }, [text])
  useEffect(() => {  // 임시 저장본 복원 (E04 ⑧) — 처음 한 번
    if (!restored && draft.data?.text && text === SAMPLE) {  // 사용자가 이미 입력했으면 덮지 않는다
      setText(draft.data.text)
      setSaved(draft.data.saved_at)
    }
    if (draft.data) setRestored(true)
  }, [draft.data, restored])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {  // 편집 1.5초 뒤 자동 임시 저장
    if (!restored || text === SAMPLE) return
    const t = setTimeout(() => { send<Json>('PUT', '/api/v1/registry/draft', { text }).then((r) => setSaved(r.saved_at)).catch(() => {}) }, 1500)
    return () => clearTimeout(t)
  }, [text, restored])
  const edit = (v: string) => { setText(v); setStep(0); setCheck(null) }
  const fetchUrl = async () => {
    setErr(null)
    try {
      edit((await send<Json>('POST', '/api/v1/registry/fetch', { url })).text)
    } catch (e) {
      setErr(e)
    }
  }
  const validate = async () => {
    setErr(null)
    try {
      const r = await send<Json>('POST', '/api/v1/registry/validate', { text })
      setCheck(r)
      if (r.valid) setStep(2)
      else setStep(1)
    } catch (e) {
      setErr(e)
    }
  }
  const publish = async () => {
    try {
      setRes(await send<Json>('POST', '/v0/publish', doc))
      setStep(3)
      send('PUT', '/api/v1/registry/draft', { text: '' }).catch(() => {})
    } catch (e) {
      setErr(e)
    }
  }
  const sig = check?.signature
  return (
    <>
      <PageHeader title="MCP 서버 메타데이터 등록 (server.json)" crumbs={['카탈로그', '등록']} desc="스키마·서명 검증 후 운영자 승인 전까지는 발견되지 않습니다 (FR-C02, MSG-008)." />
      <Card>
        <Stepper steps={['server.json', '검증', '확인', '승인 요청']} current={step} />
        {step <= 1 && (
          <>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <label className="cursor-pointer rounded border border-line px-2 py-1 text-xs hover:bg-surface-2">파일 열기
                <input type="file" accept="application/json" className="sr-only" onChange={async (e) => { const f = e.target.files?.[0]; if (f) edit(await f.text()) }} />
              </label>
              <Input aria-label="가져올 URL" className="mono w-80 text-xs" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…/server.json" />
              <Button size="sm" disabled={!/^https?:\/\//.test(url)} onClick={fetchUrl}>URL 에서 가져오기</Button>
              <span className="ml-auto text-xs text-fg-muted">{saved ? `임시 저장 ${fmtAgo(saved)}` : '편집하면 자동으로 임시 저장됩니다'}</span>
            </div>
            <Textarea aria-label="server.json" className="mono h-72 text-xs" value={text} onChange={(e) => edit(e.target.value)} />
            {check && !check.valid && (
              <div role="alert" className="mt-2 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">server.json 검증 실패 (MSG-007):
                <ul className="list-disc pl-5">{(check.inline?.length ? check.inline : check.errors.map((m: string) => ({ message: m }))).map((e: Json, n: number) => <li key={n}>{e.line ? <span className="mono">{e.line}행 </span> : null}{e.message}</li>)}</ul></div>
            )}
          </>
        )}
        {step === 2 && check && (
          <div className="space-y-4">
            <Kv rows={[['이름', <span className="mono">{check.name}</span>], ['버전', check.version], ['인증', check.auth ?? 'none'], ['원격', <span className="mono text-xs">{check.remotes.join(', ') || '—'}</span>],
              ['서명', sig?.present ? (sig.valid ? <Badge tone="ok">검증됨 · {sig.kid}</Badge> : <Badge tone="danger">서명 불일치</Badge>) : <Badge tone={sig?.required ? 'danger' : 'warn'}>{sig?.required ? '서명 필요 (정책)' : '서명 없음 — 선택'}</Badge>],
              ['다음', '게시하면 승인 대기열(SCR-E05)에 들어갑니다 — 승인자가 도구 위험도·신뢰 등급을 확정합니다.']]} />
            <div className="grid grid-cols-2 gap-4">
              <div><h3 className="mb-1 text-sm font-semibold">도구 위험도 (자동 분류)</h3>
                <table className="w-full text-sm"><tbody>{(check.risks ?? []).map((r: Json) => <tr key={r.tool} className="border-t border-line"><td className="mono py-1 text-xs">{r.tool}</td><td><RiskChip risk={r.risk} /></td></tr>)}</tbody></table>
                {!check.risks?.length && <p className="text-xs text-fg-muted">도구 목록 없음 — 등록 후 tools/list 로 채워집니다.</p>}</div>
              <div><h3 className="mb-1 text-sm font-semibold">요청 스코프</h3>
                <div className="flex flex-wrap gap-1">{(check.scopes ?? []).map((s: Json) => <Badge key={s.scope} tone={s.kind === 'admin' ? 'danger' : s.kind === 'write' ? 'warn' : 'ok'}>{s.scope} · {s.kind}</Badge>)}</div>
                {!check.scopes?.length && <p className="text-xs text-fg-muted">선언된 스코프 없음</p>}</div>
            </div>
          </div>
        )}
        {step === 3 && res && <p className="text-sm text-ok">게시됨 — {res.item.name}@{res.version.version} · 상태 <StatusBadge status={res.version.status} /> (운영자 승인 후 공개)</p>}
        {err ? <div className="mt-3"><ErrorBox error={err} /></div> : null}
        <div className="mt-4 flex justify-end gap-2">
          {step <= 1 && <Button variant="primary" onClick={validate}>검증</Button>}
          {step === 2 && <><Button onClick={() => setStep(0)}>이전</Button><Gate perm="registry:publish">{(ok, why) => <Button variant="primary" disabled={!ok || (sig?.required && !sig?.valid)} title={sig?.required && !sig?.valid ? '서명이 필요합니다 (MSG-007)' : why} onClick={publish}>승인 요청(게시)</Button>}</Gate></>}
          {step === 3 && <Button variant="primary" onClick={() => nav('/console/registry/approvals')}>승인 대기열 →</Button>}
        </div>
      </Card>
    </>
  )
}

const INTERVALS = [['', '수동'], ['300', '5분'], ['3600', '1시간'], ['21600', '6시간'], ['86400', '24시간']] as const
const SBLANK = { name: '', kind: 'custom', url: '', auto_approve: false, require_signature: false, namespaces: '', sync_interval_s: '' }
const srcStatus = (s?: string | null) => !s ? 'unknown' : s.startsWith('partial') ? 'partial' : s.startsWith('error') ? 'error' : 'ok'

export function Sources() {
  const toast = useToast()
  const q = useGet('/api/v1/registry/sources', undefined, { refetchInterval: 30_000 })
  const [edit, setEdit] = useState<Json | null>(null)
  const [f, setF] = useState(SBLANK)
  const [imp, setImp] = useState<Json | null>(null)
  const [entries, setEntries] = useState('[]')
  const [detail, setDetail] = useState<Json | null>(null)
  const act = async (fn: () => Promise<unknown>, msg: string) => {
    try {
      const r: any = await fn()
      toast('ok', `${msg}${r?.imported != null ? ` — 가져옴 ${r.imported} · 그대로 ${r.unchanged} · 실패 ${r.invalid} · 제외 ${r.filtered ?? 0}` : ''}`)
      q.refetch()
      return true
    } catch (e: any) {
      toast('error', e.message)
      return false
    }
  }
  const openEdit = (s: Json | null) => {
    setEdit(s ?? {})
    setF(s ? { name: s.name, kind: s.kind, url: s.url ?? '', auto_approve: s.auto_approve, require_signature: s.require_signature, namespaces: (s.namespaces ?? []).join(', '), sync_interval_s: s.sync_interval_s ? String(s.sync_interval_s) : '' } : SBLANK)
  }
  const save = async () => {
    const body = { ...f, url: f.url || null, namespaces: f.namespaces.split(',').map((x) => x.trim()).filter(Boolean), sync_interval_s: f.sync_interval_s ? Number(f.sync_interval_s) : null }
    const ok = await act(() => edit?.id ? send('PATCH', `/api/v1/registry/sources/${edit.id}`, body) : send('POST', '/api/v1/registry/sources', body), edit?.id ? '저장' : '추가')
    if (ok) setEdit(null)
  }
  return (
    <>
      <PageHeader title="레지스트리 소스·미러" crumbs={['카탈로그', '소스']} desc="공식 MCP Registry·Docker MCP Catalog 미러 · 사내 레지스트리 · 에어갭 파일 번들 (FR-C00·W03) — 허용 네임스페이스만 가져오고, 일부 실패는 건수로 남깁니다."
        actions={<Gate perm="catalog:write">{(ok, why) => <Button variant="primary" disabled={!ok} title={why} onClick={() => openEdit(null)}>+ 소스</Button>}</Gate>} />
      {q.error && <ErrorBox error={q.error} />}
      <DataTable data={q.data?.items ?? []} onRowClick={setDetail} columns={[
        col.accessor('name', { header: '이름', cell: ({ row }) => <span className="font-medium">{row.original.name}{!row.original.enabled && <Badge className="ml-1">중지</Badge>}</span> }),
        col.accessor('kind', { header: '종류', cell: (c) => <Badge>{c.getValue()}</Badge> }),
        col.accessor('url', { header: 'URL', cell: (c) => <span className="mono text-[11px]">{c.getValue() ?? '(파일)'}</span> }),
        col.accessor('namespaces', { header: '네임스페이스', enableSorting: false, cell: (c) => <span className="mono text-[11px]">{(c.getValue() ?? []).join(', ') || '전부'}</span> }),
        col.accessor('auto_approve', { header: '자동 승인', cell: (c) => (c.getValue() ? <Badge tone="warn">예</Badge> : '아니오') }),
        col.accessor('require_signature', { header: '서명 필수', cell: (c) => (c.getValue() ? '✓' : '—') }),
        col.accessor('item_count', { header: '항목' }),
        col.accessor('last_status', { header: '마지막 결과', cell: ({ row }) => { const r = row.original.last_result ?? {}; return <div className="flex flex-col"><StatusBadge status={srcStatus(row.original.last_status)} />
          {r.total != null && <span className="text-[11px] text-fg-muted">가져옴 {r.imported} · 그대로 {r.unchanged} · 실패 {r.invalid} · 제외 {r.filtered}</span>}</div> } }),
        col.accessor('sync_interval_s', { header: '주기', cell: ({ row }) => <div className="text-xs"><div>{INTERVALS.find(([v]) => v === String(row.original.sync_interval_s ?? ''))?.[1] ?? `${row.original.sync_interval_s}s`}</div>
          <div className="text-fg-muted">{row.original.last_sync_at ? `마지막 ${fmtAgo(row.original.last_sync_at)}` : '동기화 전'}{row.original.next_sync_at ? ` · 다음 ${fmtTime(row.original.next_sync_at)}` : ''}</div></div> }),
        col.display({ id: 'a', header: '', cell: ({ row }) => <Gate perm="catalog:write">{(ok) => <span className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
          {row.original.url && <Button size="sm" disabled={!ok} onClick={() => act(() => send('POST', `/api/v1/registry/sources/${row.original.id}/sync`), '동기화')}>동기화</Button>}
          <Button size="sm" disabled={!ok} onClick={() => { setImp(row.original); setEntries('[]') }}>가져오기</Button>
          <Button size="sm" disabled={!ok} onClick={() => openEdit(row.original)}>설정</Button></span>}</Gate> }),
      ]} />
      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? `소스 설정 — ${edit.name}` : '레지스트리 소스'} footer={<><Button onClick={() => setEdit(null)}>취소</Button><Button variant="primary" disabled={!f.name} onClick={save}>{edit?.id ? '저장' : '추가'}</Button></>}>
        <div className="space-y-3">
          <Field label="이름"><Input value={f.name} disabled={!!edit?.id} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="종류"><Select value={f.kind} disabled={!!edit?.id} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="official">official (MCP Registry)</option><option value="docker">docker (MCP Catalog)</option><option value="custom">custom</option><option value="file">file (에어갭)</option></Select></Field>
          <Field label="URL"><Input className="mono" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>
          <Field label="허용 네임스페이스 (쉼표·glob)" hint="예: io.github.acme*, io.internal/* — 비우면 전부"><Input className="mono" value={f.namespaces} onChange={(e) => setF({ ...f, namespaces: e.target.value })} /></Field>
          <Field label="예약 동기화"><Select value={f.sync_interval_s} disabled={f.kind === 'file'} onChange={(e) => setF({ ...f, sync_interval_s: e.target.value })}>{INTERVALS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.auto_approve} onChange={(e) => setF({ ...f, auto_approve: e.target.checked })} />자동 승인 (신뢰 소스만)</label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.require_signature} onChange={(e) => setF({ ...f, require_signature: e.target.checked })} />서명 필수</label>
        </div>
      </Modal>
      <Modal open={!!imp} onClose={() => setImp(null)} title={`가져오기 — ${imp?.name}`} wide footer={<><Button onClick={() => setImp(null)}>취소</Button><Button variant="primary" onClick={() => { let body: unknown; try { body = JSON.parse(entries) } catch { toast('error', 'JSON 형식 오류 (MSG-004)'); return } act(() => send('POST', `/api/v1/registry/sources/${imp!.id}/import`, body), '가져오기').then((ok) => ok && setImp(null)) }}>가져오기</Button></>}>
        <p className="mb-2 text-xs text-fg-muted">server.json 배열(또는 /v0/servers 응답의 servers)을 붙여 넣으세요 — 에어갭 번들의 registry 파일.</p>
        <Textarea aria-label="가져올 항목" className="mono h-60 text-xs" value={entries} onChange={(e) => setEntries(e.target.value)} />
      </Modal>
      <Drawer open={!!detail} onClose={() => setDetail(null)} title={`동기화 결과 — ${detail?.name ?? ''}`} width="w-[520px]">
        {detail && <div className="space-y-3 text-sm">
          <Kv rows={[['상태', <StatusBadge status={srcStatus(detail.last_status)} />], ['요약', detail.last_status ?? '—'], ['시각', fmtTime(detail.last_sync_at, true)], ['다음', detail.next_sync_at ? fmtTime(detail.next_sync_at, true) : '수동']]} />
          <h3 className="font-semibold">실패 항목 {(detail.last_result?.errors ?? []).length}</h3>
          <ul className="space-y-1">{(detail.last_result?.errors ?? []).map((e: Json, n: number) => <li key={n} className="rounded border border-line p-2"><div className="mono text-xs">{e.name ?? '(이름 없음)'}</div><div className="text-xs text-danger">{e.error}</div>
            {e.detail && <div className="text-[11px] text-fg-muted">{typeof e.detail === 'string' ? e.detail : JSON.stringify(e.detail)}</div>}</li>)}</ul>
          {detail.last_result?.error && <p className="text-xs text-danger">{detail.last_result.error}</p>}
        </div>}
      </Drawer>
    </>
  )
}

export function Sbom() {
  const q = useGet('/v1/catalog/sbom')
  const comps: Json[] = q.data?.components ?? []
  const risk = (c: Json) => (c.properties ?? []).find((p: Json) => p.name === 'agentnexus:license_risk')?.value ?? 'unknown'
  const scope = (c: Json) => (c.properties ?? []).find((p: Json) => p.name === 'agentnexus:scope')?.value ?? '-'
  const lic = (c: Json) => (c.licenses ?? []).map((l: Json) => l.license?.id ?? l.license?.name ?? l.expression).filter(Boolean).join(', ') || '미상'
  const counts = comps.reduce<Record<string, number>>((a, c) => ({ ...a, [risk(c)]: (a[risk(c)] ?? 0) + 1 }), {})
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(q.data, null, 2)], { type: 'application/vnd.cyclonedx+json' }))
    Object.assign(document.createElement('a'), { href: url, download: 'agentnexus-sbom.cdx.json' }).click()
  }
  return (
    <>
      <PageHeader title="SBOM·라이선스 리포트" crumbs={['카탈로그', 'SBOM']} desc={<>CycloneDX {q.data?.specVersion} — 플랫폼 의존성과 카탈로그 항목 (FR-P04). 고위험 라이선스는 분리 배포 검토 (MSG-023)</>}
        actions={<Button onClick={download} disabled={!q.data}>CycloneDX 내려받기</Button>} />
      {q.error && <ErrorBox error={q.error} />}
      <div className="mb-4 grid grid-cols-4 gap-3">
        <Kpi label="구성요소" value={comps.length} /><Kpi label="저위험" value={counts.low ?? 0} tone="ok" /><Kpi label="중위험" value={counts.medium ?? 0} tone={counts.medium ? 'warn' : undefined} />
        <Kpi label="고위험·미상" value={(counts.high ?? 0) + (counts.unknown ?? 0)} tone={(counts.high ?? 0) ? 'danger' : undefined} />
      </div>
      <DataTable data={comps} columns={[
        col.accessor('name', { header: '이름', cell: (c) => <span className="mono text-xs">{c.getValue()}</span> }), col.accessor('version', { header: '버전' }),
        col.accessor('type', { header: '유형' }), col.display({ id: 'scope', header: '범위', cell: ({ row }) => scope(row.original) }),
        col.display({ id: 'lic', header: '라이선스', cell: ({ row }) => lic(row.original) }),
        col.display({ id: 'risk', header: '위험', cell: ({ row }) => <Badge tone={({ low: 'ok', medium: 'warn', high: 'danger' } as Record<string, 'ok' | 'warn' | 'danger'>)[risk(row.original)] ?? 'muted'}>{risk(row.original)}</Badge> }),
        col.accessor('purl', { header: 'purl', cell: (c) => <span className="mono text-[11px] text-fg-muted">{c.getValue()}</span> }),
      ]} />
    </>
  )
}
