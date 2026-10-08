> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 4차 리뷰(round4, D22 hunk 한정) · 스냅샷 2026-10-08 14:27 KST · [← INDEX.md](../../INDEX.md) · 3차 리뷰: [round3 identity.py.md](../../../../round3/CODE-REVIEW-Lv2/app/api/identity.py.md)

### 04. app/api/identity.py (+ `app/core/auth.py:197-231` · `app/services/identity.py:173-198`)
**파일 요약 / File Summary**
- 역할 / Role: 신원 관리 API — 사용자·역할, 외부 IdP 연동(SCR-J01·J02), 워크로드, Access Token 발급·폐기(FR-L03, SCR-I05), 개인 토큰(PAT·클라이언트 키).
- 진입점 거리 / Distance: 0 — HTTP 라우터.
- LOC: 540 (3차 519 → 540)
- 주요 export: `router`, `_end_idp_sessions`(D22-⑥ 공용화), `AccessTokenIn`
- 의존(내부): `app.core.{auth, security, errors}`, `app.services.{identity, secrets, audit, broker}`, `app.models.{IdpConnection, User, RefreshToken, AccessToken, TokenExchangePolicy}`
- 외부 패키지: FastAPI, SQLAlchemy(`update`·`select` 하위 질의)
- 부수효과 / Side effects: `users.tokens_not_before`·`refresh_tokens.revoked_at` 일괄 갱신, IdP 행·시크릿 쓰기, JWKS 캐시(`_JWKS`) 비우기, 감사.
- 동시성 / Concurrency: 동기 핸들러(스레드풀). 일괄 UPDATE 는 한 트랜잭션.
- 테스트 커버리지 흔적: `test/test_boundaries4.py:31`(C64-01 Access Token 스코프), `test/test_remaining_items2.py:67`(C66-03 내장 issuer·세션 범위·LIKE 이스케이프), 회귀 `test/test_identity_boundaries.py`.
- 4차 변경 요약 / Changes in D22: D22-④ — 대상용(`mcp:`·`a2a:`) Access Token 스코프 ⊆ 그 대상 토큰 교환 정책의 `allowed_scopes`, 정책 없으면 거부(3차 M12, 13차 사용자 결정). D22-⑥ — 내장 IdP issuer 로 외부 IdP 연동 거부, 연동 중지·삭제가 같은 `_end_idp_sessions`(sub 접두 기준, LIKE 이스케이프, 하위 질의 UPDATE)(3차 L23).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/api/identity.py lines=213-228
@router.post("/api/v1/identity/idps", status_code=201)
def create_idp(body: IdpIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    for v in body.role_mapping.values():
        _check_roles([v], p)
    if db.scalars(select(IdpConnection).where(IdpConnection.issuer == body.issuer)).first():
        raise conflict("이미 연동된 issuer 입니다.")
    if body.issuer.rstrip("/") == security.issuer().rstrip("/"):  # 내장 IdP 와 같은 issuer — 내장 토큰과 섞인다 (D22-⑥)
        raise bad_request("내장 IdP 의 issuer 는 외부 IdP 로 연동할 수 없습니다.", code="issuer_reserved")
    idp = IdpConnection(id=new_id("idp"), **body.model_dump(exclude={"client_secret"}))
    if body.client_secret:
        idp.client_secret_ref = f"idp/{idp.id}/client_secret"
        secrets.put(db, idp.client_secret_ref, body.client_secret, kind="oauth_client")
    db.add(idp)
    db.commit()
    audit.append("identity.idp.create", actor=p.sub, target=idp.issuer, data={"kind": idp.kind})
    return _idp_out(idp)
```
**분석 / Analysis (Level 2)**
- L219-220(D22-⑥): 외부 IdP 의 issuer 가 내장 IdP issuer(`PUBLIC_BASE_URL/idp`)와 같으면 400 `issuer_reserved`. 끝 `/` 차이도 막는다(`rstrip("/")`).
- 왜 이걸로 충분한가: 토큰 라우팅은 `iss` **완전 일치**로 내장/외부를 가른다(`app/core/auth.py:219` 내장, `:251-252` 외부 IdP 조회). 대소문자·포트 표기가 다른 issuer 는 내장 토큰과 섞이지 않으므로 정확한 문자열 비교로 충분하다.
- 흔한 실수: 중복 issuer 검사(L217)와 순서 — 같은 issuer 409 가 먼저라 내장 issuer 는 언제나 400 으로 끝난다(내장은 DB 행이 없음). 문제없다.

```python path=app/api/identity.py lines=244-259
    if "admin" in (idp.role_mapping or {}).values() and not _is_admin(p) and any(
            k != "name" and not (k in ("enabled", "login_enabled") and v is False) for k, v in body.items()):
        raise forbidden("admin 역할로 매핑된 IdP 의 신뢰 설정은 플랫폼 관리자만 바꿀 수 있습니다.", code="admin_grant_forbidden")
    disabling = idp.enabled and body.get("enabled") is False
    for k, v in body.items():
        if k in allowed:
            setattr(idp, k, v)
    ended = _end_idp_sessions(db, idp)[1] if disabling else 0  # 연동 중지 = 그 IdP 사용자 세션 종료 (SCR-J01)
    if body.get("client_secret"):
        idp.client_secret_ref = idp.client_secret_ref or f"idp/{idp.id}/client_secret"
        secrets.put(db, idp.client_secret_ref, body["client_secret"], kind="oauth_client")
    _JWKS.pop(idp.issuer, None)
    db.commit()
    audit.append("identity.idp.update", actor=p.sub, target=idp.issuer, data={k: v for k, v in body.items()
                                                                              if k != "client_secret"} | ({"sessions_ended": ended} if disabling else {}))
    return _idp_out(idp) | ({"sessions_ended": ended} if disabling else {})
```
**분석 / Analysis (Level 2)**
- L247, L251: 연동 중지(`enabled: true → false`)일 때만 세션 종료. 59th 의 admin 매핑 IdP 보호(L244-246)는 그대로다.
- L251: `_end_idp_sessions(db, idp)[1]` — 반환 (사용자 수, 폐기한 refresh 수) 중 refresh 수를 응답·감사에 쓴다.

```python path=app/api/identity.py lines=262-287
def _end_idp_sessions(db: Session, idp: IdpConnection) -> tuple[int, int]:
    """그 IdP 로 들어온 사용자(sub=ext:{id}:*)의 세션 종료 — tokens_not_before(액세스 토큰도 다음 요청부터 401) + refresh 폐기.
    연동 중지·삭제가 같은 기준을 쓴다(issuer 기준이면 내장 issuer 와 같은 IdP 삭제가 로컬 사용자 전원을 끊는다).
    하위 질의 UPDATE — 사용자가 많아도 IN 목록 상한에 걸리지 않는다. 반환 (사용자 수, 폐기한 refresh 수) (D22-⑥)."""
    now = utcnow()
    esc = idp.id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # idp_… 의 _ 는 LIKE 와일드카드
    mine = User.sub.like(f"ext:{esc}:%", escape="\\")
    users = db.execute(update(User).where(mine).values(tokens_not_before=now)
                       .execution_options(synchronize_session=False)).rowcount
    revoked = db.execute(update(RefreshToken).where(RefreshToken.user_id.in_(select(User.id).where(mine)),
                                                    RefreshToken.revoked_at.is_(None)).values(revoked_at=now)
                         .execution_options(synchronize_session=False)).rowcount
    return users, revoked


@router.delete("/api/v1/identity/idps/{idp_id}")
def delete_idp(idp_id: str, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    idp = db.get(IdpConnection, idp_id)
    if idp is None:
        raise not_found("idp")
    # 그 IdP 로 들어온 사용자의 세션 종료 — 발급한 토큰 무효화·갱신 토큰 폐기 (D20-②, 기준은 연동 중지와 같게 D22-⑥)
    users, ended = _end_idp_sessions(db, idp)
    db.delete(idp)
    db.commit()
    audit.append("identity.idp.delete", actor=p.sub, target=idp.issuer, data={"users_signed_out": users, "refresh_revoked": ended})
    return {"deleted": idp_id, "users_signed_out": users}
```
**분석 / Analysis (Level 2)**
- L266-268(D22-⑥): 대상 = `User.sub LIKE 'ext:<idp id>:%'`. 외부 사용자 sub 형식은 `ext:{idp.id}:{원래 sub}`(`app/core/auth.py:169`)라 정확하다. 전에는 삭제가 **issuer** 기준이라 같은 issuer 를 가진 다른 행(예: 내장 issuer 와 같은 IdP)까지 끊을 수 있었다(3차 L23).
- L267: IdP id `idp_…` 의 `_` 는 LIKE 에서 "아무 한 글자" 와일드카드다. 이스케이프하지 않으면 `idpZabc` 처럼 닮은 id 의 사용자까지 걸린다(C66-03 이 `_`→`Z` 로 확인). `escape="\\"` 와 짝을 맞췄다.
- L269-273: 사용자 목록을 파이썬으로 읽어 `IN (…)` 을 만들지 않고 **하위 질의 UPDATE** — 사용자가 수만 명이어도 파라미터 상한(PostgreSQL 65535)에 걸리지 않는다. `synchronize_session=False` 는 "세션에 올라온 객체를 고치지 않음" — 이 요청은 그 사용자 객체를 다시 읽지 않으므로 맞다.
- L277-287 `delete_idp`: 세션 종료 → 행 삭제 → 커밋 → 감사.
**짚어볼 점 / Watch-outs**
- [위험:중간] **M4(인접)** "세션 종료" 범위가 **JWT 세션(`tokens_not_before`)과 refresh 토큰뿐**이다. 그 IdP 로 들어온 사용자의 PAT·클라이언트 키(`anx_pat_`·`anx_key_`)는 그대로 살아 있다 — 인증 경로가 PAT 행의 폐기·만료와 사용자 `status` 만 보고 `tokens_not_before` 는 보지 않기 때문(`app/core/auth.py:198-212`). IdP 를 지워도 사용자 행의 `status` 는 `active` 로 남는다. 시나리오: 협력사 IdP 로 로그인한 사용자가 콘솔에서 PAT(기본 90일, `app/services/identity.py:154-155`)를 만든다(외부 사용자도 `kind == "user"` 세션이면 발급 가능 — `app/api/identity.py:55-60`) → 계약 종료로 관리자가 IdP 를 삭제(`users_signed_out: N` 응답) → 그 PAT 로 게이트웨이·API 를 계속 호출. 최소 수정: `_end_idp_sessions` 에서 같은 하위 질의로 `PersonalToken.revoked_at = now`(삭제는 무조건, 연동 중지는 정책 선택), 또는 삭제 시 사용자 `status = "disabled"`. 감사 데이터에 폐기한 PAT 수 추가.

```python path=app/core/auth.py lines=197-212
def authenticate_token(db: Session, token: str) -> Principal:
    if token.startswith(("anx_pat_", "anx_key_")):
        row = db.scalars(select(PersonalToken).where(PersonalToken.token_hash == security.token_hash(token))).first()
        if row is None or row.revoked_at or (row.expires_at and row.expires_at < utcnow()):
            raise unauthorized("토큰이 유효하지 않거나 폐기되었습니다.")
        user = db.get(User, row.user_id)
        if user is None or user.status != "active":
            raise unauthorized("사용자가 비활성 상태입니다.")
        row.last_used_at = utcnow()
        db.commit()
        p = _user_principal(user, token, kind="pat" if row.kind == "pat" else "client_key", token_id=row.id)
        p.scopes = list(row.scopes or [])
        if row.kind == "client_key":
            p.roles = ["user"]
            p.team = row.team or user.team
        return p
```
**분석 / Analysis (Level 2)**
- PAT·클라이언트 키 인증: 해시로 행 조회 → 폐기·만료 → 사용자 `status` → 주체. `tokens_not_before` 비교(JWT 경로 `:229-231`)가 없다 — M4 의 근거.

```python path=app/api/identity.py lines=503-529
@router.post("/api/v1/access-tokens", status_code=201)
def issue_access_token(body: AccessTokenIn, p: Principal = Depends(require("tokens:issue")),
                       db: Session = Depends(get_db)):
    if body.subject != p.sub and db.scalars(select(User.id).where(User.sub == body.subject)).first():
        # 서비스 주체용 — 다른 사람의 sub 로 발급하면 그 사람의 세션·Task 를 열람하는 대리 토큰이 된다
        raise bad_request("다른 사용자의 sub 로는 Access Token 을 발급할 수 없습니다 — 서비스 주체(예: a2a:…, mcp:…)를 쓰세요.",
                          code="subject_is_user")
    if not _audience_ok(db, body.audience):
        raise bad_request("audience 는 게이트웨이 또는 등록된 대상(mcp:<서버 id>·a2a:<에이전트 id>)이어야 합니다.",
                          code="unknown_audience")
    if body.audience != security.API_AUDIENCE:  # 대상용 토큰 스코프는 그 대상 교환 정책의 허용 스코프 안 — 교환과 같은 규칙 (D22-④)
        from app.models import TokenExchangePolicy
        pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == body.audience,
                                                           TokenExchangePolicy.enabled.is_(True))).first()
        if pol is None:
            raise bad_request(f"{body.audience} 의 토큰 교환 정책이 없습니다 — 정책에서 허용 스코프를 먼저 정하세요.",
                              code="no_exchange_policy")
        extra = [s for s in body.scopes if s not in (pol.allowed_scopes or [])]
        if extra:
            raise bad_request(f"{body.audience} 교환 정책이 허용하지 않는 스코프: {' '.join(extra)}", code="scope_not_allowed",
                              allowed=pol.allowed_scopes or [])
    row, token = identity.issue_access_token(db, body.name, body.subject, body.audience, body.scopes,
                                             body.ttl_seconds, issued_by=p.sub)
    db.commit()
    audit.append("token.access.issue", actor=p.sub, target=row.subject,
                 data={"jti": row.id, "aud": row.audience, "scopes": row.scopes, "ttl": body.ttl_seconds})
    return {**_at_out(row), "token": token}
```
**분석 / Analysis (Level 2)**
- L506-509: 다른 **사용자** sub 로는 발급 불가(D19-④). L510-512: audience 허용 목록(D20-②).
- L513-523(D22-④): audience 가 게이트웨이(`agentnexus`)가 아니면 그 대상의 **활성** 교환 정책을 찾고, 없으면 400 `no_exchange_policy`, 요청 스코프 중 정책 밖이 있으면 400 `scope_not_allowed` + 허용 목록. 3차 M12(대상 토큰에 임의 스코프 → 게이트웨이 도구 규칙 우회)가 막혔다.
- 게이트웨이 audience 는 그대로 임의 스코프 — 13차 사용자 결정(`PRD.md:103` D22-④ "게이트웨이 audience 는 그대로").
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L8** "교환과 같은 규칙" 은 **스코프 부분집합**까지만 같다. 교환(`app/services/identity.py:191-198`)은 스코프를 비우면 정책 기본값을 쓰고, 부여할 스코프가 없으면 거부한다(`부여 가능한 스코프 없음`). 직접 발급은 빈 스코프를 그대로 통과시키고(`[]` 의 정책 밖 항목은 없음 — L520-521) `scope: ""` 토큰을 만든다(`app/services/identity.py:144-145`). 스코프 클레임이 비면 "제한 없음" 으로 해석하는 MCP 서버 구현에서는 정책 밖 권한이 된다. TTL 도 교환은 정책 값(기본 300초), 직접 발급은 최대 3600초(L473). 64th 가 C53-01 을 빈 스코프로 바꾼 것은 의도된 선택이므로, 최소 수정은 결정 기록 + 빈 스코프면 `pol.default_scopes` 로 채우기(또는 거부), TTL 은 `min(ttl, pol.ttl_seconds)` 검토.

```python path=app/services/identity.py lines=186-198
        return forbidden(f"{audience}용 토큰을 발급하지 못했습니다(스코프 {' '.join(scopes or []) or '-'}): {reason}",
                         code="token_exchange_failed")

    if pol is None or not pol.enabled:
        raise _deny("교환 정책 없음")
    requested = list(scopes) if scopes else list(pol.default_scopes or pol.allowed_scopes or [])
    granted = [s for s in requested if s in (pol.allowed_scopes or [])]
    denied = [s for s in requested if s not in (pol.allowed_scopes or [])]
    if scopes and denied:
        raise _deny(f"허용되지 않은 스코프: {' '.join(denied)}")
    if not granted:
        raise _deny("부여 가능한 스코프 없음")
    ttl = pol.ttl_seconds
```
**분석 / Analysis (Level 2)**
- 교환 규칙 원문 — L8 비교 대상. `requested` 가 비면 기본값(L191), 정책 밖이 하나라도 있으면 거부(L194-195), 남는 게 없으면 거부(L196-197), TTL 은 정책(L198).

## 파일 평가 / File Assessment
- 강점: (1) 대상용 Access Token 스코프를 교환 정책에 묶어 3차 M12 를 막았다(`app/api/identity.py:513-523`). (2) 세션 종료 기준을 sub 접두 하나로 통일하고 LIKE 와일드카드·IN 상한까지 처리했다(`:262-274`). (3) 내장 issuer 충돌 차단(`:219-220`).
- 위험: (1) [위험:중간] M4 — IdP 삭제·중지 뒤에도 그 사용자 PAT·클라이언트 키가 유효(`:262-274` ↔ `app/core/auth.py:198-212`). (2) [위험:낮음] L8 — 직접 발급 빈 스코프·TTL 이 교환 규칙과 다름.
- 개선 권고(영향순): (1) `_end_idp_sessions` 에 PAT 폐기 추가(또는 사용자 비활성). (2) 빈 스코프 처리·TTL 정렬 결정 기록.
- 파일 위험 등급 / Risk grade: **중간** — [위험:중간] 1(M4, 인접).
- 재읽기 권고 레벨: 2 — 인증 경로(`app/core/auth.py`)와 짝으로 읽어야 "무엇이 끊기고 무엇이 남는지" 가 보인다.

## 3차 리뷰 대비 / vs 3rd Review
| 3차 지적 | 4차 상태 | 근거(현재 path:line) |
|---|---|---|
| H1 admin 매핑 IdP 신뢰 설정(59th 수정) | 유지 | `app/api/identity.py:244-246` |
| M12 대상 토큰 스코프 무제한 | 해결(스코프 부분집합) · 빈 스코프·TTL(L8) | `:513-523` |
| L23 IdP 비활성·삭제 기준·내장 issuer·IN 상한 | 해결 · PAT 잔존(M4) | `:219-220`, `:262-274` |
| L32 좁힌 admin PAT(59th 수정) | 유지 | `app/api/identity.py:244`(`_is_admin`) |
