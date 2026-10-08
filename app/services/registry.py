"""MCP Registry / 통합 카탈로그 (FR-P01, FR-C00~C03, FR-P04).

- 발견 전용(Discovery only): 실행 트래픽은 통과하지 않고 메타데이터(server.json · agent-card.json)만 제공한다.
- 게시(publish) → 검증(스키마·서명) → 운영자 승인 → 발견 노출 → 등록(register) 시 인스턴스 생성(active).
- 미러: 공식 MCP Registry(/v0/servers), Docker MCP 카탈로그(YAML), 커스텀 URL, 파일(에어갭).
"""
import base64
import fnmatch
import importlib.metadata as md
import re
import uuid
from collections.abc import Callable

import jsonschema
import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import APP_VERSION
from app.core.errors import ApiError, bad_request, conflict
from app.core.http import sync_client
from app.core.ids import canonical_json, new_id, slugify
from app.db import utcnow
from app.models import CatalogItem, CatalogVersion, RegistrySource, Setting
from app.services import audit
from app.services.notify import notify

SIG_KEY = "io.agentnexus/signature"
CATALOG_TYPES = ("mcp_server", "a2a_agent", "model", "agent_profile")

# server.json (MCP Registry 2025 스키마의 검증 부분집합)
SERVER_JSON_SCHEMA = {
    "type": "object",
    "required": ["name", "description", "version"],
    "properties": {
        "$schema": {"type": "string"},
        "name": {"type": "string", "minLength": 3, "maxLength": 200,
                 "pattern": r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$"},
        "title": {"type": "string", "maxLength": 100},
        "description": {"type": "string", "minLength": 1, "maxLength": 100},
        "version": {"type": "string", "minLength": 1, "maxLength": 255},
        "websiteUrl": {"type": "string"},
        "repository": {"type": "object", "properties": {"url": {"type": "string"}, "source": {"type": "string"}}},
        "packages": {"type": "array", "items": {
            "type": "object", "required": ["registryType", "identifier", "transport"],
            "properties": {"registryType": {"enum": ["npm", "pypi", "oci", "nuget", "mcpb"]},
                           "identifier": {"type": "string"}, "version": {"type": "string"},
                           "transport": {"type": "object", "required": ["type"],
                                         "properties": {"type": {"enum": ["stdio", "streamable-http", "sse"]}}}}}},
        "remotes": {"type": "array", "items": {
            "type": "object", "required": ["type", "url"],
            "properties": {"type": {"enum": ["streamable-http", "sse"]}, "url": {"type": "string",
                                                                               "pattern": "^https?://"}}}},
        "_meta": {"type": "object"},
    },
    "anyOf": [{"required": ["remotes"], "properties": {"remotes": {"minItems": 1}}},
              {"required": ["packages"], "properties": {"packages": {"minItems": 1}}}],
}


def validate_server_json(doc: dict) -> list[str]:
    v = jsonschema.Draft202012Validator(SERVER_JSON_SCHEMA)
    errors = []
    for e in sorted(v.iter_errors(doc), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        errors.append(f"{loc}: remotes 또는 packages 가 1개 이상 필요합니다" if e.validator == "anyOf"
                      else f"{loc}: {e.message}")
    return errors


# ── 서명 (게시자 키, RS256 · EdDSA) ──
def signature_payload(doc: dict) -> bytes:
    body = {k: v for k, v in doc.items() if k != "_meta"}
    meta = {k: v for k, v in (doc.get("_meta") or {}).items() if k != SIG_KEY}
    if meta:
        body["_meta"] = meta
    return canonical_json(body).encode()


def sign_server_json(doc: dict, private_key, kid: str) -> dict:
    payload = signature_payload(doc)
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        alg, sig = "EdDSA", private_key.sign(payload)
    else:
        alg, sig = "RS256", private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    out = dict(doc)
    out["_meta"] = {**(doc.get("_meta") or {}),
                    SIG_KEY: {"alg": alg, "kid": kid, "sig": base64.urlsafe_b64encode(sig).decode().rstrip("=")}}
    return out


def trusted_keys(db: Session) -> dict[str, str]:
    row = db.get(Setting, "registry.trusted_keys")
    return dict(row.value or {}) if row else {}


def verify_signature(db: Session, doc: dict) -> tuple[bool, bool, str | None]:
    """(서명 존재, 유효, kid)."""
    sig = (doc.get("_meta") or {}).get(SIG_KEY)
    if not sig:
        return False, False, None
    pem = trusted_keys(db).get(sig.get("kid", ""))
    if not pem:
        return True, False, sig.get("kid")
    raw = base64.urlsafe_b64decode(sig["sig"] + "=" * (-len(sig["sig"]) % 4))
    key = serialization.load_pem_public_key(pem.encode())
    try:
        if isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(raw, signature_payload(doc))
        elif isinstance(key, rsa.RSAPublicKey):
            key.verify(raw, signature_payload(doc), padding.PKCS1v15(), hashes.SHA256())
        else:
            return True, False, sig.get("kid")
    except InvalidSignature:
        return True, False, sig.get("kid")
    return True, True, sig.get("kid")


def require_signature_setting(db: Session) -> bool:
    row = db.get(Setting, "registry.require_signature")
    return bool(row.value) if row else False


# ── 게시·승인 ──
def find_item(db: Session, type_: str, name: str) -> CatalogItem | None:
    return db.scalars(select(CatalogItem).where(CatalogItem.type == type_, CatalogItem.name == name)).first()


def _apply_version(item: CatalogItem, ver: CatalogVersion) -> None:
    item.version = ver.version
    keep = {k: v for k, v in (item.item_metadata or {}).items() if k.startswith("_anx_")}  # 심사·즉시 사용 기록은 버전과 무관
    item.item_metadata = {**(ver.item_metadata or {}), **keep}
    item.signature_verified = ver.signature_verified
    meta = ver.item_metadata or {}
    if item.type == "mcp_server":
        item.description = meta.get("description")
        item.display_name = meta.get("title") or item.display_name
        item.license = (meta.get("_meta") or {}).get("license") or item.license
        auth = (meta.get("_meta") or {}).get("auth") or {}
        item.auth_scheme = auth.get("type") or item.auth_scheme
    elif item.type == "a2a_agent":
        item.description = meta.get("description")
        item.display_name = meta.get("name") or item.display_name
        schemes = list((meta.get("securitySchemes") or {}).keys())
        item.auth_scheme = schemes[0] if schemes else "none"


def _external_auth(meta: dict) -> bool:
    auth = (meta.get("_meta") or {}).get("auth") or {}
    as_url = str(auth.get("authorization_server") or "").rstrip("/")
    from app.core.security import issuer
    # oauth2 인데 인가 서버가 플랫폼 issuer 로 적혀 있지 않으면 등록 때 업스트림 메타데이터(RFC 9728)에서 찾는다 — 외부일 수 있다 (59th)
    oauth_unpinned = auth.get("type") == "oauth2" and as_url != issuer()
    return bool(auth.get("token_endpoint") or auth.get("resource_metadata") or (as_url and as_url != issuer()) or oauth_unpinned)


def publish(db: Session, type_: str, name: str, version: str, metadata: dict, actor: str, *,
            source: str = "registry", source_id: str | None = None, auto_approve: bool = False,
            require_signature: bool = False, tags: list[str] | None = None, license: str | None = None,
            display_name: str | None = None, description: str | None = None, owner: str | None = None,
            auth_scheme: str | None = None) -> tuple[CatalogItem, CatalogVersion, bool]:
    """게시 — (item, version, created). 같은 버전 재게시는 내용이 같으면 멱등, 다르면 409."""
    reserved = sorted(str(k) for k in (metadata or {}) if str(k).startswith("_anx_"))
    if reserved:  # 심사·즉시 사용 기록(`_anx_*`)은 승인·등록이 쓴다 — 게시자가 도구 위험도·신뢰를 미리 넣지 못하게 (D19-④)
        raise ApiError(422, "reserved_metadata", f"게시 메타데이터에 예약 키를 쓸 수 없습니다: {', '.join(reserved)}")
    sig_verified = False
    if type_ == "mcp_server":
        errors = validate_server_json(metadata)
        if errors:
            raise ApiError(422, "invalid_server_json", "server.json 검증 실패", extra={"errors": errors})
        present, valid, kid = verify_signature(db, metadata)
        if present and not valid:
            raise ApiError(422, "invalid_signature", f"서명 검증 실패 (kid={kid})")
        if (require_signature or require_signature_setting(db)) and not valid:
            raise ApiError(422, "signature_required", "신뢰할 수 있는 게시자 서명이 필요합니다.")
        sig_verified = valid
    item = find_item(db, type_, name)
    created = item is None
    if item is None:
        item = CatalogItem(id=new_id("cat"), type=type_, name=name, source=source, source_id=source_id,
                           status="pending", version=version, item_metadata=metadata, owner=owner or actor,
                           tags=tags or [], license=license, display_name=display_name, description=description,
                           auth_scheme=auth_scheme)
        db.add(item)
        db.flush()
    else:
        if tags is not None:
            item.tags = tags
    existing = db.scalars(select(CatalogVersion).where(CatalogVersion.item_id == item.id,
                                                       CatalogVersion.version == version)).first()
    if existing:
        if canonical_json(existing.item_metadata) != canonical_json(metadata):
            raise conflict(f"{name}@{version} 은 이미 다른 내용으로 게시되었습니다. 버전을 올리세요.",
                           code="version_exists")
        return item, existing, False
    ver = CatalogVersion(id=new_id("cver"), item_id=item.id, version=version, item_metadata=metadata,
                         status="pending", signature_verified=sig_verified, created_by=actor)
    db.add(ver)
    db.flush()
    if created:
        _apply_version(item, ver)
        item.display_name = display_name or item.display_name
        item.description = description or item.description
        item.license = license or item.license
    db.commit()
    audit.append("registry.publish", actor=actor, target=f"{type_}:{name}@{version}",
                 data={"item_id": item.id, "source": source, "signature_verified": sig_verified})
    if auto_approve and _external_auth(metadata):  # 외부 인가 서버로 사용자 토큰이 가는 항목은 사람 승인 (D20-②)
        auto_approve = False
        audit.append("registry.auto_approve.hold", actor=actor, target=f"{type_}:{name}", data={"version": version, "reason": "external_auth"})
    if auto_approve:
        approve(db, item, version, actor="system:auto-approve", note=f"source {source} auto_approve")
    else:
        notify("registry.pending", f"등록 승인 대기: {name}@{version}", f"{type_} 게시 — {actor}",
               severity="warn", link="/console/registry/approvals", audience_role="operator",
               dedupe_key=f"registry.pending:{item.id}:{version}")
    return item, ver, True


def _version(db: Session, item: CatalogItem, version: str | None, status: str = "pending") -> CatalogVersion:
    stmt = select(CatalogVersion).where(CatalogVersion.item_id == item.id)
    if version:
        stmt = stmt.where(CatalogVersion.version == version)
    else:
        stmt = stmt.where(CatalogVersion.status == status).order_by(CatalogVersion.created_at.desc())
    ver = db.scalars(stmt).first()
    if ver is None:
        raise bad_request(f"{item.name} 에 {version or status} 버전이 없습니다.", code="no_version")
    return ver


def approve(db: Session, item: CatalogItem, version: str | None, actor: str, note: str | None = None) -> CatalogItem:
    ver = _version(db, item, version)
    if ver.status == "rejected":
        raise bad_request("거부된 버전은 승인할 수 없습니다. 새 버전을 게시하세요.")
    for other in db.scalars(select(CatalogVersion).where(CatalogVersion.item_id == item.id,
                                                         CatalogVersion.status == "approved",
                                                         CatalogVersion.id != ver.id)):
        other.status = "superseded"
    ver.status, ver.decided_by, ver.decided_at, ver.note = "approved", actor, utcnow(), note
    _apply_version(item, ver)
    if item.status in ("pending", "rejected", "deprecated"):
        item.status = "approved"
    db.commit()
    audit.append("registry.approve", actor=actor, target=f"{item.type}:{item.name}@{ver.version}", decision="approved",
                 data={"item_id": item.id, "note": note})
    return item


def reject(db: Session, item: CatalogItem, version: str | None, actor: str, note: str | None = None) -> CatalogItem:
    ver = _version(db, item, version)
    ver.status, ver.decided_by, ver.decided_at, ver.note = "rejected", actor, utcnow(), note
    if item.status == "pending":
        item.status = "rejected"
    db.commit()
    audit.append("registry.reject", actor=actor, target=f"{item.type}:{item.name}@{ver.version}", decision="rejected",
                 data={"item_id": item.id, "note": note})
    return item


# ── 등록(즉시 사용) — 타입별 인스턴스 생성 핸들러 (각 Epic 이 등록) ──
RegisterHandler = Callable[[Session, CatalogItem, str, dict], dict]
REGISTER_HANDLERS: dict[str, RegisterHandler] = {}


def register_handler(type_: str):
    def deco(fn: RegisterHandler) -> RegisterHandler:
        REGISTER_HANDLERS[type_] = fn
        return fn
    return deco


def register(db: Session, item: CatalogItem, actor: str, options: dict | None = None) -> dict:
    if item.status not in ("approved", "active"):
        raise bad_request(f"승인된 항목만 등록할 수 있습니다 (현재 {item.status}).", code="not_approved")
    handler = REGISTER_HANDLERS.get(item.type)
    if handler is None:
        raise bad_request(f"{item.type} 등록 핸들러가 없습니다.", code="no_handler")
    instance = handler(db, item, actor, options or {})
    item.status = "active"
    item.ref_id = instance["id"]
    db.commit()
    audit.append("catalog.register", actor=actor, target=f"{item.type}:{item.name}", data={"instance": instance["id"]})
    return instance


def namespace_of(name: str) -> str:
    return slugify(name.split("/")[-1])


@register_handler("mcp_server")
def _register_mcp(db: Session, item: CatalogItem, actor: str, options: dict) -> dict:
    """기본 MCP 인스턴스 생성 — 도구 조회·헬스·기본 정책은 mcp 서비스가 확장한다."""
    from app.models import McpServer
    meta = item.item_metadata or {}
    ns = options.get("namespace") or namespace_of(item.name)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", ns):
        raise bad_request("namespace 형식 오류 (소문자·숫자·하이픈)")
    srv = db.get(McpServer, ns)
    remotes = meta.get("remotes") or []
    packages = meta.get("packages") or []
    endpoint = options.get("endpoint") or (remotes[0]["url"] if remotes else None)
    transport = "streamable-http" if endpoint else "stdio"
    command: list[str] = []
    if not endpoint and packages:
        pkg = packages[0]
        hint = pkg.get("runtimeHint") or {"npm": "npx", "pypi": "uvx", "oci": "docker"}.get(pkg["registryType"], "")
        command = [hint, pkg["identifier"]] if hint != "docker" else ["docker", "run", "-i", "--rm", pkg["identifier"]]
    auth = dict((meta.get("_meta") or {}).get("auth") or {})
    own = f"mcp/{ns}/"  # 게시 메타데이터는 플랫폼 시크릿을 묶을 수 없다 — 시크릿 연결은 운영자가 설정 화면(gateway:write)에서
    if not str(auth.get("secret_ref") or own).startswith(own):
        auth.pop("secret_ref")
    if isinstance(auth.get("tls"), dict):
        auth["tls"] = {k: v for k, v in auth["tls"].items()
                       if k not in ("ca_secret", "client_cert_secret") or str(v).startswith(own)}
    if srv is None:
        srv = McpServer(id=ns, catalog_item_id=item.id, name=meta.get("title") or item.name, transport=transport,
                        endpoint=endpoint, command=command, server_json=meta, auth=auth, owner=actor)
        db.add(srv)
    elif srv.catalog_item_id not in (None, item.id):
        raise conflict(f"namespace '{ns}' 는 다른 서버가 사용 중입니다.")
    else:
        srv.catalog_item_id, srv.server_json, srv.endpoint, srv.transport, srv.auth = item.id, meta, endpoint, \
            transport, auth or srv.auth
        srv.status = "active"
    db.flush()
    return {"id": srv.id, "type": "mcp_server", "endpoint": endpoint, "transport": transport,
            "gateway_path": f"/mcp/{srv.id}"}


# ── 발견 API 표현 (/v0/servers) ──
def server_entry(item: CatalogItem) -> dict:
    return {"server": item.item_metadata,
            "_meta": {"io.modelcontextprotocol.registry/official": {
                "status": "deprecated" if item.status == "deprecated" else "active",
                "publishedAt": item.created_at.isoformat(), "updatedAt": item.updated_at.isoformat(),
                "isLatest": True},
                "io.agentnexus/catalog": {"id": item.id, "status": item.status, "source": item.source,
                                          "signatureVerified": item.signature_verified, "instance": item.ref_id}}}


DISCOVERABLE = ("approved", "active")


def searchable_text(item: CatalogItem) -> str:
    meta = item.item_metadata or {}
    parts = [item.name, item.display_name or "", item.description or "", " ".join(item.tags or []),
             item.auth_scheme or "", item.license or ""]
    if item.type == "mcp_server":
        m = meta.get("_meta") or {}
        parts += [str(t) for t in (m.get("tools") or [])]
        parts += [r.get("type", "") for r in meta.get("remotes") or []]
    if item.type in ("a2a_agent", "agent_profile"):
        card = meta.get("card", meta)  # Agent Card 는 스킬·보안 스킴까지 색인 (FR-C03)
        for s in card.get("skills") or []:
            parts += [s.get("id", ""), s.get("name", ""), s.get("description", ""), " ".join(s.get("tags") or [])]
        parts += list((card.get("securitySchemes") or {}).keys())
        parts += [str((v or {}).get("type", "")) for v in (card.get("securitySchemes") or {}).values()]
    return " ".join(parts).lower()


def trust_of(item: CatalogItem) -> str | None:
    return ((item.item_metadata or {}).get("_anx_review") or {}).get("trust")


def match_hint(item: CatalogItem, terms: list[str]) -> str | None:
    """이름 밖에서 일치한 도구·스킬 (E01 ⑤ 'jira-link ← jira 일치')."""
    meta = item.item_metadata or {}
    cands = [str(t) for t in ((meta.get("_meta") or {}).get("tools") or [])]
    cands += [s.get("id", "") for s in (meta.get("card", meta).get("skills") or [])]
    for t in terms:
        if t in (item.name + " " + (item.display_name or "")).lower():
            continue
        for c in cands:
            if t in c.lower():
                return f"{c} ← '{t}' 일치"
    return None


def parse_query(q: str | None) -> tuple[list[str], dict[str, str]]:
    """'jira tag:이슈 auth:oauth2' → (자유어, {tag, auth, trust, type, status})."""
    free, keys = [], {}
    for tok in (q or "").split():
        k, sep, v = tok.partition(":")
        if sep and k.lower() in ("tag", "auth", "trust", "type", "status") and v:
            keys[k.lower()] = v
        else:
            free.append(tok.lower())
    return free, keys


def search(db: Session, *, q: str | None = None, type_: str | None = None, statuses: tuple[str, ...] | None = None,
           tag: str | None = None, source: str | None = None, auth: str | None = None, trust: str | None = None,
           limit: int = 50, offset: int = 0, name_contains: str | None = None) -> tuple[list, int]:
    terms, keys = parse_query(q)
    type_, tag = type_ or keys.get("type"), tag or keys.get("tag")
    auth, trust = auth or keys.get("auth"), trust or keys.get("trust")
    if keys.get("status"):
        statuses = tuple(x for x in keys["status"].split(",") if statuses is None or x in statuses) or ("-",)
    stmt = select(CatalogItem).order_by(CatalogItem.type, CatalogItem.name)
    if type_:
        stmt = stmt.where(CatalogItem.type == type_)
    if statuses:
        stmt = stmt.where(CatalogItem.status.in_(statuses))
    if source:
        stmt = stmt.where(CatalogItem.source == source)
    if name_contains:  # 이름 부분 일치 — LIKE 와일드카드는 글자 그대로 (D22-④)
        esc = name_contains.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(func.lower(CatalogItem.name).like(f"%{esc}%", escape="\\"))
    if not (tag or auth or trust or terms):  # 파이썬 거름이 없으면 SQL 로 센 뒤 그 쪽만 읽는다 (D21-⑤: 발견 API 가 전체를 읽지 않게)
        total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
        return list(db.scalars(stmt.offset(offset).limit(limit))), int(total or 0)
    items = list(db.scalars(stmt))
    if tag:
        items = [i for i in items if tag in (i.tags or [])]
    if auth:
        wanted = set(auth.split(","))
        items = [i for i in items if (i.auth_scheme or "none") in wanted]
    if trust:
        wanted = set(trust.split(","))
        items = [i for i in items if (trust_of(i) or "-") in wanted]
    if terms:
        items = [i for i in items if all(t in searchable_text(i) for t in terms)]

        def score(i: CatalogItem) -> int:  # 점수순 — 이름 > 태그 > 도구·스킬·설명
            name, tags = (i.name + " " + (i.display_name or "")).lower(), " ".join(i.tags or []).lower()
            return sum(3 if t in name else 2 if t in tags else 1 for t in terms)
        items.sort(key=lambda i: -score(i))
    return items[offset:offset + limit], len(items)


# ── 미러 동기화 (SCR-E06) ──
def parse_docker_catalog(text: str) -> list[dict]:
    """Docker MCP 카탈로그(YAML: registry: {name: {...}}) → server.json 목록."""
    doc = yaml.safe_load(text) or {}
    out = []
    for key, entry in (doc.get("registry") or {}).items():
        if not isinstance(entry, dict) or not entry.get("image"):
            continue
        image = entry["image"]
        out.append({"name": f"io.docker.mcp/{slugify(key)}",
                    "title": entry.get("title") or key,
                    "description": (entry.get("description") or key)[:100],
                    "version": str(entry.get("version") or (image.split(":")[1] if ":" in image else "latest")),
                    "packages": [{"registryType": "oci", "identifier": image, "transport": {"type": "stdio"}}],
                    "_meta": {"tools": [t.get("name") for t in entry.get("tools") or [] if isinstance(t, dict)],
                              "license": entry.get("license"), "source": "docker-mcp-catalog"}})
    return out


def _fetch_official(base: str) -> list[dict]:
    out, cursor = [], None
    with sync_client(timeout=30) as http:
        for _ in range(50):  # 페이지 상한
            params = {"limit": 100, **({"cursor": cursor} if cursor else {})}
            res = http.get(base.rstrip("/") + "/v0/servers", params=params)
            res.raise_for_status()
            body = res.json()
            for entry in body.get("servers", []):
                out.append(entry.get("server", entry))
            cursor = (body.get("metadata") or {}).get("nextCursor") or (body.get("metadata") or {}).get("next_cursor")
            if not cursor:
                break
    return out


def import_entries(db: Session, source: RegistrySource, entries: list[dict], actor: str) -> dict:
    counts = {"imported": 0, "unchanged": 0, "invalid": 0, "filtered": 0, "errors": []}
    pats = [n.strip() for n in (source.namespaces or []) if n.strip()]
    for doc in entries:
        ns = str(doc.get("name", "")).split("/")[0]
        if pats and not any(fnmatch.fnmatchcase(ns, x) or fnmatch.fnmatchcase(str(doc.get("name", "")), x) for x in pats):
            counts["filtered"] += 1  # 허용 네임스페이스 밖 (E06 ②)
            continue
        own = find_item(db, "mcp_server", str(doc.get("name", "")))
        if own is not None and (own.source != "mirror" or own.source_id != source.id):  # 수동 게시·다른 미러 항목에 버전을 얹지 않는다 (59th)
            counts["invalid"] += 1
            if len(counts["errors"]) < 20:
                counts["errors"].append({"name": doc.get("name"), "error": "name_owned_elsewhere",
                                         "detail": f"같은 이름의 항목이 이미 있습니다 (source={own.source})"})
            continue
        try:
            _, _, created_ver = publish(db, "mcp_server", doc.get("name", ""), str(doc.get("version", "")), doc, actor,
                                        source="mirror", source_id=source.id, auto_approve=source.auto_approve,
                                        require_signature=source.require_signature)
            counts["imported" if created_ver else "unchanged"] += 1
        except ApiError as exc:
            db.rollback()
            counts["invalid"] += 1
            if len(counts["errors"]) < 20:
                counts["errors"].append({"name": doc.get("name"), "error": exc.code, "detail": exc.extra or exc.detail})
    source.last_sync_at = utcnow()
    source.item_count = counts["imported"] + counts["unchanged"]
    source.last_status = (f"{'partial' if counts['invalid'] and source.item_count else 'error' if counts['invalid'] else 'ok'}: "
                          f"imported={counts['imported']} unchanged={counts['unchanged']} invalid={counts['invalid']} "
                          f"filtered={counts['filtered']}")
    source.last_result = {**{k: v for k, v in counts.items()}, "at": utcnow().isoformat(), "total": len(entries)}
    db.commit()
    audit.append("registry.sync", actor=actor, target=source.name, data={k: v for k, v in counts.items()
                                                                          if k != "errors"})
    return counts


def due_sources(db: Session) -> list[RegistrySource]:
    """예약 동기화 대상 (E06 ③) — 주기가 있고 마지막 동기화 후 주기가 지난 원격 소스."""
    now = utcnow()
    return [s for s in db.scalars(select(RegistrySource).where(RegistrySource.enabled.is_(True),
                                                                RegistrySource.sync_interval_s.is_not(None),
                                                                RegistrySource.kind != "file"))
            if s.last_sync_at is None or (now - s.last_sync_at).total_seconds() >= s.sync_interval_s]


def sync_source(db: Session, source: RegistrySource, actor: str) -> dict:
    if not source.enabled:
        raise bad_request("비활성 소스입니다.")
    from app.services import deploy
    if source.kind in ("official", "docker") and deploy.airgap(db):
        raise bad_request("에어갭 모드 — 외부 레지스트리 동기화를 할 수 없습니다. 번들(nexus bundle import)·file 소스를 쓰세요 (ST-09).",
                          code="airgap")
    try:
        if source.kind in ("official", "custom"):
            entries = _fetch_official(source.url or "")
        elif source.kind == "docker":
            with sync_client(timeout=30) as http:
                res = http.get(source.url or "")
                res.raise_for_status()
                entries = parse_docker_catalog(res.text)
        else:
            raise bad_request("file 소스는 /import 로 가져옵니다.")
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 — 원격 오류를 상태로 기록
        source.last_status = f"error: {type(exc).__name__}: {exc}"[:500]
        source.last_result = {"error": source.last_status, "at": utcnow().isoformat()}
        source.last_sync_at = utcnow()
        db.commit()
        raise ApiError(502, "sync_failed", source.last_status) from exc
    return import_entries(db, source, entries, actor)


# ── SBOM·라이선스 (FR-P04, SCR-E08) ──
_LOW = ("mit", "apache", "bsd", "isc", "psf", "python software foundation", "unlicense", "0bsd", "zlib", "cc0",
        "postgresql")
_MED = ("mpl", "lgpl", "epl", "elastic", "elv2", "cddl", "enterprise", "ee/", "commercial")
_HIGH = ("agpl", "gpl", "sspl", "rsal", "busl", "business source")


def license_risk(lic: str | None) -> str:
    v = (lic or "").lower()
    if not v or v in ("unknown", "noassertion"):
        return "unknown"
    if any(x in v for x in _HIGH) and "lgpl" not in v:
        return "high"
    if any(x in v for x in _MED):
        return "medium"
    if any(x in v for x in _LOW):
        return "low"
    return "unknown"


def _dist_license(dist: md.Distribution) -> str | None:
    meta = dist.metadata
    lic = meta.get("License-Expression") or meta.get("License")
    if lic and len(lic) < 80 and "\n" not in lic:
        return lic
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    return classifiers[0] if classifiers else (lic.splitlines()[0][:80] if lic else None)


def sbom(db: Session) -> dict:
    components = []
    seen = set()
    for dist in md.distributions():
        name = dist.metadata.get("Name")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        lic = _dist_license(dist)
        components.append({"type": "library", "name": name, "version": dist.version,
                           "purl": f"pkg:pypi/{name.lower()}@{dist.version}",
                           "licenses": [{"license": {"name": lic}}] if lic else [],
                           "properties": [{"name": "agentnexus:license_risk", "value": license_risk(lic)},
                                          {"name": "agentnexus:scope", "value": "platform"}]})
    for item in db.scalars(select(CatalogItem).where(CatalogItem.status.in_(("approved", "active")))):
        components.append({"type": "service" if item.type != "model" else "machine-learning-model",
                           "name": item.name, "version": item.version, "bom-ref": item.id,
                           "licenses": [{"license": {"name": item.license}}] if item.license else [],
                           "properties": [{"name": "agentnexus:license_risk", "value": license_risk(item.license)},
                                          {"name": "agentnexus:scope", "value": f"catalog:{item.type}"},
                                          {"name": "agentnexus:status", "value": item.status}]})
    summary: dict[str, int] = {}
    for c in components:
        risk = next(p["value"] for p in c["properties"] if p["name"] == "agentnexus:license_risk")
        summary[risk] = summary.get(risk, 0) + 1
    return {"bomFormat": "CycloneDX", "specVersion": "1.6", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
            "metadata": {"timestamp": utcnow().isoformat(),
                         "component": {"type": "application", "name": "AgentNexus", "version": APP_VERSION},
                         "properties": [{"name": f"agentnexus:risk:{k}", "value": str(v)}
                                        for k, v in sorted(summary.items())]},
            "components": components}
