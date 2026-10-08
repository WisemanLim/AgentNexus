> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [core_Abstract.md](core_Abstract.md)

### 09. app/core/config.py
**파일 요약 / File Summary**
- 역할 / Role: 환경변수 기반 설정 싱글턴(`pydantic-settings`). 프로토콜 버전 상수, 구성요소 목록, 기본값.
- 진입점 거리 / Distance: 1 (`app/main.py:31`)
- LOC: 90
- 주요 export: `settings`, `Settings`, `APP_VERSION`, `MCP_PROTOCOL_VERSION`, `MCP_SUPPORTED_VERSIONS`, `A2A_PROTOCOL_VERSION`, `A2A_SUPPORTED_VERSIONS`, `ALL_COMPONENTS`
- 의존(내부): 없음(최하위 모듈 — 내부 in-degree 40, 가장 많이 import 되는 모듈 중 하나)
- 외부 패키지: pydantic-settings
- 부수효과 / Side effects: import 시점에 환경변수를 읽어 `settings` 생성(L90)
- 동시성 / Concurrency: none(읽기 전용으로 쓰이는 전역 객체)
- 테스트 커버리지 흔적: `test/conftest.py:10-28` 이 import 전에 환경변수를 바꿔 주입

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/config.py lines=1-10
"""wise-agentnexus — settings (env 주입: .env.local | .env.prod 는 compose env_file 로 전달)."""
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"
MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
A2A_PROTOCOL_VERSION = "1.0"  # D15-②: 1.0 기본 — 0.3(message/send…)은 협상으로 계속 수용
A2A_SUPPORTED_VERSIONS = ("1.0", "0.3")

ALL_COMPONENTS = ("identity", "registry", "gateway", "host", "observe", "admin", "console")
```
**분석 / Analysis (Level 2)**
- L2: `BaseSettings` 는 필드 이름(대소문자 무시)과 같은 환경변수를 자동으로 읽어 타입 변환한다. 예: `UPSTREAM_TIMEOUT=10` → `upstream_timeout: float = 10.0`.
- L4: 앱 버전 상수 — FastAPI 문서·감사·이벤트에 사용.
- L5-8: 프로토콜 버전 협상 테이블. MCP 는 최신(2025-11-25) 기본 + 이전 2개 수용, A2A 는 1.0 기본 + 0.3 수용. 2026-07-28(modern MCP)은 별도 상수(`app/services/mcp_modern.py:27`)로 관리된다.
- L10: 선택 기동 가능한 구성요소 7개(FR-W01).

```python path=app/core/config.py lines=13-21
class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_name: str = "wise-agentnexus"
    app_env: str = "local"
    db_engine: str = "sqlite"
    database_url: str = "sqlite:////app/data/app.db"
    data_dir: str = "/app/data"

```
**분석 / Analysis (Level 2)**
- L14 `extra="ignore"`: 알 수 없는 환경변수가 있어도 오류 없이 무시 — `.env` 파일에 다른 서비스용 변수가 섞여도 기동된다.
- L17 `app_env = "local"`: 기본 환경.
- L19-20: 기본 DB·데이터 디렉터리는 컨테이너 경로(`/app/data`).
**짚어볼 점 / Watch-outs**
- [위험:중간] L17 기본값이 `"local"` 인 것은 **fail-open** 이다. `APP_ENV` 를 빠뜨린 운영 배포에서 (a) 예측 가능한 개발 계정(`<username>-pass`, `app/seed.py:15,37-39`) 생성, (b) 테스트용 password grant 허용(`app/api/idp.py:318`(14:21 재확인)), (c) 데모 외부 IdP 라우터 장착(`app/main.py:156-158`)이 모두 켜진다. 안전한 기본값은 `"prod"` 이고 개발 환경이 명시적으로 `local` 을 주는 쪽이다.
- `model_config` 에 `env_file` 이 없다 — `.env` 파일은 compose 의 `env_file:` 로만 주입된다. 호스트에서 직접 `uvicorn` 을 띄우면 `.env.local` 이 읽히지 않는다(의도된 설계이나 주의).

```python path=app/core/config.py lines=22-49
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
```
**분석 / Analysis (Level 2)**
- L23 `components = "all"`: 쉼표 목록 또는 all.
- L25-29: 공개 기준 URL(OIDC issuer·카드 url 의 근거), 호스트→게이트웨이 내부 주소, IdP JWKS 내부 주소 — 분리 DNS(외부 도메인 ≠ 컨테이너 네트워크 이름) 대응.
- L32-33: 서명 키·암호화 키 — 비우면 `data_dir/keys` 에 자동 생성(`app/core/keys.py:49-79`).
- L36-39: 내장 IdP·부트스트랩 관리자 비밀번호·토큰 수명(초).
- L42-49: 게이트웨이 — 레이트리밋 백엔드(memory/redis), 정책 엔진(builtin/opa), 업스트림 타임아웃, 동의 대기, 원격 Task 대기, 첨부 크기.
**짚어볼 점 / Watch-outs**
- 시크릿 필드(L32-33·L37)가 일반 `str` — `pydantic.SecretStr` 를 쓰면 `repr(settings)`·로그에 값이 찍히는 사고를 막는다. 현재 `settings` 를 통째로 로그에 남기는 코드는 확인되지 않았지만(이 리뷰 범위), 방어적 타입이 낫다.
- L42 기본 `rate_limit_backend="memory"` 는 워커별 버킷 — 다중 워커에서 한도가 워커 수만큼 느슨해진다(운영 `.env.example` 은 `redis`).

```python path=app/core/config.py lines=51-82
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
```
**분석 / Analysis (Level 2)**
- L52-54: 스케줄러 주기·보존 점검 주기·실행 정체 판정(초). `0` 이면 끈다(`app/services/scheduler.py:133-134`).
- L57-61: 관측 — OTLP 외부 Collector, 내장 수신 토큰, `/metrics` 토큰, 스팬 프로세서 종류, 로그 레벨.
- L64-71: 내장 참조 서비스 주소, gRPC 공개 주소·수신 주소, 데모 워크로드 시크릿, 데모 시드 여부, stdio 허용 명령.
- L73-79: 기동 시 도구 조회 재시도·헬스 주기·A2A 감시 주기·자동 마이그레이션.
- L82: 콘솔 정적 파일 경로.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L70 `seed_demo = True` 기본 — 운영에서도 끄지 않으면 데모 MCP 서버·에이전트·카탈로그가 시드된다(`app/seed.py:140,165,314,355`). 운영 기본값은 `False` 가 안전하다.
- L68 `a2a_grpc_listen = "0.0.0.0:50051"` 모든 인터페이스 바인딩 — 컨테이너 안이라 의도된 값이지만, 호스트에서 직접 실행하면 외부에 열린다.
- L71 `mcp_stdio_allowed_commands` 가 비면 stdio 전송 전면 금지(`app/services/mcp_gateway.py:564-567`) — 안전한 기본값(좋음).

```python path=app/core/config.py lines=84-90
    def enabled(self, component: str) -> bool:
        if self.components.strip() in ("", "all"):
            return True
        return component in {c.strip() for c in self.components.split(",")}


settings = Settings()
```
**분석 / Analysis (Level 2)**
- L84-87 `enabled()`: `all` 또는 빈 값이면 전부 활성, 아니면 쉼표 목록 포함 여부. 매 호출마다 set 을 만들지만 호출 빈도가 낮아(기동 시) 문제없다.
- L90: 모듈 수준 싱글턴 — `from app.core.config import settings` 로 어디서나 같은 객체.
**짚어볼 점 / Watch-outs**
- 싱글턴이 import 시점에 고정되므로, 시험에서 설정을 바꾸려면 import **전에** 환경변수를 설정해야 한다(`test/conftest.py:1-28` 이 그렇게 한다). 런타임에 `settings.x = ...` 로 바꾸면 이미 값을 복사해 둔 모듈(예: `db.engine`)에는 반영되지 않는다.
- `enabled("Gateway")` 처럼 대소문자가 다르면 False — 입력 정규화(`.lower()`) 없음.

## 파일 평가 / File Assessment
- 강점: (1) 모든 설정이 한 곳·타입 지정. (2) 위험 기능(stdio)의 기본값이 금지. (3) 분리 DNS·내부 주소 개념이 명시적.
- 위험: (1) [중간] `app_env` 기본 `local` — 누락 시 개발 계정·password grant·데모 IdP 가 켜지는 fail-open. (2) [낮음] `seed_demo` 기본 True. (3) 시크릿 필드가 평문 str.
- 개선 권고(영향순): (1) `app_env` 기본을 `prod` 로, 개발 compose 가 `local` 명시. (2) `seed_demo` 기본 False. (3) 시크릿 필드에 `SecretStr`.
- 재읽기 권고 레벨: 3 — 설정 목록이므로 기본값 정책만 다시 보면 된다.
