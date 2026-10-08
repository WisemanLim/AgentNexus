"""등록 심사 (SCR-E04·E05·E07·G02·D10, FR-C02·G07·R00·A05) — 승인 대기열 점검·도구 위험도·스코프·버전 diff·승인 효과.

한 대기열에 MCP 서버(server.json)·원격 A2A 에이전트(Agent Card)·플랫폼 Agent Card 게시(프로파일)가 함께 들어온다.
서명·스키마 점검이 실패하면 승인할 수 없다(MSG-007). 72시간 넘게 대기하면 자동 반려(만료, ST-06).
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.db import utcnow
from app.models import A2AAgent, AgentProfile, CatalogItem, CatalogVersion, GatewayPolicy, McpServer, ToolRule
from app.services import a2a_proto as ap
from app.services import audit, registry

EXPIRE = timedelta(hours=72)
TRUST = ("official", "partner", "community", "external")
# 신뢰 등급(E05) → 원격 에이전트 내부 등급 (게이트웨이 판정)
TRUST_TO_TIER = {"official": "verified", "partner": "trusted", "community": "untrusted", "external": "untrusted"}


def tool_risk(name: str, annotations: dict | None = None) -> str:
    """이름·annotations 로 읽기(low)·쓰기(medium)·고위험(high) 자동 분류 — 승인자가 확정 (SCR-E04 ⑥·E05 ⑤)."""
    a = annotations or {}
    if a.get("destructiveHint"):
        return "high"
    from app.services.governance import HIGH_VERBS, WRITE_VERBS, tool_words
    words = tool_words(name.split(".")[-1])  # 단어 단위 (D20-④) — camelCase 포함, address·settings 같은 부분 일치 오탐 없음
    if any(w in HIGH_VERBS or w == "admin" for w in words):
        return "high"
    if a.get("readOnlyHint") is False or any(w in WRITE_VERBS for w in words):
        return "medium"
    return "low"


def scope_kind(scope: str) -> str:
    s = scope.lower()
    if any(x in s for x in ("admin", "manage", ":*")):
        return "admin"
    if any(x in s for x in ("write", "create", "delete", "update", ":rw")):
        return "write"
    return "read"


def _tools(item_type: str, meta: dict) -> list[str]:
    if item_type == "mcp_server":
        return list((meta.get("_meta") or {}).get("tools") or [])
    if item_type in ("a2a_agent", "agent_profile"):
        card = meta.get("card", meta)
        return [s.get("id", "") for s in card.get("skills") or []]
    return []


def _scopes(item_type: str, meta: dict) -> list[str]:
    if item_type == "mcp_server":
        auth = (meta.get("_meta") or {}).get("auth") or {}
        return sorted(set((auth.get("tool_scopes") or {}).values()) | set(auth.get("scopes") or []))
    card = meta.get("card", meta)
    out = []
    for sch in (card.get("securitySchemes") or {}).values():
        for flow in ((sch or {}).get("flows") or {}).values():
            out += list(((flow or {}).get("scopes") or {}).keys())
    return sorted(set(out))


def checks(db: Session, item: CatalogItem, ver: CatalogVersion) -> list[dict]:
    """서명·출처·스키마·SBOM 점검 (E05 ③). level=fail 이 있으면 승인 불가."""
    meta = ver.item_metadata or {}
    out = []

    def add(key, ok, detail, fail=True):
        out.append({"key": key, "ok": bool(ok), "level": "ok" if ok else ("fail" if fail else "warn"), "detail": detail})
    if item.type == "mcp_server":
        errs = registry.validate_server_json(meta)
        add("schema", not errs, "server.json 스키마" + (f" 오류 {len(errs)}: {errs[0]}" if errs else " ✓"))
        need = registry.require_signature_setting(db)
        add("signature", ver.signature_verified or not need,
            "게시자 서명 검증 ✓" if ver.signature_verified else ("서명 필요 (정책)" if need else "서명 없음 — 정책상 선택"),
            fail=need)
        add("sbom", bool((meta.get("_meta") or {}).get("sbom")), "SBOM 첨부" if (meta.get("_meta") or {}).get("sbom")
            else "SBOM 없음 — 라이선스 점검은 E08", fail=False)
    elif item.type in ("a2a_agent", "agent_profile"):
        card = meta.get("card", meta)
        errs = ap.validate_card(card)
        add("schema", not errs, "Agent Card 스키마" + (f" 오류 {len(errs)}: {errs[0]}" if errs else " ✓"))
        signed = bool(card.get("signatures"))
        add("signature", True, "카드 서명(JWS) ✓" if signed else "카드 서명(JWS) 없음 — partner 이상 부여 시 사유 필요", fail=False)
        if item.type == "a2a_agent":
            url = str(card.get("url") or "")
            add("tls", url.startswith("https://") or url.startswith("http://localhost") or ".test" in url or ":8" in url,
                f"url {url or '없음'}", fail=False)
    src = item.source + (f" ({item.source_id})" if item.source_id else "")
    add("provenance", True, f"출처 {src} · 요청자 {ver.created_by or item.owner or '-'}", fail=False)
    return out


def previous(db: Session, item: CatalogItem, ver: CatalogVersion) -> CatalogVersion | None:
    return db.scalars(select(CatalogVersion).where(CatalogVersion.item_id == item.id, CatalogVersion.id != ver.id,
                                                   CatalogVersion.status.in_(("approved", "superseded")))
                      .order_by(CatalogVersion.created_at.desc())).first()


def diff(item_type: str, a: dict | None, b: dict) -> dict:
    """버전 메타데이터 diff (E07 ④) — 도구·스코프·엔드포인트·라이선스 +/−/~ 와 위험 변경 수."""
    a = a or {}
    ta, tb = set(_tools(item_type, a)), set(_tools(item_type, b))
    sa, sb = set(_scopes(item_type, a)), set(_scopes(item_type, b))
    ch = []
    for t in sorted(tb - ta):
        r = tool_risk(t) if item_type == "mcp_server" else "low"
        ch.append({"op": "+", "path": "tools", "value": t, "risk": r})
    ch += [{"op": "-", "path": "tools", "value": t, "risk": "low"} for t in sorted(ta - tb)]
    for s in sorted(sb - sa):
        ch.append({"op": "+", "path": "scopes", "value": s, "risk": "high" if scope_kind(s) != "read" else "low"})
    ch += [{"op": "-", "path": "scopes", "value": s, "risk": "low"} for s in sorted(sa - sb)]
    for key in ("remotes", "url", "license", "version"):
        if a.get(key) != b.get(key) and (a or key != "version"):
            ch.append({"op": "~", "path": key, "value": f"{a.get(key)} → {b.get(key)}", "risk": "low"})
    risky = [c for c in ch if c["risk"] == "high"]
    return {"changes": ch, "count": len(ch), "risky": len(risky),
            "reapproval": any(c["path"] == "tools" and c["op"] == "+" and c["risk"] == "high" for c in ch)}


def expire_old(db: Session) -> int:
    """72시간 넘은 대기 → 만료(자동 반려, ST-06)."""
    cutoff = utcnow() - EXPIRE
    n = 0
    for v in db.scalars(select(CatalogVersion).where(CatalogVersion.status == "pending", CatalogVersion.created_at < cutoff)):
        v.status, v.decided_by, v.decided_at, v.note = "expired", "system:expire", utcnow(), "72시간 경과 자동 반려"
        item = db.get(CatalogItem, v.item_id)
        if item is not None and item.status == "pending":
            item.status = "rejected"
        n += 1
        audit.append("registry.expire", actor="system", target=f"{item.type}:{item.name}@{v.version}" if item else v.id)
    if n:
        db.commit()
    return n


def queue_row(db: Session, item: CatalogItem, ver: CatalogVersion) -> dict:
    meta = ver.item_metadata or {}
    prev = previous(db, item, ver)
    ann = (meta.get("_meta") or {}).get("annotations") or {}
    tools = [{"name": t, "risk": tool_risk(t, ann.get(t))} for t in _tools(item.type, meta)] if item.type == "mcp_server" else \
        [{"name": t, "risk": "low"} for t in _tools(item.type, meta)]
    ck = checks(db, item, ver)
    return {"checks": ck, "approvable": not any(c["level"] == "fail" for c in ck),
            "scopes": [{"scope": s, "kind": scope_kind(s)} for s in _scopes(item.type, meta)], "tools": tools,
            "diff": diff(item.type, prev.item_metadata if prev else None, meta) if prev else None,
            "previous_version": prev.version if prev else None,
            "elapsed_s": int((utcnow() - ver.created_at).total_seconds()),
            "expires_at": (ver.created_at + EXPIRE).isoformat(), "kind": (meta.get("kind") or item.type)}


def apply_approval(db: Session, item: CatalogItem, ver: CatalogVersion, actor: str, *, trust: str | None = None,
                   tool_risks: dict | None = None, allowlist_group: str | None = None) -> dict:
    """승인 효과 (E05 ⑤⑥⑦) — 유형별 활성화·도구 위험도·신뢰 등급·허용목록. 기본은 거부 유지."""
    effects: list[str] = []
    meta = ver.item_metadata or {}
    if item.type == "a2a_agent" and item.ref_id:
        a = db.get(A2AAgent, item.ref_id)
        if a is not None:
            a.status = "active"
            if trust:
                a.trust_tier = TRUST_TO_TIER[trust]
            effects.append(f"원격 에이전트 {a.id} 활성 · 신뢰 {a.trust_tier}")
    if item.type == "agent_profile" and meta.get("kind") == "a2a_card":
        pr = db.get(AgentProfile, item.ref_id or item.name)
        if pr is not None:
            pr.a2a_card, pr.a2a_status, pr.a2a_exposed = meta.get("card") or {}, "published", True
            effects.append(f"프로파일 {pr.id} A2A 게시 (/a2a/platform/{pr.id})")
    if item.type == "mcp_server":
        prev = (item.item_metadata or {}).get("_anx_review") or {}  # 재승인은 이전 확정값 위에 덮는다
        rev = {"tool_risks": {**(prev.get("tool_risks") or {}), **(tool_risks or {})}, "trust": trust or prev.get("trust"),
               "by": actor}
        item.item_metadata = {**(item.item_metadata or {}), "_anx_review": rev}
        srv = db.get(McpServer, item.ref_id) if item.ref_id else None
        if srv is not None and tool_risks:
            for tool, risk in tool_risks.items():
                row = db.scalars(select(ToolRule).where(ToolRule.server_id == srv.id, ToolRule.tool == tool)).first()
                if row is None:
                    row = ToolRule(id=new_id("tr"), server_id=srv.id, tool=tool, allowed=risk != "high")
                    db.add(row)
                row.risk, row.approval_required = risk, risk == "high"
            effects.append(f"도구 위험도 {len(tool_risks)}건 반영 (고위험 = 사람 승인)")
        if trust:
            effects.append(f"신뢰 등급 {trust}")
    if allowlist_group:
        target_type = "agent" if item.type == "a2a_agent" else "tool"
        target = item.ref_id if item.type == "a2a_agent" else f"{item.ref_id or item.name.split('/')[-1]}.*"
        db.add(GatewayPolicy(id=new_id("pol"), name=f"allow:{item.name}:{allowlist_group}", subject_type="group",
                             subject=allowlist_group, target_type=target_type, target=target, effect="allow",
                             description=f"승인 시 허용목록 추가 (E05) — {actor}"))
        effects.append(f"허용목록: group:{allowlist_group} → {target}")
    db.commit()
    audit.append("registry.review", actor=actor, target=f"{item.type}:{item.name}@{ver.version}",
                 data={"trust": trust, "tool_risks": tool_risks, "allowlist": allowlist_group, "effects": effects})
    return {"effects": effects}
