> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: 대상 아님(2차 범위 밖)

### 01. app/core/keys.py
**파일 요약 / File Summary**
- 역할 / Role: 서명 키(RS256, JWKS)와 암호화 키(Fernet)의 단일 출처. 키는 env(Helm Secret·`.env`) 우선, 없으면 `data_dir/keys` 에 만들어 보존한다. 12차(D21-③, Epic 58)에서 **회전(rotation)** 이 들어왔다 — 파일 모드는 관리 명령(`rotate_signing`·`rotate_encryption`)으로, env 모드는 `*_PREVIOUS` env 로 운영자가 회전한다.
- 진입점 거리 / Distance: 1 — 모든 토큰 발급·검증(`app/core/security.py:21-38`), JWKS(`app/api/idp.py:57-59`), 시크릿·MFA·로그 원문 암복호화(`app/services/secrets.py:59`·`:80`, `app/api/idp.py:143`, `app/core/logs.py:88`), 관리 API(`app/api/admin.py:557-588`) 가 직접 부른다.
- LOC: 232 (파일 전체가 D21-③ 로 다시 쓰였다 — `test/impl/58th/result.md` r1 "다시 쓴 keys.py")
- 주요 export: `signing_key`·`kid`·`verification_keys`·`decode_long_lived`·`jwks`·`fernet`·`env_managed`·`encryption_key_count`·`rotate_signing`·`rotate_encryption`·`drop_previous_encryption`·`ensure_keys`
- 의존(내부): `app.core.config.settings`
- 외부 패키지: cryptography(`Fernet`·`MultiFernet`·RSA·PEM), 표준 `functools.lru_cache`·`os`·`json`
- 부수효과 / Side effects: `data_dir/keys/` 아래 파일 생성·교체(`signing.pem`·`secrets.key`·`signing.prev.json`·`signing.archive.pem`·`secrets.prev.key`·`generation`), 프로세스 캐시(`lru_cache`) 비우기.
- 동시성 / Concurrency: 다중 워커·다중 프로세스가 같은 키 디렉터리를 공유한다. 캐시 무효화는 `generation` 파일의 mtime 으로 알린다. 회전 자체에는 잠금(lock)이 없다.
- 테스트 커버리지 흔적: `test/test_key_rotation.py:24-50`(C58-01 서명 회전·유예·보관 공개키), `:52-69`(C58-02 암호화 회전·재암호화·drop), `:71-78`(C58-03 권한·확인·env 409), `:80-91`(C58-04 다른 워커 반영), `:93-97`(C58-05 CLI).
- 3차 변경 요약 / Changes in D20·D21: D21-③ 하나. ① 서명 키: 새 kid, 직전 키들은 각자 유예(`until`) 동안 JWKS·검증에 남고 공개키는 영구 보관(`signing.archive.pem`). ② 암호화 키: `MultiFernet` 로 이전 키 복호화 유지, 재암호화 뒤 이전 키 제거. ③ 다른 워커 반영: `generation` 파일 stat. ④ env 모드: `SIGNING_KEY_PEM_PREVIOUS`·`ENCRYPTION_KEY_PREVIOUS`(`app/core/config.py:34-35`).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/keys.py lines=1-4
"""서명 키(RS256, JWKS)·암호화 키(Fernet) — env 우선, 없으면 data_dir/keys 에 생성·보존. 회전은 아래 rotate_* (D21-③).

여러 워커가 동시에 생성하지 않도록 O_EXCL 로 만들고, 이미 있으면 읽는다(entrypoint 시드 단계에서 선생성).
"""
```
**분석 / Analysis (Level 2)**
- 모듈 계약: "env 우선, 없으면 파일". 컨테이너가 여러 워커로 뜰 때 같은 키를 동시에 만들지 않도록 `O_EXCL` 로 만든다(L26-36). Helm 은 차트가 키를 항상 env 로 준다(`deploy/helm/agentnexus/templates/secret.yaml:10-11`) — 즉 **Helm 배포는 언제나 env 모드**이고, 파일 모드는 compose(공유 볼륨 `appdata`)에서 쓰인다. 이 차이가 아래 회전 경로를 가른다.

```python path=app/core/config.py lines=31-35
    # 키 (비우면 data_dir/keys 에 자동 생성·보존)
    signing_key_pem: str = ""
    encryption_key: str = ""
    signing_key_pem_previous: str = ""  # env 로 키를 줄 때의 회전 — 직전 서명 키(PEM, 여러 개 이어 붙임) (D21-③)
    encryption_key_previous: str = ""   # 직전 암호화 키(쉼표 구분) — 재암호화가 끝나면 비운다
```
**분석 / Analysis (Level 2)**
- L34-35(D21-③): env 모드 회전용 이전 키. 서명 쪽은 PEM 을 이어 붙이고, 암호화 쪽은 쉼표로 구분한다(Fernet 키는 urlsafe base64 라 쉼표가 들어가지 않으므로 구분자로 안전하다).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L34 는 **개인키** PEM 이어야 한다(아래 L116 이 `load_pem_private_key` 로 읽는다). 검증에는 공개키만 필요하므로 운영자가 공개키 PEM 을 넣는 실수가 자연스럽다 — 그러면 첫 서명·검증 호출에서 `ValueError` 로 모든 인증이 실패한다(시드의 `ensure_keys`(`app/seed.py:371`)가 먼저 실패하므로 api 기동 단계에서 드러나기는 한다). 최소 수정: `load_pem_public_key` 를 먼저 시도하고 실패하면 개인키로 읽기.

```python path=app/core/keys.py lines=51-78
# ── 회전 (D21-③) — 파일 모드(data_dir/keys)는 관리 명령으로 회전, env 모드는 *_PREVIOUS env 로 운영자가 회전.
# 워커·파드마다 캐시하므로 회전은 keys/generation 파일을 바꿔 알린다(호출마다 stat 1회 — 바뀌면 다시 읽음).
SIGNING_PREV = "signing.prev.json"       # 이전 서명 키들 [{pem, until}] — 각자 유예(until) 동안 JWKS·토큰 검증에 남김
SIGNING_ARCHIVE = "signing.archive.pem"  # 회전한 공개키 전부 — 백업·감사 보고서 서명은 오래 검증해야 한다
FERNET_PREV = "secrets.prev.key"         # 이전 암호화 키들(줄 단위) — 재암호화가 끝나면 비운다


def _generation() -> int:
    try:
        return (_keys_dir() / "generation").stat().st_mtime_ns
    except OSError:
        return 0


def _bump() -> None:
    g = _keys_dir() / "generation"
    g.write_text(str(time.time_ns()))
    _signing.cache_clear()
    _fernet.cache_clear()


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

```
**분석 / Analysis (Level 2)**
- L53-55: 회전 상태 파일 3개. `signing.prev.json` 은 `[{pub, until}]` — **공개키만** 남긴다(이전 개인키는 버린다, 좋은 선택). `signing.archive.pem` 은 회전한 공개키를 영구히 쌓는다(백업·감사 보고서 서명은 수년 뒤에도 검증해야 하므로). `secrets.prev.key` 는 이전 Fernet 키를 줄 단위로.
- L58-62 `_generation`: `generation` 파일의 **mtime(나노초)** 를 세대 번호로 쓴다. 파일이 없으면 0. 아래 `_signing(gen)`·`_fernet(gen)` 이 `lru_cache` 키로 이 값을 받으므로, 다른 워커가 회전하면 다음 호출에서 세대가 바뀌어 캐시가 저절로 새로 읽힌다(`test/test_key_rotation.py:80-91`).
- L65-69 `_bump`: 내용으로 `time_ns()` 를 쓰지만 판단은 mtime 으로 한다. 자기 프로세스 캐시는 직접 비운다.
- L72-77 `_write_atomic`: 임시 파일에 0600 으로 쓰고 `os.replace` — 읽는 쪽이 반쯤 쓴 파일을 보지 않는 표준 패턴. 흔한 실수: `open(path, "w")` 로 바로 덮어쓰면 읽는 워커가 빈 파일을 읽어 `ValueError` 를 낸다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L60 mtime 해상도에 의존한다. ext4·overlayfs 는 나노초지만, 일부 네트워크 FS·호스트 바인드 마운트(예: macOS Docker Desktop)는 해상도가 거칠어 **같은 틱 안의 두 번째 `_bump`**(예: `rotate_encryption` 직후 `reencrypt?drop_previous=true`)를 다른 워커가 놓칠 수 있다. 그 워커는 다음 `_bump` 까지 낡은 키 집합을 쓴다. 최소 수정: 세대 값을 파일 **내용**(단조 증가 카운터)으로 읽거나 `(st_mtime_ns, st_size, st_ino)` 묶음으로 비교.
- [위험:낮음] L58-62 + L20-23: `signing_key()`·`verification_keys()`·`fernet()` 호출마다 `_keys_dir()` 의 `mkdir(exist_ok=True)` 와 `stat` 이 돈다. 토큰 검증 1회에 `_signing(_generation())` 이 2번(L142, L136) 불리므로 시스템 호출 4회 이상 — 핫 경로 비용. 짧은 시간(예: 1초) 결과 캐시로 충분하다.

```python path=app/core/keys.py lines=80-102
def env_managed() -> dict:
    return {"signing": bool(_configured(settings.signing_key_pem)), "encryption": bool(_configured(settings.encryption_key))}


def _b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _kid_of(pub) -> str:
    der = pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


def _pems(blob: bytes) -> list[bytes]:
    marker = b"-----END"
    out, cur = [], b""
    for line in blob.splitlines(keepends=True):
        cur += line
        if line.startswith(marker):
            out.append(cur)
            cur = b""
    return [x for x in out if x.strip()]
```
**분석 / Analysis (Level 2)**
- L80-81 `env_managed`: 관리 API 가 "env 모드면 409" 를 판단하는 근거(`app/api/admin.py:570-575`). 서명·암호화를 따로 본다 — 한쪽만 env 로 줄 수도 있다.
- L89-91 `_kid_of`: kid = 공개키 DER 의 SHA-256 앞 16 hex. 키에서 결정적으로 나오므로 워커마다 같은 kid 를 만든다(별도 저장 불필요).
- L94-102 `_pems`: `-----END` 줄 단위로 여러 PEM 을 자른다. 마지막 END 뒤 쓰레기는 버린다.

```python path=app/core/keys.py lines=105-123
def _prev_entries() -> list[dict]:
    f = _keys_dir() / SIGNING_PREV
    return json.loads(f.read_text()) if f.exists() else []


@lru_cache(maxsize=2)
def _signing(gen: int):
    pem = _configured(settings.signing_key_pem).encode() or _read_or_create(_keys_dir() / "signing.pem", _new_rsa_pem)
    current = serialization.load_pem_private_key(pem, password=None)
    previous = []  # (공개키, 유예 끝 epoch | None)
    env_prev = _configured(settings.signing_key_pem_previous).encode()
    for blob in _pems(env_prev):
        previous.append((serialization.load_pem_private_key(blob, password=None).public_key(), None))
    d = _keys_dir()
    for e in _prev_entries():
        previous.append((serialization.load_pem_public_key(e["pub"].encode()), float(e["until"])))
    archive = [serialization.load_pem_public_key(b) for b in _pems((d / SIGNING_ARCHIVE).read_bytes())] \
        if (d / SIGNING_ARCHIVE).exists() else []
    return current, previous, archive
```
**분석 / Analysis (Level 2)**
- L110-123 `_signing(gen)`: (현재 개인키, [(이전 공개키, 유예 끝 epoch|None)], [보관 공개키]) 를 만든다. `maxsize=2` 라 직전 세대와 현재 세대만 캐시에 남는다.
- L115-117: env 이전 키는 `until=None` — **만료가 없다**. 파일 이전 키(L119-120)는 `until` 이 있다.
- L121-122: 보관본은 파일 모드에서만 쌓인다(env 모드에는 보관본이 없다 — env 이전 키를 지우면 그 키로 서명한 백업·감사 보고서는 검증 불가).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L115-117 env 이전 키는 운영자가 지울 때까지 영구히 검증에 쓰인다(`deploy/helm/agentnexus/values.yaml:75` 주석 "토큰 수명 뒤 비움" 이 유일한 안내). 회전 이유가 **키 유출**이면, 지우는 것을 잊는 동안 유출 키로 서명한 토큰이 계속 통과한다. 최소 수정: env 이전 키에도 유예 시각을 함께 받거나(`PEM|epoch`), 기동 시 "이전 키가 N시간 넘게 남아 있음" 경고 로그.
- [위험:낮음] L121-122 보관본에는 **폐기(revocation) 수단이 없다**. 유출로 회전한 키의 공개키도 보관본에 남으므로, 유출 개인키로 서명한 "백업" 이 `decode_long_lived`(L147-153) 를 영구히 통과한다. 복원은 `admin:write`·확인 입력이 필요하지만, `nexus bundle import` 는 번들 안 `config-backup.json` 을 기본값으로 복원한다(`app/cli/nexus.py:786-790`, [nexus.py.md](../cli/nexus.py.md) H3 와 결합). 최소 수정: `rotate_signing(compromised=True)` 이면 보관본에 넣지 않거나, 폐기 kid 목록 파일을 두고 L144 에서 제외.

```python path=app/core/keys.py lines=126-161
def signing_key():
    return _signing(_generation())[0]


def kid() -> str:
    return _kid_of(signing_key().public_key())


def _active_previous() -> list:
    now = time.time()
    return [pub for pub, until in _signing(_generation())[1] if until is None or until > now]


def verification_keys(kid_: str | None, long_lived: bool = False) -> list:
    """서명 검증용 공개키 후보 — 현재 키·유예 중인 직전 키(토큰), long_lived 면 회전한 공개키 보관본까지(백업·보고서 서명).
    kid 가 있으면 그 키만(없으면 빈 목록 = 모르는 키)."""
    current, _, archive = _signing(_generation())
    cands = [current.public_key(), *_active_previous(), *(archive if long_lived else [])]
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
    for pub in [signing_key().public_key(), *_active_previous()]:
        nums = pub.public_numbers()
        out.append({"kty": "RSA", "use": "sig", "alg": "RS256", "kid": _kid_of(pub), "n": _b64u(nums.n), "e": _b64u(nums.e)})
    return {"keys": out}
```
**분석 / Analysis (Level 2)**
- L139-144 `verification_keys`: 후보 = 현재 공개키 + 유예 중 이전 키(+ `long_lived` 면 보관본). kid 가 있으면 그 kid 만 남긴다 — 모르는 kid 면 빈 목록 → 호출자가 "알 수 없는 서명 키" 로 거부(`app/core/security.py:30-32`). 이것이 "회전 직후 이전 토큰이 계속 통과" 의 핵심이다.
- kid 가 **없는** 토큰은 후보 전체가 돌아오고, 호출자는 `cands[0]`(현재 키)만 쓴다(`app/core/security.py:33`, L153). 플랫폼이 서명하는 토큰은 모두 kid 를 붙이므로(`app/core/security.py:24`, `app/api/admin.py:281-282`, `app/services/audit.py:225`) 실제로는 문제 없다. 흔한 실수: 후보를 순서대로 모두 시도하는 루프를 짜면 kid 없는 위조 토큰에 대해 키마다 검증 비용이 든다 — 여기처럼 kid 로 좁히는 것이 맞다.
- L147-153 `decode_long_lived`: 보관본까지 보는 검증. **issuer 를 강제하지 않는다** — `**kw` 로 호출자가 넘겨야 한다. 감사 보고서 검증은 넘기고(`app/services/audit.py:237-238`), 백업 복원은 넘기지 않는다(`app/api/admin.py:302`, `app/api/ops.py:858`). 아래 [admin.py.md](../api/admin.py.md) 참고.
- L156-161 `jwks`: 현재 + 유예 중 이전 키. 보관본은 JWKS 에 넣지 않는다(외부 검증자가 오래된 키를 받아들이지 않게 — 맞는 선택).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L136 `_active_previous` 와 L142 가 같은 호출 안에서 `_signing(_generation())` 을 두 번 부른다. 그 사이 다른 워커가 회전하면 두 호출이 서로 다른 세대를 볼 수 있다(현재 키는 새 세대, 이전 키는 옛 세대). 결과는 "후보가 한 번 덜 맞음" 정도라 실해는 작지만, 한 번 읽은 튜플을 재사용하는 편이 명확하다.

```python path=app/core/keys.py lines=164-185
@lru_cache(maxsize=2)
def _fernet(gen: int) -> MultiFernet:
    if _configured(settings.encryption_key):
        primary = _configured(settings.encryption_key).encode()
    else:
        primary = _read_or_create(_keys_dir() / "secrets.key", Fernet.generate_key).strip()
    older = [k.strip().encode() for k in _configured(settings.encryption_key_previous).split(",") if k.strip()]
    f = _keys_dir() / FERNET_PREV
    if f.exists():
        older += [ln.strip() for ln in f.read_bytes().splitlines() if ln.strip()]
    return MultiFernet([Fernet(primary), *[Fernet(k) for k in older]])  # 암호화는 첫 키, 복호화는 모두


def encryption_key_count() -> int:
    """현재 + 이전(env·파일) 암호화 키 수 — 1 이면 재암호화가 끝나 이전 키가 없다."""
    f = _keys_dir() / FERNET_PREV
    files = len([ln for ln in f.read_bytes().splitlines() if ln.strip()]) if f.exists() else 0
    return 1 + files + len([k for k in _configured(settings.encryption_key_previous).split(",") if k.strip()])


def fernet() -> MultiFernet:
    return _fernet(_generation())
```
**분석 / Analysis (Level 2)**
- L174 `MultiFernet([primary, *older])`: **암호화는 첫 키, 복호화는 순서대로 모두** 시도한다. `rotate()` 는 어느 키로든 풀어서 첫 키로 다시 암호화한다 — 재암호화(`app/api/admin.py:549`)의 근거.
- L170 env 이전 키 + L171-173 파일 이전 키를 합친다. env·파일이 섞여도 동작한다.
- 흔한 실수: Fernet 키 문자열을 `strip()` 없이 읽으면 줄바꿈 때문에 `ValueError: Fernet key must be 32 url-safe base64-encoded bytes` — 여기서는 L169·L173 이 모두 strip 한다.

```python path=app/core/keys.py lines=188-203
def rotate_signing(grace_s: int) -> dict:
    """새 서명 키 — 직전 키는 grace_s 동안 JWKS·토큰 검증에 남고, 공개키는 보관본에 영구히(백업·보고서 서명 검증)."""
    if env_managed()["signing"]:
        raise RuntimeError("env")
    d = _keys_dir()
    old_pem = (d / "signing.pem").read_bytes()
    old_pub = serialization.load_pem_private_key(old_pem, password=None).public_key()
    pub_pem = old_pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    with open(d / SIGNING_ARCHIVE, "ab") as f:
        f.write(pub_pem)
    now = time.time()  # 유예 중인 이전 키는 모두 남긴다 — 유예 안에 두 번 회전해도 그 사이 토큰이 끊기지 않게 (공개키만 보관)
    keep = [e for e in _prev_entries() if float(e["until"]) > now] + [{"pub": pub_pem.decode(), "until": now + grace_s}]
    _write_atomic(d / SIGNING_PREV, json.dumps(keep).encode())
    _write_atomic(d / "signing.pem", _new_rsa_pem())
    _bump()
    return {"previous_kid": _kid_of(old_pub), "kid": kid(), "previous_valid_until": time.time() + grace_s}
```
**분석 / Analysis (Level 2)**
- 순서: env 모드면 거부(L190-191, 호출자가 409 로) → 현재 개인키에서 공개키 → 보관본에 덧붙임(L196-197) → 유예가 남은 이전 키 + 방금 키를 `signing.prev.json` 에 원자적으로(L198-200) → 새 개인키(L201) → 세대 올림(L202).
- L198-199: 이전 키를 **목록**으로 각자 유예 — 58th r2 에서 "유예 안에 두 번 회전하면 두 번 전 키의 토큰이 끊김" 결함을 찾아 고친 결과(`test/impl/58th/result.md` r2·r3).
- 중간에 프로세스가 죽어도 상태가 깨지지 않는 순서다: 이전 키 목록을 먼저 쓰고 새 키를 나중에 쓰므로, 어느 지점에서 멈춰도 "현재 키로 서명한 토큰" 은 검증 가능하다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] 잠금이 없다. 관리자 두 명(또는 CLI 와 콘솔)이 동시에 회전하면 둘 다 같은 `old_pem` 을 읽고, 나중에 쓴 쪽이 `signing.prev.json`·`signing.pem` 을 덮는다 — 먼저 회전한 쪽의 **새 키가 어디에도 남지 않아** 그 사이 그 키로 서명된 토큰(다른 워커가 이미 받아 쓴)이 즉시 무효가 된다. 확인 입력(`ROTATE`)·admin 전용이라 가능성은 낮다. 최소 수정: `fcntl.flock(keys/rotate.lock)` 으로 L192-202 를 감싸기.
- [위험:낮음] L193 `signing.pem` 이 없으면(예: 직전까지 env 모드였다가 env 를 비운 경우) `FileNotFoundError` → 500. `_read_or_create` 로 읽으면 된다.

```python path=app/core/keys.py lines=206-226
def rotate_encryption() -> dict:
    """새 암호화 키 — 이전 키는 복호화용으로 남는다(재암호화 뒤 drop_previous_encryption)."""
    if env_managed()["encryption"]:
        raise RuntimeError("env")
    d = _keys_dir()
    old = (d / "secrets.key").read_bytes().strip()
    with open(d / FERNET_PREV, "ab") as f:
        f.write(old + b"\n")
    os.chmod(d / FERNET_PREV, 0o600)
    _write_atomic(d / "secrets.key", Fernet.generate_key())
    _bump()
    return {"previous_keys": len([ln for ln in (d / FERNET_PREV).read_bytes().splitlines() if ln.strip()])}


def drop_previous_encryption() -> int:
    f = _keys_dir() / FERNET_PREV
    n = len([ln for ln in f.read_bytes().splitlines() if ln.strip()]) if f.exists() else 0
    if f.exists():
        f.unlink()
    _bump()
    return n
```
**분석 / Analysis (Level 2)**
- `rotate_encryption`: 현재 키를 이전 키 파일에 덧붙이고 새 키를 원자적으로 쓴 뒤 세대 올림. 저장 암호문 재암호화는 호출자(`app/api/admin.py:571`)가 바로 이어서 한다.
- `drop_previous_encryption`: 이전 키 파일을 지운다. "재암호화 실패 0" 확인은 호출자 책임(`app/api/admin.py:585`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L212-214: 비밀 키 재료를 담는 `secrets.prev.key` 를 `open(..., "ab")`(umask 기본 권한, 보통 0644)로 만든 뒤 `chmod 0600` 한다 — **쓰고 나서 권한을 바꾸면 그 사이 읽힐 수 있다**는, 같은 12차(D21-⑤)가 CLI 에서 고친 패턴 그대로다(`app/cli/nexus.py:54-59`). 컨테이너는 단일 사용자라 실해는 작지만 호스트 바인드 마운트면 다르다. 최소 수정: `os.open(path, O_WRONLY|O_CREAT|O_APPEND, 0o600)`.
- [위험:중간] (교차) L220-226 `drop_previous_encryption` 은 "재암호화 대상이 저장 암호문 전부" 라는 전제를 호출자에게 맡긴다. 그런데 재암호화 대상 목록에 같은 12차(D21-⑤)가 새로 만든 암호문 `A2ATask.push_config.token_enc` 가 빠져 있다 → 이 키를 지우면 진행 중 Task 의 클라이언트 웹훅 중계가 모두 실패한다. 결함 위치·시나리오는 [admin.py.md](../api/admin.py.md) M4.

## 파일 평가 / File Assessment
- 강점: (1) 이전 서명 키는 **공개키만** 보관하고 유예를 키마다 따로 둔다 — 연속 회전에도 토큰이 끊기지 않는다(`app/core/keys.py:198-200`, 시험 `test/test_key_rotation.py:24-50`). (2) 상태 파일을 원자적으로 교체하고, 쓰는 순서가 중단에 안전하다(`app/core/keys.py:72-77`, `:196-202`). (3) 다른 워커 반영을 캐시 직접 조작 없이 세대 번호 하나로 해결해 다중 워커 일관성이 단순하다(`app/core/keys.py:58-69`, `:110-111`).
- 위험: (1) [위험:낮음] 보관 공개키·env 이전 키에 폐기·만료가 없다 — 유출로 회전한 키가 백업 서명·토큰 검증에 계속 쓰일 수 있다(`app/core/keys.py:115-122`). (2) [위험:낮음] 회전에 잠금이 없고, 이전 암호화 키 파일을 umask 권한으로 만든다(`app/core/keys.py:188-217`). (3) [위험:낮음] 세대 판단이 파일 mtime 해상도에 의존(`app/core/keys.py:58-62`).
- 개선 권고(영향순): (1) 재암호화 대상 목록을 이 모듈 가까이(예: `keys.ENCRYPTED_COLUMNS`)에 두고 `drop_previous_encryption` 이 그 목록 전체의 재암호화 결과를 받아야만 지우게 — 새 암호문 컬럼이 생길 때 빠뜨리지 않게. (2) 폐기 kid 목록 + env 이전 키 만료. (3) `flock` 잠금, 0600 생성, 세대 값은 파일 내용으로.
- 파일 위험 등급 / Risk grade: 중간 — 자체 결함은 [위험:낮음] 5건이지만 플랫폼 전체의 토큰·암호문 수명을 쥔 모듈이고, 교차 [위험:중간] 1건(재암호화 범위 누락 M4)이 이 모듈의 계약(`drop_previous_encryption`)에서 시작된다.
- 재읽기 권고 레벨: 2 — 파일 모드·env 모드·다중 워커 세대 캐시가 겹쳐 있어, 회전 시나리오(파일·env × 워커 수)를 줄 단위로 따라가 봐야 한다.

## 2차 리뷰 대비 / vs 2nd Review
2차 범위 밖 파일이다(2차 INDEX §1.3 목록에 없음). 관련 2차 항목만 대조한다.

| 2차 항목 | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| (2차 지적 없음 — 키 회전은 PRD D21-③ 신규 범위) | 신규 구현 | `app/core/keys.py:51-226`, `app/api/admin.py:522-588`, `app/cli/nexus.py:578-600` |
| 2차 [security.py.md](../../../../round2/CODE-REVIEW-Lv2/app/core/security.py.md) 인용 원문 — `verify_local` 이 현재 키 하나(`keys.signing_key().public_key()`)로만 검증 | 변경(회전 대응) | 헤더 kid 로 현재·유예 중 이전 키 중에서 고른다 `app/core/security.py:28-38` → `app/core/keys.py:139-144` |
| 2차 [security.py.md](../../../../round2/CODE-REVIEW-Lv2/app/core/security.py.md) "(1차 지적 후속) 서명 키 공유 — 키 용도 분리는 남은 하드닝" | 미해결 + 범위 확대 | 같은 키·같은 보관본으로 토큰·백업·감사 보고서를 모두 검증 `app/core/keys.py:147-153`. 백업 복원은 issuer 를 요구하지 않는다 `app/api/admin.py:302`, `app/api/ops.py:858` |
