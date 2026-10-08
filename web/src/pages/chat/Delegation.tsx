// SCR-C05 원격 에이전트 위임 카드 (Agent Card 헤더·신뢰 등급·Task 상태 머신·이벤트·Artifacts 내려받기·첨부·취소) · SCR-C07 대화 이력·세션 (본문 검색·필터·미리보기·첨부·Artifacts·이어하기·내보내기·삭제)
import { createColumnHelper } from '@tanstack/react-table'
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, CodeBlock, ErrorBox, Input, Kv, Loading, Select, StatusBadge, Textarea, TraceLink, type Tone, TypeBadge, cx, useToast } from '@/components/ui'
import { request, type Json } from '@/lib/api'
import { fmtAgo, fmtTime, fmtUsd } from '@/lib/format'
import { send, useGet } from '@/lib/query'
import type { InputRequest } from './runState'

const STATES = ['submitted', 'working', 'input-required', 'completed'] as const
const TRUST: Record<string, Tone> = { verified: 'ok', trusted: 'primary', untrusted: 'warn', blocked: 'danger' }
const norm = (s?: string) => (s ?? '').replace(/^TASK_STATE_/, '').toLowerCase().replace(/_/g, '-')

function ReplyForm({ req, onReply }: { req: InputRequest; onReply: (step: string, text: string) => Promise<void> }) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    setBusy(true)
    try {
      await onReply(req.step, text.trim())
      setText('')
    } catch (e: any) {
      toast('error', e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="mt-2 rounded-md border border-warn/50 bg-warn/10 p-3 text-xs">
      <div className="mb-1 font-medium">{req.agent} 의 질문 (MSG-017)</div>
      <p className="mb-2 whitespace-pre-wrap">{req.question}</p>
      <Textarea aria-label="원격 에이전트에 답하기" rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="같은 Task·대화(contextId)로 이어서 보냅니다" />
      <div className="mt-2 flex justify-end"><Button size="sm" variant="primary" disabled={!text.trim()} loading={busy} onClick={submit}>답하고 이어서 실행</Button></div>
    </div>
  )
}

async function downloadArtifact(taskId: string, a: Json) {
  const res = await request(`/api/v1/a2a/tasks/${encodeURIComponent(taskId)}/artifacts/${a.artifactId ?? a.artifact_id}`)
  const name = a.name && a.name.includes('.') ? a.name : `${a.name || a.artifactId}.md`
  const url = URL.createObjectURL(await res.blob())
  Object.assign(document.createElement('a'), { href: url, download: name }).click()
}

export function DelegationCards({ runId, active, inputs = {}, onReply }: { runId: string; active: boolean; inputs?: Record<string, InputRequest>; onReply?: (step: string, text: string) => Promise<void> }) {
  const toast = useToast()
  const q = useGet('/api/v1/a2a/tasks', { run_id: runId }, { refetchInterval: active ? 2000 : false })
  useEffect(() => { q.refetch() }, [active]) // eslint-disable-line react-hooks/exhaustive-deps — 실행 종료 시 최종 상태 재조회
  const [open, setOpen] = useState<string | null>(null)
  const detail = useGet(open ? `/api/v1/a2a/tasks/${open}` : null)
  const tasks: Json[] = (q.data?.items ?? []).filter((t: Json) => t.direction === 'outbound')
  if (!tasks.length) return null
  return (
    <>
      {tasks.map((t) => {
        const state = norm(t.state)
        const terminal = ['completed', 'failed', 'canceled', 'rejected'].includes(state)
        return (
          <Card key={t.id} className="border-a2a/50" title={<span className="flex flex-wrap items-center gap-2"><TypeBadge type="a2a" />원격 에이전트 위임 — {t.agent_id}{t.agent?.name && t.agent.name !== t.agent_id && <span className="text-xs font-normal text-fg-muted">{t.agent.name}</span>}
              {t.agent && <Badge tone={TRUST[t.agent.trust_tier] ?? 'muted'}>{t.agent.trust_tier}</Badge>}{t.agent?.version && <span className="text-xs font-normal text-fg-muted">v{t.agent.version}</span>}</span>}
            actions={<span className="mono text-xs text-fg-muted" title={t.id}>Task {(t.remote_task_id ?? t.id).slice(0, 8)}</span>}>
            {(t.agent?.skills ?? []).length > 0 && <div className="mb-2 flex flex-wrap gap-1 text-xs"><span className="text-fg-muted">스킬</span>{t.agent.skills.map((x: string) => <Badge key={x} tone="a2a">{x}</Badge>)}</div>}
            <ol className="mb-2 flex flex-wrap items-center gap-1 text-xs" aria-label="Task 상태">
              {STATES.map((s, i) => {
                const idx = STATES.indexOf(state as (typeof STATES)[number])
                const done = (idx > i && s !== 'input-required') || (state === 'completed' && s === 'completed')  // 거치지 않은 input-required 는 완료로 보지 않음
                return <li key={s} className={cx('rounded-full px-2 py-0.5', s === state ? 'bg-a2a text-white' : done ? 'bg-ok/15 text-ok' : 'bg-surface-2 text-fg-muted')}>{done && s !== state ? '✓ ' : ''}{s}</li>
              })}
              {['failed', 'canceled', 'rejected'].includes(state) && <li><StatusBadge status={state} /></li>}
            </ol>
            <Kv rows={[['상태 메시지', t.status_message || '—'], ['경로', <span className="mono text-xs">게이트웨이 /a2a/{t.agent_id} · trace <TraceLink id={t.trace_id} /></span>],
              ['Artifacts', `${t.artifacts_count}개`], ['갱신', fmtAgo(t.updated_at)]]} />
            {state === 'input-required' && (() => {
              const req = Object.values(inputs).find((r) => r.status === 'pending' && r.taskId === (t.remote_task_id ?? t.id))
              return req && onReply ? <ReplyForm req={req} onReply={onReply} />
                : <p className="mt-2 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-xs">{t.agent_id} 가 추가 입력을 요청했습니다 (MSG-017).</p>
            })()}
            {(t.events ?? []).length > 0 && (
              <details className="mt-2 text-xs"><summary className="cursor-pointer text-fg-muted">스트림 이벤트 {t.events.length}</summary>
                <ol className="mt-1 space-y-0.5 border-l border-line pl-3">{t.events.map((e: Json, i: number) => <li key={i}><StatusBadge status={e.state} /> <span className="text-fg-muted">{e.text}</span></li>)}</ol></details>
            )}
            <div className="mt-2 flex gap-2">
              {t.artifacts_count > 0 && <Button size="sm" onClick={() => setOpen(open === t.id ? null : t.id)}>{open === t.id ? 'Artifacts 닫기' : 'Artifacts 보기'}</Button>}
              {!terminal && <Button size="sm" variant="danger" onClick={() => confirm('위임한 Task 를 취소할까요? 받은 부분 결과는 보존됩니다 (MSG-002).') && send('POST', `/api/v1/a2a/tasks/${t.id}/cancel`).then(() => q.refetch()).catch((e) => toast('error', e.message))}>Task 취소</Button>}
            </div>
            {open === t.id && (detail.isLoading ? <Loading /> : (
              <div className="mt-2 space-y-2">{(detail.data?.artifacts ?? []).map((a: Json) => (
                <div key={a.artifactId ?? a.artifact_id} className="rounded-md border border-line p-2 text-xs">
                  <div className="mb-1 flex items-center gap-2 font-medium">📄 {a.name || a.artifactId}
                    <Button size="sm" className="ml-auto" onClick={() => downloadArtifact(t.id, a)}>내려받기</Button>
                    <Button size="sm" onClick={() => send('POST', '/api/v1/chat/attachments/from-artifact', { task_id: t.id, artifact_id: a.artifactId ?? a.artifact_id, session_id: t.session_id ?? undefined })
                      .then((r: any) => toast('ok', `첨부로 추가: ${r.name} — 다음 요청에 붙일 수 있습니다`)).catch((e) => toast('error', e.message))}>첨부로 추가</Button></div>
                  <CodeBlock value={(a.parts ?? []).map((p: Json) => p.text ?? JSON.stringify(p.data ?? p)).join('\n')} maxH="max-h-40" />
                </div>
              ))}</div>
            ))}
          </Card>
        )
      })}
    </>
  )
}

const col = createColumnHelper<Json>()
const PERIODS = [['', '전체 기간'], ['1', '오늘'], ['7', '7일'], ['30', '30일'], ['90', '90일']] as const

// SCR-C07 대화 이력 — 제목·본문 검색(300ms)·프로파일·기간·출처 필터(URL 보존) · 미리보기 · 첨부·Artifacts · 이어하기·내보내기·삭제
export function ChatHistory() {
  const nav = useNavigate()
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const [text, setText] = useState(sp.get('q') || '')
  useEffect(() => {
    const t = setTimeout(() => {
      if ((sp.get('q') || '') === text) return
      const p = new URLSearchParams(sp)
      if (text) p.set('q', text)
      else p.delete('q')
      setSp(p, { replace: true })
    }, 300)
    return () => clearTimeout(t)
  }, [text, sp, setSp])
  const setF = (k: string, v: string) => { const p = new URLSearchParams(sp); if (v) p.set(k, v); else p.delete(k); setSp(p, { replace: true }) }
  const list = useGet('/api/v1/sessions', { q: sp.get('q') || undefined, profile_id: sp.get('profile') || undefined, days: sp.get('days') || undefined,
    source: sp.get('source') || undefined, limit: 500 })
  const profiles = useGet('/api/v1/agents')
  const files = useGet('/api/v1/chat/files', { limit: 12 })
  const [sel, setSel] = useState<string | null>(null)
  const s = useGet(sel ? `/api/v1/sessions/${sel}/summary` : null).data
  const download = async (path: string, name: string) => {
    const res = await request(path)
    const url = URL.createObjectURL(await res.blob())
    Object.assign(document.createElement('a'), { href: url, download: name }).click()
  }
  const remove = async () => {
    if (!sel || !confirm('대화를 삭제할까요? 내용은 지워지고 메타데이터만 감사용으로 남습니다 (MSG-002).')) return
    try {
      await send('DELETE', `/api/v1/sessions/${sel}`)
      setSel(null)
      list.refetch()
      toast('ok', '삭제했습니다')
    } catch (e: any) {
      toast('error', e.message)
    }
  }
  const fileLink = (f: Json) => f.kind === 'attachment' ? `/api/v1/chat/attachments/${f.id}` : `/api/v1/a2a/tasks/${encodeURIComponent(f.task_id)}/artifacts/${f.artifact_id}`
  const total = list.data?.items?.length ?? 0
  return (
    <div className="flex min-h-0 flex-1 gap-4 overflow-auto p-6">
      <div className="min-w-0 flex-1">
        <h1 className="mb-3 text-xl font-semibold text-navy">대화 이력</h1>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Input aria-label="제목·내용 검색" placeholder="⌕ 제목·내용 검색" value={text} onChange={(e) => setText(e.target.value)} className="w-72" />
          <Select aria-label="프로파일" className="w-44" value={sp.get('profile') || ''} onChange={(e) => setF('profile', e.target.value)}>
            <option value="">프로파일: 전체</option>{(profiles.data?.items ?? []).map((p: Json) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>
          <Select aria-label="기간" className="w-32" value={sp.get('days') || ''} onChange={(e) => setF('days', e.target.value)}>{PERIODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          <Select aria-label="출처" className="w-36" value={sp.get('source') || ''} onChange={(e) => setF('source', e.target.value)}>
            <option value="">출처: 전체</option><option value="chat">채팅 UI</option><option value="api">API</option></Select>
          <span className="text-xs text-fg-muted">{sp.get('q') ? `‘${sp.get('q')}’ 검색 결과 ${total}` : `전체 ${total}`}</span>
        </div>
        {list.error && <ErrorBox error={list.error} />}
        <DataTable data={list.data?.items ?? []} filter={false} onRowClick={(r) => setSel(r.id)} empty={sp.get('q') ? '검색 결과가 없습니다 (ST-02) — 다른 단어로 찾아보세요.' : '대화 이력이 없습니다.'} columns={[
          col.accessor('title', { header: '제목', cell: ({ row }) => <span className="font-medium">{row.original.source === 'api' && <Badge className="mr-1">API</Badge>}{row.original.title}</span> }),
          col.accessor('profile_name', { header: '프로파일', cell: (c) => c.getValue() ?? '—' }),
          col.accessor('updated_at', { header: '마지막', cell: (c) => fmtAgo(c.getValue()) }),
          col.accessor('messages', { header: '메시지' }),
          col.accessor('cost', { header: '비용', cell: (c) => fmtUsd(c.getValue()) }),
        ]} />
        <Card title="최근 첨부·Artifacts 모아보기" className="mt-4">
          <div className="flex flex-wrap gap-2">
            {(files.data?.items ?? []).map((f: Json) => (
              <button key={`${f.kind}:${f.id ?? f.artifact_id}`} type="button" onClick={() => download(fileLink(f), f.name)}
                className="rounded-md border border-line px-2 py-1 text-left text-xs hover:bg-surface-2">
                {f.kind === 'attachment' ? '📎' : '📄'} {f.name}<span className="block text-[11px] text-fg-muted">{f.kind === 'attachment' ? '내 첨부' : `A2A · ${f.agent}`} · {fmtAgo(f.created_at)}</span>
              </button>
            ))}
            {!files.data?.items?.length && <p className="text-xs text-fg-muted">첨부나 원격 Artifacts 가 없습니다.</p>}
          </div>
        </Card>
      </div>
      <aside className="w-[360px] shrink-0">
        {!sel ? <Card><p className="text-sm text-fg-muted">세션을 고르면 요약을 미리 봅니다.</p></Card> : !s ? <Loading /> : (
          <Card title={s.title}>
            <Kv rows={[['프로파일', s.profile_name ?? s.profile_id ?? '—'], ['시작', fmtTime(s.created_at, true)], ['메시지', `${s.messages} · 위임 ${s.delegations}`],
              ['비용', fmtUsd(s.cost)], ['출처', s.source === 'api' ? 'API' : '채팅 UI'], ['trace', `${s.traces}개`]]} />
            {s.last_answer && <div className="mt-3"><div className="mb-1 text-xs font-medium text-fg-muted">마지막 응답</div><p className="line-clamp-6 text-sm whitespace-pre-wrap">{s.last_answer}</p></div>}
            {(s.files ?? []).length > 0 && <div className="mt-3"><div className="mb-1 text-xs font-medium text-fg-muted">첨부·Artifacts {s.files.length}</div>
              <div className="flex flex-wrap gap-1">{s.files.map((f: Json) => <Button key={`${f.kind}:${f.id ?? f.artifact_id}`} size="sm" onClick={() => download(fileLink(f), f.name)}>{f.kind === 'attachment' ? '📎' : '📄'} {f.name}</Button>)}</div></div>}
            <div className="mt-4 grid gap-2">
              <Button variant="primary" disabled={s.profile_archived} title={s.profile_archived ? '프로파일이 폐기됐습니다 — 새 대화에서 프로파일을 고르세요' : undefined} onClick={() => nav(`/chat/${s.id}`)}>이어하기 →</Button>
              <div className="grid grid-cols-2 gap-2"><Button onClick={() => download(`/api/v1/sessions/${s.id}/export`, `${s.id}.md`)}>내보내기 .md</Button><Button variant="danger" onClick={remove}>삭제</Button></div>
            </div>
            <p className="mt-3 text-[11px] text-fg-muted">보존 기간은 운영 설정을 따르고, 저장 전 PII 는 마스킹됩니다 (MSG-021). 삭제는 감사에 기록됩니다.</p>
          </Card>
        )}
      </aside>
    </div>
  )
}
