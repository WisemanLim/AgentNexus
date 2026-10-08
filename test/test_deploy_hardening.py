"""Epic 54 — 운영 배포 보강 (D20-③): a2a-grpc gRPC 헬스 · compose prod a2a-grpc 최소 env · 이미지·잠금 파일 고정 ·
번들 반입 서명 검증 기본 · 서명 검증 뒤 그 이미지 그대로 기동. 차트 불변식은 `deploy/helm/check-rendered.py`(make helm-check)."""
import subprocess
from pathlib import Path

import grpc
import yaml
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.cli import nexus
from app.core.http import StreamingASGITransport, register_mount
from app.services import a2a_grpc
from test.test_cli_install import cli_env, run  # noqa: F401 — autouse 픽스처

ROOT = Path(__file__).resolve().parents[1]


# ── C54-01 grpc.health.v1 — 기본(liveness)은 항상 SERVING · readiness 는 위임 대상 /health 까지 ──
def test_grpc_health_service(client):
    register_mount("http://e54-gw-up", async_transport=StreamingASGITransport(client.app))
    register_mount("http://e54-gw-down", async_transport=StreamingASGITransport(
        Starlette(routes=[Route("/health", lambda _: JSONResponse({"status": "degraded"}, status_code=503))])))

    async def ask(gw: str, service: str) -> bytes:
        server = await a2a_grpc.serve("127.0.0.1:0", gateway_url=gw)
        try:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}") as ch:
                return await ch.unary_unary(f"/{a2a_grpc.HEALTH_SERVICE}/Check")(a2a_grpc.health_request(service), timeout=5)
        finally:
            await server.stop(0)
    serving, not_serving = a2a_grpc._STATUS[True], a2a_grpc._STATUS[False]
    assert client.portal.call(ask, "http://e54-gw-up", "") == serving
    assert client.portal.call(ask, "http://e54-gw-up", "readiness") == serving
    assert client.portal.call(ask, "http://e54-gw-down", "") == serving  # 살아는 있다
    assert client.portal.call(ask, "http://e54-gw-down", "readiness") == not_serving  # 위임 대상이 안 되면 트래픽 받지 않음


# ── C54-02 compose — 엣지 a2a-grpc 는 .env.prod 전체 대신 필요한 값만 · 헬스는 grpc.health.v1 ──
def test_compose_a2a_grpc_minimal_env_and_health():
    prod = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())["services"]["a2a-grpc"]
    assert "env_file" not in prod and set(prod["environment"]) == {"APP_ENV", "LOG_LEVEL", "A2A_GRPC_TLS_CERT", "A2A_GRPC_TLS_KEY"}
    for name, svc in yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())["services"].items():
        if name not in ("a2a-grpc", "db"):
            assert svc.get("env_file") == [".env.prod"], name  # 다른 서비스는 그대로
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["a2a-grpc"]
    assert base["healthcheck"]["test"][-3:] == ["app.grpc_server", "--probe", "readiness"]


# ── C54-03 이미지·잠금 — e2e 베이스 digest · 잠금 파일 없으면 빌드 실패(고정 안 된 설치로 넘어가지 않음) ──
def test_dockerfile_pins_and_lockfiles():
    df = (ROOT / "Dockerfile").read_text()
    for line in (ln for ln in df.splitlines() if ln.startswith("FROM ") and " AS " in ln):
        img = line.split()[1]
        assert "@sha256:" in img or img in ("base", "web-dev") or img.split(":")[0] in ("base", "web-dev"), line
    assert 'test -f pnpm-lock.yaml || { echo' in df and 'test -f package-lock.json || { echo' in df
    assert "[ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; else" not in df


# ── C54-04 번들 반입 — 서명 검증이 기본(매니페스트 서명이 파일 해시 전체를 묶음) · --allow-unsigned 는 경고 ──
def test_bundle_import_verifies_by_default(cli_env, monkeypatch):  # noqa: F811
    import json
    b = cli_env / "signed"
    b.mkdir()
    (b / "api.tar").write_bytes(b"api")
    (b / "manifest.json").write_text(json.dumps({"format": "agentnexus-airgap/1", "files": {"api.tar": nexus._sha256(b / "api.tar")}}))
    no_key = run("bundle", "import", str(b), "--no-restore-config")
    assert no_key.exit_code == 1 and "서명 검증 불가" in no_key.output and "--allow-unsigned" in no_key.output
    calls = []

    def fake_run(cmd, **_k):
        calls.append(cmd)
        if cmd[:2] == ["cosign", "verify-blob"]:
            ok = Path(cmd[-1]).name == "manifest.json" and Path(cmd[-2]).read_text() == "good"
            return subprocess.CompletedProcess(cmd, 0 if ok else 1)
        return subprocess.CompletedProcess(cmd, 0, stdout="Loaded image: api:1.0\n")
    monkeypatch.setattr(nexus.subprocess, "run", fake_run)
    monkeypatch.setattr(nexus.shutil, "which", lambda name: "/usr/bin/cosign")
    key = cli_env / "ops.pub"  # 검증 키는 번들 밖에서 받은 것 (59th)
    key.write_text("pub")
    (b / "manifest.json.sig").write_text("forged")
    bad = run("bundle", "import", str(b), "--no-restore-config", "--cosign-key", str(key))
    assert bad.exit_code == 1 and "매니페스트 서명" in bad.output and not any(c[:2] == ["docker", "load"] for c in calls)
    (b / "manifest.json.sig").write_text("good")
    good = run("bundle", "import", str(b), "--no-restore-config", "--cosign-key", str(key))
    assert good.exit_code == 0, good.output
    assert ["docker", "load", "-i", str(b / "api.tar")] in calls
    unsigned = run("bundle", "import", str(b), "--no-restore-config", "--allow-unsigned", "--dry-run")
    assert unsigned.exit_code == 0 and "서명 검증 생략" in unsigned.output


# ── C54-05 설치 — 이미지 서명을 검증했으면 그 이미지로 기동(--build 로 다시 만들지 않음) ──
def test_install_runs_verified_images(cli_env):  # noqa: F811
    Path("install.yaml").write_text("mode: compose\nairgap: false\nprofile: full\n")
    Path(".env.example").write_text("APP_ENV=prod\nAPI_PORT=8800\n")
    Path("cosign.pub").write_text("pub")
    signed = run("install", "compose", "--dry-run", "--cosign-key", "cosign.pub")
    unsigned = run("install", "compose", "--dry-run", "--allow-unsigned")
    assert signed.exit_code == 0 and unsigned.exit_code == 0
    assert "up -d --build" not in signed.output and "up -d" in signed.output and "up -d --build" in unsigned.output
