"""wise-agentnexus — settings (env 주입: .env.local | .env.prod 는 compose env_file 로 전달)."""
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"
MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
A2A_PROTOCOL_VERSION = "1.0"  # D15-②: 1.0 기본 — 0.3(message/send…)은 협상으로 계속 수용
A2A_SUPPORTED_VERSIONS = ("1.0", "0.3")

ALL_COMPONENTS = ("identity", "registry", "gateway", "host", "observe", "admin", "console")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_name: str = "wise-agentnexus"
    app_env: str = "local"
    db_engine: str = "sqlite"
    database_url: str = "sqlite:////app/data/app.db"
    # PostgreSQL 세션 상한 (D22-①, 0 = 끔) — 오래 걸리는 문장·잠금 대기가 풀 연결을 붙잡지 않게. 마이그레이션은 상한 없이 실행
    db_statement_timeout_ms: int = 120_000
    db_lock_timeout_ms: int = 15_000
    # 트랜잭션을 연 채 쉬는 연결 종료 — 요청 세션이 스트림·원격 대기 동안 열려 있을 수 있어 기본 끔 (운영에서 켤 때는 최장 스트림보다 길게)
    db_idle_tx_timeout_ms: int = 0
    # 주체별 동시 모델 요청 상한 (D22-③, 0 = 끔) — 큰 max_tokens 요청을 동시에 많이 열어 팀 예산 예약을 잠그지 않게
    model_max_inflight_per_subject: int = 32
    data_dir: str = "/app/data"

    # 구성요소 선택 기동 (FR-W01) — "all" 또는 쉼표 목록
    components: str = "all"
    # 외부에서 보이는 기준 URL (OIDC issuer·Agent Card url 등)
    public_base_url: str = "http://127.0.0.1:8000"
    # Host → Gateway 호출 대상 (비우면 같은 프로세스의 public 주소 대신 내부 주소 사용)
    gateway_url: str = "http://127.0.0.1:8000"
    # 내부 서비스가 IdP JWKS 를 조회할 주소 (컨테이너 네트워크)
    idp_internal_url: str = "http://127.0.0.1:8000"

    # 키 (비우면 data_dir/keys 에 자동 생성·보존)
    signing_key_pem: str = ""
    encryption_key: str = ""
    signing_key_pem_previous: str = ""  # env 로 키를 줄 때의 회전 — 직전 서명 키(PEM, 여러 개 이어 붙임) (D21-③)
    encryption_key_previous: str = ""   # 직전 암호화 키(쉼표 구분) — 재암호화가 끝나면 비운다
    # env 모드 2단계 회전 (D22-②) — 다음 키를 먼저 모든 파드에 배포(검증·복호화만) → 현재 키로 올림: 롤링 중 401·복호화 실패 없음
    signing_key_pem_next: str = ""      # 다음 서명 키 PEM(개인·공개) — JWKS·검증에만, 서명은 아직 안 함
    encryption_key_next: str = ""       # 다음 암호화 키(쉼표) — 복호화만
    signing_key_previous_until: str = ""  # env 이전 서명 키의 토큰 검증 끝(ISO 8601) — 지나면 백업·보고서 서명 검증에만
    signing_revoked_kids: str = ""      # 폐기 kid(쉼표) — 유출 키: 토큰·백업·보고서 어디서도 검증하지 않음

    # 내장 IdP
    builtin_idp: bool = True
    bootstrap_admin_password: str = ""
    access_token_ttl: int = 3600
    exchange_token_ttl: int = 300

    # 게이트웨이
    rate_limit_backend: str = "memory"  # memory | redis
    valkey_url: str = ""
    policy_engine: str = "builtin"  # builtin | opa
    opa_url: str = ""
    upstream_timeout: float = 30.0
    mcp_consent_timeout_s: float = 60.0  # MCP sampling·elicitation 동의 대기 (SCR-C08: 무응답 시 자동 거부)
    a2a_remote_wait_s: float = 120.0  # 원격 Task 가 working 이면 tasks/get 으로 기다리는 최대 시간 (FR-R03)
    attachment_max_mb: int = 20

    # 예약 작업 (FR-O02·O03) · 실행 복구 (NFR-S1)
    scheduler_interval_s: int = 60  # 0 = 끔
    retention_interval_s: int = 3600
    run_stale_s: int = 90

    # 관측
    otlp_endpoint: str = ""
    otlp_ingest_token: str = ""  # 내장 OTLP 수신기 토큰 (비우면 data_dir/keys 자동 생성·공유)
    metrics_token: str = ""  # /metrics 스크레이프 토큰 (비우면 observe:read 사용자 토큰만)
    span_processor: str = "batch"  # batch | simple
    log_level: str = "INFO"

    # 내장 참조 서비스 (데모 MCP 서버·데모 원격 A2A 에이전트)
    mcp_servers_url: str = "http://mcp-servers:8100"
    a2a_demo_url: str = "http://a2a-demo:8200"
    # A2A gRPC 바인딩 (D16-②) — 카드에 광고할 host:port(비우면 GRPC 인터페이스 미광고) · 수신 대기 주소(a2a-grpc 서비스)
    a2a_grpc_public_url: str = ""
    a2a_grpc_listen: str = "0.0.0.0:50051"
    a2a_grpc_tls_cert: str = ""  # PEM 파일 경로 — cert·key 가 모두 있으면 TLS 로 수신 (D17-②), 없으면 평문(앞단 TLS 종료)
    a2a_grpc_tls_key: str = ""
    a2a_demo_client_secret: str = ""  # 데모 에이전트 워크로드 시크릿(K8s Secret 주입) — 비우면 data_dir/workloads 파일
    seed_demo: bool = True
    mcp_stdio_allowed_commands: str = ""
    # 기동 후 참조 MCP 서버 도구 조회 재시도(초) · 헬스 점검 주기(초, 0=끔)
    mcp_refresh_on_start_s: int = 90
    mcp_health_interval_s: int = 60
    # A2A 장기 Task 감시 주기(초, 0=끔)
    a2a_watch_interval_s: int = 5

    # 기동 시 마이그레이션·시드 (entrypoint)
    auto_migrate: bool = True

    # 콘솔 정적 파일 (Vite 빌드 결과)
    console_dist: str = "/app/web/dist"

    def enabled(self, component: str) -> bool:
        if self.components.strip() in ("", "all"):
            return True
        return component in {c.strip() for c in self.components.split(",")}


settings = Settings()
