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
