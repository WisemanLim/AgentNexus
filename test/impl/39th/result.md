# impl 39th — 결과: 품질 검토 반영 (Epic 39 · D16-④) + 7차 완료 게이트

판정: **PASS** — pytest C39-01~26 **26/26** · prod 스모크 P39-01~10 **10/10** · 완료 게이트 통과.

## 검토 입력 (`.review/`)

| 산출물 | 결과 |
|--------|------|
| `delegated/security-review.md` (security-reviewer 폴백) | SEC-01~12 — High 3 · Medium 6 · Low 3 |
| `delegated/code-review.md` (code-reviewer 폴백) | BUG-01~12 — High 3 · Medium 6 · Low 3 |
| `REVIEW-InDepth.md` | 종합 🟡 주의(조정 후 상용 가능) · D-01~19 |
| `CODE-REVIEW-Lv2/` (40개 파일 라인 리뷰) | Top-3: 시크릿 참조 병합(높음) · 업스트림 발견 AS 로 토큰 전송(높음) · 승인 통제 경합·우회(중간) |

판정 표(수정 / 수정 안 함 + 이유)는 `scenario.md`. 수정은 실제 코드로 다시 확인한 결함만, 기능 추가 없음.

## 회차

| 회차 | 결과 | 비고 |
|------|------|------|
| pytest r1 | 10/11 | C39-11 시험 결함(템플릿 주석의 `/health` 를 셈) → `httpGet` 유무로 판정 |
| pytest r2 | 98/98 (+ 영향 스위트) | |
| pytest r3 | 19/19 | Lv2 반영 1차(시크릿 참조·AS 고정·CLI·2인 검토·경쟁·푸시·폴링) |
| pytest r4~r5 | 84/84 (+ 영향 스위트) | Lv2 반영 2차(전송처 변경·refresh·code·도구 기록·동의 예외·A/B·마스킹·리포트 서명·CLI context) |
| prod r1 | 10/10 | 1차 빌드 |
| prod r2 | 10/10 | 최종 빌드 |

## 7차 완료 게이트

| 항목 | 결과 | 로그 |
|------|------|------|
| pytest SQLite | **298 passed + 1 skipped**(PG 전용) | `logs/gate-pytest-sqlite-final.log` |
| pytest PostgreSQL | **299 passed** | `logs/gate-pytest-pg-r3.log` |
| Vitest | 27/27 | `logs/web-test.log` |
| Playwright | **77/77** (최종 코드) | `logs/gate-e2e-final.log` |
| helm-check | lint + 시나리오 4종 template OK | `logs/helm-check.log` |
| prod 리허설 (PostgreSQL · 2 workers · Valkey · migration 0018) | 36th 10/10 · 37th 9/9 · 39th 10/10 · 회귀 32nd 11/11 · 33rd 7/7 · 35th 8/8 | `test/impl/36th·37th/logs`, `logs/smoke-prod-r2.log`, `logs/regress-*-r2/` |
| kind 운영 클러스터 | 27/27 (`test/impl/38th`) | |

### 게이트 중 실패와 처리

- SQLite r1: `test_contract`(36th REST 경로 9개 — 스냅샷 갱신) · `test_deploy` 차트 env 정합(`A2A_GRPC_*` — host_only·ConfigMap 빈 값). 37th 회귀로 기록.
- SQLite 중간 회차의 `test_continue_with_partial_results`(노드 시간 초과 1초)·`test_mcp_proxy_overhead_p95` 실패는 호스트 부하(prod 스택·kind 동시 기동) — 단독·최종 실행 통과. egress 가드 A/B 측정에서 가드에 따른 일관된 차이 없음(`logs/perf-guard-ab.log`).
- Playwright 1차 75/77(E22-05·E30-01) — 두 스펙 재실행 9/9, 최종 코드 전체 77/77.
- prod 37th r2: P37-03~07 실패 — 데모 A2A 에이전트가 runtime 이미지의 `UVICORN_WORKERS=2` 로 2 워커, 메모리 Task 가 엇갈림(기존 결함) → compose·Helm 데모 서비스 `UVICORN_WORKERS=1` 후 r3·r4 9/9.

### 리허설 정리

임시 계정 `rehearsal`·`rehearsal-lock`(실행 중 생성한 비밀번호, 출력·저장 안 함) 비활성화 · 시험 Access Token 폐기 · 로그인 실패 창 키 삭제 · `make prod-stop` → `make local-all` 복귀(health ok). 이전 차수 폴더(`32nd`·`33rd`·`35th`)에는 쓰지 않음(스크립트 사본의 LOG 를 이 차수로).

## 남은 결정 항목 (사용자 판단 필요)

1. `/idp/token` 토큰 교환 grant 에 confidential 클라이언트(워크로드) 인증을 요구할지 — 현재는 사용자 토큰만으로 업스트림 audience 토큰 발급, `act` 는 호출자 신고.
2. 호출자 신고 헤더(`X-AgentNexus-Agent`·`Data-Class`·`Run`)를 인증 주체·서버 판정으로 대체할지.
3. 업스트림 `readOnlyHint` 를 trusted 등급에서만 믿을지 · 클라이언트 푸시 URL 에 사설망 차단을 둘지.
4. 라이선스·공급망: LICENSE·NOTICE·THIRD_PARTY·SECURITY.md·Dependabot·CI 확장(D-01~04·18) · Chart `home` 소유 도메인(D-15) · `.docs/`·`.wds/` 커밋 여부(D-19).
5. scrypt 강도 상향(N=2^17, 재해싱) · refresh token 재사용 탐지(계열 폐기).
