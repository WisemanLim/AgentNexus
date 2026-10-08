> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: [round2 identity.py.md](../../../../round2/CODE-REVIEW-Lv2/app/api/identity.py.md) · [round2 idp.py.md](../../../../round2/CODE-REVIEW-Lv2/app/api/idp.py.md)

### 09. app/api/identity.py + app/api/idp.py (TOTP 1회)
**파일 요약 / File Summary**
- 역할 / Role: `identity.py` 는 신원 관리 API(사용자·역할·외부 IdP 연동·워크로드·PAT·Access Token). `idp.py` 는 내장 OIDC IdP(로그인·토큰·MFA). 11차 D20-② 로 신원 경계 네 가지가 바뀌었다 — ① admin 역할 부여·관리자 계정 변경·IdP 역할 매핑의 admin 은 admin 만, ② IdP **삭제** 시 그 IdP 사용자 세션 종료, ③ Access Token audience 허용 목록(게이트웨이·등록된 `mcp:`·`a2a:` 대상), ④ TOTP 코드 1회 사용.
- 진입점 거리 / Distance: 0 — HTTP 라우트(`/api/v1/identity/*`, `/api/v1/access-tokens`, `/idp/token`·`/idp/login` 등).
- LOC: `identity.py` 519 (2차 스냅샷 490 → +29), `idp.py` 454 (2차 스냅샷 421 → +33)
- 주요 export(범위 안): `_check_roles`·`create_user`·`patch_user`, `create_idp`·`patch_idp`·`_end_idp_sessions`·`delete_idp`, `_audience_ok`·`issue_access_token`, `idp._mfa_ok`
- 의존(내부): `app.core.auth`(`ROLE_PERMS`·`_JWKS`·외부 IdP 토큰 검증), `app.services.identity`·`secrets`·`audit`·`ratelimit`·`totp`
- 외부 패키지: FastAPI·pydantic·SQLAlchemy
- 부수효과 / Side effects: 사용자·IdP·토큰 행 변경, 세션 무효화(`tokens_not_before`·refresh 폐기), 외부 JWKS 캐시 비우기, Valkey 키 `totp-used:*`.
- 동시성 / Concurrency: 동기 핸들러(스레드풀). TOTP 1회 판정은 Valkey `INCR` 로 원자적.
- 테스트 커버리지 흔적: `test/test_identity_boundaries.py:18-32`(C53-01 audience), `:34-53`(C53-02 admin 부여), `:55-71`(C53-03 IdP 삭제), `:107-114`(C53-07 TOTP 1회). **IdP 패치로 검증 키(`jwks_uri`) 등을 바꾸는 경로의 시험은 없다.**
- 3차 변경 요약: D20-② — `identity.py:129-130`·`:146`·`:163-166`·`:210-211`·`:233-234`·`:266-277`·`:474-481`·`:500-502`, `idp.py:148-149`.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/api/identity.py lines=125-130
def _check_roles(roles: list[str], p: Principal | None = None) -> None:
    bad = [r for r in roles if r not in ROLE_PERMS]
    if bad:
        raise bad_request(f"알 수 없는 역할: {', '.join(bad)}")
    if p is not None and "admin" in roles and "admin" not in (p.roles or []):  # admin 부여는 admin 만 (D20-②)
        raise forbidden("admin 역할은 플랫폼 관리자만 부여할 수 있습니다.", code="admin_grant_forbidden")
```
**분석 / Analysis (Level 2)**
- 역할 검증 공용 함수. 모르는 역할은 400, 그리고(D20-②) 부여하려는 역할에 `admin` 이 있는데 호출자가 admin 이 아니면 403 `admin_grant_forbidden`.
- 흔한 실수: 역할 **이름**으로만 막으면, 그 역할과 동등하거나 일부가 더 넓은 다른 역할을 통한 상승은 막지 못한다(아래 Watch-out).
**짚어볼 점 / Watch-outs**
- [위험:중간] **M8(INDEX §5) — admin 외 역할로의 권한 상승.** `security` 역할은 `identity:write` 를 갖지만 `admin:write`·`gateway:write`·`catalog:write`·`models:write`·`limits:write` 는 없다(`app/core/auth.py:26-31`). L129 는 `admin` 만 보므로 보안 담당은 `PATCH /api/v1/identity/users/<자기 id> {"roles": ["security", "operator"]}` 로 **운영자 권한 전체**(백업·복원 `admin:write`, 시크릿 관리 `gateway:write`, 카탈로그 쓰기 등)를 스스로 얻는다. 2차 M3 의 최소 수정안은 "부여 역할 권한 ⊆ 호출자 권한, admin 부여는 admin 만" 이었고 D20-② 는 뒷부분만 반영했다(53rd scenario "사용자 결정" 은 admin 에 대한 것). 같은 파일 안에 이미 올바른 비교가 있다: Inspector 가상 주체는 `role_perms(r) <= p.permissions()` 로 막는다(`app/api/mcp.py:472-476`). 최소 수정: `_check_roles` 에서 `"*" not in p.permissions()` 이면 `role_perms(r) <= p.permissions()` 를 모든 부여 역할에 요구(자기 계정 역할 변경도 같은 규칙). 범위가 사용자 결정과 다르면 결정 기록 필요.
- [위험:낮음] **L32** L129 는 호출자의 **역할**을 본다. admin 의 PAT 를 `identity:write` 스코프로 좁혀 발급해도(`app/core/auth.py:68-80` — 스코프는 `has()` 에만 적용) 그 토큰으로 새 admin 사용자를 만들 수 있어, 스코프 축소가 사실상 무의미해진다(새 계정으로 전체 권한 회복). 최소 수정: `p.has("*")` 또는 "스코프 없는 대화형 admin 세션" 조건.

```python path=app/api/identity.py lines=144-171
@router.post("/api/v1/identity/users", status_code=201)
def create_user(body: UserIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    _check_roles(body.roles, p)
    if db.scalars(select(User).where(User.username == body.username)).first():
        raise conflict("이미 존재하는 사용자 이름입니다.")
    uid = new_id("usr")
    user = User(id=uid, sub=uid, issuer=security.issuer(), username=body.username, name=body.name, email=body.email,
                password_hash=security.hash_password(body.password), roles=body.roles, team=body.team)
    db.add(user)
    db.commit()
    audit.append("identity.user.create", actor=p.sub, target=user.sub, data={"roles": body.roles})
    return _user_out(user)


@router.patch("/api/v1/identity/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, p: Principal = Depends(require("identity:write")),
               db: Session = Depends(get_db)):
    user = identity.get_user(db, user_id)
    changes = body.model_dump(exclude_none=True)
    if "admin" in (user.roles or []) and "admin" not in (p.roles or []):  # 관리자 계정 변경도 관리자만 (D20-②)
        raise forbidden("플랫폼 관리자 계정은 관리자만 바꿀 수 있습니다.", code="admin_grant_forbidden")
    if "roles" in changes:
        _check_roles(changes["roles"], p)
    for k, v in changes.items():
        setattr(user, k, v)
    db.commit()
    audit.append("identity.user.update", actor=p.sub, target=user.sub, data=changes)
    return _user_out(user)
```
**분석 / Analysis (Level 2)**
- L146: 생성 시 역할 검증(D20-②).
- L163-164(D20-②): 대상이 admin 이면 admin 만 바꿀 수 있다 — 보안 담당이 관리자를 비활성화하거나 팀을 바꾸는 것도 막힌다.
- L165-166: 역할 변경이 있으면 같은 검증.
- L167-168 `setattr` 로 `roles`·`team`·`status`·`name` 을 그대로 반영(pydantic 이 키를 제한).

```python path=app/api/identity.py lines=208-247
@router.post("/api/v1/identity/idps", status_code=201)
def create_idp(body: IdpIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    for v in body.role_mapping.values():
        _check_roles([v], p)
    if db.scalars(select(IdpConnection).where(IdpConnection.issuer == body.issuer)).first():
        raise conflict("이미 연동된 issuer 입니다.")
    idp = IdpConnection(id=new_id("idp"), **body.model_dump(exclude={"client_secret"}))
    if body.client_secret:
        idp.client_secret_ref = f"idp/{idp.id}/client_secret"
        secrets.put(db, idp.client_secret_ref, body.client_secret, kind="oauth_client")
    db.add(idp)
    db.commit()
    audit.append("identity.idp.create", actor=p.sub, target=idp.issuer, data={"kind": idp.kind})
    return _idp_out(idp)


@router.patch("/api/v1/identity/idps/{idp_id}")
def patch_idp(idp_id: str, body: dict, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    idp = db.get(IdpConnection, idp_id)
    if idp is None:
        raise not_found("idp")
    allowed = {"name", "client_id", "audience", "jwks_uri", "groups_claim", "roles_claim", "role_mapping", "enabled",
               "login_enabled", "internal_url", "scopes"}
    if body.get("login_enabled") and not idp.login_enabled and (idp.last_test or {}).get("status") != "ok":
        raise bad_request("콘솔 로그인에 노출하려면 먼저 시험 로그인에 성공해야 합니다 (SCR-J02 ④).", code="test_login_required")
    if isinstance(body.get("role_mapping"), dict):
        _check_roles(list(body["role_mapping"].values()), p)
    disabling = idp.enabled and body.get("enabled") is False
    for k, v in body.items():
        if k in allowed:
            setattr(idp, k, v)
    ended = _end_idp_sessions(db, idp) if disabling else 0  # 연동 중지 = 그 IdP 사용자 세션 종료 (SCR-J01)
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
- L210-211(D20-②): 생성 시 역할 매핑의 대상 역할도 같은 검증 — `admin` 매핑은 admin 만.
- L224-247 `patch_idp`: 허용 키(L229-230)만 반영. L233-234(D20-②): 본문에 `role_mapping` 이 있으면 검증. L235·L239: 비활성화(`enabled: false`)면 그 IdP 사용자 세션 종료. L243: 외부 JWKS 캐시 비우기.
**짚어볼 점 / Watch-outs**
- [위험:높음] **H1(INDEX §5) — 이미 admin 으로 매핑된 IdP 의 검증 설정을 비관리자가 바꿔 admin 토큰을 위조할 수 있다.** L233 의 검증은 본문에 `role_mapping` 이 **있을 때만** 돈다. 그런데 L229-230 허용 키에는 신뢰의 뿌리인 `jwks_uri`·`internal_url`·`audience`·`client_id`·`groups_claim`·`roles_claim` 이 들어 있고, 이들 변경에는 아무 조건이 없다. 외부 IdP 토큰은 Bearer 로 바로 받아들여지며(`app/core/auth.py:251-259`), 서명 키는 `idp.jwks_uri`(없으면 `internal_url` 경유 디스커버리)에서 가져오고(`app/core/auth.py:126-135`), 역할은 토큰의 그룹·역할 클레임을 `role_mapping` 으로 바꿔 매긴다(`app/core/auth.py:158-164`, `:176`). 실패 시나리오: ① 관리자가 Entra IdP 를 연동하며 `{"AgentNexus-Admins": "admin"}` 매핑(흔한 구성) ② `security` 역할 사용자(`identity:write`, admin 아님)가 `PATCH /api/v1/identity/idps/<id> {"jwks_uri": "https://attacker.example/jwks.json"}` — L233 을 건너뛰어 200, L243 이 캐시까지 비워 준다 ③ 자기 키로 `iss`=그 IdP issuer, `aud`=그 IdP audience/client_id, `groups`=`["AgentNexus-Admins"]` 인 JWT 를 서명 ④ 아무 API 에 Bearer 로 제시 → 외부 사용자 upsert 로 `roles=["admin"]` → **플랫폼 관리자**. `groups_claim` 을 자신이 바꿀 수 있는 클레임으로 돌리는 방식도 같다. D20-② 가 막으려던 "IdP 역할 매핑의 admin 은 admin 만" 이 매핑을 건드리지 않는 우회로로 무력화된다. 감사(`identity.idp.update`, L245)에는 남지만 막지는 못한다. 최소 수정: IdP 의 현재 또는 새 `role_mapping` 값 중 하나라도 호출자 권한 밖(특히 `admin`)이면 **모든 패치**를 admin 만(관리자 계정 규칙 L163 과 같은 원리), 최소한 `jwks_uri`·`internal_url`·`audience`·`client_id`·`groups_claim`·`roles_claim` 변경은 admin 만. 시험: 비관리자가 admin 매핑 IdP 의 `jwks_uri` 를 바꾸면 403.

```python path=app/core/auth.py lines=144-164
def _external_verify(idp: IdpConnection, token: str) -> dict:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "RS256")
    if alg not in EXTERNAL_ALGS:
        raise jwt.InvalidAlgorithmError(f"허용하지 않는 서명 알고리즘: {alg}")
    for attempt in (False, True):
        for k in _external_keys(idp, refresh=attempt).get("keys", []):
            if header.get("kid") in (None, k.get("kid")) and k.get("alg") in (None, alg):
                key = jwt.PyJWK(k).key
                return jwt.decode(token, key, algorithms=[alg],
                                  audience=idp.audience or idp.client_id, issuer=idp.issuer, leeway=30)
    raise jwt.InvalidTokenError("signing key not found")


def _map_roles(idp: IdpConnection, claims: dict) -> list[str]:
    values = claims.get(idp.groups_claim, []) or []
    values = values if isinstance(values, list) else [values]
    roles_v = claims.get(idp.roles_claim, []) or []
    values += roles_v if isinstance(roles_v, list) else [roles_v]
    roles = sorted({idp.role_mapping[v] for v in values if v in (idp.role_mapping or {})})
    return roles or ["user"]
```
**분석 / Analysis (Level 2)**
- (H1 근거 — 리뷰 범위 밖 파일, 인용만) 외부 토큰 검증은 비대칭 알고리즘만 허용하고(L141·L147), 키는 `_external_keys(idp)` 가 IdP 설정에서 가져온다. 키를 어디서 받을지(`jwks_uri`·`internal_url`)가 곧 신뢰의 뿌리이므로, 그 설정을 바꿀 수 있는 사람은 그 IdP 가 매핑하는 가장 높은 역할을 가진 것과 같다.

```python path=app/api/identity.py lines=250-277
def _end_idp_sessions(db: Session, idp: IdpConnection) -> int:
    """IdP 사용자(sub=ext:{id}:*)의 refresh 폐기 + tokens_not_before — 발급된 액세스 토큰도 다음 요청부터 401."""
    now, n = utcnow(), 0
    users = list(db.scalars(select(User).where(User.sub.like(f"ext:{idp.id}:%"))))
    for u in users:
        u.tokens_not_before = now
        for r in db.scalars(select(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None))):
            r.revoked_at, n = now, n + 1
    return n


@router.delete("/api/v1/identity/idps/{idp_id}")
def delete_idp(idp_id: str, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    idp = db.get(IdpConnection, idp_id)
    if idp is None:
        raise not_found("idp")
    # 그 IdP 로 들어온 사용자의 세션 종료 — 발급한 토큰 무효화·갱신 토큰 폐기 (D20-②)
    from app.models import RefreshToken
    now = utcnow()
    ids = [u.id for u in db.scalars(select(User).where(User.issuer == idp.issuer))]
    for u in db.scalars(select(User).where(User.id.in_(ids))):
        u.tokens_not_before = now
    ended = db.execute(update(RefreshToken).where(RefreshToken.user_id.in_(ids), RefreshToken.revoked_at.is_(None))
                       .values(revoked_at=now)).rowcount if ids else 0
    db.delete(idp)
    db.commit()
    audit.append("identity.idp.delete", actor=p.sub, target=idp.issuer, data={"users_signed_out": len(ids), "refresh_revoked": ended})
    return {"deleted": idp_id, "users_signed_out": len(ids)}
```
**분석 / Analysis (Level 2)**
- L250-258 `_end_idp_sessions`(비활성화 경로): 사용자 sub 접두 `ext:<idp id>:` 로 찾는다.
- L261-277 `delete_idp`(D20-②): 그 IdP **issuer** 를 가진 사용자의 `tokens_not_before = now` + refresh 폐기 후 IdP 삭제. 발급된 액세스 토큰도 다음 요청부터 401(2차 M4 해결).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L23** ① 비활성화(L253)와 삭제(L269)가 **다른 기준**으로 사용자를 고른다(sub 접두 vs issuer). 지금은 외부 사용자를 만들 때 둘이 함께 정해져(`app/core/auth.py:169`, `:173`) 결과가 같지만, 한쪽만 바뀌면 어긋난다 — 같은 함수를 쓰는 편이 안전하다. ② IdP 생성 시 issuer 가 **내장 IdP issuer 와 같은지** 막지 않는다(L212 는 IdP 끼리만 중복 확인). 그런 IdP 를 삭제하면 L269 가 `issuer == 내장 issuer` 인 **모든 로컬 사용자(관리자 포함)** 를 로그아웃시킨다(`identity:write` 보유자에 의한 전원 로그아웃). ③ L269-273 은 사용자 id 를 모두 메모리에 모아 `IN (…)` 으로 다시 질의 — 수만 명 IdP 면 바인드 파라미터 상한(PostgreSQL 65,535)에 걸려 삭제가 500 이 된다. 최소 수정: 공용 함수 + `UPDATE … WHERE issuer = :iss`(하위 질의) + `create_idp` 에서 `issuer == security.issuer()` 거부.

```python path=app/api/identity.py lines=474-508
def _audience_ok(db: Session, aud: str) -> bool:
    """Access Token audience 허용 목록 (D20-②) — 게이트웨이 API 또는 등록된 MCP 서버·A2A 에이전트."""
    from app.models import A2AAgent, McpServer
    if aud == security.API_AUDIENCE:
        return True
    kind, _, ident = aud.partition(":")
    return bool(ident) and ((kind == "mcp" and db.get(McpServer, ident) is not None)
                            or (kind == "a2a" and db.get(A2AAgent, ident) is not None))


@router.get("/api/v1/access-tokens")
def list_access_tokens(_: Principal = Depends(require("tokens:issue")), db: Session = Depends(get_db),
                       subject: str | None = None):
    stmt = select(AccessToken).order_by(AccessToken.created_at.desc())
    if subject:
        stmt = stmt.where(AccessToken.subject == subject)
    return {"items": [_at_out(t) for t in db.scalars(stmt)]}


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
    row, token = identity.issue_access_token(db, body.name, body.subject, body.audience, body.scopes,
                                             body.ttl_seconds, issued_by=p.sub)
    db.commit()
    audit.append("token.access.issue", actor=p.sub, target=row.subject,
                 data={"jti": row.id, "aud": row.audience, "scopes": row.scopes, "ttl": body.ttl_seconds})
    return {**_at_out(row), "token": token}
```
**분석 / Analysis (Level 2)**
- L474-481 `_audience_ok`(D20-②): 게이트웨이 API(`agentnexus`) 또는 실제로 등록된 `mcp:<서버 id>`·`a2a:<에이전트 id>` 만. URL·`model:` 등 그 밖은 400 `unknown_audience`(2차 M5 — 사용자 결정 "등록된 대상만").
- L496-499: 다른 사용자의 sub 로는 발급 불가(대리 토큰 방지).
**짚어볼 점 / Watch-outs**
- [위험:중간] **M12(INDEX §5) — 등록 대상 audience 토큰의 스코프가 무제한이다.** `scopes` 는 자유 목록(L462)이고, `mcp:`·`a2a:` audience 로 직접 발급할 때 그 대상의 토큰 교환 정책(`TokenExchangePolicy.allowed_scopes` — 게이트웨이가 교환할 때 쓰는 상한, `app/services/identity.py:173-205`)과 대조하지 않는다. 실패 시나리오: `tokens:issue`(운영자·보안 담당) 보유자가 `{"subject": "svc:x", "audience": "mcp:jira", "scopes": ["jira:admin", …]}` 로 발급 → 그 MCP 서버가 플랫폼 JWKS 로 토큰을 검증하고 스코프로 도구를 허용하므로(예: 내장 서버 `app/builtin/mcp_auth.py:30-40`) **게이트웨이의 도구 규칙·사람 승인·감사를 거치지 않고** 도구를 직접 호출할 수 있다(그 서버에 네트워크로 닿을 수 있을 때 — compose 내장 서버는 내부망이라 해당 없음, 인터넷에 열린 외부 MCP 서버는 해당). 2차 M5 의 근본 우려(교환 정책·게이트웨이 승인 우회)가 audience 제한만으로는 남는다. 최소 수정: audience 가 `mcp:`·`a2a:` 면 `scopes ⊆ 그 대상 교환 정책의 allowed_scopes`, 정책이 없으면 거부.

```python path=app/api/idp.py lines=137-149
def _mfa_ok(user: User, otp: str) -> bool:
    """MFA 를 등록한 사용자는 TOTP 6자리가 맞아야 한다 (SCR-A05 ③)."""
    if not user.mfa_enabled:
        return True
    from app.services import totp
    try:
        secret = keys.fernet().decrypt((user.mfa_secret or "").encode()).decode()
    except Exception:  # noqa: BLE001 — 키 교체 등으로 복호화 불가 → 거부
        return False
    if not totp.verify(secret, otp):
        return False
    from app.services import ratelimit  # 같은 코드는 한 번만 (유효 창 ±1 단계 = 90초) — 엿본 코드 재사용 거부 (D20-②)
    return ratelimit.backend().counter_add(f"totp-used:{user.id}:{otp.strip()}", 1, 120) == 1
```
```python path=app/services/totp.py lines=25-31
def verify(secret: str, code: str, window: int = 1) -> bool:
    """앞뒤 window 스텝(±30초)까지 허용 — 시계 오차."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == DIGITS):
        return False
    now = time.time()
    return any(hmac.compare_digest(code_at(secret, now + i * STEP), code) for i in range(-window, window + 1))
```
**분석 / Analysis (Level 2)**
- `_mfa_ok`: MFA 미등록이면 통과, 등록이면 비밀을 복호화(키 회전 등으로 못 풀면 거부)해 TOTP 검증. L148-149(D20-②): 같은 코드는 사용자별 한 번 — `totp-used:<사용자>:<코드>` 카운터가 1 일 때만 통과(120초 기억, 유효 창 ±1 단계 = 90초를 덮음).
- `totp.verify`: 앞뒤 공백 제거 **그리고 안쪽 공백 제거**(L27) 후 6자리 숫자 확인, ±1 단계 비교.
**짚어볼 점 / Watch-outs**
- [위험:중간] **M1(INDEX §5) — 공백을 넣으면 같은 코드를 다시 쓸 수 있다.** `totp.verify` 는 `"123 456"` 을 `"123456"` 으로 정규화해 통과시키지만(`app/services/totp.py:27`), 1회 사용 키는 `otp.strip()`(L149) — 안쪽 공백이 남아 `"123456"`·`"123 456"`·`"12 3456"`·`"1 23456"` … 이 **서로 다른 키**가 된다. 실패 시나리오: 공격자가 비밀번호를 알고 사용자의 OTP 를 어깨너머·피싱 프록시로 엿봄 → 사용자가 먼저 로그인해 그 코드가 "사용됨" 이 된 뒤에도, 90초 안에 `"123 456"` 으로 다시 로그인 성공. D20-② 가 막으려던 "엿본 코드 재사용"(L148 주석)이 그대로 열린다. 시험은 같은 문자열 재사용만 본다(`test/test_identity_boundaries.py:107-114`). 최소 수정: 정규화를 한 곳으로 — `code = otp.strip().replace(" ", "")` 를 키에 쓰거나, 더 정확히는 `totp.verify` 가 **맞은 시간 단계(step)** 를 돌려주게 하고 키를 `totp-step:<사용자>:<step>` 으로(RFC 6238 §5.2 — 같은 단계의 코드는 한 번만, 이전 단계 재사용도 거부).
- [위험:낮음] 1회 사용 표시는 TOTP 확인 직후 남는다 — 그 뒤 IP 정책 거부 등으로 로그인이 실패해도 코드는 소모된다(사용자는 다음 코드를 기다려야 함). 그리고 Valkey 장애 5초 동안은 워커 로컬로 떨어져(`app/services/ratelimit.py:129-139`) 다른 워커에서 같은 코드를 다시 받을 수 있다.

## 파일 평가 / File Assessment
- 강점: (1) admin 부여·관리자 계정 변경·IdP admin 매핑을 같은 규칙(`_check_roles`)으로 묶었다(`app/api/identity.py:125-130`, `:146`, `:163-166`, `:210-211`, `:233-234`). (2) IdP 삭제가 세션·refresh 를 함께 끊는다(`app/api/identity.py:266-277`). (3) Access Token audience 를 실제 등록 대상으로 제한(`app/api/identity.py:474-481`).
- 위험: (1) [위험:높음] admin 매핑 IdP 의 검증 키 설정을 비관리자가 바꿔 admin 토큰 위조(`app/api/identity.py:224-247` ↔ `app/core/auth.py:126-164`, `:251-259`). (2) [위험:중간] TOTP 1회 키와 검증 정규화 불일치로 재사용 가능(`app/api/idp.py:149` ↔ `app/services/totp.py:27`). (3) [위험:중간] admin 외 역할로의 자기 권한 상승(`app/api/identity.py:129`) · [위험:중간] 등록 대상 Access Token 스코프 무제한(`app/api/identity.py:500-504`).
- 개선 권고(영향순): (1) admin(또는 호출자 밖 역할) 매핑을 가진 IdP 는 모든 패치를 admin 만 + 시험. (2) TOTP 는 시간 단계 기준 1회. (3) 부여 역할 권한 ⊆ 호출자 권한. (4) 대상 토큰 스코프를 교환 정책으로 제한.
- 파일 위험 등급 / Risk grade: 높음 — 신원·인가 경계에 [위험:높음] 1건 + [위험:중간] 3건.
- 재읽기 권고 레벨: 2 — 신뢰의 뿌리(서명 키 출처·역할 매핑)가 `app/core/auth.py` 에 있어, 관리 API 의 각 필드가 그 뿌리의 어디를 바꾸는지 줄 단위로 대조해야 한다.

## 2차 리뷰 대비 / vs 2nd Review
| 2차 지적 (2차 INDEX §5) | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| M3 `identity:write` 가 자기에게 admin 부여 (IdP 역할 매핑 경로 포함) | 부분 해결 + 우회 | admin 직접 부여·매핑 차단 `app/api/identity.py:129-130`, `:210-211`, `:233-234`. 비 admin 역할 상승(M8)·기존 admin 매핑 IdP 의 검증 키 변경(H1) 남음 |
| M4 IdP 삭제가 세션을 끝내지 않음 | 해결 | `:266-277` · 시험 `test/test_identity_boundaries.py:55-71` |
| M5 Access Token `audience` 무제약 | 부분 해결 | 등록 대상만 `:474-481`, `:500-502`. 스코프 무제한(M12) |
| M7 MFA 실패 미집계·TOTP 재사용 | 해결(집계 D19) · 부분(재사용) | 1회 사용 `app/api/idp.py:148-149` — 정규화 불일치로 우회(M1) |
| H3 토큰으로 토큰 발급 · M1 `ttl_days: null` · M2 클라이언트 키 팀 | 해결 유지(C51-14) | 이번 범위 hunk 아님 — 현재 코드 확인: 로그인 세션만 `app/api/identity.py:59`, `ttl_days: int`(None 불가) `:35`, 다른 팀은 `admin:write` 만 `:72-74` |
