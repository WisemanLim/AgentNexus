"""Epic 3 — 카탈로그·레지스트리 (FR-P01, C00~C03, P04)."""
import copy

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.core.http import register_mount
from app.db import SessionLocal
from app.models import McpServer, Notification
from app.services import registry


def server_json(name="io.example/orders", version="1.0.0", **kw) -> dict:
    doc = {"$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
           "name": name, "description": "Orders REST → MCP tools", "version": version,
           "remotes": [{"type": "streamable-http", "url": "https://mcp.example.test/orders/mcp"}],
           "_meta": {"auth": {"type": "oauth2"}, "tools": ["orders.search", "orders.get"], "license": "MIT"}}
    doc.update(kw)
    return doc


def test_publish_requires_permission_and_valid_schema(client, H):
    assert client.post("/v0/publish", json=server_json(), headers=H["user"]).status_code == 403
    bad = server_json(name="no-slash", description="x" * 101)
    bad.pop("remotes")
    res = client.post("/v0/publish", json=bad, headers=H["dev"])
    assert res.status_code == 422 and res.json()["code"] == "invalid_server_json"
    errs = " ".join(res.json()["errors"])
    assert "name" in errs and "description" in errs and "remotes 또는 packages" in errs


def test_publish_approve_discover_register(client, H):
    res = client.post("/v0/publish", json=server_json(), headers=H["dev"])
    assert res.status_code == 201 and res.json()["version"]["status"] == "pending"
    item_id = res.json()["item"]["id"]
    # 승인 전: 발견·사용자 카탈로그 비노출, 승인자에게만 대기열 노출
    assert "io.example/orders" not in [s["server"]["name"] for s in client.get("/v0/servers").json()["servers"]]
    assert client.get(f"/v1/catalog/{item_id}", headers=H["user"]).status_code == 404
    pending = client.get("/v0/servers", params={"include_pending": True}, headers=H["operator"]).json()["servers"]
    assert "io.example/orders" in [s["server"]["name"] for s in pending]
    queue = client.get("/api/v1/registry/approvals", headers=H["operator"]).json()["items"]
    assert any(q["item"]["id"] == item_id for q in queue)
    with SessionLocal() as db:
        assert db.query(Notification).filter(Notification.kind == "registry.pending").count() >= 1
    # 승인 전 등록 불가
    assert client.post(f"/v1/catalog/{item_id}/register", headers=H["operator"]).json()["code"] == "not_approved"
    assert client.post(f"/v1/catalog/{item_id}/approve", headers=H["dev"]).status_code == 403
    ok = client.post(f"/v1/catalog/{item_id}/approve", json={"note": "보안 검토 완료"}, headers=H["operator"])
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    names = [s["server"]["name"] for s in client.get("/v0/servers").json()["servers"]]
    assert "io.example/orders" in names
    one = client.get("/v0/servers/io.example/orders").json()
    assert one["_meta"]["io.agentnexus/catalog"]["id"] == item_id
    assert client.get(f"/v1/catalog/{item_id}", headers=H["user"]).json()["license_risk"] == "low"
    # 즉시 사용 — 등록 → 인스턴스 생성
    reg = client.post(f"/v1/catalog/{item_id}/register", headers=H["operator"]).json()
    assert reg["instance"]["id"] == "orders" and reg["instance"]["gateway_path"] == "/mcp/orders"
    assert reg["item"]["status"] == "active"
    with SessionLocal() as db:
        srv = db.get(McpServer, "orders")
        assert srv.endpoint == "https://mcp.example.test/orders/mcp" and srv.auth == {"type": "oauth2"}


def test_versioning_idempotency_and_conflict(client, H):
    first = client.post("/v0/publish", json=server_json(name="io.example/tickets", version="1.0.0"), headers=H["dev"])
    item_id = first.json()["item"]["id"]
    client.post(f"/v1/catalog/{item_id}/approve", headers=H["operator"])
    # 같은 버전·같은 내용 → 멱등
    again = client.post("/v0/publish", json=server_json(name="io.example/tickets", version="1.0.0"), headers=H["dev"])
    assert again.json()["created"] is False
    # 같은 버전·다른 내용 → 409
    changed = server_json(name="io.example/tickets", version="1.0.0", description="changed")
    assert client.post("/v0/publish", json=changed, headers=H["dev"]).status_code == 409
    # 새 버전은 승인 전까지 현재 버전 유지
    client.post("/v0/publish", json=server_json(name="io.example/tickets", version="1.1.0",
                                                description="v1.1"), headers=H["dev"])
    assert client.get(f"/v1/catalog/{item_id}", headers=H["user"]).json()["version"] == "1.0.0"
    client.post(f"/v1/catalog/{item_id}/approve", json={"version": "1.1.0"}, headers=H["operator"])
    cur = client.get(f"/v1/catalog/{item_id}", headers=H["user"]).json()
    assert cur["version"] == "1.1.0" and cur["description"] == "v1.1"
    hist = client.get(f"/v1/catalog/{item_id}/versions", headers=H["user"]).json()["items"]
    assert {(h["version"], h["status"]) for h in hist} == {("1.1.0", "approved"), ("1.0.0", "superseded")}
    # 거부
    client.post("/v0/publish", json=server_json(name="io.example/tickets", version="2.0.0"), headers=H["dev"])
    rej = client.post(f"/v1/catalog/{item_id}/reject", json={"version": "2.0.0", "note": "권한 과다"},
                      headers=H["security"])
    assert rej.status_code == 200 and rej.json()["status"] == "approved"  # 기존 승인 버전은 유지


def test_publisher_signature(client, H):
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    assert client.put("/api/v1/registry/trust", json={"keys": {"pub-1": pem}, "require_signature": True},
                      headers=H["security"]).status_code == 200
    unsigned = server_json(name="io.example/signed")
    assert client.post("/v0/publish", json=unsigned, headers=H["dev"]).json()["code"] == "signature_required"
    signed = registry.sign_server_json(unsigned, key, "pub-1")
    res = client.post("/v0/publish", json=signed, headers=H["dev"])
    assert res.status_code == 201 and res.json()["item"]["signature_verified"] is True
    tampered = copy.deepcopy(signed)
    tampered["version"] = "9.9.9"
    assert client.post("/v0/publish", json=tampered, headers=H["dev"]).json()["code"] == "invalid_signature"
    client.put("/api/v1/registry/trust", json={"keys": {"pub-1": pem}, "require_signature": False},
               headers=H["security"])


def test_mirror_sync_official_docker_and_file(client, H):
    pages = {
        None: {"servers": [{"server": server_json(name="io.github.acme/weather", version="0.3.0", _meta={"license": "MIT"})}],
               "metadata": {"nextCursor": "p2"}},
        "p2": {"servers": [{"server": server_json(name="io.github.acme/maps", version="1.2.0", _meta={"license": "MIT"})},
                           {"server": {"name": "broken", "version": "1"}}], "metadata": {}},
    }

    def official(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])

    docker_yaml = """
registry:
  github:
    title: GitHub
    description: GitHub repositories, issues and PRs
    image: mcp/github:1.4
    license: MIT
    tools: [{name: list_issues}, {name: create_issue}]
"""
    register_mount("https://registry.mirror.test", sync_transport=httpx.MockTransport(official))
    register_mount("https://catalog.docker.test",
                   sync_transport=httpx.MockTransport(lambda r: httpx.Response(200, text=docker_yaml)))
    register_mount("https://down.mirror.test", sync_transport=httpx.MockTransport(lambda r: httpx.Response(500)))

    src = client.post("/api/v1/registry/sources", json={"name": "mirror-test", "kind": "official",
                                                        "url": "https://registry.mirror.test", "auto_approve": True},
                      headers=H["operator"]).json()
    counts = client.post(f"/api/v1/registry/sources/{src['id']}/sync", headers=H["operator"]).json()
    assert counts["imported"] == 2 and counts["invalid"] == 1
    names = [s["server"]["name"] for s in client.get("/v0/servers", params={"search": "acme"}).json()["servers"]]
    assert sorted(names) == ["io.github.acme/maps", "io.github.acme/weather"]  # auto_approve → 즉시 발견
    again = client.post(f"/api/v1/registry/sources/{src['id']}/sync", headers=H["operator"]).json()
    assert again["unchanged"] == 2 and again["imported"] == 0

    dsrc = client.post("/api/v1/registry/sources", json={"name": "docker-test", "kind": "docker",
                                                         "url": "https://catalog.docker.test/catalog.yaml"},
                       headers=H["operator"]).json()
    dcounts = client.post(f"/api/v1/registry/sources/{dsrc['id']}/sync", headers=H["operator"]).json()
    assert dcounts["imported"] == 1
    pend = client.get("/v1/catalog", params={"q": "github", "status": "pending"}, headers=H["operator"]).json()
    item = pend["items"][0]
    assert item["name"] == "io.docker.mcp/github" and item["source"] == "mirror"

    fsrc = client.post("/api/v1/registry/sources", json={"name": "airgap-test", "kind": "file"},
                       headers=H["operator"]).json()
    fcounts = client.post(f"/api/v1/registry/sources/{fsrc['id']}/import",
                          json=[server_json(name="io.internal/hr", version="1.0.0"), {"name": "x"}],
                          headers=H["operator"]).json()
    assert fcounts["imported"] == 1 and fcounts["invalid"] == 1

    down = client.post("/api/v1/registry/sources", json={"name": "down", "kind": "official",
                                                         "url": "https://down.mirror.test"}, headers=H["operator"]).json()
    res = client.post(f"/api/v1/registry/sources/{down['id']}/sync", headers=H["operator"])
    assert res.status_code == 502
    status = next(s for s in client.get("/api/v1/registry/sources", headers=H["operator"]).json()["items"]
                  if s["id"] == down["id"])
    assert status["last_status"].startswith("error")


def test_agent_card_catalog_and_unified_search(client, H):
    card = {"name": "Observability Analyst", "description": "장애 분석 위임 에이전트", "url": "https://agents.test/obs",
            "version": "1.0.0", "capabilities": {"streaming": True, "pushNotifications": True},
            "skills": [{"id": "incident-analysis", "name": "장애 분석", "description": "Dynatrace 문제 분석",
                        "tags": ["sre", "dynatrace"]}],
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}}
    res = client.post("/v1/catalog", json={"type": "a2a_agent", "name": "observability-analyst-test",
                                           "metadata": card, "tags": ["sre"]}, headers=H["dev"])
    assert res.status_code == 201 and res.json()["auth_scheme"] == "bearer"
    client.post(f"/v1/catalog/{res.json()['id']}/approve", headers=H["operator"])
    found = client.get("/v1/catalog", params={"q": "dynatrace"}, headers=H["user"]).json()
    assert "observability-analyst-test" in [i["name"] for i in found["items"]]  # 스킬 태그로 검색
    only_a2a = client.get("/v1/catalog", params={"type": "a2a_agent"}, headers=H["user"]).json()
    assert all(i["type"] == "a2a_agent" for i in only_a2a["items"]) and only_a2a["counts"]["a2a_agent"] >= 1
    tool_hit = client.get("/v1/catalog", params={"q": "orders.search"}, headers=H["user"]).json()
    assert any(i["name"] == "io.example/orders" for i in tool_hit["items"])  # 도구명으로 검색


def test_sbom_and_license_report(client, H):
    bom = client.get("/v1/catalog/sbom", headers=H["security"]).json()
    assert bom["bomFormat"] == "CycloneDX" and bom["specVersion"] == "1.6"
    fastapi = next(c for c in bom["components"] if c["name"].lower() == "fastapi")
    assert {p["name"]: p["value"] for p in fastapi["properties"]}["agentnexus:license_risk"] == "low"
    summary = client.get("/v1/catalog/sbom", params={"format": "summary"}, headers=H["security"]).json()
    assert summary["risk_counts"].get("low", 0) > 10
    assert any(r["scope"] == "catalog:mcp_server" for r in summary["components"])
    assert registry.license_risk("AGPL-3.0") == "high" and registry.license_risk("Elastic License 2.0") == "medium"
    assert registry.license_risk("LGPL-2.1") == "medium" and registry.license_risk(None) == "unknown"


def test_registry_validate_before_publish(client, H):
    ok = client.post("/api/v1/registry/validate", headers=H["dev"], json={
        "name": "io.test/validate-me", "version": "1.0.0", "description": "검증",
        "remotes": [{"type": "streamable-http", "url": "https://x.example/mcp"}],
        "_meta": {"auth": {"type": "oauth2", "tool_scopes": {"a": "x:read"}}, "tools": ["a"]}}).json()
    assert ok["valid"] is True and ok["auth"] == "oauth2" and ok["remotes"] == ["https://x.example/mcp"]
    bad = client.post("/api/v1/registry/validate", headers=H["dev"], json={"name": "no version"}).json()
    assert bad["valid"] is False and bad["errors"]
    assert client.post("/api/v1/registry/validate", headers=H["user"], json={}).status_code == 403
