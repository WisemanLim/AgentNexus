# impl 21st — 등록 → 심사 → 사용 (Epic 21 · FR-C01~C03·G03·G07·O05·R00·W04·A05·S01·I02 · SCR-E01~E07·G02·G03·D10·F03·H07·H08 · ST-06)

## 범위

| 항목 | 구현 |
|------|------|
| E05 등록 승인 대기열 | MCP 서버(server.json)·원격 A2A 에이전트(Agent Card)·플랫폼 Agent Card 게시를 **한 대기열**에서 · 상태 탭(대기·승인·반려·만료) 건수 · 점검(스키마·서명·출처·SBOM·TLS) — `fail` 이면 승인 불가(MSG-007) · 요청 스코프 종류(read/write/admin) · **도구 위험도 자동 분류 → 승인자 확정**(고위험 = 사람 승인 규칙) · 신뢰 등급(official/partner/community/external → 게이트웨이 등급) · 허용목록 그룹(정책 생성, 비우면 기본 거부 유지) · 서명 없는 카드에 partner 이상은 사유 필요 · **72시간 경과 자동 반려**(ST-06) |
| E04 server.json 등록 | 원문 검증 → **줄 번호 인라인 오류**(JSON 구문 포함) · URL 에서 가져오기(1MB) · 도구 위험도·스코프·서명 상태 · 사용자별 **임시 저장**(자동) |
| E07 버전 | 두 버전 diff(도구·스코프·엔드포인트·라이선스 +/−/~, 새 고위험 도구 = 재승인) · 버전 고정 · 롤백(확인 필수, 영향 프로파일 수) · 변경 이력(불변 감사) · 재승인·롤백해도 심사 기록(`_anx_review`) 유지 |
| E01·E02 카탈로그 | 퍼지 검색 150ms 디바운스 · `tag:` `auth:` `trust:` `type:` `status:` 토큰 · 인증·신뢰·상태 필터(URL 보존) · 점수순(이름 > 태그 > 도구·스킬) · Agent Card 스킬·보안 스킴 색인과 **일치 힌트** · 유형 탭 건수(같은 조건) · 등록 → 노출 현황 · 상세 탭(개요·원본·도구·버전·사용처·이력, Discovery only 안내) |
| E03 즉시 사용 위저드 | ① 등록(인스턴스)·사전 템플릿에 붙이기 ② 대상별 최소 스코프·짧은 수명 교환 정책 → **시험 교환**(서버가 선언하지 않은 스코프 = MSG-019, 단계 유지) ③ 읽기 허용·**쓰기 명시 허용**·고위험 사람 승인 · 적용 주체·데이터 분류 라우팅·호출 한도 → **Rego 미리보기(dry-run, 저장 안 함)** → 적용(정책 버전·감사) ④ **60초 노출 확인**(호스트 도구 목록·카탈로그) |
| E06 소스 | 허용 네임스페이스(glob) — 밖은 `filtered` · 부분 실패 건수(`partial`)·실패 목록 · **예약 동기화 주기**(5분~7일, 스케줄러 lease) · 다음 동기화 시각 |
| G02·G03 원격 A2A | 등록 위저드(조회·점검 dry-run → 등록 정보 → 결과) · **개발자 등록은 pending, 승인 전 호출 불가(MSG-008)** · 신뢰 등급 지정은 보안 담당 · 카드 캐시(ETag·TTL·다음 재검증)·재검증 이력·색인 |
| D10 플랫폼 Agent Card | 카드 편집(이름·SemVer·설명·capabilities·스킬·스코프) → 검증·미리보기(url 은 게이트웨이 고정) → **게시 요청 → E05 승인 후 공개** · 직접 노출은 승인 권한자만, 중지는 즉시 |
| F03 MCP 설정 | **적용 전 시험**(후보 엔드포인트·인증·시크릿으로 PRM/AS 발견·tools/list·스코프 최소화, 저장 안 함) → 통과해야 적용 |
| H07·H08 정책 | 24시간 허용·거부 건수 · 엔진(내장/OPA) 상태 · **2인 검토**: 초안 → 회귀 시험(등록 케이스 + 최근 24h 실제 요청 재평가) → 게시 요청(사유 MSG-003) → **작성자 외 승인** · 초안 후 정책 변경 시 `stale_draft` · 삭제도 초안으로 · 버전 diff · 롤백(확인·사유) · Rego 미리보기 · 회귀 시험 케이스 관리 · **직접 쓰기는 관리자 비상 경로만(감사 `break_glass`)** |

마이그레이션 0010: `catalog_versions.decided_*·note` 확장, `a2a_agents.owner_team·etag·cache_ttl_s`, `a2a_card_checks`, `agent_profiles.a2a_card·a2a_status`, `policy_drafts`, `policy_tests`, `registry_sources.namespaces·sync_interval_s·last_result`.

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C21-01 | 대기열 점검·도구 위험도(low/high)·건수 → 승인(신뢰·위험도·허용목록 정책) → 새 버전 고위험 도구 diff 재승인 → diff API → 고정 → 롤백 확인 필수 → 이력 | `test_review_queue_risks_trust_allowlist_and_versions` | pytest |
| C21-02 | 서명 필수 정책이면 승인 불가(MSG-007) · 72시간 만료 → 반려 | `test_signature_policy_blocks_approval_and_expiry` | pytest |
| C21-03 | 원문 검증 줄 번호 · 선언 안 된 도구 스코프 · 위험도·스코프 종류 · 임시 저장 사용자별 | `test_registry_validate_inline_and_draft` | pytest |
| C21-04 | A2A dry-run(저장 안 함) · 개발자 등록 pending · partner 사유 필요 · 승인 → 활성·trusted · 재검증 이력 | `test_a2a_registration_request_then_approval` | pytest |
| C21-05 | 카드 SemVer 검증 · 직접 노출 `review_required` · 게시 요청 → 승인 전 404 → 승인 → 공개 카드 | `test_platform_agent_card_editor_and_review` | pytest |
| C21-06 | 직접 쓰기 409 · 시험 실패면 게시 요청 불가 · 사유 필수 · 자기 승인 403 · 승인 → v1 · 수정 → v2 · diff · 롤백(확인·사유) · stale_draft · 비상 경로 감사 · Rego · 삭제 초안(회귀 케이스가 막음 → 케이스 제거 후 게시) | `test_policy_two_person_review_with_regression_tests` | pytest |
| C21-07 | 후보 엔드포인트 실패 · 저장 안 함 · PRM/AS·tools/list 통과 · 과다 스코프 경고 · 없는 서버 404 | `test_mcp_config_test_before_apply` | pytest |
| C21-08 | 등록 전 ② 불가 · 시험 교환 성공·미선언 스코프 MSG-019 · dry-run 저장 안 함(읽기 1·쓰기 차단 1·승인 1) · 적용(규칙·한도·템플릿) · 재적용 버전 증가 · 노출 확인 ≤60s | `test_instant_use_wizard` | pytest |
| C21-09 | `tag:`·`auth:` 토큰 · 점수순 · 일치 힌트 · 신뢰 필터 · 노출 현황 · 네임스페이스 필터·부분 실패·간격 검증 · 예약 동기화 1회(주기 전 재실행 없음) | `test_catalog_search_filters_and_sources` | pytest |
| E21-01~06 | 심사 패널 승인 → 신뢰 필터 · 정책 2인 검토(두 브라우저) · 즉시 사용 4단계 60초 노출 · 카드 편집 → 게시 요청 → 승인 → 공개 · F03 실패면 적용 불가 · A2A 등록 요청 pending | `web/e2e/review.spec.ts` | Playwright |
| P21-01~ | prod: 0010 · 대기열·승인 효과 · 재승인 diff · 롤백 · A2A pending 호출 거부 → 승인 → 활성 · 정책 직접 쓰기 409 → 2인 검토 → 적용(두 워커) · 즉시 사용 노출(두 워커) · F03 저장 안 함 · 카드 게시 심사 | `smoke-prod.sh` | live |
| R | 전체 회귀 (기존 A2A 등록 시험은 승인 단계 추가) | logs | make |

## 환경 가정

- 정책 2인 검토: 정책 권한자(`policy:write`)는 초안·시험·게시 요청을, 다른 정책 권한자가 승인한다. 관리자(`admin`) 직접 쓰기는 장애 대응용 비상 경로로 남기고 감사에 `break_glass` 로 기록한다(PRD 9.2 기존 API 호환).
- 즉시 사용 ③ 의 허용 정책은 그 항목 전용(`tool-allow/<id>` · `agent-allow/<id>`)이며 운영자(`catalog:write`) 권한으로 만든다 — 기존 정책 변경은 H08 경로로만.
- 회귀 시험의 "최근 요청 재평가" 는 사용 기록(최근 24시간, 최대 300건, 주체·대상·등급 중복 제거)을 내장 엔진으로 다시 평가한 근사다. OPA 엔진이면 번들 동기 상태만 보여 준다.
- prod 리허설은 실행 중 만든 임시 계정(`rehearsal` 관리자·`rehearsal-sec` 보안 담당·`rehearsal-dev` 개발자, 비밀번호 출력·저장 안 함)만 쓰고 끝나면 비활성화한다.
