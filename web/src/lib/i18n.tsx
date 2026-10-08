// ko/en 사전 — 기본 언어는 운영 설정(ui.language), 사용자가 상단 바에서 바꾸면 이 브라우저에만 기억
import { createContext, useContext, useState, type ReactNode } from 'react'

const ko = {
  'nav.overview': '개요', 'nav.chat': '채팅', 'nav.agents': '에이전트', 'nav.catalog': '카탈로그', 'nav.mcp': 'MCP 서버',
  'nav.a2a': 'A2A 에이전트', 'nav.gateway': 'AI 게이트웨이', 'nav.models': '모델', 'nav.identity': 'Identity',
  'nav.observe': '관측·감사', 'nav.admin': '운영·설정',
  'top.search': '카탈로그·trace_id·정책 검색', 'top.approvals': '승인 대기', 'top.notifications': '알림', 'top.me': '내 계정',
  'top.logout': '로그아웃', 'common.loading': '불러오는 중…', 'common.empty': '항목이 없습니다.', 'common.retry': '다시 시도',
  'common.save': '저장', 'common.cancel': '취소', 'common.close': '닫기', 'common.refresh': '새로고침', 'common.search': '검색',
  'common.noperm': '권한이 없습니다', 'common.approve': '승인', 'common.reject': '거부', 'common.create': '추가',
  'common.delete': '삭제', 'common.run': '실행', 'common.details': '상세',
}
type Key = keyof typeof ko
const en: Record<Key, string> = {
  'nav.overview': 'Overview', 'nav.chat': 'Chat', 'nav.agents': 'Agents', 'nav.catalog': 'Catalog', 'nav.mcp': 'MCP Servers',
  'nav.a2a': 'A2A Agents', 'nav.gateway': 'AI Gateway', 'nav.models': 'Models', 'nav.identity': 'Identity',
  'nav.observe': 'Observability', 'nav.admin': 'Operations',
  'top.search': 'Search catalog · trace_id · policies', 'top.approvals': 'Pending approvals', 'top.notifications': 'Notifications',
  'top.me': 'My account', 'top.logout': 'Sign out', 'common.loading': 'Loading…', 'common.empty': 'No items.',
  'common.retry': 'Retry', 'common.save': 'Save', 'common.cancel': 'Cancel', 'common.close': 'Close', 'common.refresh': 'Refresh',
  'common.search': 'Search', 'common.noperm': 'Permission required', 'common.approve': 'Approve', 'common.reject': 'Reject',
  'common.create': 'Add', 'common.delete': 'Delete', 'common.run': 'Run', 'common.details': 'Details',
}
const dicts = { ko, en }
export type Lang = keyof typeof dicts

function initial(): Lang {
  try {
    const v = localStorage.getItem('anx.lang')
    if (v === 'ko' || v === 'en') return v
  } catch {
    /* 저장소 없음 */
  }
  return 'ko' // 운영 설정 기본값(ui.language=ko) — 상단 바에서 바꾸면 이 브라우저에 기억
}

const Ctx = createContext<{ lang: Lang; setLang: (l: Lang) => void; t: (k: Key) => string }>({
  lang: 'ko', setLang: () => {}, t: (k) => ko[k],
})

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, set] = useState<Lang>(initial)
  const setLang = (l: Lang) => {
    set(l)
    document.documentElement.lang = l
    try {
      localStorage.setItem('anx.lang', l)
    } catch {
      /* 무시 */
    }
  }
  return <Ctx.Provider value={{ lang, setLang, t: (k) => dicts[lang][k] ?? k }}>{children}</Ctx.Provider>
}

export const useT = () => useContext(Ctx)
