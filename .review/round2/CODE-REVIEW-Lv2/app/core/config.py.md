> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [core_Abstract.md](core_Abstract.md) · 1차 리뷰: [config.py.md](../../../../CODE-REVIEW-Lv2/app/core/config.py.md)

### 03. app/core/config.py
**파일 요약 / File Summary**
- 역할 / Role: 환경변수 기반 설정 싱글턴(`pydantic-settings`), 프로토콜 버전 상수, 구성요소 목록, 기본값 정책.
- 진입점 거리 / Distance: 1 (`app/main.py:32`)
- LOC: 92 (현재 `wc -l`) · 1차 90 → +2
- 주요 export: `settings`, `Settings`, `APP_VERSION`, `MCP_PROTOCOL_VERSION`, `MCP_SUPPORTED_VERSIONS`, `A2A_PROTOCOL_VERSION`, `A2A_SUPPORTED_VERSIONS`, `ALL_COMPONENTS`
- 의존(내부): 없음(최하위). `app/` 안에서 40개 파일이 import(`grep -l "from app.core.config import"` 40건).
- 외부 패키지: pydantic-settings
- 부수효과 / Side effects: import 시점에 환경변수를 읽어 `settings` 생성(L92)
- 동시성 / Concurrency: 없음(읽기 전용 전역 객체로 사용)
- 테스트 커버리지 흔적: `test/conftest.py:9-28` 이 import 전에 환경변수 주입(`APP_ENV=test` L11, 백그라운드 주기 0 L24-27). 기본값 자체(누락 시 동작)를 검증하는 시험은 없음.
- 2차 변경 요약 / Changes since 1st review: L69-70 `a2a_grpc_tls_cert`·`a2a_grpc_tls_key` 추가(Epic 41 D17-② gRPC TLS — `test/impl/41st/result.md:1`). 그 밖의 줄은 1차 인용과 동일. **7~10차(Epic 39~50)에서 새로 생긴 튜닝 값은 설정이 아니라 모듈 상수로 들어갔다**: gRPC 수신 상한 `app/services/a2a_grpc.py:260-264`, 푸시 재시도 `app/core/http.py:156`(`attempts=3, base_delay=0.5`), 데이터 등급 기본값 5초 캐시 `app/services/policy.py:22-35`, refresh 재사용 유예 `app/services/identity.py:77`(`REUSE_GRACE_S = 10`). stdio 허용 목록 의미는 바뀌었지만(공백 포함 항목 = 전체 명령 일치, `app/services/mcp_gateway.py:528-537`) 설정 필드 L73 은 그대로다.

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
- L2: `BaseSettings` 는 필드 이름(대소문자 무시)과 같은 환경변수를 자동으로 읽어 타입 변환한다(`UPSTREAM_TIMEOUT=10` → `float 10.0`). 변환 실패(`MCP_HEALTH_INTERVAL_S=abc`)는 import 시 `ValidationError` 로 기동 실패 — 빠른 실패라 좋다.
- L4-8: 프로토콜 버전 협상 테이블(MCP 최신 + 이전 2개, A2A 1.0 + 0.3).
- L10: 선택 기동 가능한 구성요소 7개(FR-W01). `enabled()`(L86-89)는 이 튜플로 입력을 검증하지 않는다(아래).

```python path=app/core/config.py lines=13-20
class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_name: str = "wise-agentnexus"
    app_env: str = "local"
    db_engine: str = "sqlite"
    database_url: str = "sqlite:////app/data/app.db"
    data_dir: str = "/app/data"
```
**분석 / Analysis (Level 2)**
- L14 `extra="ignore"`: 모르는 환경변수가 있어도 무시 — 다른 서비스용 변수가 섞인 `.env` 로도 기동된다. 반대로 **오타 난 변수도 조용히 무시**된다(`SEED_DEMOO=false` 는 적용되지 않음).
- L17 `app_env`: 아래 Watch-out.
- L18 `db_engine`: 표시 전용 문자열 — 실제 엔진은 `database_url` 로 결정된다(`app/db.py:42-52`). 사용처는 `/health`·시스템 정보 표시뿐(`app/main.py:202`, `app/api/system.py:19`).
- L19-20: 기본 DB·데이터 디렉터리는 컨테이너 경로. 호스트에서 기본값으로 import 하면 `app/db.py:45` 가 `/app/data` 생성을 시도해 권한 오류가 날 수 있다.
**짚어볼 점 / Watch-outs**
- [위험:중간] L17 `app_env = "local"` 기본값은 **fail-open** 이며 2차에서 영향 범위가 넓어졌다. 이 값이 `local`/`test` 이면 켜지는 것(현재 디스크 확인): (a) 예측 가능한 개발 계정 `<username>-pass` 생성(`app/seed.py:37-39`) — 목록에 **`admin`(admin 역할)** 이 포함된다(`app/seed.py:16-24`), 즉 `admin`/`admin-pass` 로 관리자 로그인 가능, (b) OAuth password grant 광고·허용(`app/api/idp.py:40`·`:326`), (c) `http://localhost` redirect_uri 허용(`app/api/idp.py:65`, 2차 SEC-05 수정이 이 값에 의존), (d) 데모 외부 IdP 라우터·시드(`app/main.py:162`, `app/seed.py:295`), (e) **푸시 URL 의 사설·루프백 차단(SSRF 가드) 해제**(`app/services/a2a_gateway.py:644`, 2차 SEC-04 수정이 이 값에 의존). 시나리오: 운영자가 Helm/compose 템플릿 대신 직접 만든 매니페스트·`docker run`·호스트 `uvicorn` 으로 띄우며 `APP_ENV` 를 빠뜨리면, 공격자가 `admin`/`admin-pass` 개발 계정으로 password grant 로그인하고 A2A 푸시 URL 로 내부 서비스(db·valkey)를 겨냥할 수 있다. 완화: 제공 템플릿은 모두 `prod` 를 명시(`deploy/helm/agentnexus/templates/configmap.yaml:9`, `.env.prod:3`, `.env.example:2`). 최소 수정: 기본값을 `"prod"` 로 바꾸고 개발 compose·`.env.local:2`(이미 `APP_ENV=local`)·`test/conftest.py:11`(이미 `test`)가 명시하게 한다 — 기존 개발·시험 경로는 이미 값을 주므로 회귀 위험이 낮다.
- [위험:낮음] L17·L18(그리고 L42·L44·L60) 열거형 설정이 자유 문자열이라 오타가 조용히 다른 동작으로 떨어진다. 예: `RATE_LIMIT_BACKEND=valkey` 는 `== "redis"` 비교(`app/services/ratelimit.py:155`)에 걸리지 않아 워커별 메모리 버킷으로 폴백(다중 워커에서 한도가 워커 수만큼 느슨해짐). `APP_ENV=production` 은 위 (a)~(e) 의 `not in ("local","test")` 판정에서는 운영으로, `app/api/a2a.py:542` 의 `== "prod"` 판정(OAuth2 token_url https 강제)에서는 비운영으로 취급돼 판정이 엇갈린다. 최소 수정: `app_env: Literal["local","test","dev","staging","prod"]`, `rate_limit_backend: Literal["memory","redis"]` 등으로 타입을 좁혀 기동 시 거부.

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
- L23: `all` 또는 쉼표 목록(FR-W01).
- L25-29: 공개 기준 URL(OIDC issuer·카드 url 근거), 호스트→게이트웨이 내부 주소, IdP JWKS 내부 주소 — 외부 도메인과 컨테이너 네트워크 이름이 다른 분리 DNS 대응.
- L32-33: 서명 키·암호화 키 — 비우거나 `CHANGE_ME` 면 `data_dir/keys` 에 자동 생성(`app/core/keys.py:43-46` `_configured` 가 두 값을 모두 "미설정" 으로 본다 — 2차에서 확인, CLI `write_env` 수정과 이중 방어).
- L36-39: 내장 IdP·부트스트랩 관리자 비밀번호·토큰 수명(초).
- L42-49: 게이트웨이 — 레이트리밋 백엔드·정책 엔진·업스트림 타임아웃·동의 대기·원격 Task 대기·첨부 크기.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L32-33·L37(그리고 L58-59·L71) 시크릿이 일반 `str`(1차 지적 그대로). 저장소 검색에서 `settings` 를 통째로 덤프·로그하는 코드는 없어(`repr(settings`·`settings.model_dump` 0건) 실피해 경로는 확인되지 않았다. 추가로 `CHANGE_ME` placeholder 를 "미설정" 으로 보는 처리가 5곳에 흩어져 있고 공백 처리도 다르다: `app/core/keys.py:43-46`(strip), `app/core/tracing.py:102`(strip), `app/api/observe.py:38`(strip 없음), `app/seed.py:187`, `app/builtin/a2a_agent.py:55`. 최소 수정: `SecretStr` + `field_validator` 하나에서 `""`/`CHANGE_ME` 를 `None` 으로 정규화.
- L42 `rate_limit_backend="memory"` 기본은 다중 워커에서 한도가 느슨해지지만, 배포 템플릿은 모두 `redis` 로 덮는다(`deploy/helm/agentnexus/values.yaml:51`, `.env.prod:28`, `.env.local:21`).

```python path=app/core/config.py lines=51-61
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
```
**분석 / Analysis (Level 2)**
- L52-54: 스케줄러 주기(0 = 끔, `app/services/scheduler.py:133-134`)·보존 점검 주기·실행 정체 판정(초).
- L57-61: OTLP 외부 Collector, 내장 수신 토큰(비면 `data_dir/keys/otlp-ingest.token` 자동 생성, `app/core/tracing.py:98-112`), `/metrics` 토큰(비면 observe:read 사용자 토큰만, `app/api/observe.py:38`), 스팬 프로세서, 로그 레벨.
- 자동 생성 OTLP 토큰 파일은 `0o644` 로 만들어진다(`app/core/tracing.py:109`) — 공유 볼륨에서 다른 컨테이너가 읽게 하려는 의도이지만 같은 호스트 다른 사용자도 읽을 수 있다(교차 확인 대상: tracing 보고서).

```python path=app/core/config.py lines=63-84
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
```
**분석 / Analysis (Level 2)**
- L64-65: 내장 참조 서비스 주소(컨테이너 DNS 이름).
- L67-68: gRPC 카드 광고 주소·수신 주소. `0.0.0.0` 은 컨테이너 안에서 의도된 값(호스트 직접 실행 시 외부 노출 주의).
- L69-70 (2차 신규): gRPC 서버 TLS 인증서·키 **파일 경로**. 둘 다 있으면 `grpc.ssl_server_credentials` 로 수신(`app/services/a2a_grpc.py:251-256`, `:271`), Helm 은 `a2aGrpc.tls.secretName` 이 있을 때 `/tls/tls.crt|key` 를 넣는다(`deploy/helm/agentnexus/templates/configmap.yaml:33-34`).
- L71: 데모 에이전트 워크로드 시크릿(K8s Secret 주입용).
- L73: stdio 허용 목록 — 비면 stdio 전송 전면 금지(안전 기본값). 2차에서 항목 의미가 바뀌었다: 공백 없는 항목은 실행 파일만 비교, 공백 있는 항목은 명령+인자 전체 일치(`app/services/mcp_gateway.py:528-537`). 실행 파일만 적은 항목(`python`·`npx`)은 인자로 임의 코드 실행을 허용하므로 운영은 전체 명령으로 고정하라고 호출 대상 docstring 이 경고한다 — 설정 쪽 주석에는 이 형식 설명이 없다.
- L75-78: 기동 후 도구 조회 재시도·헬스 주기·A2A 감시 주기(0 = 끔, `app/main.py:101-107`).
- L81·L84: 자동 마이그레이션, 콘솔 정적 파일 경로.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L72 `seed_demo = True` 기본(1차 지적 그대로). 2차 확인: Helm 차트·compose·env 템플릿 어디에도 `SEED_DEMO` 가 없다(`deploy/`·`docker-compose*.yml`·`.env.*` 검색 0건). 따라서 Helm 에서 `demo.enabled: false`(`deploy/helm/agentnexus/values.yaml:104-105`)로 데모 서비스를 끄더라도 시드는 계속 데모 MCP 서버·카탈로그·원격 A2A 에이전트와 그 **워크로드 신원(client_credentials)**을 만든다(`app/seed.py:136-140`·`:162-165`·`:312-314`·`:355`). 결과: 존재하지 않는 `mcp-servers:8100` 을 헬스 루프가 계속 점검(unhealthy)하고, 운영 DB 에 데모 자격 증명이 남는다. 최소 수정: 기본 `False`, 또는 Helm configmap 에 `SEED_DEMO: {{ .Values.demo.enabled }}`.
- [위험:낮음] L69-70 둘 중 **하나만** 설정하면(예: Secret 마운트 실수로 key 경로만 빠짐) `server_tls()` 가 `None` 을 돌려 경고 없이 평문 수신으로 떨어진다(`app/services/a2a_grpc.py:253-256`, `:271`). fail-open 설정 조합. 최소 수정: `model_validator` 로 "둘 다 있거나 둘 다 없음" 강제(또는 a2a-grpc 기동 시 거부).

```python path=app/core/config.py lines=86-92
    def enabled(self, component: str) -> bool:
        if self.components.strip() in ("", "all"):
            return True
        return component in {c.strip() for c in self.components.split(",")}


settings = Settings()
```
**분석 / Analysis (Level 2)**
- L86-89 `enabled()`: `all`/빈 값이면 전부 활성, 아니면 쉼표 목록 포함 여부. 호출은 기동 시 위주라 매번 set 을 만드는 비용은 무시할 수준.
- L92: 모듈 수준 싱글턴 — import 시점에 고정된다. 시험이 설정을 바꾸려면 import **전에** 환경변수를 바꿔야 한다(`test/conftest.py:9-28`). 런타임 `settings.x = ...` 는 이미 값을 복사해 둔 객체(예: `app/db.py:55` 의 `engine`)에 반영되지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L89 입력 검증·정규화가 없다(1차의 대소문자 지적 + 2차 보강). `COMPONENTS=gateway,idnetity` 처럼 오타가 있으면 `identity` 가 조용히 꺼져 내장 IdP·사용자 API 없이 기동되고, `Gateway` 처럼 대소문자가 다르면 아무 구성요소도 켜지지 않는다(시스템 라우터만). 최소 수정: 기동 시 `set(parts) - set(ALL_COMPONENTS)` 가 비어 있지 않으면 `ValueError`, 비교는 `.lower()`.

## 파일 평가 / File Assessment
- 강점: (1) 모든 설정이 한 곳·타입 지정, 숫자 변환 실패는 기동 시 거부. (2) 위험 기능(stdio)의 기본값이 금지, 키는 비우면 자동 생성. (3) 분리 DNS·내부 주소 개념이 명시적.
- 위험: (1) [위험:중간] `app_env` 기본 `local` fail-open — 2차에서 SSRF 가드·redirect 검사까지 이 값에 묶여 영향 확대 — `app/core/config.py:17`. (2) [위험:낮음] 열거형 설정 자유 문자열·구성요소 이름 미검증 — `app/core/config.py:17-18,42,86-89`. (3) [위험:낮음] `seed_demo` 기본 True(Helm 미설정)·gRPC TLS 반쪽 설정 시 평문 — `app/core/config.py:69-72`.
- 개선 권고(영향순): (1) `app_env` 기본 `prod` + `Literal` 타입. (2) `model_validator` 로 TLS 쌍·구성요소 이름·placeholder 정규화를 한 곳에서. (3) 시크릿 `SecretStr`. (4) 7~10차에서 상수로 들어간 운영 튜닝 값(gRPC 상한·푸시 재시도·캐시 TTL)을 필요 시 설정으로 노출할지 결정(현재는 코드 수정 없이는 조정 불가).
- 파일 위험 등급 / Risk grade: 중간 — 미해결 [위험:중간] 1건(`app_env` fail-open), 나머지는 낮음.
- 재읽기 권고 레벨: 3 — 설정 목록이므로 기본값 정책과 `app_env` 를 참조하는 판정 지점만 다시 보면 된다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| [중간] L17 `app_env` 기본 `local` fail-open(개발 계정·password grant·데모 IdP) | 미해결(영향 확대) | L17 그대로; 판정 지점 `app/seed.py:37`·`:295`, `app/api/idp.py:40`·`:65`·`:326`, `app/main.py:162`, `app/services/a2a_gateway.py:644` |
| `model_config` 에 `env_file` 없음(compose 로만 주입) | 미해결(의도된 설계 — 결함 아님) | L14 |
| 시크릿 필드가 평문 `str` → `SecretStr` 권장 | 미해결 | L32-33·L37·L58-59·L71 |
| L42 `rate_limit_backend="memory"` 기본 → 다중 워커 한도 완화 | 미해결(배포 템플릿이 `redis` 로 완화) | L42; `deploy/helm/agentnexus/values.yaml:51`, `.env.prod:28` |
| [낮음] L70 `seed_demo=True` 기본 | 미해결 | L72; Helm·compose·env 템플릿에 `SEED_DEMO` 없음 |
| L68 `a2a_grpc_listen=0.0.0.0` | 미해결(컨테이너 내 의도된 값) | L68 |
| 싱글턴 import 시점 고정 — 런타임 변경 미반영 | 미해결(설계) | L92 |
| `enabled("Gateway")` 대소문자 미정규화 | 미해결 | L86-89 |
