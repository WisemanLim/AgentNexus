"""Epic 66 — 남은 기록 항목 2 (D22-⑥, 3차 검토 VERIFY '기록' 중 결함·하드닝): 프로브 리슨 주소 · listen 반납 비차단 ·
IdP 내장 issuer 거부·세션 종료 기준 통일 · a2a-grpc readiness 완화·TLS 오버레이 예시 · CLI 설정 디렉터리 0700 ·
번들 반입 구성 복원 기본 끔 · 헬스 요청 varint."""
import asyncio
import json
import os
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

from app.cli import nexus
from app.core import security
from app.core.config import settings
from app.db import SessionLocal, utcnow
from app.grpc_server import probe
from app.models import RefreshToken, User
from app.services import a2a_grpc, mcp_modern, ratelimit
from test.test_cli_install import cli_env, run  # noqa: F401 — 픽스처

ROOT = Path(__file__).resolve().parents[1]


# ── C66-01 프로브 — 특정 주소로만 수신하면 그 주소로 (전에는 늘 127.0.0.1), 와일드카드면 루프백 ──
def test_probe_uses_listen_host(client, monkeypatch):
    server = client.portal.call(a2a_grpc.serve, "127.0.0.1:0", "http://127.0.0.1:9")
    try:
        port = server.bound_port
        got = {}
        for listen in (f"127.0.0.1:{port}", f"0.0.0.0:{port}", f"127.0.0.9:{port}"):
            monkeypatch.setattr(settings, "a2a_grpc_listen", listen)
            got[listen.split(":")[0]] = probe("")
    finally:
        client.portal.call(server.stop, 0)
    assert got == {"127.0.0.1": 0, "0.0.0.0": 0, "127.0.0.9": 1}  # 127.0.0.9 에는 수신이 없다 — 설정 주소로 갔다는 뜻


# ── C66-02 listen 반납 — Valkey 면 스레드에 넘기고 기다리지 않음(이벤트 루프가 왕복만큼 멈추지 않게), 메모리는 즉시 ──
def test_listen_release_does_not_block_loop(monkeypatch):
    done = []

    class SlowRedis(ratelimit.RedisBackend):
        def __init__(self):  # 연결 없이
            pass

        def holder_release(self, key, member):
            time.sleep(0.3)
            done.append(threading.current_thread() is not threading.main_thread())
    monkeypatch.setattr(ratelimit, "backend", lambda: SlowRedis())

    async def scenario():
        t0 = time.perf_counter()
        mcp_modern.listen_release("usr_e66", "h1")
        took = time.perf_counter() - t0
        await asyncio.sleep(0.5)
        return took
    assert asyncio.run(scenario()) < 0.1 and done == [True]
    mem = ratelimit.MemoryBackend()
    monkeypatch.setattr(ratelimit, "backend", lambda: mem)
    mem.holder_acquire(mcp_modern._listen_key("usr_e66"), "h2", 5, 60)
    mcp_modern.listen_release("usr_e66", "h2")
    assert mem.holder_count(mcp_modern._listen_key("usr_e66")) == 0


# ── C66-03 IdP — 내장 issuer 는 연동 거부 · 연동 중지·삭제 모두 sub(ext:{id}:*) 기준, LIKE 와일드카드(_)는 글자 그대로 ──
def test_idp_builtin_issuer_and_session_scope(client, H):
    same = client.post("/api/v1/identity/idps", json={"name": "e66-self", "issuer": security.issuer(), "client_id": "x"},
                       headers=H["security"])
    assert same.status_code == 400 and same.json()["code"] == "issuer_reserved"
    iss = f"https://idp-e66-{uuid.uuid4().hex[:6]}.example"
    idp = client.post("/api/v1/identity/idps", json={"name": "e66", "issuer": iss, "client_id": "x"}, headers=H["security"]).json()
    iid = idp["id"]
    mine, other = f"usr_e66_{uuid.uuid4().hex[:6]}", f"usr_e66_{uuid.uuid4().hex[:6]}"
    look_alike = iid.replace("_", "Z")  # LIKE 의 _ 가 와일드카드면 이 IdP 사용자로 잘못 잡힌다
    with SessionLocal() as db:
        for uid, sub in ((mine, f"ext:{iid}:{mine}"), (other, f"ext:{look_alike}:{other}")):
            db.add(User(id=uid, sub=sub, issuer=iss, username=None, roles=["user"], status="active"))
            db.add(RefreshToken(id="rt_" + uid, user_id=uid, client_id="agentnexus-console", token_hash=uuid.uuid4().hex,
                                scope="openid", expires_at=utcnow() + timedelta(days=1)))
        db.commit()
    off = client.patch(f"/api/v1/identity/idps/{iid}", json={"enabled": False}, headers=H["security"]).json()
    assert off["sessions_ended"] == 1
    gone = client.delete(f"/api/v1/identity/idps/{iid}", headers=H["security"]).json()
    assert gone["users_signed_out"] == 1  # 전에는 issuer 기준 — 같은 issuer 의 다른 사용자도 끊겼다
    with SessionLocal() as db:
        assert db.get(User, mine).tokens_not_before is not None and db.get(RefreshToken, "rt_" + mine).revoked_at is not None
        assert db.get(User, other).tokens_not_before is None and db.get(RefreshToken, "rt_" + other).revoked_at is None
        for uid in (mine, other):
            db.delete(db.get(RefreshToken, "rt_" + uid))
            db.delete(db.get(User, uid))
        db.commit()


# ── C66-04 배포 — a2a-grpc readiness 실패 허용 6회 · TLS 인증서 볼륨 오버레이 예시(자동 포함 밖) · 인증서 커밋 금지 ──
def test_grpc_readiness_threshold_and_tls_overlay():
    tpl = (ROOT / "deploy/helm/agentnexus/templates/a2a-grpc.yaml").read_text()
    assert "grpc: { port: 50051, service: readiness }" in tpl and "failureThreshold: 6" in tpl
    example = (ROOT / "deploy/compose/a2a-grpc-tls.example.yml").read_text()
    assert "/certs:ro" in example and "A2A_GRPC_TLS_CERT: /certs/tls.crt" in example
    assert not list((ROOT / "compose").glob("*tls*"))  # compose/*.yml 은 Makefile 이 자동 포함
    assert "certs/" in (ROOT / ".gitignore").read_text().split()


# ── C66-05 CLI — 설정 디렉터리 0700(이미 있던 0755 도) · 파일은 연 파일 기준 0600 ──
def test_cli_config_dir_private(cli_env):  # noqa: F811
    d = Path(os.environ["NEXUS_CONFIG_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o755)
    (d / "config.json").write_text("{}")
    os.chmod(d / "config.json", 0o644)
    nexus._save({"url": "http://127.0.0.1:8800", "access_token": "e66"})
    assert oct(d.stat().st_mode)[-3:] == "700" and oct((d / "config.json").stat().st_mode)[-3:] == "600"
    assert json.loads((d / "config.json").read_text())["access_token"] == "e66"


# ── C66-06 번들 반입 — 구성 복원(운영 데이터 덮어쓰기)은 --restore-config 를 줄 때만 ──
def test_bundle_import_restore_opt_in(cli_env):  # noqa: F811
    b = cli_env / "b66"
    b.mkdir()
    (b / "config-backup.json").write_text("{}")
    (b / "manifest.json").write_text(json.dumps({"format": "agentnexus-airgap/1",
                                                 "files": {"config-backup.json": nexus._sha256(b / "config-backup.json")}}))
    default = run("bundle", "import", str(b), "--dry-run")
    explicit = run("bundle", "import", str(b), "--dry-run", "--restore-config")
    assert default.exit_code == 0 and "admin/restore" not in default.output
    assert explicit.exit_code == 0 and "POST /api/v1/admin/restore" in explicit.output


# ── C66-07 헬스 요청 — 서비스 이름 길이는 varint (128 바이트 이상도 정확히) ──
def test_health_request_varint():
    for name in ("", "readiness", "s" * 127, "x" * 300):
        req = a2a_grpc.health_request(name)
        assert a2a_grpc._service_of(req) == name
    assert a2a_grpc.health_request("x" * 300)[1:3] == bytes([0xAC, 0x02])
    assert a2a_grpc._service_of(b"\x0a\xff\xff\xff\xff\xff\xff") == ""  # 깨진 varint
