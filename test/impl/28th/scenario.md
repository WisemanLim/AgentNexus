# impl 28th — 어두운 테마 접근성 (Epic 28 · D14-①)

## 범위

| 항목 | 구현 |
|------|------|
| 점검 | `web/e2e/a11y-dark.spec.ts` — 시스템 다크(`colorScheme: dark`) 콘솔 60 화면 + 채팅 · 명시 다크(`anx.theme=dark`, 시스템 밝음) · 명시 라이트(시스템 다크) — 공용 `a11y-shared.ts`(26th 와 같은 화면 목록·axe 태그) |
| 글자 토큰 | 어두운 테마 `--primary-fg #8b9cff`·`--mcp-fg`·`--a2a-fg #b197fc`·`--ok-fg #4ade80`·`--warn-fg #fbbf24`·`--model-fg #22d3ee` — `.text-primary` 등이 토큰을 따름(밝은 테마는 원색) |
| `dark:` 변형 | Tailwind 기본(시스템만) → `@custom-variant dark` = 명시 다크 **또는** (시스템 다크 ∧ 명시 라이트 아님) — 토큰 규칙과 일치 |
| 비활성 메뉴 | 권한 없는 하위 메뉴 `role=link aria-disabled=true` (비활성 컴포넌트 대비 예외를 보조기기·점검기가 인식) |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| E28-01 | 시스템 다크 콘솔 60 화면 위반 0 | `a11y-dark.spec.ts` | Playwright |
| E28-02 | 시스템 다크 채팅·대화 이력 위반 0 | 〃 | Playwright |
| E28-03 | 명시 다크(토글) — `data-theme=dark` · 주요 화면 위반 0 | 〃 | Playwright |
| E28-04 | 명시 라이트 + 시스템 다크 — 밝은 배경·`dark:` 미적용 · 위반 0 | 〃 | Playwright |
| R | 밝은 테마(26th) 회귀 없음 · 전체 e2e | `logs/a11y-dark-r2.log` · 완료 게이트(31st) | Playwright |
