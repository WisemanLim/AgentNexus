> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 4차 리뷰(round4, D22 hunk 한정) · 스냅샷 2026-10-08 14:27 KST · [← INDEX.md](../../INDEX.md) · 3차 리뷰: [round3 keys.py.md](../../../../round3/CODE-REVIEW-Lv2/app/core/keys.py.md)

### 01. app/core/keys.py (+ `app/api/admin.py` 키 회전 절 · `app/core/config.py:38-47` · `SECURITY.md:48-59`)
**파일 요약 / File Summary**
- 역할 / Role: 서명 키(RS256, JWKS)·암호화 키(Fernet)의 단일 출처. 파일 모드(`data_dir/keys`)는 관리 명령으로 회전하고, env 모드(Helm Secret·`.env`)는 운영자가 env 를 바꿔 회전한다. 13차(D22-②, Epic 62)에서 **회전 세대 카운터·회전 잠금(flock)·env 2단계 회전(`*_NEXT`)·공개키 PEM 이전 키·토큰 검증 끝 시각·폐기 kid** 가 들어왔다.
- 진입점 거리 / Distance: 1 — 토큰 서명·검증(`app/core/security.py:21-38`), JWKS(`app/api/idp.py:59`), 시크릿 암복호화(`app/services/secrets.py:59`), 관리 API(`app/api/admin.py:583-618`), 백업·감사 보고서 서명 검증(`app/api/admin.py:302`, `app/api/ops.py:859`, `app/services/audit.py:237`)이 직접 부른다.
- LOC: 277 (3차 스냅샷 232 → 277) · `app/api/admin.py` 618 (키 회전 절 L523-618)
- 주요 export: `EnvManaged`·`signing_key`·`kid`·`verification_keys`·`decode_long_lived`·`jwks`·`fernet`·`env_managed`·`encryption_key_count`·`rotate_signing`·`rotate_encryption`·`drop_previous_encryption`·`ensure_keys`
- 의존(내부): `app.core.config.settings`
- 외부 패키지: cryptography(`Fernet`·`MultiFernet`·RSA·PEM), 표준 `fcntl`(신규 — Unix 전용)·`datetime`·`contextlib`
- 부수효과 / Side effects: `data_dir/keys/` 아래 `generation`(이제 내용이 카운터)·`rotate.lock`(신규)·`signing.prev.json`·`signing.archive.pem`·`secrets.prev.key` 생성·교체, 프로세스 캐시(`lru_cache`) 비우기.
- 동시성 / Concurrency: 여러 워커·파드가 같은 키 디렉터리를 공유(compose 볼륨). 회전·이전 키 제거는 `keys/rotate.lock` 의 `flock(LOCK_EX)` 안에서. 읽는 쪽은 잠그지 않고 `generation` 내용으로 캐시를 무효화한다.
- 테스트 커버리지 흔적: `test/test_key_mgmt.py:51`(C62-01 카운터), `:64`(C62-02 동시 회전), `:82`(C62-03 env 다음 키), `:100`(C62-04 공개키·검증 끝·폐기 kid), `:119`(C62-05 감사 선기록), `:138`(C62-06 Helm nonce). 회귀 `test/test_key_rotation.py`.
- 4차 변경 요약 / Changes in D22: D22-② 하나 — 3차 Lv2 L1(mtime 세대)·L2(env 이전 키)·L3(회전 잠금)·L6(감사 시점)·L7(env 롤링)·L27(existingSecret 재기동)을 59th 에서 "기록" 으로 남겼던 것을 고쳤다(`test/impl/62nd/scenario.md` 범위 표).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/config.py lines=38-47
    # 키 (비우면 data_dir/keys 에 자동 생성·보존)
    signing_key_pem: str = ""
    encryption_key: str = ""
    signing_key_pem_previous: str = ""  # env 로 키를 줄 때의 회전 — 직전 서명 키(PEM, 여러 개 이어 붙임) (D21-③)
    encryption_key_previous: str = ""   # 직전 암호화 키(쉼표 구분) — 재암호화가 끝나면 비운다
    # env 모드 2단계 회전 (D22-②) — 다음 키를 먼저 모든 파드에 배포(검증·복호화만) → 현재 키로 올림: 롤링 중 401·복호화 실패 없음
    signing_key_pem_next: str = ""      # 다음 서명 키 PEM(개인·공개) — JWKS·검증에만, 서명은 아직 안 함
    encryption_key_next: str = ""       # 다음 암호화 키(쉼표) — 복호화만
    signing_key_previous_until: str = ""  # env 이전 서명 키의 토큰 검증 끝(ISO 8601) — 지나면 백업·보고서 서명 검증에만
    signing_revoked_kids: str = ""      # 폐기 kid(쉼표) — 유출 키: 토큰·백업·보고서 어디서도 검증하지 않음
```
**분석 / Analysis (Level 2)**
- L43-47(D22-②): env 모드 회전 설정 4개가 늘었다. `SIGNING_KEY_PEM_NEXT`·`ENCRYPTION_KEY_NEXT` 는 "다음 키" — 모든 파드가 먼저 **검증·복호화에만** 쓰도록 미리 배포한다. `SIGNING_KEY_PREVIOUS_UNTIL` 은 env 이전 서명 키가 **토큰** 검증에서 빠지는 시각(ISO 8601), `SIGNING_REVOKED_KIDS` 는 어디서도 믿지 않을 kid 목록.
- 왜 2단계인가: 1단계 없이 현재 키를 바로 바꾸면 롤링 업데이트(RollingUpdate) 동안 옛 파드는 새 kid 를 모르고 새 암호문을 못 푼다(3차 L7). 1단계로 "모두가 새 키를 알고 있는 상태" 를 먼저 만든 뒤 2단계에서 서명·암호화 키를 올리면, 롤링 중 어느 쪽 파드든 서로의 산출물을 받는다.
- 흔한 실수: `SIGNING_KEY_PREVIOUS_UNTIL` 을 시간대 없이(`2026-10-09T00:00:00`) 주면 `datetime.timestamp()` 가 컨테이너 로컬 시간대로 해석한다(컨테이너는 보통 UTC 라 문제가 드러나지 않을 뿐). `Z` 또는 `+09:00` 을 붙이는 습관이 안전하다.

```python path=app/core/keys.py lines=61-89
class EnvManaged(RuntimeError):
    """키가 env(Secret)로 주입돼 앱이 회전하지 않는다 — 운영자가 Secret·`*_PREVIOUS`·`*_NEXT` 로 회전."""


def _generation() -> int:
    """회전 세대 = keys/generation 의 카운터(내용). mtime 은 파일시스템에 따라 같은 틱의 두 회전을 구분하지 못한다 (D22-②)."""
    try:
        return int((_keys_dir() / "generation").read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def _bump() -> None:
    _write_atomic(_keys_dir() / "generation", str(_generation() + 1).encode())
    _signing.cache_clear()
    _fernet.cache_clear()


@contextmanager
def _rotation_lock():
    """회전·이전 키 제거는 한 번에 하나 — 동시에 돌면 둘 다 같은 현재 키를 직전 키로 옮겨 새 키 하나가 사라진다 (D22-②)."""
    fd = os.open(_keys_dir() / "rotate.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

```
**분석 / Analysis (Level 2)**
- L61-62 `EnvManaged`: 전에는 넓은 `RuntimeError` 를 던져 관리 API 가 다른 실패까지 409 로 바꿀 수 있었다(3차 L6). 전용 예외로 좁혔다 — `app/api/admin.py:598` 이 이것만 잡는다.
- L65-70 `_generation`(D22-②): 세대 = `generation` 파일의 **내용**(정수). 3차 L1 의 mtime 해상도 문제(같은 틱의 두 번째 회전을 다른 워커가 놓침)가 사라졌다. 이전 버전이 쓴 내용(`time.time_ns()` 문자열)도 정수라 그대로 이어 +1 된다(`test/impl/62nd/result.md` 결과 요약 첫 줄).
- L73-76 `_bump`: 읽고(+1) 원자적으로 쓴다(`_write_atomic` — 임시 파일 + `os.replace`). 읽기-수정-쓰기라 동시에 두 번 돌면 하나가 사라질 수 있는데, 모든 호출처(`rotate_*`·`drop_previous_encryption`)가 아래 잠금 안이라 안전하다.
- L79-88 `_rotation_lock`(D22-②): `flock` 은 **열린 파일 설명(open file description)** 단위 잠금이라, 같은 프로세스의 두 스레드도 각자 `os.open` 하면 서로 배제된다(C62-02 가 두 스레드로 확인). `finally` 에서 풀고 닫는다.
- 흔한 실수: `fcntl.lockf`(POSIX 레코드 잠금)는 같은 프로세스 안에서는 배제되지 않는다 — 여기서 `flock` 을 고른 이유다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] 읽는 쪽(`_generation` → `_signing`)은 잠그지 않는다. `rotate_signing` 은 prev → `signing.pem` → `_bump` 순서로 쓰므로 중간 상태를 읽은 워커도 "옛 세대 번호 + 일관된 키 묶음" 을 캐시했다가 `_bump` 뒤 다시 읽는다 — 정합성은 유지된다(분석 결과, 시험 없음).
- [위험:낮음] 호출마다 `_keys_dir()` 의 `mkdir` + 파일 읽기 1회(이전은 `stat`). 토큰 검증 1회에 `_signing(_generation())` 이 두 번(L185, L179) 불린다 — 3차 L1 의 "핫 경로 비용" 부분은 그대로다(59th 기록 판정과 같은 맞바꿈).

```python path=app/core/keys.py lines=137-166
def _public(blob: bytes):
    """PEM → 공개키 — env 이전·다음 키는 공개키 PEM 도 받는다(검증만 하므로 개인키를 둘 필요 없음, D22-②)."""
    if b"PRIVATE KEY" in blob:
        return serialization.load_pem_private_key(blob, password=None).public_key()
    return serialization.load_pem_public_key(blob)


def _env_until() -> float | None:
    v = settings.signing_key_previous_until.strip()
    return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() if v else None


@lru_cache(maxsize=2)
def _signing(gen: int):
    """(현재 개인키, 이전 [(공개키, 토큰 검증 끝 epoch | None)], 보관 공개키, 다음 공개키) — 폐기 kid 는 모두에서 뺀다."""
    pem = _configured(settings.signing_key_pem).encode() or _read_or_create(_keys_dir() / "signing.pem", _new_rsa_pem)
    current = serialization.load_pem_private_key(pem, password=None)
    revoked = {k.strip() for k in settings.signing_revoked_kids.split(",") if k.strip()}
    keep = lambda pub: _kid_of(pub) not in revoked  # noqa: E731
    until = _env_until()
    env_prev = [_public(b) for b in _pems(_configured(settings.signing_key_pem_previous).encode())]
    previous = [(pub, until) for pub in env_prev]
    d = _keys_dir()
    for e in _prev_entries():
        previous.append((serialization.load_pem_public_key(e["pub"].encode()), float(e["until"])))
    archive = [serialization.load_pem_public_key(b) for b in _pems((d / SIGNING_ARCHIVE).read_bytes())] \
        if (d / SIGNING_ARCHIVE).exists() else []
    archive += env_prev  # env 이전 키도 토큰 유예 뒤 백업·보고서 서명 검증에는 남는다(폐기 kid 로만 끊음)
    nxt = [_public(b) for b in _pems(_configured(settings.signing_key_pem_next).encode())]
    return (current, [(p, u) for p, u in previous if keep(p)], [p for p in archive if keep(p)], [p for p in nxt if keep(p)])
```
**분석 / Analysis (Level 2)**
- L137-141 `_public`(D22-②): env 이전·다음 키는 공개키 PEM 도 받는다(3차 L2 — 공개키를 넣으면 인증 전체가 실패하던 문제). `b"PRIVATE KEY"` 포함 여부로 개인키·공개키를 가른다 — `BEGIN PRIVATE KEY`·`BEGIN RSA PRIVATE KEY` 모두 걸린다.
- L144-146 `_env_until`: 빈 값이면 `None`(= 만료 없음, D21 동작 그대로). 형식이 틀리면 `ValueError` 가 `_signing` 에서 터진다 → 시드 단계 `ensure_keys`(`app/seed.py:371`)가 먼저 실패하므로 **기동 단계에서 드러난다**(fail fast). Helm 은 `maxUnavailable: 0`(`deploy/helm/agentnexus/templates/api.yaml:12`)이라 옛 파드가 남아 서비스는 유지된다.
- L149-166 `_signing(gen)`: 반환이 4튜플로 늘었다 — (현재 개인키, 이전 [(공개키, 토큰 검증 끝)], 보관 공개키, 다음 공개키). 폐기 kid(L154-155)는 이전·보관·다음 **모두**에서 뺀다(현재 키는 빼지 않는다 — 현재 키가 유출됐다면 먼저 회전해야 한다).
**짚어볼 점 / Watch-outs**
- [위험:중간] **M5** 폐기 kid 는 이전·보관·다음 키에서만 빠지고 **현재 키에는 적용되지 않는다**(L153 `current` 는 `keep` 을 거치지 않음 → L186 `current.public_key()`, L201 `signing_key().public_key()` 가 그대로 후보·JWKS). 그런데 절차는 "키가 유출됐으면 그 kid 를 `SIGNING_REVOKED_KIDS` 에 — 토큰·백업·보고서 어디서도 검증하지 않는다"(`SECURITY.md:59`)라고만 적는다. 유출 사고에서 가장 흔한 대상은 **현재 키**이고, 2단계 회전보다 env 한 줄 추가가 훨씬 빠르므로 운영자가 이것부터 하기 쉽다. 시나리오: 현재 서명 키 유출 → 운영자가 그 kid 를 폐기 목록에 넣고 재배포 → 경고 없이 아무 효과 없음 → 공격자가 그 키로 서명한 임의 사용자(관리자 포함, `app/core/auth.py:224-231` 는 sub 로 사용자를 찾을 뿐) 토큰이 계속 통과. 최소 수정: `_signing` 에서 현재 kid 가 폐기 목록에 있으면 기동 실패("현재 키가 폐기됨 — 먼저 회전")로 바꾸고, `SECURITY.md:59` 에 "현재 키면 2단계 회전을 먼저" 를 명시. (위임 결함 리뷰 CR4-04 와 같은 지점 — 부록 D)

**분석 / Analysis (Level 2) — 이어서**
- L164 `archive += env_prev`: env 모드에는 파일 보관본이 없으므로, env 이전 키가 **백업·감사 보고서 서명 검증의 유일한 보관처**가 된다. 즉 env 모드에서 `SIGNING_KEY_PEM_PREVIOUS` 를 비우면 그 키로 서명한 백업·보고서는 검증할 수 없게 된다(`SECURITY.md:57-58` 이 "백업·보고서 서명 검증에만 남는다" 고 적은 이유).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L6(교차)** 같은 저장소의 다른 안내는 반대로 말한다: `.env.example:25` "직전 키. 재암호화·토큰 만료 뒤 비운다", `deploy/helm/agentnexus/values.yaml:81` "(토큰 수명 뒤 비움, D21-③)". 이 안내대로 비우면 **복원할 때가 돼서야** "알 수 없는 서명 키(kid)"(`app/core/keys.py:195`)로 백업 복원이 거부된다. 또 Helm 차트는 `SIGNING_KEY_PEM` 은 `lookup` 으로 보존하지만 `*_PREVIOUS` 는 `with` 로 values 에 있을 때만 넣으므로(`deploy/helm/agentnexus/templates/secret.yaml:10`, `:12`), values 파일에서 빠진 채 `helm upgrade` 하면 조용히 사라진다. 최소 수정: 두 주석을 SECURITY.md 와 맞추고, 서명 이전 키는 `lookup` 으로도 보존.
- [위험:낮음] **L6(교차)** `_public` 은 키 종류를 확인하지 않는다. EC 공개키를 `*_NEXT`·`*_PREVIOUS` 에 넣으면 기동은 되지만 `jwks()` 의 `pub.public_numbers().n`(L202-203)에서 `AttributeError` → `/idp/jwks` 500 → JWKS 로 검증하는 MCP 서버·외부 검증자가 모두 실패한다. 최소 수정: `_public` 에서 `isinstance(k, rsa.RSAPublicKey)` 확인 후 아니면 기동 실패.

```python path=app/core/keys.py lines=177-204
def _active_previous() -> list:
    now = time.time()
    return [pub for pub, until in _signing(_generation())[1] if until is None or until > now]


def verification_keys(kid_: str | None, long_lived: bool = False) -> list:
    """서명 검증용 공개키 후보 — 현재 키·유예 중인 직전 키·다음 키(토큰), long_lived 면 회전한 공개키 보관본까지
    (백업·보고서 서명). 폐기 kid 는 없다. kid 가 있으면 그 키만(없으면 빈 목록 = 모르는 키)."""
    current, _, archive, nxt = _signing(_generation())
    cands = [current.public_key(), *_active_previous(), *nxt, *(archive if long_lived else [])]
    return [k for k in cands if _kid_of(k) == kid_] if kid_ else cands


def decode_long_lived(token: str, **kw) -> dict:
    """백업·감사 보고서처럼 오래 남는 서명 검증 — 회전 전 공개키(보관본)까지 kid 로 찾는다 (D21-③)."""
    import jwt
    cands = verification_keys(jwt.get_unverified_header(token).get("kid"), long_lived=True)
    if not cands:
        raise jwt.InvalidTokenError("알 수 없는 서명 키(kid)")
    return jwt.decode(token, cands[0], **kw)


def jwks() -> dict:
    out = []
    for pub in [signing_key().public_key(), *_active_previous(), *_signing(_generation())[3]]:  # 다음 키도 미리 공개 (D22-②)
        nums = pub.public_numbers()
        out.append({"kty": "RSA", "use": "sig", "alg": "RS256", "kid": _kid_of(pub), "n": _b64u(nums.n), "e": _b64u(nums.e)})
    return {"keys": out}
```
**분석 / Analysis (Level 2)**
- L177-179 `_active_previous`: 토큰 검증 끝(`until`)이 지나지 않은 이전 키만. env 이전 키도 이제 `until` 을 가진다(L156-158).
- L182-187 `verification_keys`: 토큰 검증 후보 = 현재 + 유효한 이전 + **다음 키**(L186). 1단계에서 다음 키 공개키가 검증 후보에 들어가므로, 2단계 롤링 중 새 파드가 새 키로 서명한 토큰을 옛 파드도 받는다. `long_lived=True`(백업·보고서)면 보관본까지.
- L199-204 `jwks`: 다음 키도 JWKS 에 **미리** 공개한다(L201). 외부 검증자(MCP 서버 등)가 JWKS 를 캐시해도 2단계 전에 새 kid 를 받아 둔다 — 2단계 패턴의 핵심.
- 흔한 실수: JWKS 에만 다음 키를 넣고 `verification_keys` 에 빠뜨리면 플랫폼 자신이 자기 토큰을 거부한다. 두 곳 모두 넣었다.

```python path=app/core/keys.py lines=207-230
@lru_cache(maxsize=2)
def _fernet(gen: int) -> MultiFernet:
    if _configured(settings.encryption_key):
        primary = _configured(settings.encryption_key).encode()
    else:
        primary = _read_or_create(_keys_dir() / "secrets.key", Fernet.generate_key).strip()
    older = [k.strip().encode() for k in _configured(settings.encryption_key_previous).split(",") if k.strip()]
    older += [k.strip().encode() for k in _configured(settings.encryption_key_next).split(",") if k.strip()]  # 다음 키 — 복호화만 (D22-②)
    f = _keys_dir() / FERNET_PREV
    if f.exists():
        older += [ln.strip() for ln in f.read_bytes().splitlines() if ln.strip()]
    return MultiFernet([Fernet(primary), *[Fernet(k) for k in older]])  # 암호화는 첫 키, 복호화는 모두


def encryption_key_count() -> int:
    """현재 + 이전(env·파일) 암호화 키 수 — 1 이면 재암호화가 끝나 이전 키가 없다."""
    f = _keys_dir() / FERNET_PREV
    files = len([ln for ln in f.read_bytes().splitlines() if ln.strip()]) if f.exists() else 0
    env = f"{_configured(settings.encryption_key_previous)},{_configured(settings.encryption_key_next)}"
    return 1 + files + len([k for k in env.split(",") if k.strip()])


def fernet() -> MultiFernet:
    return _fernet(_generation())
```
**분석 / Analysis (Level 2)**
- L213-214(D22-②): 다음 암호화 키를 `older`(복호화 전용)에 넣는다. `MultiFernet` 은 **암호화는 첫 키, 복호화는 모든 키를 순서대로** 시도한다(L218 주석) — 다음 키를 복호화에만 쓰는 정확한 방법이다.
- L221-226 `encryption_key_count`: "현재 + 이전" 키 수. 다음 키(`ENCRYPTION_KEY_NEXT`)도 더한다(L225).
**짚어볼 점 / Watch-outs**
- [위험:중간] **M3(교차)** 1단계(다음 키 배포) 파드도 `encryption_key_count()` 가 2 이상을 돌려준다 — 상태 화면(`app/api/admin.py:586`)이 "이전 키가 남음" 과 "회전 진행 중" 을 구분하지 못한다. 더 큰 문제는 아래 `reencrypt_keys` 절 참고.

```python path=app/core/keys.py lines=233-271
def rotate_signing(grace_s: int) -> dict:
    """새 서명 키 — 직전 키는 grace_s 동안 JWKS·토큰 검증에 남고, 공개키는 보관본에 영구히(백업·보고서 서명 검증)."""
    if env_managed()["signing"]:
        raise EnvManaged("signing")
    d = _keys_dir()
    with _rotation_lock():
        old_pem = (d / "signing.pem").read_bytes()
        old_pub = serialization.load_pem_private_key(old_pem, password=None).public_key()
        pub_pem = old_pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        _append_private(d / SIGNING_ARCHIVE, pub_pem)
        now = time.time()  # 유예 중인 이전 키는 모두 남긴다 — 유예 안에 두 번 회전해도 그 사이 토큰이 끊기지 않게 (공개키만 보관)
        keep = [e for e in _prev_entries() if float(e["until"]) > now] + [{"pub": pub_pem.decode(), "until": now + grace_s}]
        _write_atomic(d / SIGNING_PREV, json.dumps(keep).encode())
        _write_atomic(d / "signing.pem", _new_rsa_pem())
        _bump()
        return {"previous_kid": _kid_of(old_pub), "kid": kid(), "previous_valid_until": time.time() + grace_s}


def rotate_encryption() -> dict:
    """새 암호화 키 — 이전 키는 복호화용으로 남는다(재암호화 뒤 drop_previous_encryption)."""
    if env_managed()["encryption"]:
        raise EnvManaged("encryption")
    d = _keys_dir()
    with _rotation_lock():
        old = (d / "secrets.key").read_bytes().strip()
        _append_private(d / FERNET_PREV, old + b"\n")
        _write_atomic(d / "secrets.key", Fernet.generate_key())
        _bump()
        return {"previous_keys": len([ln for ln in (d / FERNET_PREV).read_bytes().splitlines() if ln.strip()])}


def drop_previous_encryption() -> int:
    f = _keys_dir() / FERNET_PREV
    with _rotation_lock():
        n = len([ln for ln in f.read_bytes().splitlines() if ln.strip()]) if f.exists() else 0
        if f.exists():
            f.unlink()
        _bump()
        return n
```
**분석 / Analysis (Level 2)**
- L238-248 `rotate_signing`: 잠금 안에서 보관본 추가 → 이전 목록 갱신(유예 지난 항목 정리) → 새 `signing.pem` → `_bump`. 반환 직전 `kid()` 는 새 세대로 읽어 새 kid 를 돌려준다.
- L256-261 `rotate_encryption`: 이전 키를 `secrets.prev.key` 에 0600 으로 덧붙이고(59th `_append_private`) 새 키로 교체.
- L264-271 `drop_previous_encryption`: 이전 키 파일 삭제 + `_bump` 를 잠금 안에서. env 모드 확인은 없다 — env 모드에서는 파일이 없어 0 을 돌려줄 뿐이라 해는 없다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L5** 잠금은 "파일 쓰기" 만 묶는다. 실제 위험 구간인 **재암호화 → 이전 키 제거** 는 잠금 밖이다(`app/api/admin.py:614-616`). 관리자 A 의 `reencrypt?drop_previous=true` 가 시작할 때 캡처한 키 묶음(`f = keys.fernet()`, `app/api/admin.py:547` — 기본 K1·이전 K0)으로 도는 동안 관리자 B 가 `rotate --kind encryption`(K1→K2, 이전 [K0,K1]) 과 재암호화를 먼저 끝내면, A 가 늦게 K1 으로 다시 쓴 행이 남은 채 A 의 `drop_previous_encryption` 이 K0·K1 을 모두 지운다 → 그 행은 영구히 복호화 불가. 두 관리자의 동시 작업이 필요해 빈도는 낮지만 되돌릴 수 없다. 최소 수정: `reencrypt` 시작 때 `_generation()` 을 기록하고 drop 직전 값이 바뀌었으면 거부(또는 재암호화+drop 전체를 `_rotation_lock` 안에서).

```python path=app/api/admin.py lines=583-618
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
        out = keys.rotate_signing(_token_grace_s(db)) if body.kind == "signing" else keys.rotate_encryption()
    except keys.EnvManaged:
        raise conflict("키가 env(Secret)로 주입되어 앱이 회전할 수 없습니다 — 새 키를 *_NEXT 로 먼저 배포하고, 현재 키로 올리며 "
                       "이전 키를 *_PREVIOUS 로 옮긴 뒤 재암호화하세요 (SECURITY.md 키 회전).", code="keys_env_managed") from None
    # 회전은 끝났다 — 재암호화가 실패해도 회전 기록은 남긴다 (D22-②)
    audit.append("keys.rotate", actor=p.sub, target=body.kind, data={k: v for k, v in out.items() if k != "previous_valid_until"})
    if body.kind == "encryption":
        re = reencrypt(db)
        audit.append("keys.reencrypt", actor=p.sub, target="encryption", data=re)
        out = {**out, **re}
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
- L590-600 `rotate_keys`: env 모드면 `EnvManaged` → 409 와 2단계 절차 안내(L599-600).
- L601-602(D22-②): **회전 직후** `keys.rotate` 감사를 남기고, 재암호화 결과는 별도 `keys.reencrypt` 감사(L605). 3차 L6 — 재암호화가 예외로 끝나면 회전 기록이 없던 문제 — 가 고쳐졌다(C62-05).
- L610-618 `reencrypt_keys`: env 모드 회전의 4단계(`SECURITY.md:56`)가 부르는 API. `failed == 0` 이고 `drop_previous` 면 파일 이전 키를 지운다.
- 동기 함수(`def`)라 FastAPI 가 스레드풀에서 돌린다 — `flock` 대기·RSA 키 생성이 이벤트 루프를 막지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:중간] **M3** env 2단계 회전에서 **재암호화를 어느 파드가 처리하느냐**가 결과를 정한다. `reencrypt`(`app/api/admin.py:540-580`)는 요청을 받은 파드의 첫 키로 다시 암호화한다(L547 `f = keys.fernet()`). 절차(`SECURITY.md:53-56`)는 2단계 "재배포" 직후 `nexus keys reencrypt` 를 부르라고만 하고 롤아웃 완료를 기다리라는 말이 없다. 시나리오: 2단계 롤링 중 `nexus keys reencrypt` 가 아직 1단계 파드(`ENCRYPTION_KEY`=A, `ENCRYPTION_KEY_NEXT`=B)에 닿음 → 모든 시크릿·MFA 비밀·로그 원문·웹훅 토큰을 **옛 키 A 로** 다시 씀 → 응답 `failed: 0` → 운영자는 5단계(`SECURITY.md:58`)대로 `ENCRYPTION_KEY_PREVIOUS`(A)를 비우고 재배포 → 새 파드는 B 만 알아 **저장 암호문 전체가 복호화 불가**. 응답에 어떤 키로 썼는지 정보가 없어(L614-618) 운영자가 알아챌 수 없다. 최소 수정: ① `settings.encryption_key_next` 가 설정된 파드(=1단계, 회전 진행 중)는 `reencrypt` 를 409 로 거부 ② 응답에 첫 키 지문(예: `sha256(primary)[:12]`)을 넣고 CLI 가 기대 지문과 비교 ③ 절차 4단계 앞에 `kubectl rollout status`·`docker compose ps` 확인 추가 ④ `encryption_key_count` 에서 다음 키 제외.
- [위험:낮음] **L7(교차, INFRA)** Helm `existingSecret` 에서 1단계도 파드 재기동이 필요하다 — 체크섬이 상수라 `helm upgrade` 만으로는 파드가 다음 키를 읽지 않는다. 절차는 2단계에만 `rotationNonce` 를 언급한다(`SECURITY.md:55`) — 1단계(`SECURITY.md:51`)에서 nonce 를 안 바꾸면 2단계 롤링 중 옛 파드가 새 kid 를 몰라 3차 L7 과 같은 간헐 401·복호화 실패가 그대로 난다. [INFRA.md](../../INFRA.md) 참고.

```markdown path=SECURITY.md lines=48-59
**env 모드**(Secret 주입 — 앱이 회전하지 않음, API 는 409 `keys_env_managed`):

1. 새 키 생성(`openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048` · `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
2. **1단계 — 다음 키 배포**: `SIGNING_KEY_PEM_NEXT` = 새 키(공개키 PEM 도 됨), `ENCRYPTION_KEY_NEXT` = 새 키 → 재배포.
   모든 파드가 새 kid 를 JWKS·검증에, 새 암호화 키를 복호화에 쓴다(아직 서명·암호화는 옛 키) (62nd).
3. **2단계 — 올림**: `SIGNING_KEY_PEM` = 새 키, `SIGNING_KEY_PEM_PREVIOUS` = 이전 키(개인·공개 PEM, 여러 개 이어 붙임),
   `ENCRYPTION_KEY` = 새 키, `ENCRYPTION_KEY_PREVIOUS` = 이전 키(쉼표), `*_NEXT` 비움 → 재배포. 1단계 덕분에 롤링 중
   옛·새 파드가 서로의 토큰·암호문을 받는다. Helm `existingSecret` 이면 체크섬이 그대로이므로 `secrets.rotationNonce` 값을 바꿔 재기동.
4. `nexus keys reencrypt` → `failed` 0 확인.
5. `SIGNING_KEY_PREVIOUS_UNTIL`(ISO 8601) 에 서명 토큰 최대 수명이 지나는 시각을 넣는다 — 그 뒤 이전 키는 토큰 검증·JWKS 에서
   빠지고 백업·보고서 서명 검증에만 남는다. 이전 암호화 키는 재암호화 실패 0 확인 뒤 비우고 재배포.
6. 키가 유출됐으면 그 kid 를 `SIGNING_REVOKED_KIDS`(쉼표)에 — 토큰·백업·보고서 어디서도 검증하지 않는다(파일 모드 보관본 포함).
```
**분석 / Analysis (Level 2)**
- 절차는 키 코드와 맞게 2단계로 갱신됐다(62nd). 위 M3·L7 두 공백(재암호화 시점·1단계 nonce), M5(L59 — 현재 키 폐기는 회전이 먼저), L6(이전 서명 키 보존)이 문서 쪽 수정 대상이다.

## 파일 평가 / File Assessment
- 강점: (1) 3차 Low 6건(L1·L2·L3·L6·L7·L27)을 코드로 해소 — 세대 카운터(`app/core/keys.py:65-76`), flock 회전 잠금(`:79-88`), 공개키·검증 끝·폐기 kid(`:137-166`). (2) 다음 키를 JWKS·검증·복호화에 **모두** 넣어 2단계 회전이 실제로 성립한다(`:186`, `:201`, `:214`). (3) 감사 선기록과 전용 예외로 실패 경로가 분명해졌다(`app/api/admin.py:598-605`).
- 위험: (1) [위험:중간] M3 — env 2단계 중 1단계 파드가 재암호화하면 옛 키로 다시 쓰고, 절차대로 옛 키를 지우면 저장 암호문 전체 손실(`app/api/admin.py:547`, `:610-618`, `SECURITY.md:53-58`). (2) [위험:중간] M5 — 현재 키의 kid 를 폐기 목록에 넣어도 효과 없음(`app/core/keys.py:153-166`, `SECURITY.md:59`). (3) [위험:낮음] L5·L6 — 재암호화·이전 키 제거가 잠금 밖, 이전 서명 키 안내 불일치·Helm 보존 누락·키 종류 미확인.
- 개선 권고(영향순): (1) 1단계 파드의 재암호화 거부 + 첫 키 지문 응답 + 절차에 롤아웃 완료 확인. (2) 현재 kid 폐기 시 기동 실패 + 절차 문구. (3) drop 전 세대 비교. (4) `.env.*`·`values.yaml` 주석 정정, 서명 이전 키 `lookup` 보존, RSA 여부 확인.
- 파일 위험 등급 / Risk grade: **중간** — [위험:중간] 2(M3·M5) + [위험:낮음] 다수.
- 재읽기 권고 레벨: 2 — 회전 순서(파일 쓰기·세대·캐시)와 운영 절차가 맞물리는 곳이라 블록이 아니라 줄 단위로 읽어야 한다.

## 3차 리뷰 대비 / vs 3rd Review
| 3차 지적 | 4차 상태 | 근거(현재 path:line) |
|---|---|---|
| L1 mtime 세대 | 해결 | `app/core/keys.py:65-76` · C62-01 |
| L2 env 이전 키 개인키 필수·만료 없음·폐기 불가 | 해결 | `:137-166` · C62-04 |
| L3 회전 잠금 없음 | 해결(파일 쓰기) · 재암호화+drop 은 밖(L5) | `:79-88`, `:264-271` |
| L6 회전 감사 시점·넓은 예외 | 해결 | `app/api/admin.py:598-605` |
| L7 env 롤링 중 옛 파드 | 코드 해결 · existingSecret 1단계 nonce 문서 공백(L7) | `:186`, `:201`, `:214` · `SECURITY.md:51` |
| M4 재암호화 대상 누락(59th 수정) | 유지 | `app/api/admin.py:574-579` |
