# impl 59th — 3차 품질 검토 (Epic 59 · D21-④)

## 범위

| 항목 | 내용 |
|------|------|
| 대상 | 11~12차(Epic 52~58·60) 변경분 — 코드의 `D20-`·`D21-` 표시 hunk 와 그 경로 (35개 파일) |
| 위임 검토 | 보안(`/security-review` 폴백 — security-reviewer 서브에이전트) · 결함(`/code-review` 폴백 — code-reviewer 서브에이전트) → `.review/round3/delegated/` 원문 |
| 라인 리뷰 | Lv2(3년 차 인수인계, D20·D21 hunk 블록 모드) — `.review/round3/CODE-REVIEW-Lv2/` (INDEX·파일별 12·INFRA) |
| 검증 | 지적마다 코드 경로를 따라가 판정 — `.review/round3/VERIFY.md`. **확인된 결함만** 고친다 (설계 결정·사용자 결정과 같은 것은 기록) |
| 이전 검토 보존 | `.review/`·`.review/round2/` 는 고치지 않는다 |

## 수용 기준 → 시험 케이스 (확인된 결함)

| # | 결함 (출처) | 수용 기준 | 시험 | 방식 |
|---|-------------|-----------|------|------|
| C59-01 | admin 매핑 IdP 의 `jwks_uri` 등을 보안 담당이 바꿔 자기 JWT 로 admin 획득 · 좁힌 admin PAT 로 admin 부여 (Lv2 H1·L32) | 보안 담당의 신뢰 설정 변경 403 `admin_grant_forbidden` · 이름·중지는 200 · admin 은 200 · admin 매핑 없는 IdP 는 그대로 · admin 역할 + `admin:write` 스코프일 때만 admin | `test_review3_fixes.py::test_admin_mapped_idp_trust_fields_admin_only` | pytest |
| C59-02 | 스트림 중 끊김 — 사용 기록·정산 없음(예산·한도 우회) · 예산 예약이 활동 중 계속 연장돼 누적 (CR3-02·Lv2 H2·L10) | 끊기면 받은 만큼 `cancelled` 사용 기록·예약 정산 · 예산 예약은 예약마다 고정 만료(요청이 계속 와도 300초 뒤 빠짐) · 정산은 사용 기록 커밋 뒤 | `test_stream_disconnect_settles_and_holds_expire` + `test_model_gateway2.py` | pytest |
| C59-03 | 번들 반입이 번들 안 `cosign.pub` 로 검증 — 바꿔치기한 번들이 통과 (Lv2 H3) | `--cosign-key`(번들 밖) 없으면 중단 · 번들 안을 가리키는 키 거부 · 공격자 서명은 운영자 키로 실패 | `test_bundle_import_needs_out_of_band_key` + C54-04 | pytest |
| C59-04 | TOTP 1회 키가 안쪽 공백을 지우지 않음 — `"123 456"` 으로 재사용 (Lv2 M1) | 공백 넣은 같은 코드 400 | `test_totp_reuse_with_space_rejected` | pytest |
| C59-05 | 원격 SSE 한 줄을 끝까지 모은 뒤 길이 검사(메모리 고갈) · 압축 응답 조각 팽창 (Lv2 M3·L34) | 줄바꿈 없는 큰 줄은 상한에서 오류 이벤트 · 요청은 `Accept-Encoding: identity` · 압축 응답 502 | `test_remote_sse_line_cap_and_no_compression` | pytest |
| C59-06 | 재암호화가 A2A 웹훅 토큰을 빠뜨림(이전 키 제거 뒤 중계 실패) · 전 행을 한 트랜잭션 (Lv2 M4·CR3-03·L5) | 웹훅 토큰도 새 키로, 이전 키 제거 뒤 복호화 · 500행 배치 커밋 | `test_reencrypt_covers_push_tokens` + C58-02 | pytest |
| C59-07 | 미러 자동 승인 — 인가 서버 없는 oauth2 항목은 등록 때 업스트림이 인가 서버를 정함 (Lv2 M5) | 고정 안 된 oauth2 보류 · 플랫폼 issuer 고정·인가 없음은 승인 | `test_mirror_holds_unpinned_oauth` + C53-05 | pytest |
| C59-08 | 미러 동기화가 같은 이름의 수동 게시 항목에 버전 추가·자동 승인 (Lv2 M6) | `name_owned_elsewhere` 로 건너뜀 · 항목 버전 그대로 | `test_mirror_sync_skips_items_owned_elsewhere` | pytest |
| C59-09 | 게이트웨이 단독 프로세스가 승인 만료 뒤 Host 실행을 자기 프로세스에서 재개 (Lv2 M7) | Host 없으면 재개 예약 안 함(Host 복구 작업이 이어받음) · all 이면 재개 | `test_resume_only_in_host` | pytest |
| C59-10 | 개요 KPI 자식 스팬 집계가 보존 기간 전체 GROUP BY (Lv2 M10) | 집계 하위 질의에 `start_time >= since` | `test_overview_child_spans_bounded` + C55-01 | pytest |
| C59-11 | 설치가 태그로 서명 검증 뒤 같은 태그의 로컬 이미지·빌드로 기동 가능 (Lv2 M11) | `pull` → 받은 다이제스트마다 `cosign verify` → `up -d --no-build --pull never` · 다이제스트 없으면 중단 | `test_install_verifies_pulled_digests` + C54-05 | pytest |
| C59-12 | 한도 폴백 모델이 없거나 꺼져 있으면 원래 모델로 한도 없이 진행 (Lv2 M13) | 429 (폴백 모델 이름 포함) | `test_limit_fallback_missing_model_is_429` | pytest |
| C59-13 | 에이전트 인증 변경이 교환 토큰 캐시를 못 버림 — 키 형식 불일치·시험은 합성 키 (SEC3-01·CR3-01·L14) | 실제 경로로 채운 `("x", sub, id)` 가 PATCH 뒤 없음 · 커밋 뒤 폐기 | `test_exchange_token_cache_dropped` + C60-04 | pytest |
| C59-14 | 이전 암호화 키·공개키 보관본을 umask 권한으로 만든 뒤 chmod (SEC3-02·SEC3-04·L3) | umask 022 에서도 0600 · 이전 버전이 만든 0644 도 0600 | `test_rotation_files_private` | pytest |
| C59-15 | 백업 서명 검증에 발급자·필수 클레임 없음 (SEC3-03·L4) | 같은 키·다른 `iss` 거부 | `test_backup_signature_requires_backup_issuer` | pytest |
| C59-16 | 인증 없는 푸시 수신이 본문 전체를 상한 없이 읽음 · OTLP 구조 오류 500 (Lv2 L16·L12) | 상한 초과 413(다 읽기 전) · 모르는 토큰 404 · 구조 오류 400 | `test_inbound_caps` + C60-07 | pytest |
| C59-17 | 위험 동사 누락 — overwrite·erase·shutdown·flush·upsert·save·submit·clear (Lv2 M2 일부) | 게이트웨이 high · 심사 제안 high/medium | `test_missing_risk_verbs` + C55-04 | pytest |

기록만(수정 안 함) — 판정 근거는 `.review/round3/VERIFY.md`: M2 나머지(붙여 쓴 소문자·활용형 — 복수 명사 오탐과 맞바꿈) · M8(사용자 결정 "admin 부여는 admin 만") ·
M9 · M12 · CR3-04/L18 · L1·L2·L6~L9·L11·L13·L15·L17·L19~L31·L33·L35 등.

## 완료 게이트 (12차)

| 단계 | 내용 |
|------|------|
| 회귀 | `make test`(SQLite) · `make test-pg` · `make web-test` · web-build + web-e2e · `make helm-check` |
| prod 리허설 | `make prod-build` → `make db-migrate ENV=prod` → `make db-ping ENV=prod` → `smoke-prod.sh`(P59-01~07) + 회귀 사본(36th~51st·55th) → 정리(임시 계정 비활성·토큰 폐기) → `make prod-stop` → `make local-all` |
