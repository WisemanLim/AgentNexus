"""Epic 11 — CLI `nexus` (FR-P03·S03, SCR-A01·A04·A11·E04·F05). TestClient 를 HTTP 로 주입해 in-process 실행."""
import json
import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from app.cli import nexus
from app.db import SessionLocal
from app.models import CatalogVersion, DeviceCode

runner = CliRunner()


@pytest.fixture(autouse=True)
def cli_env(client, tmp_path, monkeypatch):
    monkeypatch.setattr(nexus, "HTTP", client)
    monkeypatch.setenv("NEXUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("NEXUS_TOKEN", raising=False)
    return tmp_path


def run(*args, token: str | None = None, env: dict | None = None):
    return runner.invoke(nexus.app, list(args), env={**({"NEXUS_TOKEN": token} if token else {}), **(env or {})})


def test_requires_login():
    res = run("whoami")
    assert res.exit_code == 2 and "로그인이 필요합니다" in res.output


def test_device_login_flow(client, monkeypatch, cli_env):
    def approve(_s):  # 사용자가 브라우저에서 코드를 승인하는 순간
        with SessionLocal() as db:
            dc = db.scalars(select(DeviceCode).where(DeviceCode.status == "pending",
                                                     DeviceCode.client_id == "nexus-cli")
                            .order_by(DeviceCode.expires_at.desc())).first()
        client.post("/idp/device", data={"user_code": dc.user_code, "username": "dev", "password": "dev-pass"})
    monkeypatch.setattr(nexus.time, "sleep", approve)
    res = run("login", "--device")
    assert res.exit_code == 0, res.output
    assert "브라우저에서 코드 입력" in res.output and "로그인 완료" in res.output and "developer" in res.output
    cfg_file = cli_env / "cfg" / "config.json"
    assert cfg_file.stat().st_mode & 0o777 == 0o600
    assert json.loads(cfg_file.read_text())["access_token"]
    who = json.loads(run("whoami", "--json").stdout)  # 저장된 토큰 사용
    assert who["sub"] == "usr_dev" and "registry:publish" in who["permissions"]
    assert run("logout").exit_code == 0 and run("whoami").exit_code == 2


def test_denied_device_login(client, monkeypatch):
    def deny(_s):
        with SessionLocal() as db:
            dc = db.scalars(select(DeviceCode).where(DeviceCode.status == "pending")).first()
            dc.status = "denied"
            db.commit()
    monkeypatch.setattr(nexus.time, "sleep", deny)
    res = run("login")
    assert res.exit_code == 1 and "로그인 실패: 거부되었습니다" in res.output


def test_scaffold_publish_and_pending(tokens, cli_env):
    target = cli_env / "weather"
    res = run("mcp", "scaffold", "weather", "--dir", str(target), "--title", "날씨 MCP", token=tokens["dev"])
    assert res.exit_code == 0, res.output
    assert {"server.py", "anx_auth.py", "server.json", "Dockerfile"} <= {p.name for p in target.iterdir()}
    assert run("mcp", "scaffold", "weather", "--dir", str(target), token=tokens["dev"]).exit_code == 1  # 덮어쓰기 거부
    pub = run("registry", "publish", str(target / "server.json"), "--json", token=tokens["dev"])
    assert pub.exit_code == 0, pub.output
    out = json.loads(pub.stdout)
    assert out["item"]["name"] == "io.agentnexus.custom/weather" and out["version"]["status"] == "pending"
    with SessionLocal() as db:
        assert db.get(CatalogVersion, out["version"]["id"]).created_by == "usr_dev"
    denied = run("registry", "publish", str(target / "server.json"), token=tokens["user"])
    assert denied.exit_code == 1 and "403" in denied.output


def test_catalog_list_and_register(tokens):
    res = run("catalog", "list", "--type", "mcp_server", "--json", token=tokens["dev"])
    items = json.loads(res.stdout)["items"]
    assert items and all(i["type"] == "mcp_server" for i in items)
    text = run("catalog", "list", "--type", "mcp_server", token=tokens["dev"]).output
    assert items[0]["id"] in text


def test_inspect_approval_and_approve(client, H, tokens):
    client.post("/api/v1/mcp-servers/jira/refresh", headers=H["operator"])
    assert "jira" in run("mcp", "list", token=tokens["dev"]).output
    assert run("mcp", "list", token=tokens["user"]).exit_code == 1  # user 는 mcp:read 없음
    res = run("mcp", "inspect", "jira", "transition_issue", "--args", '{"key":"ORI-1440","to":"Done"}',
              token=tokens["dev"])
    body = json.loads(res.stdout)
    ap = body["error"]["data"]["approval_id"]
    listed = json.loads(run("approvals", "list", "--json", token=tokens["dev"]).stdout)["items"]
    assert ap in {a["id"] for a in listed}
    assert f"{ap}: approved" in run("approvals", "approve", ap, "--note", "CLI 승인", token=tokens["dev"]).output
    again = json.loads(run("mcp", "inspect", "jira", "transition_issue", "--args", '{"key":"ORI-1440","to":"Done"}',
                           "--approval", ap, token=tokens["dev"]).stdout)
    assert again.get("error") is None and again["result"]["isError"] is False


def test_chat_streams_run(tokens):
    res = run("chat", "tpl-qa", "장애 대응 런북 알려줘", token=tokens["user"])
    assert res.exit_code == 0, res.output
    assert re.search(r"\[succeeded\] 인용 [1-9]\d*건", res.output) and res.output.count(" 완료 (") == 2


def test_usage_and_audit_verify(tokens):
    u = run("usage", "--group-by", "kind", token=tokens["finops"])
    assert u.exit_code == 0 and "합계 $" in u.output
    assert "유효" in run("audit-verify", token=tokens["security"]).output


def test_install_check(monkeypatch, cli_env):
    monkeypatch.chdir(cli_env)
    monkeypatch.setattr(nexus, "_reachable", lambda _u: True)  # 외부 접근 점검은 아래 시험에서
    monkeypatch.setattr(nexus.shutil, "which", lambda _c: "/usr/bin/x")
    monkeypatch.setattr(nexus.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    fail = json.loads(run("install", "--check", "--json", "--port", "1").stdout)
    assert fail["ok"] is False and fail["checks"]["env_files"] is False
    for f in (".env.local", ".env.prod"):
        Path(f).write_text("APP_ENV=x\n")
    ok = run("install", "--check", "--port", "1")
    assert ok.exit_code == 0 and "점검 통과" in ok.output
    helm = json.loads(run("install", "--check", "--mode", "helm", "--json", "--port", "1").stdout)
    assert set(helm["checks"]) >= {"helm", "kubectl"}


def test_bundle_export_import_roundtrip(tokens, monkeypatch, cli_env):
    calls = []

    def fake_run(cmd, **_k):
        calls.append(cmd)
        if cmd[:2] == ["docker", "save"]:
            Path(cmd[3]).write_bytes(b"image:" + cmd[4].encode())
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(nexus.subprocess, "run", fake_run)
    out = cli_env / "bundle"
    dry = run("bundle", "export", "--out", str(out), "--dry-run", token=tokens["admin"])
    assert "docker save wise-agentnexus-api:prod" in dry.output and not out.exists()
    chart = cli_env / "agentnexus-0.1.0.tgz"
    chart.write_bytes(b"helm-chart")
    res = run("bundle", "export", "--out", str(out), "--image", "wise-agentnexus-api:prod", "--include", str(chart),
              token=tokens["admin"])
    assert res.exit_code == 0, res.output
    manifest = json.loads((out / "manifest.json").read_text())
    assert set(manifest["files"]) == {"wise-agentnexus-api__prod.tar", "config-backup.json", "agentnexus-0.1.0.tgz"}
    imp = run("bundle", "import", str(out), "--allow-unsigned", "--restore-config", token=tokens["admin"])  # 복원은 명시할 때만 (66th)
    assert imp.exit_code == 0, imp.output
    assert "체크섬 확인: 3 files" in imp.output and "구성 복원" in imp.output and "agentnexus-0.1.0.tgz" in imp.output
    assert ["docker", "load", "-i", str(out / "wise-agentnexus-api__prod.tar")] in calls
    (out / "wise-agentnexus-api__prod.tar").write_bytes(b"tampered")
    bad = run("bundle", "import", str(out), "--dry-run", "--allow-unsigned", token=tokens["admin"])
    assert bad.exit_code == 1 and "체크섬 불일치" in bad.output


def test_module_entrypoint_help():
    res = subprocess.run(["python", "-m", "app.cli", "--help"], capture_output=True, text=True, check=False,
                         cwd=Path(__file__).resolve().parents[1])
    assert res.returncode == 0 and "install" in res.stdout and "bundle" in res.stdout
