# 시험 결과 / Test Result — impl 26th (Epic 26 접근성 — KWCAG 2.2 + WCAG 2.2 AA)

- 일자 Date: 2026-10-07
- 대상 Target(profile): web 콘솔 번들(local) · Playwright 1.55 Chromium · axe-core 4.10 (@axe-core/playwright 4.10.2)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 3 (e2e 3 — R 제외), 통과 Pass: 3, 실패 Fail: 0, 최종 Verdict: **PASS** (round 6)
- axe 위반: round 1 **84건**(대비 59·버튼 이름 11·스크롤 영역 5·본문 링크 5·목표 크기 4, 화면 단위) → round 3 35 → round 4 2 → round 6 **0** (콘솔 60 화면 + 로그인·채팅·대화 이력)
- 회귀: Playwright 전체 66건 중 1건(E24-02 로케이터 — 이전 실행이 만든 키와 이름 겹침) → 로케이터 정확 일치로 PASS · Vitest 26 PASS

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E26-01 | PASS (round 6) | `logs/a11y-r1~r6.log` |
| E26-02 | PASS (round 4) | round 1 은 시험 코드(`browser.newPage` 금지) · round 2~3 은 채팅에 `main` 없음·대비 |
| E26-03 | PASS (round 2) | round 1 은 로그인 직후 포커스 위치(시험) — 새 문서에서 첫 Tab |
| R | PASS | `logs/26-e2e-full-r1.log`(65/66 → E24-02 로케이터 수정 `logs/26-e2e-gaps-r2.log` 5/5) · `logs/26-web-test.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix |
|-------|----------|------------|----------|
| 1 | 상태 배지(정상) 대비 부족 59건 | `text-ok` #2F9E44 on 연녹색 ≈ 3.3:1 | 의미 색 글자용 진한 토큰(`--ok-fg` 등) |
| 1 | 표 머리글 빈 버튼 11건 | 이름 없는 동작 열에도 정렬 버튼 | 이름·정렬 없는 열은 버튼 없음, `aria-sort` |
| 1 | 스크롤 영역 키보드 접근 5건 | `pre` overflow 에 포커스 불가 | `tabIndex=0` |
| 1 | 본문 링크 색으로만 구분 5건 | 밑줄 없음 | 본문 링크 밑줄 |
| 1 | 목표 크기 4건(수백 노드) | 16px 체크박스·12px 점·좁은 막대 | 18px+간격 · 점·막대는 포인터 단축 + 동등 컨트롤 |
| 2 | 보조 글자(`text-fg-muted`) 대비 부족 다수 | #6b7280 on #f5f6fa ≈ 4.4:1 | #5b6472 (5.6:1) |
| 3~5 | 경고·모델·프로필·위험 글자색, 줄 번호, React Flow 저작 표시 대비 | 원색을 글자로 사용 | 테마별 글자 토큰 · #9ca3af · 저작 표시 배경·글자색 |
| 1 | 반복 영역 건너뛰기·화면 제목 없음 (KWCAG) | — | 「본문 바로가기」·`main#main`·문서 제목 |

## 첨부 / Logs

- `logs/` (a11y-r1~r6 · 26-e2e-full-r1 · 26-e2e-gaps-r2 · 26-web-test)
