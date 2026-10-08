> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../INDEX.md) · 디렉터리 개요: [test_Abstract.md](test_Abstract.md)

### 38. test/conftest.py
**파일 요약 / File Summary**
- 역할 / Role: pytest 공통 설정 — import 전에 환경변수(임시 SQLite·데이터 디렉터리·구성요소·주기 0)를 주입, alembic 으로 스키마 생성·시드, in-process 로 메인 앱 + 데모 MCP 서버 + 데모 A2A + 데모 IdP 를 같은 이벤트 루프에 마운트, 역할별 토큰·헤더 fixture.
- 진입점 거리 / Distance: 테스트 전용(모든 test_*.py 가 `client`·`H`·`tokens` fixture 사용)
- LOC: 106
- 주요 export(fixture): `_schema`(autouse), `tmp_root`, `client`, `tokens`, `H`, `anyio_backend`; 함수 `login`, `approve_agent`
- 의존(내부): `app.builtin.{mcp_servers,a2a_agent}`, `app.core.http`, `app.main`, `app.services.orchestrator`, `app.seed`
- 외부 패키지: pytest, alembic, fastapi.testclient, httpx
- 부수효과 / Side effects: 임시 디렉터리 생성·삭제, 환경변수 설정, transport 마운트, DB 스키마·시드
- 동시성 / Concurrency: TestClient 가 백그라운드 스레드에서 이벤트 루프 구동
- 테스트 커버리지 흔적: 해당 없음(인프라)

## 라인 리뷰 / Line Walk (Level 2)

```python path=test/conftest.py lines=1-28
"""pytest 공통 — 임시 SQLite + 데이터 디렉터리로 앱을 띄우고 alembic 으로 스키마를 만든다.

앱 모듈 import 전에 환경변수를 바꿔야 하므로 이 파일 최상단에서 설정한다.
"""
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="agentnexus-test-")
os.environ.update({
    "APP_ENV": "test",
    # TEST_DATABASE_URL 이 있으면 그 DB(PostgreSQL 등)로 시험 — make test-pg
    "DB_ENGINE": "postgres" if os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql") else "sqlite",
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_TMP}/test.db",
    "DATA_DIR": f"{_TMP}/data",
    "SPAN_PROCESSOR": "simple",
    "RATE_LIMIT_BACKEND": "memory",
    "PUBLIC_BASE_URL": "http://testserver",
    "GATEWAY_URL": "http://testserver",
    "IDP_INTERNAL_URL": "http://testserver",
    "MCP_SERVERS_URL": "http://mcp-servers",
    "A2A_DEMO_URL": "http://a2a-demo",
    "COMPONENTS": "all",
    "MCP_REFRESH_ON_START_S": "0",
    "MCP_HEALTH_INTERVAL_S": "0",
    "SCHEDULER_INTERVAL_S": "0",  # 예약 작업은 시험이 직접 호출
    "A2A_WATCH_INTERVAL_S": "0",
})
```
**분석 / Analysis (Level 2)**
- L1-4: 왜 최상단에서 환경변수를 바꾸는가 — `app` 모듈 import 시점에 `settings`(`app/core/config.py:90`)·`engine`(`app/db.py:50`)이 **고정**되므로 import **전에** 설정해야 한다(설정 싱글턴의 제약을 정확히 반영).
- L9-28: 임시 디렉터리, `TEST_DATABASE_URL` 이 있으면 PostgreSQL(make test-pg), 없으면 임시 SQLite. `SPAN_PROCESSOR=simple`(즉시 export), `RATE_LIMIT_BACKEND=memory`, 내부 주소를 `testserver`·가상 호스트로, 주기 작업 0(시험이 직접 호출).
**짚어볼 점 / Watch-outs**
- L26-27 스케줄러·감시기 주기를 0 으로 꺼서 **시험이 결정적**이다(백그라운드 작업이 상태를 바꾸지 않음) — 좋은 격리.
- `_TMP` 는 모듈 전역 — 세션 끝에 `_schema` fixture 가 지운다.

```python path=test/conftest.py lines=30-53
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402


def _alembic_cfg() -> Config:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _schema():
    command.upgrade(_alembic_cfg(), "head")
    from app import seed
    seed.run()
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def tmp_root() -> str:
    return _TMP

```
**분석 / Analysis (Level 2)**
- L35-38 `_alembic_cfg`: alembic 설정(스크립트 위치).
- L41-47 `_schema`(session·autouse): alembic upgrade head → `seed.run()` → 세션 후 임시 디렉터리 삭제. 실제 마이그레이션으로 스키마를 만든다(모델 `create_all` 이 아니라 — **마이그레이션 정합성까지 검증**).
- L50-52 `tmp_root`.
**짚어볼 점 / Watch-outs**
- L43-45 마이그레이션 + 시드를 세션 1회 — 모든 시험이 같은 DB·같은 시드를 공유한다. 시험 간 상태 변경(예: `test_mcp.py:138` 가 규칙 삭제)이 다른 시험에 영향을 줄 수 있어, 시험 순서 의존이 생길 수 있다(일부 시험이 `try/finally` 로 복원 — `test_mcp.py:295-296`).

```python path=test/conftest.py lines=55-76
@pytest.fixture(scope="session")
def client():
    """메인 앱 + 데모 MCP 서버를 같은 이벤트 루프에서 in-process 로 연결 (compose 의 api·mcp-servers 대응)."""
    import httpx
    from fastapi.testclient import TestClient

    from app.builtin import mcp_servers
    from app.core.http import StreamingASGITransport, register_mount
    from app.main import EMBEDDED, app
    EMBEDDED.append(mcp_servers.app)
    register_mount("http://testserver", async_transport=httpx.ASGITransport(app=app))
    # 서버→클라이언트 요청(sampling·elicitation)이 SSE 응답 도중 오가므로 본문을 흘려 주는 전송을 쓴다
    register_mount("http://mcp-servers", async_transport=StreamingASGITransport(mcp_servers.app))
    from app.builtin import a2a_agent
    register_mount("http://a2a-demo", async_transport=httpx.ASGITransport(app=a2a_agent.app))
    with TestClient(app) as c:
        # 서버 측 동기 호출(SSO 브로커 → 같은 앱의 데모 IdP 디스커버리·토큰·JWKS)도 in-process 로
        from app.core.http import SyncASGITransport
        from app.services import orchestrator
        register_mount("http://testserver", sync_transport=SyncASGITransport(app, lambda: orchestrator.LOOP))
        yield c

```
**분석 / Analysis (Level 2)**
- L57-75 `client`(session): 메인 앱 + 데모 MCP 서버를 같은 루프에 — `EMBEDDED.append(mcp_servers.app)` 로 lifespan 공동 구동, `register_mount` 로 가상 호스트(`testserver`·`mcp-servers`·`a2a-demo`)를 in-process transport 에 연결. MCP 서버는 **StreamingASGITransport**(SSE 역채널 교착 회피), 나머지는 일반 ASGITransport. TestClient 컨텍스트 안에서 동기 호출용 `SyncASGITransport` 도 마운트(SSO 브로커 → 데모 IdP in-process).
**짚어볼 점 / Watch-outs**
- L65 `register_mount("http://testserver", async_transport=httpx.ASGITransport(app=app))` — 게이트웨이가 자기 자신을 부르는 경로(A2A 데모가 게이트웨이 경유)를 in-process 로 닫는다. 실제 네트워크 없이 전체 토폴로지를 재현하는 핵심.
- 이 마운트들은 `register_mount` 가 운영 코드에서도 상시 동작하기에 가능하다(`app/core/http.py` 리뷰의 "운영 가드 없음" 지적의 이면 — 시험 편의와 운영 안전의 trade-off).

```python path=test/conftest.py lines=78-106
def login(client, username: str, password: str | None = None) -> str:
    """test 환경 전용 password grant 로 사용자 액세스 토큰 발급."""
    res = client.post("/idp/token", data={"grant_type": "password", "username": username,
                                          "password": password or f"{username}-pass", "client_id": "test"})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def approve_agent(client, H, agent_id: str) -> None:
    """개발자 등록 원격 에이전트(pending) → 운영자 승인(E05) 으로 활성화."""
    item = client.get(f"/api/v1/a2a/agents/{agent_id}", headers=H["operator"]).json()["catalog_item_id"]
    res = client.post(f"/v1/catalog/{item}/approve", json={"note": "시험 승인"}, headers=H["operator"])
    assert res.status_code == 200, res.text


@pytest.fixture(scope="session")
def tokens(client) -> dict[str, str]:
    return {u: login(client, u) for u in ("admin", "operator", "dev", "security", "finops", "user", "user2")}


@pytest.fixture(scope="session")
def H(tokens):
    """역할별 Authorization 헤더: H['admin'] 등."""
    return {u: {"Authorization": f"Bearer {t}"} for u, t in tokens.items()}


@pytest.fixture
def anyio_backend():
    return "asyncio"
```
**분석 / Analysis (Level 2)**
- L78-83 `login`: test 전용 password grant 로 토큰 발급(`<username>-pass`).
- L86-90 `approve_agent`: 개발자 등록(pending) → 운영자 승인(E05).
- L93-101 `tokens`·`H`: 역할별 토큰·Authorization 헤더 fixture.
- L104-106 `anyio_backend`: asyncio.
**짚어볼 점 / Watch-outs**
- L80-82 password grant 는 `app_env in (local, test)` 에서만 허용(`app/api/idp.py`) — 시험 환경이라 안전. 비밀번호 규칙(`<username>-pass`)은 시드(`app/seed.py:15`)와 짝.
- `tokens` 가 session 범위라 토큰이 세션 내내 재사용된다 — 만료(기본 3600초)보다 시험이 오래 걸리면 401 이 날 수 있다(보통 그 전에 끝남).

## 파일 평가 / File Assessment
- 강점: (1) 설정 싱글턴 제약을 정확히 반영한 import 전 환경 주입. (2) 실제 마이그레이션 + 시드로 스키마 정합성까지 검증. (3) in-process 멀티서비스 토폴로지(StreamingASGITransport 로 SSE 역채널까지) 재현.
- 위험: (1) 세션 범위 공유 DB·시드로 시험 간 상태 결합(순서 의존 가능성). (2) 세션 범위 토큰의 만료 가능성.
- 개선 권고(영향순): (1) 상태를 바꾸는 시험은 `try/finally` 복원 또는 함수 범위 DB 트랜잭션 롤백 fixture. (2) 토큰 fixture 를 필요 시 재발급.
- 재읽기 권고 레벨: 3 — 인프라라 구조만 이해하면 된다.
