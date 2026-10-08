> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 4차 리뷰(round4, D22 hunk 한정) · 스냅샷 2026-10-08 14:27 KST · [← INDEX.md](../../INDEX.md) · 3차 리뷰: [round3 registry.py.md](../../../../round3/CODE-REVIEW-Lv2/app/services/registry.py.md)(catalog.py 포함)

### 07. app/api/catalog.py (`/v0` 발견·게시) (+ `app/services/registry.py:395-432` 검색)
**파일 요약 / File Summary**
- 역할 / Role: 통합 카탈로그 API + MCP Registry 호환 `/v0`(발견 `GET /v0/servers`, 게시 `POST /v0/publish`). 검색·페이지는 `registry.search`.
- 진입점 거리 / Distance: 0 — HTTP 라우터(`/v0/servers` 는 **인증 없이도** 열려 있다 — `optional_principal`).
- LOC: 768 (3차 758 → 768) · `app/services/registry.py` 603 (3차 591 → 603)
- 주요 export: `router`·`v0_servers`·`v0_publish`·`V0_ANON_RPS`·`V0_ANON_BURST`(신규) · `registry.search(name_contains=…)`(신규 인자)
- 의존(내부): `app.services.{registry, ratelimit}`, `app.core.auth.optional_principal`
- 외부 패키지: FastAPI, SQLAlchemy(`func.lower`·`like(escape=)`)
- 부수효과 / Side effects: 익명 요청마다 레이트리밋 버킷 키 `rl:v0anon:<IP>` 생성(메모리 또는 Valkey).
- 동시성 / Concurrency: 동기 핸들러(스레드풀).
- 테스트 커버리지 흔적: `test/test_boundaries4.py:160`(C64-06 이름 검색·SQL 경로·익명 429·인증 200), `:180`(C64-07 소유자 없는 항목 403/201).
- 4차 변경 요약 / Changes in D22: D22-④ — `/v0/servers?search=` 를 이름 부분 일치 SQL 로(전체 로드 제거, 3차 L24), 익명 호출 IP 별 5 rps·버스트 30, 소유자 없는 항목의 새 버전은 승인 권한자만(3차 L25).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/api/catalog.py lines=508-526
# ───────── MCP Registry 호환 /v0 (발견 전용) ─────────
V0_ANON_RPS, V0_ANON_BURST = 5.0, 30  # 익명 발견 API — IP 별 (D22-④)


@router.get("/v0/servers", tags=["registry"])
def v0_servers(request: Request, db: Session = Depends(get_db), p: Principal | None = Depends(optional_principal),
               search: str | None = None, limit: int = Query(30, ge=1, le=100), cursor: str | None = None,
               include_pending: bool = False):
    if p is None:  # 인증 없는 호출 — IP 별 요청 한도 (전체 목록을 반복해 읽는 부하를 막는다)
        ok, wait = ratelimit.backend().take(f"rl:v0anon:{request.client.host if request.client else '-'}", V0_ANON_RPS, V0_ANON_BURST)
        if not ok:
            raise ApiError(429, "rate_limited", "발견 API 요청이 너무 많습니다 — 인증하거나 잠시 뒤 다시 시도하세요.",
                           headers={"Retry-After": str(max(1, int(wait) + 1))})
    statuses = None if include_pending and p and p.has("catalog:approve") else registry.DISCOVERABLE
    start = int(cursor) if cursor and cursor.isdigit() else 0
    # search = 서버 이름 부분 일치(MCP 레지스트리 사양) — SQL 로 거르고 SQL 로 페이지 (전체를 읽어 파이썬으로 거르지 않음, D22-④)
    page, total = registry.search(db, type_="mcp_server", statuses=statuses, name_contains=search, limit=limit, offset=start)
    nxt = str(start + limit) if start + limit < total else None
    return {"servers": [registry.server_entry(i) for i in page], "metadata": {"count": len(page), "nextCursor": nxt}}
```
**분석 / Analysis (Level 2)**
- L516-520(D22-④): 인증 주체가 없으면 클라이언트 IP 별 토큰 버킷(`take`) — 넘으면 429 + `Retry-After`. 인증 호출은 한도 밖.
- L521: 승인 권한자만 `include_pending`.
- L523-524: `search` 를 `name_contains` 로 넘겨 **SQL 에서 거르고 SQL 에서 페이지**. MCP Registry 사양의 `search` 는 서버 이름 부분 일치다 — 도구·설명은 보지 않는다(콘솔 `/v1/catalog?q=` 는 그대로 전체 색인, `test/impl/64th/result.md` 의도한 동작 변경).
- L525: 다음 커서 = 오프셋. 단순하지만 행이 추가·삭제되면 페이지 경계가 밀린다(사양상 커서는 불투명 — 등급 없음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L12** 한도 키가 `request.client.host` 다. 이 값은 uvicorn 의 프록시 헤더 신뢰 설정에 달렸다. ① Helm 기본값 `api.forwardedAllowIps: "*"`(`deploy/helm/agentnexus/values.yaml:29`, `templates/api.yaml:47`)이면 **누구의** `X-Forwarded-For` 든 믿는다 — Ingress 가 클라이언트가 보낸 XFF 를 그대로 이어 붙이는 구성에서는 요청마다 XFF 를 바꿔 한도를 완전히 피한다. ② compose prod 는 플래그 없이 기동(`Dockerfile:58`, uvicorn 기본 신뢰 = 127.0.0.1)이라 앞단 리버스 프록시 뒤에서는 **모든 익명 호출이 프록시 IP 하나의 버킷**을 나눠 쓴다 — 익명 클라이언트 하나가 5 rps 를 넘기면 모든 익명 발견이 429. ③ 메모리 백엔드(`app/services/ratelimit.py:25`, `:32-41`)는 버킷을 지우지 않으므로, ①에서 XFF 를 바꿔 가며 부르면 키가 끝없이 늘어 워커 메모리가 자란다. 최소 수정: values 주석의 "운영은 Ingress CIDR 로 좁힌다" 를 차트 검증(`agentnexus.validate`)으로 강제하거나 prod 에서 `*` 경고, 메모리 버킷에 크기 상한(LRU), compose 문서에 `--forwarded-allow-ips` 안내.

```python path=app/services/registry.py lines=395-415
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
```
**분석 / Analysis (Level 2)**
- L410-412(D22-④): `name_contains` 를 소문자로 바꾸고 `\`·`%`·`_` 를 이스케이프한 뒤 `lower(name) LIKE '%…%' ESCAPE '\'`. 사용자가 `_` 를 넣어도 "아무 한 글자" 가 아니라 글자 `_` 로 찾는다 — LIKE 이스케이프는 `\` 를 **먼저** 바꿔야(L411 순서) 이스케이프 문자 자체가 이중 해석되지 않는다.
- L413-415: 파이썬 거름(태그·인증·신뢰·자유어)이 없으면 SQL `COUNT` + `OFFSET/LIMIT` — 발견 API 는 이제 이 경로만 탄다(D21-⑤ 의 남은 부분 3차 L24 해소).
- 성능 메모: `lower(name) LIKE '%x%'` 는 앞 와일드카드라 일반 인덱스를 못 쓴다. 카탈로그 행 수(수백~수천)에서는 문제없다 — 등급 없음.

```python path=app/api/catalog.py lines=537-548
@router.post("/v0/publish", status_code=201, tags=["registry"])
def v0_publish(doc: dict = Body(...), p: Principal = Depends(require("registry:publish")),
               db: Session = Depends(get_db)):
    existing = registry.find_item(db, "mcp_server", str(doc.get("name", "")))
    # 소유자 없는 항목(시드·내장)도 승인 권한자만 — 누구나 다음 버전을 선점하지 못하게 (D22-④)
    if existing is not None and not p.has("catalog:approve") and (existing.source == "mirror" or existing.owner != p.sub):
        # 새 버전은 그 항목의 게시자(또는 승인 권한자)만 — 다른 팀·미러 항목의 다음 버전 선점 방지 (D20-②)
        raise forbidden("다른 게시자의 항목입니다 — 새 버전은 소유자 또는 카탈로그 승인 권한자만 게시합니다.", code="not_item_owner")
    item, ver, created = registry.publish(db, "mcp_server", str(doc.get("name", "")), str(doc.get("version", "")), doc,
                                          p.sub, source="registry")
    return {"item": item_out(item), "version": {"id": ver.id, "version": ver.version, "status": ver.status},
            "created": created}
```
**분석 / Analysis (Level 2)**
- L541-544(D22-④): 승인 권한자가 아니면 미러 항목이거나 `existing.owner != p.sub` 일 때 403. `owner` 가 `None`(시드·내장 항목)이면 `None != p.sub` 가 참이라 **소유자 없는 항목도** 승인 권한자만 새 버전을 낸다 — 3차 L25 해소. 조건 하나로 두 경우를 덮는 간결한 형태지만, 읽는 사람이 `None` 처리를 놓치기 쉬워 주석(L541)이 필요했다.
- 다른 게시 경로 확인: 콘솔 `POST /v1/catalog`(`app/api/catalog.py:98-106`)은 같은 이름이 있으면 409 라 새 버전 경로가 아니다. `create_from_openapi`(`app/api/mcp.py:682`)는 `catalog:approve` 필요. 우회 경로 없음.

## 파일 평가 / File Assessment
- 강점: (1) 발견 API 가 SQL 거름·페이지로 전체 로드를 끝냈다(`app/services/registry.py:410-415`). (2) LIKE 이스케이프 순서가 정확하다(`:411`). (3) 소유자 없는 항목 선점이 막혔다(`app/api/catalog.py:542`).
- 위험: (1) [위험:낮음] L12 — 익명 한도 키가 프록시 헤더 신뢰 설정에 의존(Helm 기본 `*` 면 우회·메모리 증가, compose 면 공용 버킷).
- 개선 권고(영향순): (1) `forwardedAllowIps` 를 prod 에서 좁히도록 검증·경고. (2) 메모리 백엔드 버킷 상한. (3) 커서를 불투명 값으로(선택).
- 파일 위험 등급 / Risk grade: **낮음** — [위험:낮음]만.
- 재읽기 권고 레벨: 3 — 블록 단위로 충분하다.

## 3차 리뷰 대비 / vs 3rd Review
| 3차 지적 | 4차 상태 | 근거(현재 path:line) |
|---|---|---|
| L24 익명 `/v0/servers?search=` 전체 로드 | 해결 · 한도 키 신뢰(L12) | `app/api/catalog.py:516-524`, `app/services/registry.py:410-415` |
| L25 소유자 없는 항목 새 버전 | 해결 | `app/api/catalog.py:541-544` |
| M6 미러의 로컬 항목 선점(59th 수정) | 유지 | (이번 hunk 밖) |
