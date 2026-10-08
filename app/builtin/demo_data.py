"""데모 참조 서버용 가상 데이터 (UI 설계서 와이어프레임과 같은 가상 조직 '한결테크' · 프로젝트 ORION). 실제와 무관."""

JIRA_ISSUES = [
    {"key": "ORI-1440", "summary": "결제 샌드박스 연동 차단", "status": "In Progress", "priority": "Highest",
     "assignee": "윤도현", "updated": "2026-09-29", "labels": ["payment", "blocker"],
     "description": "PG 샌드박스 인증서 만료로 결제 승인 테스트가 실패한다. 인증서 갱신 대기."},
    {"key": "ORI-1437", "summary": "주문 API p95 지연 증가", "status": "Open", "priority": "High",
     "assignee": "김서연", "updated": "2026-09-28", "labels": ["performance"],
     "description": "배포 v2.8 이후 /orders 검색 p95 가 420ms → 910ms 로 증가. 인덱스 누락 의심."},
    {"key": "ORI-1431", "summary": "모바일 로그인 세션 만료 오류", "status": "Done", "priority": "Medium",
     "assignee": "박지훈", "updated": "2026-09-27", "labels": ["auth", "mobile"],
     "description": "리프레시 토큰 회전 누락으로 30분 후 강제 로그아웃. 핫픽스 배포 완료."},
    {"key": "ORI-1429", "summary": "정산 배치 중복 실행", "status": "In Review", "priority": "High",
     "assignee": "이하은", "updated": "2026-09-26", "labels": ["batch", "settlement"],
     "description": "스케줄러 재기동 시 정산 배치가 2회 실행되어 중복 레코드 발생. 분산 락 적용 PR 리뷰 중."},
    {"key": "ORI-1425", "summary": "상품 상세 이미지 CDN 캐시 미적중", "status": "Open", "priority": "Low",
     "assignee": "정민수", "updated": "2026-09-25", "labels": ["cdn"],
     "description": "쿼리스트링 버전 파라미터로 캐시 키가 분산됨."},
    {"key": "ORI-1418", "summary": "고객센터 티켓 자동 분류 정확도 저하", "status": "Open", "priority": "Medium",
     "assignee": "윤도현", "updated": "2026-09-24", "labels": ["ml", "support"],
     "description": "신규 카테고리 추가 후 분류 정확도 91% → 78%. 재학습 필요."},
]

CONFLUENCE_PAGES = [
    {"id": "1001", "space": "ORION", "title": "ORION 결제 아키텍처", "updated": "2026-09-20",
     "body": "결제는 PG 게이트웨이 → 결제 서비스 → 정산 배치 순서로 처리한다. 샌드박스 인증서는 분기마다 갱신한다."},
    {"id": "1002", "space": "ORION", "title": "장애 대응 런북 — 주문 API", "updated": "2026-09-18",
     "body": "p95 지연 경보 시 1) Dynatrace 문제 확인 2) 최근 배포 롤백 검토 3) DB 슬로우쿼리 확인."},
    {"id": "1003", "space": "ORION", "title": "주간 보고 템플릿", "updated": "2026-09-01",
     "body": "## 요약\n## 주요 이슈\n## 다음 주 계획"},
    {"id": "1004", "space": "HR", "title": "휴가 정책", "updated": "2026-08-01",
     "body": "연차는 입사일 기준으로 부여한다."},
]

DYNATRACE_PROBLEMS = [
    {"id": "P-26093", "title": "orders-api 응답 시간 저하", "service": "orders-api", "status": "OPEN",
     "severity": "PERFORMANCE", "started": "2026-09-28T09:12:00+09:00",
     "root_cause": "orders 테이블 created_at 인덱스 누락 — 배포 v2.8 마이그레이션 실패",
     "impacted": ["checkout-web", "mobile-app"]},
    {"id": "P-26088", "title": "payment-sandbox TLS 핸드셰이크 실패", "service": "payment-gateway", "status": "OPEN",
     "severity": "ERROR", "started": "2026-09-27T14:03:00+09:00", "root_cause": "샌드박스 인증서 만료",
     "impacted": ["payment-service"]},
    {"id": "P-26071", "title": "settlement-batch CPU 포화", "service": "settlement-batch", "status": "CLOSED",
     "severity": "RESOURCE", "started": "2026-09-25T02:00:00+09:00", "root_cause": "중복 실행",
     "impacted": ["settlement-db"]},
]

METRICS = {
    ("orders-api", "p95_latency_ms"): [420, 430, 445, 610, 880, 905, 910, 902],
    ("orders-api", "error_rate_pct"): [0.2, 0.2, 0.3, 0.9, 1.4, 1.5, 1.3, 1.4],
    ("payment-gateway", "error_rate_pct"): [0.1, 0.1, 12.5, 13.0, 12.8, 13.1, 12.9, 13.2],
}

ORDERS = [
    {"id": "O-20260928-001", "customer": "C-1021", "amount": 128000, "status": "shipped", "created": "2026-09-28"},
    {"id": "O-20260928-002", "customer": "C-1088", "amount": 54000, "status": "payment_failed",
     "created": "2026-09-28"},
    {"id": "O-20260927-017", "customer": "C-1021", "amount": 9900, "status": "delivered", "created": "2026-09-27"},
]
