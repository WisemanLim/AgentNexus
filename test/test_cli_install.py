"""Epic 20 — CLI 설치·컨텍스트·감사·번들 (SCR-A01·A02·A04·A11)."""
import json
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

from app.cli import nexus
from app.db import SessionLocal
from app.models import Setting

runner = CliRunner()


@pytest.fixture(autouse=True)
def cli_env(client, tmp_path, monkeypatch):
    monkeypatch.setattr(nexus, "HTTP", client)
    monkeypatch.setenv("NEXUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("NEXUS_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*args, token: str | None = None, input: str | None = None):
    return runner.invoke(nexus.app, list(args), input=input, env={"NEXUS_TOKEN": token} if token else {})


def test_precheck_report_airgap_recommendation_and_install_yaml(monkeypatch, cli_env):
    monkeypatch.setattr(nexus, "_reachable", lambda u: "ghcr.io" not in u)  # 외부 레지스트리 하나 차단
    monkeypatch.setattr(nexus.shutil, "which", lambda _c: "/usr/bin/x")
    monkeypatch.setattr(nexus.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=""))
    for f in (".env.local", ".env.prod"):
        Path(f).write_text("APP_ENV=x\n")
    res = run("install", "--check", "--port", "1", "--save", "--profile", "gateway-only")
    assert res.exit_code == 0, res.output
    assert "⚠ external" in res.output and "에어갭 번들 설치 권장" in res.output and "통과" in res.output
    rep = json.loads(Path("nexus-precheck.json").read_text())
    assert rep["airgap_recommended"] is True and rep["selection"] == {"mode": "compose", "airgap": True, "profile": "gateway-only"}
    assert {i["key"] for i in rep["items"]} >= {"resources", "disk", "docker", "compose_v2", "port_1", "time", "external"}
    assert Path("install.yaml").read_text() == "mode: compose\nairgap: true\nprofile: gateway-only\n"
    inter = run("install", "--check", "--port", "1", "--interactive", input="helm\nn\nregistry-only\n")
    assert inter.exit_code == 0 and "install.yaml 저장" in inter.output
    assert "profile: registry-only" in Path("install.yaml").read_text() and "mode: helm" in Path("install.yaml").read_text()
    Path(".env.prod").unlink()
    fail = run("install", "--check", "--port", "1")
    assert fail.exit_code == 1 and "✘ env_files" in fail.output  # 실패 잔존 → 설치 거부


def test_install_compose_plan_secrets_and_signature_gate(cli_env):
    Path("install.yaml").write_text("mode: compose\nairgap: true\nprofile: full\n")
    Path(".env.example").write_text("APP_ENV=prod\nAPI_PORT=8800\nDB_PASSWORD=CHANGE_ME\nENCRYPTION_KEY=CHANGE_ME\n# X=CHANGE_ME\n")
    dry = run("install", "compose", "--dry-run")
    assert dry.exit_code == 0 and "COMPOSE_PROFILES=demo,ollama" in dry.output and "생성" in dry.output
    unsigned = run("install", "compose")
    assert unsigned.exit_code == 1 and "서명 검증 키가 없습니다" in unsigned.output and not Path(".env.prod").exists()
    n = nexus.write_env(Path(".env.example"), Path(".env.gen"))
    body = Path(".env.gen").read_text()
    assert n == 2 and "CHANGE_ME" not in body.split("# X")[0] and oct(Path(".env.gen").stat().st_mode)[-3:] == "600"
    Fernet(next(x for x in body.splitlines() if x.startswith("ENCRYPTION_KEY=")).split("=", 1)[1].encode())  # 유효한 키


def test_install_code_local_then_claim(client, cli_env):
    with SessionLocal() as db:
        before = db.get(Setting, "setup.completed").value if db.get(Setting, "setup.completed") else None
        row = db.get(Setting, "setup.completed")
        if row is not None:
            row.value = False
            db.commit()
    try:
        res = run("install", "code", "--local")
        assert res.exit_code == 0 and "일회용 설정 코드:" in res.output
        code = res.output.split("일회용 설정 코드:")[1].split()[0]
        assert client.post("/api/v1/setup/claim", json={"code": code}).status_code == 200
    finally:
        with SessionLocal() as db:
            row = db.get(Setting, "setup.completed")
            if row is not None:
                row.value = before
                db.commit()


def test_context_switch_and_audit_list(tokens, cli_env):
    assert run("context", "add", "dev", "http://testserver").exit_code == 0
    assert run("context", "add", "prod", "https://nexus.prod.example").exit_code == 0
    assert run("context", "use", "dev").exit_code == 0
    run("login", "--token", tokens["security"])
    use = run("context", "use", "prod")
    assert "현재 컨텍스트: prod" in use.output and "로그인 필요" in use.output
    back = run("context", "use", "dev")
    assert "로그인 필요" not in back.output  # 컨텍스트별 토큰 보존
    lst = run("context", "list")
    assert "* dev" in lst.output and "로그인됨" in lst.output
    events = json.loads(run("audit", "list", "--since", "2d", "--json").stdout)
    assert events["items"] and all("action" in e for e in events["items"])
    assert run("context", "use", "nope").exit_code == 2


def test_bundle_import_quarantine_license_and_airgap(tokens, monkeypatch, cli_env, client, H):
    calls = []

    def fake_run(cmd, **_k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=f"Loaded image: {cmd[-1].split('/')[-1][:-4]}:1.0\n")
    monkeypatch.setattr(nexus.subprocess, "run", fake_run)
    b = cli_env / "bundle"
    b.mkdir()
    files = {"api.tar": b"api", "mcp.tar": b"mcp", "grafana.tar": b"grafana"}
    for n, data in files.items():
        (b / n).write_bytes(data)
    man = {"format": "agentnexus-airgap/1", "files": {n: nexus._sha256(b / n) for n in files},
           "licenses": {"grafana.tar": "AGPL-3.0"}}
    (b / "manifest.json").write_text(json.dumps(man))
    (b / "mcp.tar").write_bytes(b"tampered")
    res = run("bundle", "import", str(b), "--registry", "registry.local:5000", "--apply-airgap", "--no-restore-config", "--allow-unsigned",
              token=tokens["admin"])
    try:
        assert res.exit_code == 1  # 부분 실패
        assert "라이선스 분리 배포 대상" in res.output and "grafana.tar(AGPL-3.0)" in res.output
        assert "격리" in res.output and (b / "quarantine" / "mcp.tar").exists() and not (b / "mcp.tar").exists()
        loads = [c for c in calls if c[:2] == ["docker", "load"]]
        assert [c[-1] for c in loads] == [str(b / "api.tar")]  # 격리·분리 대상은 적재 제외
        assert ["docker", "push", "registry.local:5000/api:1.0"] in calls
        assert "에어갭 프로파일 적용" in res.output
        assert client.get("/api/v1/admin/profiles", headers=H["admin"]).json()["current"]["airgap"] is True
    finally:
        client.post("/api/v1/admin/profiles/apply", json={"profile": "full", "airgap": False, "confirm": True}, headers=H["admin"])
