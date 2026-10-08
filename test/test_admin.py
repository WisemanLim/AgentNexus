"""Epic 10 — 독립 실행·연동·운영 (FR-P02·W01·W03·P04, SCR-A05~A09·B03~B05·L01~L06)."""
import json
import os
import subprocess
import sys

import httpx
from sqlalchemy import select

from app.core.http import register_mount
from app.db import SessionLocal
from app.models import CatalogItem, GatewayPolicy, Model
from app.services.notify import notify


def _routes_with(components: str) -> set[str]:
    code = "from app.main import app; import json; print(json.dumps(sorted(app.openapi()['paths'])))"
    env = {**os.environ, "COMPONENTS": components}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True,
                         cwd=os.path.join(os.path.dirname(__file__), ".."))
    return set(json.loads(out.stdout.strip().splitlines()[-1]))


def test_component_selection_independent_run():
    gw = _routes_with("gateway,identity")
    assert "/v1/chat/completions" in gw and "/mcp/{server_id}" in gw and "/idp/token" in gw
    assert "/api/v1/agents" not in gw and "/v0/servers" not in gw and "/api/v1/audit/verify" not in gw
    reg = _routes_with("registry,identity")
    assert "/v0/servers" in reg and "/v1/catalog" in reg and "/v1/chat/completions" not in reg
    host = _routes_with("host,gateway,identity")
    assert "/api/v1/sessions" in host and "/a2a/platform" in host


def test_system_health_and_profiles(client, H):
    h = client.get("/api/v1/system/health", headers=H["operator"]).json()
    assert h["dependencies"]["database"]["ok"] and set(h["components"]) >= {"gateway", "host", "registry"}
    assert "rate_limit" in h["dependencies"] and "mcp_servers" in h["dependencies"]
    assert client.get("/api/v1/system/health", headers=H["user"]).status_code == 403
    prof = client.get("/api/v1/admin/profiles", headers=H["operator"]).json()
    assert {p["id"] for p in prof["items"]} >= {"full", "gateway-only", "registry-only", "a2a-only", "airgap"}


def test_connectors_and_generated_configs(client, H):
    register_mount("http://grafana.test", sync_transport=httpx.MockTransport(
        lambda r: httpx.Response(200 if r.url.path == "/api/health" else 404, json={"database": "ok"})))
    c = client.post("/api/v1/admin/connectors", json={"kind": "observability", "name": "사내 Grafana",
                                                      "config": {"url": "http://grafana.test", "product": "grafana",
                                                                 "health_path": "/api/health"},
                                                      "secret": "glsa_token_value"}, headers=H["operator"]).json()
    assert c["credential_configured"] is True and "glsa" not in json.dumps(c)
    assert client.post(f"/api/v1/admin/connectors/{c['id']}/test", headers=H["operator"]).json()["status"] == "ok"
    bad = client.post("/api/v1/admin/connectors", json={"kind": "api_gateway", "name": "kong", "config": {
        "url": "http://kong-down.invalid"}}, headers=H["operator"]).json()
    assert client.post(f"/api/v1/admin/connectors/{bad['id']}/test", headers=H["operator"]).json()["status"] == "error"
    kong = client.get("/api/v1/admin/connectors/api-gateway/config", params={"product": "kong"},
                      headers=H["operator"]).json()
    assert kong["config"]["services"][0]["routes"][0]["paths"][0] == "/v1/chat/completions"
    apisix = client.get("/api/v1/admin/connectors/api-gateway/config", params={"product": "apisix",
                                                                               "upstream": "http://anx:8000"},
                        headers=H["operator"]).json()
    assert apisix["config"]["routes"][0]["upstream"]["nodes"] == {"anx:8000": 1}
    dash = client.get("/api/v1/admin/connectors/grafana/dashboard", headers=H["operator"]).json()
    exprs = " ".join(p["targets"][0]["expr"] for p in dash["dashboard"]["panels"])
    assert "anx_llm_tokens_total" in exprs and "anx_approvals_pending" in exprs


def test_updates_versions(client, H):
    u = client.get("/api/v1/admin/updates", headers=H["operator"]).json()
    assert u["migration"] == "0021" and u["protocols"]["mcp"]["current"] == "2025-11-25"
    assert set(u["protocols"]["a2a"]["supported"]) == {"0.3", "1.0"} and u["dependencies"]["fastmcp"]


def test_backup_restore_roundtrip_and_tamper(client, H):
    assert client.post("/api/v1/admin/backup", json={}, headers=H["dev"]).status_code == 403
    bk = client.post("/api/v1/admin/backup", json={"include_secrets": False}, headers=H["admin"]).json()
    assert "secrets" not in bk["bundle"]["tables"] and bk["bundle"]["tables"]["gateway_policies"]
    with SessionLocal() as db:
        pol = db.scalars(select(GatewayPolicy).where(GatewayPolicy.name == "기본 모델 허용")).first()
        pid = pol.id
        db.delete(pol)
        db.get(Model, "fast-commercial").price_in = 999
        db.commit()
    tampered = json.loads(json.dumps(bk["bundle"]))
    tampered["tables"]["models"][0]["price_in"] = "0"
    assert client.post("/api/v1/admin/restore", json={"bundle": tampered, "signature": bk["signature"], "confirm": "RESTORE"},
                       headers=H["admin"]).json()["code"] == "invalid_backup"
    assert client.post("/api/v1/admin/restore", json={"bundle": bk["bundle"], "signature": bk["signature"]},
                       headers=H["admin"]).json()["code"] == "confirm_required"
    res = client.post("/api/v1/admin/restore", json={"bundle": bk["bundle"], "signature": bk["signature"], "confirm": "RESTORE"},
                      headers=H["admin"]).json()
    assert res["restored"]["gateway_policies"] >= 4
    with SessionLocal() as db:
        assert db.get(GatewayPolicy, pid) is not None and float(db.get(Model, "fast-commercial").price_in) == 0.15
        assert db.get(CatalogItem, bk["bundle"]["tables"]["catalog_items"][0]["id"]).item_metadata is not None


def test_settings_validation(client, H):
    s = client.put("/api/v1/admin/settings", json={"ui.language": "en", "retention": {"logs_days": 7}},
                   headers=H["operator"]).json()
    assert s["ui.language"] == "en" and s["retention"]["logs_days"] == 7 and s["retention"]["audit_days"] == 365
    assert client.put("/api/v1/admin/settings", json={"ui.language": "fr"}, headers=H["operator"]).status_code == 400
    assert client.put("/api/v1/admin/settings", json={"nope": 1}, headers=H["operator"]).status_code == 400
    assert client.put("/api/v1/admin/settings", json={"retention": {"logs_days": 0}},
                      headers=H["operator"]).status_code == 400
    client.put("/api/v1/admin/settings", json={"ui.language": "ko"}, headers=H["operator"])


def test_notifications_audience_and_read(client, H):
    notify("test.security", "보안 알림", audience_role="security")
    notify("test.all", "전체 공지")
    user = client.get("/api/v1/notifications", headers=H["user"]).json()
    titles = {n["title"] for n in user["items"]}
    assert "전체 공지" in titles and "보안 알림" not in titles
    sec = client.get("/api/v1/notifications", headers=H["security"]).json()
    assert "보안 알림" in {n["title"] for n in sec["items"]}
    before = sec["unread"]
    target = next(n["id"] for n in sec["items"] if n["title"] == "보안 알림")
    assert client.post("/api/v1/notifications/read", json={"ids": [target]}, headers=H["security"]).json()["marked"] == 1
    assert client.get("/api/v1/notifications", headers=H["security"]).json()["unread"] == before - 1


def test_global_search_scoped_by_permission(client, H):
    client.post("/api/v1/mcp-servers/jira/refresh", headers=H["operator"])
    r = client.get("/api/v1/search", params={"q": "jira"}, headers=H["dev"]).json()["results"]
    assert any(t["id"] == "jira.search_issues" for t in r.get("tools", [])) and r.get("catalog")
    u = client.get("/api/v1/search", params={"q": "user"}, headers=H["user"]).json()["results"]
    assert "users" not in u and "tools" not in u  # 권한 밖 결과 미노출
    a = client.get("/api/v1/search", params={"q": "user"}, headers=H["admin"]).json()["results"]
    assert a.get("users")


def test_setup_status_and_complete(client, H):
    st = client.get("/api/v1/setup/status", headers=H["admin"]).json()
    ids = {s["id"]: s for s in st["steps"]}
    assert set(ids) == {"identity", "models", "registry", "observability", "templates"}
    assert ids["identity"]["done"] and ids["templates"]["done"]
    assert client.post("/api/v1/setup/complete", headers=H["admin"]).json()["completed"] is True
    assert client.get("/api/v1/setup/status", headers=H["admin"]).json()["completed"] is True


def test_console_spa_fallback(client, tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "console_dist", str(tmp_path / "missing"))
    assert client.get("/console").status_code == 503
    (tmp_path / "_app").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "_app" / "a.js").write_text("console.log(1)")
    monkeypatch.setattr(settings, "console_dist", str(tmp_path))
    for path in ("/console", "/console/agents/tpl-qa/graph", "/chat/ses_1", "/login/callback", "/setup"):
        res = client.get(path)
        assert res.status_code == 200 and "id=root" in res.text and res.headers["cache-control"] == "no-cache", path
    assert client.get("/", follow_redirects=False).headers["location"] == "/console"
    assert client.get("/api/v1/nope").status_code == 404  # API 경로는 폴백하지 않음


def test_observe_timeseries(client, H):
    r = client.get("/api/v1/observe/timeseries", params={"window_minutes": 60, "buckets": 6}, headers=H["operator"]).json()
    assert len(r["items"]) == 6 and r["bucket_seconds"] == 600
    assert set(r["items"][0]) >= {"t", "model", "tool", "remote_agent", "errors", "cost_usd"}
    assert client.get("/api/v1/observe/timeseries", headers=H["user"]).status_code == 403


def test_demo_workload_secret_from_env(client, monkeypatch):
    """Helm 다중 파드: 공유 볼륨 없이 Secret 으로 같은 워크로드 시크릿을 api(시드)·a2a-demo 에 주입."""
    from app import seed
    from app.builtin import a2a_agent
    from app.core.config import settings
    from app.core import security
    from app.models import Workload
    file_cred = a2a_agent._credentials()
    monkeypatch.setattr(settings, "a2a_demo_client_secret", "anx_ws_helm_injected_value")
    try:
        with SessionLocal() as db:
            seed.seed_a2a(db)
            db.commit()
        assert a2a_agent._credentials() == {"client_id": "wl-a2a-demo", "client_secret": "anx_ws_helm_injected_value"}
        tok = client.post("/idp/token", data={"grant_type": "client_credentials", "client_id": "wl-a2a-demo",
                                              "client_secret": "anx_ws_helm_injected_value"})
        assert tok.status_code == 200 and tok.json()["access_token"]
        monkeypatch.setattr(settings, "a2a_demo_client_secret", "CHANGE_ME")  # placeholder = 미설정 → 파일 방식
        assert a2a_agent._credentials() == file_cred
    finally:  # 다른 시험이 쓰는 파일 기반 시크릿으로 복원
        with SessionLocal() as db:
            wl = db.scalars(select(Workload).where(Workload.client_id == "wl-a2a-demo")).first()
            wl.secret_hash = security.hash_password(file_cred["client_secret"])
            db.commit()
