# impl 51st — 결과: 2차 품질 검토 (Epic 51 · D19-④) + 10차 완료 게이트

판정: **PASS** — 확인된 결함 수정 pytest C51-01~18(C51-06 = Helm) **17/17** · Helm 불변식(C51-06) · prod 스모크 P51-01~05 **5/5**.

## 검토 산출물 (`.review/round2/`)

| 산출물 | 내용 |
|--------|------|
| `delegated/security-review.md` | 보안 리뷰(`/security-review` 폴백) 원문 — High 1 · Medium 4 · Low 3, 1차 SEC-01~12 재확인 |
| `delegated/code-review.md` | 결함 리뷰(`/code-review` 폴백) 원문 — High 1 · Medium 1 · Low 2, 1차 BUG-01~12 재확인 |
| `CODE-REVIEW-Lv2/` | Lv2 라인 리뷰 — 파일별 31개 + INFRA.md + INDEX.md |
| `VERIFY.md` | 지적마다 코드 경로 검증 → 수정 / 기존 결정 / 기록 판정과 근거 |

## 검증 결과 요약 (`VERIFY.md`)

- **수정한 확인 결함** — [높음] 6건: 자격 증명 전송처(MCP 인가 서버·토큰 엔드포인트·mode — mcp_gateway·api/mcp 같은 결함) · 한도 폴백 모델의 정책 우회 · 기계 주체 자기 승인 · 토큰으로 토큰 발급(승격) · 푸시 URL 검사 이벤트 루프 차단(CR2-01, 게이트웨이·플랫폼) · hosted-openapi 요청 단위 egress(SEC2-01, 지적 시나리오는 불성립·가드 공백은 성립).
  [중간] 다수: 보안 응답 헤더(SEC2-05) · 푸시 콜백 내부 주소 · 푸시 전달 직전 재검사 · 푸시·폴링 결과 소독 · 비활성 사용자·차단 에이전트 폴링 · 샘플링 중계 deny 정책 · 헬스 루프 DB 오류 종료 · 모델 `data_classes` 미적용 · 모르는 데이터 등급(MCP·A2A) · 외부 IdP 표시 이름 정책 매치 · 등록 승인 우회(PATCH) · 내장 IdP 토큰 발급 루프 차단 · IP 정책 경로 누락 · MFA 무제한 · 클라이언트 키 팀 자유·무기한 PAT · `caller_context` 루프 차단 · Alibaba IMDS · 업스트림 세션 유휴 시각 · Helm(NetworkPolicy 내부 호출자 · `SEED_DEMO` · 프록시 헤더 신뢰 값).
- **기존 결정**(이전 차수에 근거와 함께 결정, 사실 변화 없음): DNS 재바인딩(egress NetworkPolicy) · jwks 같은 origin · LIKE · 로그인 CSRF · redirect 사전 등록 · `app_env` 기본값 · 정책 단독 롤백(H08 ⑥) · 보존 실행(아카이브 = 증명 보존) · 사용자 이름 잠금 창.
- **기록**(이번 범위 밖 개선 후보): observe 집계 성능 · bundle 서명 기본값 · a2a-grpc 프로브/PDB · 증감 카운터 TTL · 도구 분류 휴리스틱 · `identity:write` 권한 범위 등.

## 시험

| 단계 | 결과 | 로그 |
|------|------|------|
| r1 (C51-01~06 초안 — 플랫폼 경로 미수정) | 5/6 — 플랫폼 `push_check` 가 루프 스레드에서 해석(수정 전 재현) | `logs/pytest-r1.log` |
| r2~r3 (+a2a_server·consent·health) | 6/6 · 8/8 | `logs/pytest-r2.log`·`r3` |
| r4~r5 (+llm 폴백·허용 등급) | 52/53 → 시험 단언 형식(OpenAI 오류 모양) 수정 뒤 통과 | `logs/pytest-r4.log`·`r5` |
| r6 (+내장 IdP) | 56/56 (identity·SSO 회귀 포함) | `logs/pytest-r6.log` |
| r7 (+승인·등급·전송처 API) | 12/12 | `logs/pytest-r7.log` |
| r8 (A2A·MCP·정책 회귀 15개 파일) | **108/108** | `logs/pytest-r8.log` |
| r9~r10 (+토큰 발급) | 66/67 → `test_cli` 의 "첫 pending 디바이스 코드" 가 C51-12 가 남긴 코드를 집음(시험 간 간섭) → C51-12 정리 추가 → **67/67** | `logs/pytest-r9.log`·`r10` |
| r11 (+정책 주체 이름) | 46/46 | `logs/pytest-r11.log` |
| r12~r13 (+INDEX H2·H4·M16 — 게시 예약 키·secret_ref 재부착·재등록) | 95/97 → 39th C39-12 "자기 네임스페이스 재부착 허용" 단언을 새 규칙(새 값 직접 입력만 허용)으로 고침 · `test_timeouts_429_and_join_rules` 1회 타이밍 실패(재실행 2회 통과) → **51/51 ×2** | `logs/pytest-r12.log`·`r13` |
| r14~r15 (+INDEX M14·M18·M23·M25·M41·M45·M47) | 129/130 → 시험 단언(빈 경로 제거) 수정 → **16/16** | `logs/pytest-r14.log`·`r15` |
| r16 (+플랫폼 tasks/list 워커 무관 — prod 리허설에서 발견) | **51/51** | `logs/pytest-r16.log` |
| Helm (C51-06) | 4 시나리오 OK · 불변식 음성 확인(SEED_DEMO 불일치·내부 호출자 누락 → FAIL) | `logs/helm-check-r1.log` |

## 10차 완료 게이트

| 항목 | 결과 | 로그 |
|------|------|------|
| pytest SQLite | 353 passed + 1 skipped (1차) → 최종 **356 passed + 1 skipped** | `logs/gate-pytest-sqlite.log` → `logs/final-pytest-sqlite.log` |
| pytest PostgreSQL | 354 passed (1차) → 최종 **357 passed** | `logs/gate-pytest-pg.log` → `logs/final-pytest-pg.log` |
| Vitest | 27/27 | `logs/gate-web-test.log` |
| Playwright | **77/77** (1차·3차·최종) | `logs/gate-e2e.log`·`gate3-e2e.log`·`final-e2e.log` |
| helm-check | 4 시나리오 OK | `logs/gate-helm-check.log` |
| prod 리허설 (PostgreSQL · 2 workers · Valkey · 0019) | `prod-build` → `db-migrate` → `db-ping`(postgres OK) → 51st **5/5** (r3) + 회귀 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8(소유권 조정 사본) · 44th 7/7 · 45th 5/5 · 47th 2/2(k6·NFR-P1 제외) | `logs/smoke-prod-r3.log`, `logs/regress/`, `logs/regress-*/` |

### 최종 prod 리허설 (INDEX 수정·목록 수정 반영 이미지)

- 3차(`logs/regress3/`): 51st 5/5 · 회귀 전부 통과, 단 **44th P44-01 실패** — 주인의 `tasks/list` 에 자기 Task 가 없음. 원인: 플랫폼 목록이 요청을 받은 워커의 메모리만 본다(2 workers — 이전 통과는 라우팅 운). get·cancel 은 영속 행으로 판정하는데 목록만 빠져 있던 다중 워커 결함 → 영속 행(주체·프로파일, 최근 50) + 메모리 병합으로 수정(C51-18).
- 4차(`logs/regress4/`, 수정 이미지): 51st **5/5** · 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8 · 44th **7/7**(+반복 3회 7/7) · 47th 2/2 · 45th 4/5 → P45-05(리스 보유자 16초 안정) 1회 실패 — 재빌드 직후 이전 컨테이너의 리스(TTL 120초)가 관측 창 안에서 넘어감, 재실행 2회 5/5(`45th-repeat1·2.log`).

### 게이트 중 처리

- 51st 스모크 r1 — P51-02 빈 응답: 스크립트의 `"$(rpc … "{\"taskId\":…}")"` 중첩 인용이 bash 3.2 에서 깨짐(제품은 같은 요청에 400·-32602 정상 — 수동 확인). 본문을 변수로 만들어 r3 5/5. P51-04 r1 — user 역할은 클라이언트 키 자체가 불가(`gateway:read`)라 팀 검사 전에 막힘 → PAT 로 팀 지정 확인.
- 회귀 스모크는 이전 차수 스크립트를 고치지 않고 scratch 사본(LOG → `51st/logs/regress-*`)으로 실행. 47th 사본은 k6 측정(P47-03·04)을 뺐다(NFR-P1 측정 생략 — 10차 사용자 결정).

### 리허설 정리

임시 계정 `rehearsal*` 8개 모두 disabled · 시험 PAT 없음(51st 는 실행 중 폐기) · `make prod-stop`(prod 컨테이너 0) → `make local-all`(health ok) · `~/.kube/config` 무변경 · 다른 프로젝트 컨테이너 미접촉 · 이전 차수 폴더에 쓰지 않음.
