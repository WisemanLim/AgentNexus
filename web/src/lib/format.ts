export const shortId = (id?: string | null, n = 4) => (!id ? '—' : id.length <= n * 2 + 1 ? id : `${id.slice(0, n)}…${id.slice(-n)}`)

export function fmtTime(ts?: string | null, withDate = false) {
  if (!ts) return '—'
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  const pad = (n: number) => String(n).padStart(2, '0')
  const t = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  return withDate ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${t}` : t
}

export function fmtAgo(ts?: string | null) {
  if (!ts) return '—'
  const s = Math.round((Date.now() - new Date(ts).getTime()) / 1000)
  if (s < 60) return `${Math.max(s, 0)}초 전`
  if (s < 3600) return `${Math.floor(s / 60)}분 전`
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`
  return `${Math.floor(s / 86400)}일 전`
}

export const fmtUsd = (v?: number | null) => (v == null ? '—' : v === 0 ? '$0' : v < 0.01 ? `$${v.toFixed(6)}` : `$${v.toFixed(2)}`)
export const fmtNum = (v?: number | null) => (v == null ? '—' : new Intl.NumberFormat('ko-KR').format(v))
export const fmtPct = (v?: number | null, digits = 1) => (v == null ? '—' : `${(v * 100).toFixed(digits)}%`)
export const fmtMs = (v?: number | null) => (v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`)

export const json = (v: unknown) => JSON.stringify(v, null, 2)
