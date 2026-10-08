# 시나리오 / Scenario — impl 10th · Epic 10 독립 실행·연동·운영 (FR-P02·W01·W03·P04)

| id | 목적 | 입력 | 기대 | 우선 |
|----|------|------|------|------|
| E10-01 | 구성요소 선택 기동 (FR-W01) | COMPONENTS=gateway,identity / registry,identity / host,gateway,identity | 해당 라우터만 노출 | P0 |
| E10-02 | 시스템 헬스·배포 프로파일 (SCR-B05·L01) | health / profiles | 구성요소·의존성 상태, user 403, 프로파일 6종 | P0 |
| E10-03 | 외부 연동 커넥터 (FR-W03, SCR-L02) | Grafana 연결 시험 / 장애 URL / Kong·APISIX 설정 / Grafana 대시보드 | ok / error, 시크릿 미노출, 게이트웨이 경로 라우팅 설정, anx_* 패널 | P0 |
| E10-04 | 업데이트·사양 버전 (SCR-L04) | updates | migration 0005, MCP 2025-11-25, A2A 0.3·1.0 | P1 |
| E10-05 | 백업·복구 (SCR-L05) | 백업 → 정책 삭제·단가 변경 → 복원 / 변조 번들 | 서명 번들, 원복, 변조 invalid_backup, 개발자 403, 속성명≠컬럼명(metadata) 보존 | P0 |
| E10-06 | 일반 설정 검증 (SCR-L06) | 언어·보존 | 반영, 잘못된 값 400 | P1 |
| E10-07 | 알림 센터 대상·읽음 (SCR-B04) | 역할 지정 알림 | 대상 역할만 노출, 읽음 처리·미읽음 수 | P1 |
| E10-08 | ⌘K 전역 검색 권한 범위 (SCR-B03) | dev/user/admin | 도구·카탈로그 적중, 권한 밖 결과 미노출 | P1 |
| E10-09 | 첫 실행 상태 (SCR-A05~A09) | setup/status·complete | 5단계, 완료 플래그 | P1 |
| E10-10 | PostgreSQL 회귀 | `make test-pg` | PASS | P0 |
| E10-11 | live 독립 실행 | `smoke.sh` | 전체 → 게이트웨이 단독(api+valkey, LLM 프록시만) → 레지스트리 단독 → 복귀, 백업·복구 11/11 | P0 |
