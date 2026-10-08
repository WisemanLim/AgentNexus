# 시험 결과 / Test Result — impl 28th (Epic 28 어두운 테마 접근성)

- 일자 Date: 2026-10-07
- 대상 Target(profile): web 콘솔 번들(local) · Playwright 1.55 Chromium · axe-core 4.10
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 4 (e2e 4 — R 제외), 통과 Pass: 4, 실패 Fail: 0, 최종 Verdict: **PASS** (round 4)
- 위반: round 1 시스템 다크 **67건**(활성 내비 `text-primary` on 짙은 남색 · 정상 배지) → round 2 0(시스템) / 명시 다크 15건 → round 3·4 **0** (시스템 다크·명시 다크·명시 라이트+시스템 다크)
- 밝은 테마 26th 시험 동시 실행 PASS (`logs/a11y-dark-r2.log`)

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix |
|-------|----------|------------|----------|
| 1 | 어두운 테마 활성 메뉴·링크 대비 부족 (실제 결함) | `text-primary` #3F51E5 를 어두운 배경에도 그대로 | 테마별 글자 토큰 `--primary-fg` 등 |
| 2 | 명시 다크에서 경고 배너 글자가 어두움 (실제 결함) | Tailwind `dark:` 가 시스템 설정만 따르고 `data-theme` 를 무시 — 반대로 시스템 다크 + 명시 라이트에선 어두운 변형이 밝은 배경에 적용 | `@custom-variant dark` 를 토큰 규칙과 같게 |
| 2 | 권한 없는 하위 메뉴 대비 | 비활성 표시(50%)가 단순 span 이라 비활성 컴포넌트로 인식되지 않음 | `role=link aria-disabled=true` |

## 첨부 / Logs

- `logs/` (a11y-dark-r1~r4)
