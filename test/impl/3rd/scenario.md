# 시나리오 / Scenario — impl 3rd · Epic 3 카탈로그·레지스트리 (FR-P01, C00~C03, P04)

| id | 목적 | 입력 | 기대 | 우선 |
|----|------|------|------|------|
| E3-01 | 게시 권한·server.json 검증 (FR-C02) | user 게시 / 잘못된 이름·설명 101자·remotes·packages 없음 | 403 / 422 `invalid_server_json` + 필드별 오류 | P0 |
| E3-02 | 게시 → 승인 → 발견 → 등록 (FR-C00·C01·P01) | dev 게시, operator 승인·등록 | 승인 전 `/v0/servers`·사용자 카탈로그 비노출, 대기열·알림 노출, 승인 후 발견, 등록 시 `mcp_servers` 인스턴스·`/mcp/{ns}` | P0 |
| E3-03 | 버전·멱등·충돌 | 같은 버전 재게시 / 다른 내용 / 새 버전 / 거부 | 멱등 created=false, 409, 승인 전 현재 버전 유지, superseded 이력, 거부 시 기존 승인 유지 | P0 |
| E3-04 | 게시자 서명 (FR-C02 "서명") | Ed25519 신뢰키 + require_signature | 미서명 거부, 서명 OK `signature_verified`, 변조 `invalid_signature` | P0 |
| E3-05 | 미러 (SCR-E06) | 공식 v0 페이지네이션 · Docker YAML · 파일(에어갭) · 장애 소스 | imported 2/invalid 1, 재동기 unchanged, Docker→pending, 파일 import, 502 + last_status error | P0 |
| E3-06 | Agent Card 카탈로그·통합 검색 (FR-C03) | a2a 카드 등록, 스킬 태그·도구명 검색 | 스킬·도구명 검색 적중, type 필터·counts | P1 |
| E3-07 | SBOM·라이선스 등급 (FR-P04) | CycloneDX / summary | specVersion 1.6, fastapi=low, AGPL=high, ELv2·LGPL=medium | P1 |
| E3-08 | PostgreSQL 회귀 | `make test-pg` | 전 케이스 PASS | P0 |
| E3-09 | local curl 스모크 | `smoke.sh` | 9/9 | P0 |
