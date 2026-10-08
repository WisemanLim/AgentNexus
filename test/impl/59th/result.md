# impl 59th — 결과: 3차 품질 검토 (Epic 59 · D21-④) + 12차 완료 게이트

판정: **PASS** — pytest C59-01~17 **17/17** · 전체 회귀 SQLite **419 passed + 1 skipped** · PostgreSQL **420 passed** · 12차 완료 게이트(아래).

## 검토 → 검증 → 수정

| 단계 | 결과 | 위치 |
|------|------|------|
| 위임 보안 (`/security-review` 폴백) | Medium 2 · Low 2 | `.review/round3/delegated/security-review.md` |
| 위임 결함 (`/code-review` 폴백) | High 1 · Medium 2 · Low 1 | `.review/round3/delegated/code-review.md` |
| Lv2 라인 리뷰 (D20·D21 hunk, 블록 모드) | High 3 · Medium 13 · Low 35 · 파일별 12 + INFRA | `.review/round3/CODE-REVIEW-Lv2/` |
| 검증 | 수정 17건 · 부분 4 · 사용자 결정 1 · 나머지 기록 · 기각 0 | `.review/round3/VERIFY.md` |

검토 3개는 서로 독립 실행(백그라운드)했고, 검토가 읽는 동안 소스를 고치지 않았다. Lv2 검토는 하위 에이전트를 쓰지 않게 했다(2차의 중복 작성 회피).

## 시험 회차

| 단계 | 결과 | 로그 |
|------|------|------|
| r1 (C59 만) | **17/17** | `logs/pytest-r1.log` |
| 전체 SQLite r1 | 417/419 — ① 계약 스냅샷에 58th 의 `/api/v1/admin/keys*` 3개가 없었다(58th 는 부분 회귀만 돌려 놓침 → `UPDATE_CONTRACT=1` 로 갱신, 추가 3개 외 차이 없음) ② C59-13 이 앞 시험이 채운 실제 교환 토큰을 먼저 만나 가짜 교환이 불리지 않음(시험 순서 의존 → 그 키를 먼저 비움) | `logs/pytest-full-sqlite-r1.log` |
| 전체 SQLite r2 | **419 passed + 1 skipped** | `logs/pytest-full-sqlite-r2.log` |
| 전체 PostgreSQL | **420 passed** | `logs/pytest-full-pg-r1.log` |
| helm-check | 4 시나리오 OK | `logs/helm-check.log` |
| Vitest | **27/27** | `logs/vitest.log` |
| web-build + Playwright r1 | 76/77 — E30-01(동의 카드 거부 뒤 카드 사라짐 30초 대기)이 한 번 시간 초과. 이번 차수 변경은 동의 결정 경로(`mcp_consent.decide`)를 건드리지 않았다 | `logs/playwright.log` |
| Playwright E30 단독 · 전체 r2 | 2/2 · **77/77** — 재현 안 됨(부하 중 일시 지연으로 판단) | `logs/playwright-extend-r2.log`·`logs/playwright-r2.log` |

## 의도한 동작 변경 (기존 시험 갱신)

- C54-04 — 번들 반입 검증 키를 번들 밖 `--cosign-key` 로.
- C60-04 — 교환 토큰 캐시 키를 실제 형식 `("x", sub, id)` 로(전에는 실제와 다른 합성 키로 통과 — SEC3-01 이 드러난 이유).
- C53-05·미러 동기화(`test_catalog.py`) — 시험 자료 `server_json` 기본값이 인가 서버 없는 oauth2 라 이제 보류된다. 자동 승인을 보려는 항목은 인가 없음으로.

## 12차 완료 게이트

| 단계 | 결과 | 로그 |
|------|------|------|
| local 정지 → `make prod-build` → `make db-migrate ENV=prod` → `make db-ping ENV=prod` | 모두 0 (마이그레이션 0020 그대로 — 12차는 스키마 변경 없음) · 전 컨테이너 healthy | `logs/prod-build.log`·`prod-migrate.log`·`prod-ping.log` |
| 12차 스모크 `smoke-prod.sh` | **7/7** — 카드 URL · 푸시 발급자 필수·모르는 토큰 404 · 스케줄러 리스 5 · 서명 키 회전(이전 토큰 20/20·JWKS 20/20·새 로그인 kid 4/4·새 토큰 20/20 — 두 워커) · 암호화 키 회전(실패 0·두 워커 키 2→1·다른 프로세스 값 동일) · OTLP 400/413·namespace 409·`/v0/servers` 페이지 · Valkey 예약 고정 만료·푸시 수신 413 | `logs/smoke-prod-r1.log` |
| 회귀 (원본 55th + 사본 36th~51st, LOG → 59th) | 55th 5/5 · 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8(소유권 순서 조정 사본) · 44th 7/7 · 45th 5/5(ZCARD 사본) · 47th 2/2(k6 제외) · 51st 5/5 — 키 회전 뒤 실행 | `logs/regress-*.log`·`regress-summary.txt` |
| 정리 | 임시 계정 rehearsal* 8개 비활성 · 갱신 토큰 154개 폐기(PAT·Access Token 0) · `make prod-stop` · `make local-all`(healthy) · kubeconfig sha1 `b88c119b` 그대로 | `logs/rehearsal-cleanup.log`·`prod-stop.log`·`local-all-restore.log` |

NFR-P1 재측정은 생략(사용자 결정). 반복 엔진은 폴백(이 스킬이 직접 수행), 검토는 서브에이전트 3개(위임 보안·결함 폴백 + Lv2).