> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: [round2 registry.py.md](../../../../round2/CODE-REVIEW-Lv2/app/services/registry.py.md)

### 10. app/services/registry.py + app/api/catalog.py (+ 도구 위험도 분류 hunk: app/services/governance.py·review.py)
**파일 요약 / File Summary**
- 역할 / Role: 내부 레지스트리(카탈로그) — 게시(publish)·승인·등록(즉시 사용)·미러 동기화·발견 API(`/v0/servers`, MCP Registry 호환). D20-② 로 ① 새 버전 게시는 소유자·승인 권한자만(`catalog.py:531-534`) ② 미러 자동 승인이어도 외부 인가 서버로 사용자 토큰이 가는 항목은 보류(`registry.py:151-155`, `:209-211`). D21-⑤ 로 발견 API 가 거름 조건이 없으면 SQL 로 세고 한 페이지만 읽는다(`registry.py:408-410`). 같은 11차 D20-④ 의 도구 위험도 분류(이름 → 단어 비교)는 카탈로그 심사(`review.tool_risk`)와 게이트웨이 기본 규칙(`governance.classify_risk`)이 공유하므로 여기서 함께 본다.
- 진입점 거리 / Distance: 1 — `POST /v0/publish`·`GET /v0/servers`(`app/api/catalog.py:509-538`), 예약 미러 동기화(`app/services/scheduler.py:63-80` → `sync_source` → `import_entries`), 등록(`app/api/catalog.py:311-326` → `register` → `mcp_gateway.refresh_tools`).
- LOC: `registry.py` 591 (2차 스냅샷 575 → +16), `catalog.py` 758
- 주요 export(범위 안): `_external_auth`·`publish`·`approve`·`search`·`import_entries`; `v0_servers`·`v0_publish`; `governance.tool_words`·`HIGH_VERBS`·`WRITE_VERBS`·`classify_risk`·`default_rules`; `review.tool_risk`
- 의존(내부): `app.core.security.issuer`, `audit`, `notify`, `mcp_gateway`(등록 후 인증 발견), 모델 `CatalogItem`·`CatalogVersion`·`McpServer`·`RegistrySource`
- 외부 패키지: SQLAlchemy, PyYAML(미러 파싱)
- 부수효과 / Side effects: 카탈로그 행·버전 생성, 승인 상태 변경, 감사·알림.
- 동시성 / Concurrency: 동기(스레드풀·스케줄러 스레드). 게시 경합은 DB 고유 제약에 의존.
- 테스트 커버리지 흔적: `test/test_identity_boundaries.py:73-84`(C53-04 소유자만), `:86-96`(C53-05 외부 인가 보류), `test/test_remaining_items.py:21-29`(C60-01 신뢰 어휘), `:32-46`(C60-02 SQL 페이지), `test/test_observe_perf2.py:126-134`(C55-04 단어 분류).
- 3차 변경 요약: 위 D20-②·D21-⑤ 와 D20-④(`governance.py:25-37`, `:60`, `review.py:29-33`), D21-⑤ 신뢰 어휘(`governance.py:71-72`).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/registry.py lines=151-155
def _external_auth(meta: dict) -> bool:
    auth = (meta.get("_meta") or {}).get("auth") or {}
    as_url = str(auth.get("authorization_server") or "").rstrip("/")
    from app.core.security import issuer
    return bool(auth.get("token_endpoint") or auth.get("resource_metadata") or (as_url and as_url != issuer()))
```
**분석 / Analysis (Level 2)**
- `_external_auth`(D20-②): 게시 메타데이터 `_meta.auth` 에 `token_endpoint`·`resource_metadata` 가 있거나, `authorization_server` 가 플랫폼 issuer 가 아니면 "외부 인가" 로 본다. 이런 항목은 게이트웨이가 사용자 플랫폼 토큰을 그 외부 AS 로 보내므로(`app/services/mcp_gateway.py:173`) 사람이 승인해야 한다.

```python path=app/services/registry.py lines=158-218
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
```
**분석 / Analysis (Level 2)**
- L164-166(D19-④): 게시 입력의 `_anx_*` 예약 키 거부 — 2차 H2(심사 기록 주입) 해결.
- L190-196: 같은 버전 재게시는 내용이 같으면 멱등, 다르면 409.
- L209-211(D20-②): 자동 승인 소스라도 외부 인가 항목은 보류 + 감사 `registry.auto_approve.hold`.
**짚어볼 점 / Watch-outs**
- [위험:중간] **M5(INDEX §5) — 외부 인가 판정을 메타데이터에서 빼면 자동 승인을 지나고, 첫 등록이 업스트림이 고른 AS 를 저장한다.** `_external_auth` 는 **게시 문서에 적힌** 세 키만 본다. 게시자가 `_meta.auth = {"type": "oauth2"}` 만 적으면 False → 자동 승인(L212-213). 이후 운영자가 그 "승인됨" 항목을 등록하면(`app/api/catalog.py:311-324` — `catalog:write`) `refresh_tools` 가 AS 가 비어 있는 oauth2 서버에 대해 업스트림의 보호 리소스 메타데이터(PRM)를 조회해 거기 적힌 AS·토큰 엔드포인트를 **그대로 DB 에 저장**한다(`app/services/mcp_gateway.py:717-728`, 아래 인용). 그 뒤 모든 사용자의 도구 호출은 사용자 **원본 플랫폼 토큰**을 그 AS 로 보낸다(`app/services/mcp_gateway.py:144-154` → `:173`). 실패 시나리오: 공개 레지스트리(누구나 게시)를 `auto_approve` 미러로 연결한 설치 → 공격자가 그 레지스트리에 `type: oauth2` 만 적은 서버를 게시 → 자동 승인 → 운영자가 "승인된 항목" 으로 믿고 등록 → 그 서버의 PRM 이 `authorization_servers: ["https://attacker-as"]` → 도구를 쓰는 사용자마다 플랫폼 토큰(사용자 권한 전체의 Bearer)이 공격자에게. 2차 H6 의 "기존 결정" 은 **카탈로그 사람 승인을 전제**로 했고(2차 INDEX §5.1 H6, VERIFY:53), D20-② 는 그 전제를 지키려는 것인데 이 경로에서 깨진다. 최소 수정: 자동 승인 보류 조건을 "`type == oauth2` 이고 `authorization_server` 가 플랫폼 issuer 로 **명시되지 않음**" 까지 넓히고, `refresh_tools` 의 첫 발견(TOFU)에서 플랫폼 issuer 가 아닌 AS 를 받으면 저장하지 말고 운영자 확인(재탐색 API)을 요구.

```python path=app/services/mcp_gateway.py lines=716-729
    try:
        if srv.transport == "streamable-http" and srv.auth.get("type") == "oauth2":
            if not srv.auth.get("authorization_server"):
                found = await discover_auth(srv)
                srv.auth.update({k: v for k, v in found.items() if k != "type"})

            def _pol():
                with SessionLocal() as db:
                    s = db.get(McpServer, server_id)
                    s.auth = {**(s.auth or {}), **{k: v for k, v in found.items() if k != "type"}}
                    if (s.auth.get("authorization_server") or security.issuer()) == security.issuer():
                        ensure_exchange_policy(db, s)
                    db.commit()
            await run_in_threadpool(_pol)
```
**분석 / Analysis (Level 2)**
- (M5 근거 — 범위 밖 파일, 인용만) AS 가 비어 있는 oauth2 서버는 업스트림이 알려 주는 AS 를 발견해(L719) 저장한다(L725). 이미 고정된 AS 와 다른 값을 거부하는 `_pinned`(`app/services/mcp_gateway.py:196-204`)는 **처음 값이 없을 때는** 막지 않는다 — 처음 한 번 믿는 방식(TOFU).

```python path=app/services/registry.py lines=393-427
def search(db: Session, *, q: str | None = None, type_: str | None = None, statuses: tuple[str, ...] | None = None,
           tag: str | None = None, source: str | None = None, auth: str | None = None, trust: str | None = None,
           limit: int = 50, offset: int = 0) -> tuple[list, int]:
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
```
**분석 / Analysis (Level 2)**
- L408-410(D21-⑤): 태그·인증·신뢰·자유어 거름이 **없으면** SQL `COUNT` + `OFFSET/LIMIT` 로 한 페이지만 읽는다 — 익명 `/v0/servers` 기본 호출이 카탈로그 전체를 읽지 않게(2차 M20 일부 해결). `stmt.order_by(None).subquery()` 로 정렬을 뺀 서브쿼리에서 세는 것이 정석이다(정렬은 개수에 의미가 없고 비용만 든다).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L24** 거름이 하나라도 있으면(L411) 여전히 **전체 행을 읽어** 파이썬에서 거르고 자른다. `/v0/servers?search=a` 는 익명으로 호출되므로(`app/api/catalog.py:510-515` — `optional_principal`) 같은 비용을 누구나 반복 유발할 수 있다. 자유어(`searchable_text`)는 SQL `LIKE`·전문 검색으로, 태그는 JSON 포함 질의로 옮기거나 익명 호출에 요청 한도.

```python path=app/services/registry.py lines=465-477
def import_entries(db: Session, source: RegistrySource, entries: list[dict], actor: str) -> dict:
    counts = {"imported": 0, "unchanged": 0, "invalid": 0, "filtered": 0, "errors": []}
    pats = [n.strip() for n in (source.namespaces or []) if n.strip()]
    for doc in entries:
        ns = str(doc.get("name", "")).split("/")[0]
        if pats and not any(fnmatch.fnmatchcase(ns, x) or fnmatch.fnmatchcase(str(doc.get("name", "")), x) for x in pats):
            counts["filtered"] += 1  # 허용 네임스페이스 밖 (E06 ②)
            continue
        try:
            _, _, created_ver = publish(db, "mcp_server", doc.get("name", ""), str(doc.get("version", "")), doc, actor,
                                        source="mirror", source_id=source.id, auto_approve=source.auto_approve,
                                        require_signature=source.require_signature)
            counts["imported" if created_ver else "unchanged"] += 1
```
**분석 / Analysis (Level 2)**
- 미러 동기화: 허용 네임스페이스 거름(E06 ②) 뒤 항목마다 `publish(source="mirror", auto_approve=소스 설정)`.
**짚어볼 점 / Watch-outs**
- [위험:중간] **M6(INDEX §5) — 미러가 로컬 항목의 다음 버전을 선점한다(D20-② 의 반대 방향).** D20-② 는 수동 게시가 미러·남의 항목에 버전을 얹는 것을 막았지만(`app/api/catalog.py:531-534`), 미러 동기화(L474-476)는 **같은 이름의 기존 항목이 누구 것이든** 버전을 추가하고, 소스가 `auto_approve` 면 승인까지 한다(`approve` → `_apply_version` 으로 항목 메타데이터가 미러 버전으로 바뀜 — `app/services/registry.py:233-245`). 실패 시나리오: 사내 팀이 `io.github.acme/jira` 를 수동 게시·운영 중 → 공개 레지스트리에 누군가 같은 이름으로 더 높은 버전을 게시 → 네임스페이스 거름이 없거나 넓은 auto_approve 미러가 그 버전을 사내 항목에 추가·승인 → 이후 재등록·발견 API 는 공격자 `remotes` 를 따른다. 최소 수정: `import_entries` 에서 `find_item` 결과가 있고 `source != "mirror"` 이거나 `source_id != source.id` 면 `invalid`(사유 `name_owned_elsewhere`)로 건너뛰기.

```python path=app/api/catalog.py lines=509-538
@router.get("/v0/servers", tags=["registry"])
def v0_servers(db: Session = Depends(get_db), p: Principal | None = Depends(optional_principal),
               search: str | None = None, limit: int = Query(30, ge=1, le=100), cursor: str | None = None,
               include_pending: bool = False):
    statuses = None if include_pending and p and p.has("catalog:approve") else registry.DISCOVERABLE
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page, total = registry.search(db, q=search, type_="mcp_server", statuses=statuses, limit=limit, offset=start)
    nxt = str(start + limit) if start + limit < total else None
    return {"servers": [registry.server_entry(i) for i in page], "metadata": {"count": len(page), "nextCursor": nxt}}


@router.get("/v0/servers/{server_id:path}", tags=["registry"])
def v0_server(server_id: str, db: Session = Depends(get_db)):
    item = db.get(CatalogItem, server_id) or registry.find_item(db, "mcp_server", server_id)
    if item is None or item.type != "mcp_server" or item.status not in registry.DISCOVERABLE:
        raise not_found("server")
    return registry.server_entry(item)


@router.post("/v0/publish", status_code=201, tags=["registry"])
def v0_publish(doc: dict = Body(...), p: Principal = Depends(require("registry:publish")),
               db: Session = Depends(get_db)):
    existing = registry.find_item(db, "mcp_server", str(doc.get("name", "")))
    if existing is not None and not p.has("catalog:approve") and (existing.source == "mirror" or existing.owner not in (None, p.sub)):
        # 새 버전은 그 항목의 게시자(또는 승인 권한자)만 — 다른 팀·미러 항목의 다음 버전 선점 방지 (D20-②)
        raise forbidden("다른 게시자의 항목입니다 — 새 버전은 소유자 또는 카탈로그 승인 권한자만 게시합니다.", code="not_item_owner")
    item, ver, created = registry.publish(db, "mcp_server", str(doc.get("name", "")), str(doc.get("version", "")), doc,
                                          p.sub, source="registry")
    return {"item": item_out(item), "version": {"id": ver.id, "version": ver.version, "status": ver.status},
            "created": created}
```
**분석 / Analysis (Level 2)**
- L513: `include_pending` 은 승인 권한자만, 그 밖은 `DISCOVERABLE`(승인·활성) 상태만.
- L514-516: 커서 = 숫자 오프셋. `search()` 가 전체 개수를 주므로 다음 커서를 정확히 계산.
- L531-534(D20-②): 기존 항목의 새 버전은 소유자(게시자) 또는 `catalog:approve` 만, 미러 항목은 수동 게시 불가 → 403 `not_item_owner`.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L25** L532 `existing.owner not in (None, p.sub)` — 소유자가 **비어 있는** 항목은 누구나(`registry:publish` 를 가진 개발자) 다음 버전을 게시할 수 있다. 지금 생성 경로는 소유자를 채우지만(`app/services/registry.py:182` `owner or actor`, 시드 `system:seed`), 소유자 컬럼 도입 전 행·직접 삽입 행이 있으면 열린다. 또 소유는 **사람 단위**라 같은 팀 동료는 다음 버전을 못 올린다(운영상 불편 — 팀 단위 소유 검토). 최소 수정: `owner is None` 은 승인 권한자만.

```python path=app/services/governance.py lines=25-37
# 도구 이름 → 단어 (snake·kebab·점·camelCase·약어) — 동사 목록과 단어 단위로 비교 (D20-④: createIssue 같은 camelCase 와
# drop·revoke·grant·transfer 같은 동사가 빠지던 것). 부분 문자열이 아니라 단어라 address(add)·settings(set) 같은 오탐이 없다
HIGH_VERBS = frozenset("delete remove drop truncate purge destroy kill terminate transition approve reject execute exec run "
                       "deploy rollback restart grant revoke transfer pay purchase refund wipe".split())
WRITE_VERBS = HIGH_VERBS | frozenset("create update write post put patch send merge close assign comment upload set insert add "
                                     "modify edit rename move archive publish invite share reset cancel disable enable import "
                                     "push commit release schedule trigger invoke stop start link".split())


def tool_words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]
```
```python path=app/services/review.py lines=23-34
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
```
**분석 / Analysis (Level 2)**
- D20-④: 도구 이름을 단어로 나눠(snake·kebab·점·camelCase·약어 — `createIssue` → `create issue`, `HTTPDelete` → `http delete`) 동사 목록과 **단어 단위**로 비교한다. 전에는 부분 문자열 비교라 `address`(add)·`settings`(set) 이 쓰기로 오분류되고, camelCase `createIssue` 는 놓쳤다.
- 게이트웨이 기본 규칙(`classify_risk`, `governance.py:60` — 쓰기면 high = 사람 승인, 아니면 low = 허용)과 카탈로그 심사 제안(`tool_risk`, `review.py:30-33`)이 같은 함수를 쓴다.
**짚어볼 점 / Watch-outs**
- [위험:중간] **M2(INDEX §5) — 단어 단위 비교에 거짓 음성(위험 도구를 low 로)이 남는다(심사 제안은 일부 새로 생김).** 단어 경계가 대문자·구분자로만 정해지므로 ① **소문자로 붙여 쓴 이름**(`deleteuser`, `sendmail`, `flushdb`) ② **활용형**(`deletes`, `deleted_items`) ③ **목록에 없는 동사**(`upsertRecord`, `saveFile`, `submitOrder`, `overwriteFile`, `eraseDisk`, `shutdownServer`, `clearCache`)가 모두 "low" 가 된다. 확인: 이 파일의 `tool_words`·동사 목록을 그대로 옮겨 돌린 결과 위 이름들이 전부 비쓰기로 판정됐다(`createIssue`·`delete_users`·`HTTPDelete`·`writeFile` 은 high). 이전과 비교: 게이트웨이 기본 규칙은 전에도 구분자 경계 정규식이었으므로(2차 `governance.py.md:43-44` 인용 원문 `(^|[._-])(create|…|set)([._-]|$)`) 이 이름들은 **전에도 low** 였다 — 게이트웨이 쪽은 camelCase·동사 보강으로 나아졌을 뿐 이 공백은 그대로다. 반면 카탈로그 심사 제안(`tool_risk`)은 55th 범위 표가 "부분 문자열 오탐(address·settings) 제거" 라고 적은 대로 전에는 부분 문자열 비교였으므로(`test/impl/55th/scenario.md:10` — 이전 원문은 2차 범위 밖이라 직접 대조하지 못함), ①②와 `overwriteFile`(부분 문자열 `write`)은 **심사 제안에서 medium/high → low 로 퇴행**했다. 실패 시나리오: 신규 MCP 서버의 `deleteuser`(또는 `sendmail`) 도구가 기본 규칙으로 `risk=low`·허용 → 사람 승인 없이 바로 호출(2차 M13 "새 도구 자동 허용" 과 겹침), 카탈로그 승인자 화면도 "low" 를 제안한다. 시험은 camelCase·새 동사·읽기 이름만 본다(`test/test_observe_perf2.py:126-134`). 최소 수정: 단어마다 동사 **접두 일치**(`w.startswith(v)` — 길이 4 이상 동사)와 간단한 어미 제거(`-s`·`-ed`·`-ing`)를 더하고, 빠진 동사(`upsert`·`save`·`submit`·`overwrite`·`erase`·`clear`·`flush`·`shutdown`·`reboot`·`install`·`uninstall`·`apply`·`replace`·`sync`·`mutate`) 보강. 동사를 하나도 못 찾은 도구는 untrusted 서버에서 low 대신 medium(승인 권장).

```python path=app/services/governance.py lines=52-72
def classify_risk(tool: dict, trusted: bool = True) -> str:
    """MCP 도구 annotations(readOnlyHint·destructiveHint)와 이름 규칙으로 기본 위험도. annotations 는 서버가 주는 힌트라
    위험을 낮추는 readOnlyHint 는 trusted 등급 서버만 믿는다(D17-④) — 높이는 destructiveHint 는 언제나."""
    ann = tool.get("annotations") or {}
    if ann.get("destructiveHint") is True:
        return "high"
    if ann.get("readOnlyHint") is True and trusted:
        return "low"
    if any(w in WRITE_VERBS for w in tool_words(tool.get("name", ""))):
        return "high"
    return "low"


def default_rules(db: Session, server_id: str, tools: list[dict]) -> list[ToolRule]:
    """즉시 사용 시 '정책 기본값 적용' — 읽기 도구 허용, 쓰기·파괴 도구는 승인 필요. 기존 규칙은 유지."""
    from app.models import CatalogItem
    from app.services import registry
    srv = db.get(McpServer, server_id)
    item = db.get(CatalogItem, srv.catalog_item_id) if srv and srv.catalog_item_id else None
    # 승인자가 정한 신뢰 등급(E05 — official·partner) — 전에는 "trusted" 만 비교해 승인 경로에서는 늘 거짓이었다 (D21-⑤)
    trusted = bool(item) and registry.trust_of(item) in ("official", "partner", "trusted")
```
**분석 / Analysis (Level 2)**
- L56-59: `destructiveHint` 는 언제나 위험을 올리고, 위험을 낮추는 `readOnlyHint` 는 신뢰 서버만 믿는다(D17-④).
- L71-72(D21-⑤, C60-01): 신뢰 판정이 승인 어휘(`official`·`partner`)를 인정한다. 전에는 `"trusted"` 만 비교했는데 승인 API 는 그 값을 만들 수 없어(심사 신뢰 어휘 `app/services/review.py:18`) 승인 경로에서 늘 거짓이었다(60th r1 이 시험의 비현실 값 때문에 이 죽은 경로가 드러나지 않았음을 기록).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L26** 이 수정은 **통제를 완화**하는 방향이다 — 이제 official·partner 서버가 `readOnlyHint: true` 를 붙인 도구는 이름이 `delete_repository` 여도 low(허용)가 된다(L58-59 가 이름 판정보다 먼저). 힌트는 서버가 런타임에 주는 값이라, partner 서버가 탈취되거나 실수하면 사람 승인이 빠진다. D17-④ 의 원래 의도와 같지만 "죽은 경로가 살아났다" 는 사실을 운영자에게 알리고, 최소한 이름이 `HIGH_VERBS` 에 걸리면 힌트로 low 까지는 내리지 않는(medium) 절충을 검토. 또 시험이 함수를 부르지 않고 같은 식을 다시 쓰고 소스 문자열을 단언한다(`test/test_remaining_items.py:21-28`) — `default_rules` 를 실제로 불러 확인하는 편이 회귀를 잡는다.

## 파일 평가 / File Assessment
- 강점: (1) 게시 경로의 예약 키 거부·소유자 확인·외부 인가 보류가 각각 감사와 함께 들어갔다(`app/services/registry.py:164-166`, `:209-211`, `app/api/catalog.py:531-534`). (2) 발견 API 의 기본 호출을 SQL 페이지로(`app/services/registry.py:408-410`). (3) 위험도 분류를 한 함수로 모아 게이트웨이·심사가 같은 결과를 낸다(`app/services/review.py:28-29`).
- 위험: (1) [위험:중간] 외부 인가 보류가 메타데이터 키에만 의존 — PRM TOFU 로 자동 승인 항목이 외부 AS 를 얻음(`app/services/registry.py:151-155` ↔ `app/services/mcp_gateway.py:717-728`). (2) [위험:중간] 미러 동기화가 로컬 항목 버전을 선점·자동 승인(`app/services/registry.py:465-476`). (3) [위험:중간] 단어 단위 분류의 거짓 음성 — 게이트웨이는 기존 공백 유지, 카탈로그 심사 제안은 일부 퇴행(`app/services/governance.py:34-37`, `:60`).
- 개선 권고(영향순): (1) 자동 승인 보류 조건 확대 + 첫 AS 발견 시 운영자 확인. (2) 미러는 자기 항목에만 버전 추가. (3) 접두·어미 처리 + 동사 보강 + 미분류 medium. (4) 익명 검색 경로 SQL 화.
- 파일 위험 등급 / Risk grade: 중간 — 카탈로그·공급망 경계에 [위험:중간] 3건.
- 재읽기 권고 레벨: 2 — 승인 상태가 실제 신뢰를 뜻하는지(자동 승인·TOFU·미러)가 여러 파일에 흩어져 있어 줄 단위 추적이 필요하다.

## 2차 리뷰 대비 / vs 2nd Review
| 2차 지적 (2차 INDEX §5) | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| H2 `_anx_review` 주입 | 해결(D19-④) | `app/services/registry.py:164-166` |
| M17 게시 `_meta.auth` 전송처 키 복사 — auto_approve 경로 | 부분 해결 | 명시 키는 보류 `:209-211`. 키 생략 + PRM 발견 경로(M5) 남음 |
| M18 미러 독 항목이 동기화·보존을 멈춤 | 해결(D19-④) | `app/services/scheduler.py:77-79`(항목 외 예외도 소스 단위 격리) |
| M19 게시자 ≠ 소유자 버전 선점 | 부분 해결 | 수동 게시 `app/api/catalog.py:531-534`. 미러 → 로컬 방향(M6)·소유자 없음(L25) 남음 |
| M20 익명 `/v0/servers` 전체 행 로드 | 부분 해결 | 거름 없음 `:408-410`. 거름 있음 `:411-427` 은 전체 로드(L24) |
| M13 새 도구 `allowed=True` + low 분류 즉시 호출 | 미해결(VERIFY:77 기록) + 분류 거짓 음성 남음(M2) | `app/services/governance.py:60`, `:79-83`(`allowed=True`, `approval_required=risk == "high"`) |
| M16 등록 옵션 `endpoint` 무검증 | 이번 범위 밖(확인하지 않음) | — |
