> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정 — 백업 서명 검증·키 회전 절) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: 대상 아님

### 02. app/api/admin.py (키 회전 절 · 백업 서명 검증)
**파일 요약 / File Summary**
- 역할 / Role: 운영·설정 API(배포 프로파일·헬스·백업/복구·설정·알림·검색·업데이트). 이번 리뷰 대상은 D21-③ 로 바뀐 두 곳 — ① 백업 복원의 서명 검증(`decode_long_lived`, L302) ② 키 회전 관리 API(L522-588: 상태·회전·재암호화).
- 진입점 거리 / Distance: 0 — HTTP 라우트(`/api/v1/admin/keys`, `/keys/rotate`, `/keys/reencrypt`, `/api/v1/admin/restore`). CLI `nexus keys …`(`app/cli/nexus.py:582-600`)가 같은 API 를 부른다.
- LOC: 588 (리뷰 범위 L268-320, L522-588)
- 주요 export(범위 안): `make_backup`·`restore`·`KeyRotateIn`·`_token_grace_s`·`reencrypt`·`key_status`·`rotate_keys`·`reencrypt_keys`
- 의존(내부): `app.core.keys`([keys.py.md](../core/keys.py.md)), `app.core.auth`(`require`·`Principal`), `app.services.audit`·`identity`, 모델 `Secret`·`User`·`LogEntry`
- 외부 패키지: FastAPI·pydantic·SQLAlchemy·PyJWT·cryptography(`InvalidToken`)
- 부수효과 / Side effects: 키 파일 교체(`keys.rotate_*`), 저장 암호문 전체 UPDATE(재암호화), 감사 기록.
- 동시성 / Concurrency: 동기 핸들러(`def`) — FastAPI 가 스레드풀에서 돌린다. 재암호화는 요청 스레드 하나에서 테이블 3개를 끝까지 훑는다.
- 테스트 커버리지 흔적: `test/test_key_rotation.py:24-50`·`:52-69`·`:71-78`(C58-01~03). 재암호화 범위(어떤 컬럼을 도는지)를 단언하는 시험은 없다 — `:52-69` 는 `Secret` 값 하나만 확인한다.
- 3차 변경 요약: D21-③ — 회전 API 3개 추가, 복원·격리 복구 시험(`app/api/ops.py:858`)이 보관 공개키까지 보는 `decode_long_lived` 로 바뀜.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/api/admin.py lines=275-285
def make_backup(db: Session, actor: str, include_secrets: bool = False) -> dict:
    tables = BACKUP_TABLES + ([Secret] if include_secrets else [])
    data = {m.__tablename__: [_to_row(o) for o in db.scalars(select(m))] for m in tables}
    body = {"format": "agentnexus-backup/1", "version": APP_VERSION, "created_at": utcnow().isoformat(),
            "created_by": actor, "tables": data}
    digest = sha256_hex(canonical_json(body))
    sig = jwt.encode({"sha256": digest, "iss": "agentnexus-backup"}, keys.signing_key(), algorithm="RS256",
                     headers={"kid": keys.kid()})
    audit.append("admin.backup", actor=actor, data={"tables": {k: len(v) for k, v in data.items()}, "sha256": digest,
                                                    "secrets": include_secrets})
    return {"bundle": body, "sha256": digest, "signature": sig}
```
**분석 / Analysis (Level 2)**
- 백업 번들 서명: 본문(canonical JSON) SHA-256 을 `{"sha256", "iss": "agentnexus-backup"}` 로 서명하고 헤더에 kid 를 붙인다(L281-282). kid 가 있어야 회전 뒤에도 보관 공개키에서 그 키를 찾을 수 있다([keys.py.md](../core/keys.py.md) L139-144).
- 같은 서명 키가 사용자 토큰·감사 보고서·백업을 모두 서명한다 — 그래서 **검증 쪽이 iss 로 용도를 구분해야** 한다.

```python path=app/api/admin.py lines=294-306
@router.post(f"{A}/restore")
def restore(body: RestoreIn, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """서명 검증 후 병합 복원(merge) — 같은 키는 백업 값으로 덮어쓰고, 백업에 없는 행은 유지한다."""
    if body.confirm != "RESTORE":
        raise bad_request("복원은 운영 데이터를 덮어씁니다 — 확인란에 RESTORE 를 입력하세요 (MSG-002). 먼저 격리 복구 시험을 권장합니다.",
                          code="confirm_required")
    digest = sha256_hex(canonical_json(body.bundle))
    try:
        claims = keys.decode_long_lived(body.signature, algorithms=["RS256"])  # 회전 전 키로 서명한 백업도 (D21-③)
    except jwt.PyJWTError as exc:
        raise bad_request(f"백업 서명 검증 실패: {exc}", code="invalid_backup") from exc
    if claims.get("sha256") != digest:
        raise bad_request("백업 내용이 서명과 일치하지 않습니다(변조).", code="invalid_backup")
```
**분석 / Analysis (Level 2)**
- L297-299: 확인 입력 `RESTORE` — 실수 방지.
- L302(D21-③): 회전 전 키로 서명한 백업도 복원되도록 보관본까지 보는 `decode_long_lived`.
- L305-306: 본문 해시 대조 — 서명이 맞아도 본문이 바뀌었으면 거부.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L302 `issuer="agentnexus-backup"` 를 요구하지 않는다. 감사 보고서 검증은 같은 이유로 iss·필수 클레임을 요구한다(`app/services/audit.py:236-238` 주석 "같은 서명 키로 사용자 토큰도 발급한다 — 리포트 발급자·필수 클레임까지 맞아야"). 지금은 `sha256` 대조(L305) 때문에 다른 용도 서명(감사 보고서의 `sha256` 은 줄바꿈으로 끝나는 JSONL 해시, `app/services/audit.py:215-217`)을 백업으로 쓰기는 어렵지만, 회전으로 검증 키가 보관본까지 넓어진 만큼 용도 구분은 명시가 맞다. 같은 결함: `app/api/ops.py:858`(격리 복구 시험). 최소 수정: `decode_long_lived(sig, algorithms=["RS256"], issuer="agentnexus-backup", options={"require": ["iss", "sha256"]})`.

```python path=app/api/admin.py lines=522-536
# ───────── 키 회전 (D21-③) — 관리 명령으로 수동. 파일 모드만 앱이 회전, env 모드는 Secret 교체 + *_PREVIOUS ─────────
class KeyRotateIn(BaseModel):
    kind: str = Field(pattern="^(signing|encryption)$")
    confirm: str = ""


def _admin_only(p: Principal) -> None:
    if "admin" not in (p.roles or []):
        raise forbidden("키 회전은 플랫폼 관리자만 할 수 있습니다.", code="admin_only")


def _token_grace_s(db: Session) -> int:
    """직전 서명 키 유예 — 서명 토큰(액세스·Access Token·위임) 최대 수명 + 10분. PAT·갱신 토큰은 서명 토큰이 아니다."""
    from app.services import identity
    return max(settings.access_token_ttl, identity.auth_policy(db)["access_token_ttl_min"] * 60, 3600) + 600
```
**분석 / Analysis (Level 2)**
- L523-525: `kind` 는 정규식으로 두 값만. `confirm` 은 기본 빈 문자열 → L568 에서 400.
- L528-530 `_admin_only`: `require("admin:write")`(운영자도 가진 권한) 위에 **역할 `admin`** 을 한 번 더 요구한다 — 키 회전은 운영자(operator)도 못 한다(C58-03).
- L533-536 `_token_grace_s`: 직전 서명 키 유예 = max(액세스 토큰 TTL, 인증 정책 TTL, 1시간) + 10분. 서명 토큰 중 수명이 가장 긴 것을 덮어야 한다. 확인한 서명 토큰 수명: Access Token API ≤ 3600초(`app/api/identity.py:463`), 교환 토큰 ≤ 900초(`app/api/identity.py:355`, `app/api/catalog.py:344`), 위임 600초(`app/services/identity.py:166`), 워크로드·사용자 액세스 토큰 = `access_token_ttl`(`app/services/identity.py:133`), 설정 토큰 3600초(`app/services/setup.py:120`) — 모두 유예 안이다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] `identity.issue_access_token` 자체는 TTL 을 24시간까지 허용한다(`app/services/identity.py:142-143`). 지금은 API 가 3600초로 막지만(`app/api/identity.py:463`), 다른 호출처가 생기면 유예(최소 70분)보다 긴 토큰이 회전 뒤 일찍 끊긴다. `_token_grace_s` 가 서비스 쪽 상한을 참조하도록 상수를 공유하는 편이 안전하다.

```python path=app/api/admin.py lines=539-554
def reencrypt(db: Session) -> dict:
    """저장 암호문을 현재(첫) 암호화 키로 — 시크릿·MFA 비밀·로그 원문. 이전 키로도 못 푸는 행은 failed 로 센다."""
    from cryptography.fernet import InvalidToken

    from app.models import LogEntry, Secret, User
    f = keys.fernet()
    out = {"secrets": 0, "mfa": 0, "logs": 0, "failed": 0}
    for model, col, label in ((Secret, "ciphertext", "secrets"), (User, "mfa_secret", "mfa"), (LogEntry, "raw_enc", "logs")):
        for row in db.scalars(select(model).where(getattr(model, col).is_not(None))).yield_per(500):
            try:
                setattr(row, col, f.rotate(getattr(row, col).encode()).decode())
                out[label] += 1
            except InvalidToken:
                out["failed"] += 1
    db.commit()
    return out
```
**분석 / Analysis (Level 2)**
- 저장 암호문 3종(시크릿 `Secret.ciphertext`, MFA 비밀 `User.mfa_secret`, 로그 원문 `LogEntry.raw_enc`)을 `MultiFernet.rotate` 로 현재(첫) 키로 다시 암호화한다. 어느 키로도 못 푸는 행은 `failed` 로 센다.
- L547 `yield_per(500)`: DB 커서에서 500행씩 가져온다(전체를 한 번에 목록으로 만들지 않음).
**짚어볼 점 / Watch-outs**
- [위험:중간] **M4(INDEX §5) — 재암호화 범위 누락.** 같은 12차(D21-⑤, C60-09)가 만든 암호문 `A2ATask.push_config["token_enc"]`(클라이언트 웹훅 토큰, `app/services/a2a_gateway.py:698-700`)가 L546 목록에 없다. 실패 시나리오: 관리자가 `POST /keys/rotate {"kind":"encryption"}` → 재암호화 결과 `failed: 0` → 안내대로 `POST /keys/reencrypt?drop_previous=true`(L585-586 도 `failed == 0` 이므로 이전 키 삭제) → 진행 중 A2A Task 의 푸시가 들어오면 `_relay_push` 가 `keys.fernet().decrypt(...)` 에서 `InvalidToken`(`app/services/a2a_gateway.py:900-902`) — 예외를 잡지 않는 백그라운드 Task 라 경고 로그(`:903-904`)도 없이 클라이언트 웹훅 중계가 끊긴다. env 모드도 같다(운영자가 `ENCRYPTION_KEY_PREVIOUS` 를 비우는 순간). 덧붙여 `app/services/mcp_modern.py:212` 의 MCP 요청 상태 토큰(수명 `STATE_TTL_S = 600`, `app/services/mcp_modern.py:76`)도 이전 키로 만든 것은 drop 직후 풀 수 없다(10분 이내 일시적). 최소 수정: L546 에 `A2ATask.push_config` 의 `token_enc` 재암호화(JSON 안 필드라 행 단위로 꺼내 바꿔 다시 대입)를 추가하고, `_relay_push` 의 복호화 실패를 잡아 경고 로그. drop 은 "회전 시각 + `STATE_TTL_S`" 이후에만 허용.
- [위험:낮음] L547-553: `yield_per` 로 읽으면서 같은 세션에서 행을 바꾸고 마지막에 한 번 `commit` — 바뀐(dirty) 객체는 세션이 끝까지 강하게 붙잡으므로 결국 **전 행이 메모리에 남고 한 트랜잭션**이 된다. `LogEntry` 는 보존 기간 동안 커질 수 있는 테이블이라 대형 설치에서 메모리·잠금·요청 시간(인그레스 타임아웃) 위험. 최소 수정: 배치마다 `commit()` + `expunge_all()`(재암호화는 행 단위로 멱등이라 중간 커밋이 안전).

```python path=app/api/admin.py lines=557-588
@router.get("/api/v1/admin/keys")
def key_status(_: Principal = Depends(require("admin:read"))):
    return {"env_managed": keys.env_managed(), "signing": {"kid": keys.kid(), "jwks_kids": [k["kid"] for k in keys.jwks()["keys"]]},
            "encryption": {"keys": keys.encryption_key_count()}}


@router.post("/api/v1/admin/keys/rotate")
def rotate_keys(body: KeyRotateIn, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """서명 키: 새 kid, 직전 키는 서명 토큰 최대 수명 동안 JWKS·검증에 유지(공개키는 백업·보고서 검증용으로 영구 보관).
    암호화 키: 새 키로 바꾸고 저장 암호문을 즉시 재암호화, 이전 키는 복호화용으로 남김(재암호화 확인 뒤 reencrypt?drop_previous)."""
    _admin_only(p)
    if body.confirm != "ROTATE":
        raise bad_request("키 회전은 확인란에 ROTATE 를 입력하세요 (MSG-002).", code="confirm_required")
    try:
        out = keys.rotate_signing(_token_grace_s(db)) if body.kind == "signing" else {**keys.rotate_encryption(), **reencrypt(db)}
    except RuntimeError:
        raise conflict("키가 env(Secret)로 주입되어 앱이 회전할 수 없습니다 — 새 키로 Secret 을 바꾸고 이전 키를 "
                       "SIGNING_KEY_PEM_PREVIOUS·ENCRYPTION_KEY_PREVIOUS 로 옮긴 뒤 재암호화하세요 (README 키 회전).",
                       code="keys_env_managed") from None
    audit.append("keys.rotate", actor=p.sub, target=body.kind, data={k: v for k, v in out.items() if k != "previous_valid_until"})
    return {"kind": body.kind, **out}


@router.post("/api/v1/admin/keys/reencrypt")
def reencrypt_keys(drop_previous: bool = False, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """env 모드 회전 뒤 또는 회전 후 확인용 재암호화 — 실패 0 이고 drop_previous 면 파일의 이전 암호화 키를 지운다."""
    _admin_only(p)
    out = reencrypt(db)
    if drop_previous and out["failed"] == 0:
        out["dropped"] = keys.drop_previous_encryption()
    audit.append("keys.reencrypt", actor=p.sub, target="encryption", data=out)
    return out
```
**분석 / Analysis (Level 2)**
- L557-560 상태: 현재 kid·JWKS 의 kid 목록·암호화 키 수(1 이면 이전 키 없음)·env 주입 여부. `admin:read` 면 볼 수 있다(키 값은 노출 없음).
- L563-577 회전: admin 역할 + 확인 입력 → 서명이면 `rotate_signing(유예)`, 암호화면 `rotate_encryption()` 후 즉시 `reencrypt(db)`. env 모드면 `RuntimeError("env")` → 409 `keys_env_managed`.
- L580-588 재암호화: env 모드 회전 뒤(운영자가 Secret 을 바꾸고 재기동한 다음) 또는 확인용. `failed == 0` 이고 `drop_previous` 면 이전 키 파일 삭제.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L571-576: 감사(`keys.rotate`)는 회전·재암호화가 **모두 성공한 뒤** 남는다. 암호화 회전에서 `reencrypt(db)` 가 DB 오류로 예외를 내면 키는 이미 바뀌었는데(L571 왼쪽이 먼저 실행) 감사 기록이 없다. 최소 수정: 회전 직후 감사를 먼저 남기고 재암호화 결과는 별도 감사로.
- [위험:낮음] L572 `except RuntimeError` 가 넓다 — `rotate_*` 안에서 다른 이유의 `RuntimeError` 가 나면 "env 로 주입되어…" 409 로 잘못 안내한다. 전용 예외(`keys.EnvManaged`)가 명확하다.
- [위험:낮음] L573-574 안내 문구가 "README 키 회전" 절을 가리키지만 README 에는 env 표 한 줄(`README.md:250`)뿐이다. env 모드 절차(새 키 → `*_PREVIOUS` → 재기동 → `reencrypt` → 이전 키 제거)와 **롤링 재기동 중 신·구 파드가 서로의 키를 모르는 구간**(신 파드가 새 키로 암호화·서명한 값을 구 파드가 못 읽음 — [INFRA.md](../../INFRA.md) 와 INDEX §5 L7)을 문서로 남겨야 한다.

## 파일 평가 / File Assessment
- 강점: (1) 키 회전을 admin 역할 + 확인 입력 + 감사로 묶고, env 모드는 앱이 손대지 않고 409 로 절차를 안내한다(`app/api/admin.py:528-530`, `:567-575`). (2) 유예 시간을 서명 토큰 최대 수명에서 계산한다(`app/api/admin.py:533-536`). (3) 재암호화가 "못 푸는 행" 을 세어 drop 의 안전 조건으로 쓴다(`app/api/admin.py:551-552`, `:585`).
- 위험: (1) [위험:중간] 재암호화 범위 누락 → drop 뒤 A2A 웹훅 중계 실패(`app/api/admin.py:546` ↔ `app/services/a2a_gateway.py:698-700`). (2) [위험:낮음] 대형 테이블 재암호화가 한 트랜잭션·전 행 메모리(`app/api/admin.py:547-553`). (3) [위험:낮음] 복원 서명 검증에 issuer 미요구(`app/api/admin.py:302`, `app/api/ops.py:858`).
- 개선 권고(영향순): (1) 암호문 컬럼 목록을 한 곳(keys 모듈)에 등록하고 `reencrypt` 가 그 목록을 돌게 + 목록 누락을 잡는 시험(모델 전체에서 Fernet 토큰 모양 값을 찾는 시험). (2) 배치 커밋. (3) 복원 검증에 iss·필수 클레임.
- 파일 위험 등급 / Risk grade: 중간 — [위험:중간] 1건(M4), 나머지는 운영·방어적 [위험:낮음].
- 재읽기 권고 레벨: 2 — 회전 API 는 짧지만 `keys` 모듈의 세대 캐시·env 모드와 맞물려 있어 [keys.py.md](../core/keys.py.md) 와 나란히 읽어야 한다.

## 2차 리뷰 대비 / vs 2nd Review
2차 범위 밖 파일. 관련 2차 항목 없음(키 회전은 D21-③ 신규).

| 2차 항목 | 3차 상태 | 근거 |
|---|---|---|
| (해당 없음) | 신규 구현 | `app/api/admin.py:522-588` |
