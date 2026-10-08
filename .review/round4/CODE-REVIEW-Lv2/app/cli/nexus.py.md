> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 4차 리뷰(round4, D22 hunk 한정) · 스냅샷 2026-10-08 14:27 KST · [← INDEX.md](../../INDEX.md) · 3차 리뷰: [round3 nexus.py.md](../../../../round3/CODE-REVIEW-Lv2/app/cli/nexus.py.md) · [round3 mcp_modern.py.md](../../../../round3/CODE-REVIEW-Lv2/app/services/mcp_modern.py.md)

### 10. app/cli/nexus.py (+ `app/services/mcp_modern.py:57-81` listen 반납)
**파일 요약 / File Summary**
- 역할 / Role: 운영 CLI `nexus`(typer) — 로그인·컨텍스트·설치·번들 반출입·키 회전 등. 이번 범위는 설정 파일 권한과 번들 반입의 구성 복원 기본값. 같은 D22-⑥ 묶음의 `mcp_modern.listen_release`(MCP `subscriptions/listen` 구독 반납)를 함께 본다.
- 진입점 거리 / Distance: 0 — 콘솔 스크립트 `nexus`(`main`, `app/cli/nexus.py:817-818`). `listen_release` 는 1 — `/mcp/{server}` listen 스트림의 `finally`(`app/services/mcp_modern.py:514-517`)와 열기 실패 경로(`app/api/mcp.py:237-241`).
- LOC: 823 (3차 804 → 823) · `app/services/mcp_modern.py` 517 (3차 507 → 517)
- 주요 export: `app`(typer), `_save`·`_write_private`·`bundle_import` · `mcp_modern.listen_acquire`·`listen_release`·`listen_count`
- 의존(내부): CLI 는 HTTP 로만 서버와 대화(`_req`). `mcp_modern` → `app.services.ratelimit`(보유자 슬롯)
- 외부 패키지: typer, httpx, (외부 명령) cosign·docker
- 부수효과 / Side effects: `~/.config/…` 설정 디렉터리·`config.json` 생성·권한 변경, `docker load/tag/push`, `/api/v1/admin/restore` 호출. listen 반납은 Valkey `ZREM`(스레드).
- 동시성 / Concurrency: CLI 는 단일 프로세스. `listen_release` 는 이벤트 루프에서 불리며 Valkey 왕복을 실행기(executor)로 넘긴다.
- 테스트 커버리지 흔적: `test/test_remaining_items2.py:106`(C66-05 0700·0600), `:118`(C66-06 반입 구성 복원 기본 끔), `:40`(C66-02 listen 반납이 0.1초 안에 돌아옴·스레드에서 실행).
- 4차 변경 요약 / Changes in D22: D22-⑥ — 설정 디렉터리 0700(이미 있던 것도)·파일 권한은 연 파일에 `fchmod`(3차 L30), `bundle import` 구성 복원 기본 **끔**(`--restore-config` 때만, 3차 L33 — 66th 가 3차 VERIFY 판정을 정정), listen 반납 비차단(3차 L22).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/cli/nexus.py lines=45-61
def _save(cfg: dict) -> None:
    if cfg.get("current"):  # 현재 컨텍스트에도 반영 (SCR-A11 context use)
        cfg.setdefault("contexts", {})[cfg["current"]] = {k: cfg.get(k) for k in ("url", "access_token", "refresh_token",
                                                                                   "expires_at") if cfg.get(k)}
    d = _config_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)  # 설정 디렉터리도 본인만 — 이미 있던 디렉터리도 (D22-⑥)
    os.chmod(d, 0o700)
    _write_private(d / "config.json", json.dumps(cfg, indent=2))


def _write_private(path: Path, text: str) -> None:
    """토큰·시크릿 파일 — 처음부터 0600 으로 만든다(쓰고 나서 권한을 바꾸면 그 사이 읽힐 수 있다, D21-⑤)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # 이미 있던 파일의 권한도 — 연 파일에 (경로로 바꾸면 그 사이 바꿔치기된 대상에 적용될 수 있다, D22-⑥)
    with os.fdopen(fd, "w") as f:
        f.write(text)

```
**분석 / Analysis (Level 2)**
- L50-51(D22-⑥): `mkdir(mode=0o700)` 은 **새로 만들 때만** 권한을 정하고(그것도 umask 를 거친다), 이미 있던 디렉터리는 그대로다 — 그래서 L51 `os.chmod(d, 0o700)` 를 함께 둔다. 흔한 실수: `mkdir(mode=…)` 만 믿는 것.
- L57-58(D22-⑥): `O_CREAT` 의 모드 `0o600` 도 **새 파일**에만 적용된다. 이미 있던 0644 파일은 `fchmod` 로 바꾼다 — 경로(`os.chmod(path)`)가 아니라 **연 파일 기술자**에 적용해 "확인과 사용 사이 바꿔치기(TOCTOU)" 를 피한다.
- 참고: L51 디렉터리는 경로 기반 `chmod` 다(파일과 다른 방식). 디렉터리는 사용자 홈 아래라 바꿔치기 위험이 실질적이지 않다 — 등급 없음.

```python path=app/cli/nexus.py lines=731-750
@bundle.command("import")
def bundle_import(path: Path = typer.Argument(Path("agentnexus-airgap")),
                  restore_config: bool = typer.Option(False, "--restore-config/--no-restore-config",
                                                      help="번들의 구성 백업으로 운영 데이터를 덮어쓴다 — 명시할 때만 (기본 끔, D22-⑥)"),
                  registry_host: str | None = typer.Option(None, "--registry", help="사설 레지스트리로 적재 (docker tag·push)"),
                  verify: bool = typer.Option(True, "--verify/--allow-unsigned",
                                              help="cosign 서명 검증(기본) — --cosign-key 로 manifest.json.sig(파일 해시 전체)·*.tar.sig. "
                                                   "PoC 만 --allow-unsigned (D20-③)"),
                  cosign_key: Path | None = typer.Option(None, "--cosign-key",
                                                         help="서명 검증 공개키 — 번들과 다른 경로로 받은 키. 번들 안의 키는 번들과 함께 "
                                                              "바꿔치기될 수 있어 쓰지 않는다 (59th)"),
                  apply_airgap: bool = typer.Option(False, "--apply-airgap", help="적재 후 에어갭 프로파일 적용"),
                  dry_run: bool = False):
    """번들 반입(SCR-A04) — SHA-256·서명 검증 → 불일치는 격리·제외 → docker load → 사설 레지스트리 적재 → 구성 복원 → 에어갭."""
    manifest = json.loads((path / "manifest.json").read_text())
    unsafe = [n for n in manifest["files"] if not (path / n).resolve().is_relative_to(path.resolve())]
    if unsafe:  # 매니페스트 이름이 번들 밖을 가리키면 격리 이동이 번들 밖 파일을 옮기게 된다
        typer.secho(f"번들 밖 경로가 매니페스트에 있어 중단합니다: {unsafe}", fg="red", err=True)
        raise typer.Exit(1)
    bad = [n for n, h in manifest["files"].items() if not (path / n).exists() or _sha256(path / n) != h]
```
**분석 / Analysis (Level 2)**
- L733-734(D22-⑥): `--restore-config/--no-restore-config`, 기본 `False`. 전에는 기본이 켜짐이라 번들 반입이 **확인 입력 없이** 운영 구성을 덮었다(`RESTORE` 를 CLI 가 대신 채움, 3차 L33).
- L739-741: 검증 공개키는 번들 **밖**(59th H3 수정 유지).
- L750 `bad`: 매니페스트에 **적힌** 파일만 해시를 확인한다.

```python path=app/cli/nexus.py lines=781-814
    ok_files = [n for n in manifest["files"] if n not in bad and n not in lic]
    typer.echo(f"체크섬 확인: {len(ok_files)} files" + (f" · 실패 {len(bad)}" if bad else ""))
    tars = [n for n in ok_files if n.endswith(".tar")]
    extra = [n for n in manifest["files"] if not n.endswith(".tar") and n != "config-backup.json"]
    if extra:
        typer.echo(f"동봉 파일: {', '.join(extra)} — 예: helm install agentnexus {path}/<chart>.tgz")
    if dry_run:
        typer.echo("\n".join([f"docker load -i {t}" for t in tars] + (["POST /api/v1/admin/restore"]
                                                                       if restore_config else [])))
        if bad:
            raise typer.Exit(1)
        return
    if bad:  # 부분 실패 — 성공분은 적재하되 종료 코드로 알린다 (ST-05)
        typer.secho("부분 실패 — 격리한 파일을 확인하세요. 나머지는 계속 적재합니다.", fg="yellow", err=True)
    for t in tars:
        out = subprocess.run(["docker", "load", "-i", str(path / t)], check=True, capture_output=True, text=True)
        if registry_host:
            for line in (out.stdout or "").splitlines():
                if line.startswith("Loaded image: "):
                    img = line.split(": ", 1)[1].strip()
                    dst = f"{registry_host.rstrip('/')}/{img.split('/')[-1]}"
                    subprocess.run(["docker", "tag", img, dst], check=True)
                    subprocess.run(["docker", "push", dst], check=True)
                    typer.echo(f"적재: {dst}")
    if restore_config and (path / "config-backup.json").exists() and "config-backup.json" not in bad:
        bk = json.loads((path / "config-backup.json").read_text())
        res = _req("POST", "/api/v1/admin/restore", json={"bundle": bk["bundle"], "signature": bk["signature"],
                                                            "confirm": "RESTORE"})  # --restore-config 가 명시 동의
        typer.echo(f"구성 복원: {sum(res['restored'].values())} rows")
    if apply_airgap:
        r = _req("POST", "/api/v1/admin/profiles/apply", json={"profile": "full", "airgap": True, "confirm": True})
        typer.echo(f"에어갭 프로파일 적용 — {r['commands']['compose']}")
    if bad:
        raise typer.Exit(1)
```
**분석 / Analysis (Level 2)**
- L787-792: 드라이런 출력에도 복원은 플래그가 있을 때만(C66-06).
- L805-809: 복원은 `--restore-config` + 파일 존재 + `bad` 에 없을 때. 서버는 백업 서명(`issuer=agentnexus-backup`, `app/api/admin.py:302`)을 다시 검증한다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L17(인접)** L805 조건은 "`bad` 에 없음" 이지 "매니페스트에 있음" 이 아니다. `bad`·`ok_files` 는 매니페스트 목록에서만 만들어지므로(L750, L781), 서명된 매니페스트에 **없는** `config-backup.json` 을 번들 디렉터리에 넣어 두면 해시·서명 확인 없이 복원 대상이 된다. 서버가 백업 서명을 확인하므로 위조는 안 되지만, **같은 플랫폼의 예전 정상 백업**을 넣으면 구성이 과거로 되돌아간다(재생 replay). 기본 끔으로 노출은 줄었다. 최소 수정: `"config-backup.json" in ok_files` 로 조건 변경.

```python path=app/services/mcp_modern.py lines=57-81
def listen_acquire(sub: str) -> str | None:
    """구독 1개 확보 → 보유자 id (한도 초과면 None). 워커 전체 합은 보유자 집합(prod 는 Valkey) — 만료(최대 구독 시간 + 여유)는
    넣을 때 정해져, 보유 워커가 반납 없이 죽어도 그 주체가 계속 구독하는 동안에도 정해진 시간 뒤 풀린다 (D20-④)."""
    import uuid

    from app.services import ratelimit, scheduler
    holder = f"{scheduler.WORKER}:{uuid.uuid4().hex[:12]}"
    return holder if ratelimit.backend().holder_acquire(_listen_key(sub), holder, MAX_LISTENS, int(LISTEN_MAX_S) + 60) else None


def listen_release(sub: str, holder: str | None) -> None:
    """반납은 취소 중에도 — await 하지 않는다(45th). Valkey 면 스레드에 넘기고 기다리지 않아 끊길 때마다 이벤트 루프가
    왕복 시간(최대 0.5초)만큼 멈추지 않게, 메모리 백엔드는 즉시 (D22-⑥)."""
    from app.services import ratelimit
    if not holder:
        return
    b = ratelimit.backend()
    if isinstance(b, ratelimit.RedisBackend):
        try:
            asyncio.get_running_loop().run_in_executor(None, b.holder_release, _listen_key(sub), holder)
            return
        except RuntimeError:  # 루프 밖(스레드) — 그대로
            pass
    b.holder_release(_listen_key(sub), holder)

```
**분석 / Analysis (Level 2)**
- L57-64 `listen_acquire`: 보유자 슬롯(고정 만료 = 최대 구독 시간 + 60초, D20-④). 호출처는 스레드풀(`app/api/mcp.py:233`)이라 Valkey 왕복이 루프를 막지 않는다.
- L67-80 `listen_release`(D22-⑥): Valkey 백엔드면 `run_in_executor` 로 **넘기고 기다리지 않는다**(L76) — `finally` 안에서 `await` 하면 끊긴 연결의 취소 범위가 그 `await` 를 다시 취소해 반납이 빠지므로(45th 결정) 동기 호출을 유지하면서, 그 동기 왕복(최대 0.5초 소켓 타임아웃)이 이벤트 루프를 멈추던 문제(3차 L22)를 없앴다. 메모리 백엔드는 즉시(L80).
- L78-79: 루프가 없거나 실행기가 이미 닫혀(종료 중) `RuntimeError` 면 동기로 — 반납이 빠지지 않는다.
- 흔한 실수: `run_in_executor` 가 돌려준 future 를 버리면 그 안의 예외가 "Future exception was never retrieved" 로만 남는다. 여기서는 `holder_release` 가 연결 오류를 `_guard` 로 삼키므로(`app/services/ratelimit.py:178-188`, `:233-234`) 예외가 나오지 않는다. 다만 Valkey 장애 5초 창 동안의 반납은 **워커 메모리**로 가서(실제 보유자는 Valkey 에 있음) 효과가 없고, 그 슬롯은 고정 만료까지 남는다 — 기존 설계 그대로, 등급 없음.

```python path=app/api/mcp.py lines=233-241
    holder = await run_in_threadpool(mcp_modern.listen_acquire, p.sub)  # 워커 전체 합 (D18-② · 보유자 단위 D20-④)
    if holder is None:
        raise RpcError(gw.RATE_LIMITED, f"동시 구독 한도({mcp_modern.MAX_LISTENS}) 초과", {"limit": mcp_modern.MAX_LISTENS},
                       http_status=429)
    try:
        return await _listen_open(server_id, p, rid, params, hdr, request, honored, uris, task_ids, holder)
    except BaseException:  # 스트림을 열기 전에 실패하면 확보한 구독을 돌려준다(열린 뒤에는 listen_events 가 돌려준다)
        mcp_modern.listen_release(p.sub, holder)  # 동기 — 취소 중에도 반납
        raise
```
**분석 / Analysis (Level 2)**
- 열기 전에 실패하면 `except BaseException` 에서 반납(취소 포함) — 열린 뒤에는 `listen_events` 의 `finally`(`app/services/mcp_modern.py:514-517`)가 반납. 두 경로 모두 같은 비차단 함수를 쓴다.

## 파일 평가 / File Assessment
- 강점: (1) 권한을 "새로 만들 때" 와 "이미 있을 때" 둘 다 처리하고 파일은 `fchmod`(`app/cli/nexus.py:50-58`). (2) 운영 데이터를 덮는 복원을 명시 동의로 바꾸고, 3차 판정 오류를 문서로 정정(`:733-734`, `test/impl/66th/result.md`). (3) 취소에 안전하면서 루프를 막지 않는 반납(`app/services/mcp_modern.py:74-80`).
- 위험: (1) [위험:낮음] L17 — 매니페스트 밖 `config-backup.json` 복원(`app/cli/nexus.py:805`).
- 개선 권고(영향순): (1) 복원 대상은 `ok_files` 안에서만. (2) 디렉터리도 `os.open(O_DIRECTORY)` + `fchmod` 로 통일(선택).
- 파일 위험 등급 / Risk grade: **낮음** — [위험:낮음]만.
- 재읽기 권고 레벨: 3 — 바뀐 곳은 블록 단위로 충분. 번들 반입 전체(`:731-814`)는 공급망 경계라 처음 맡으면 2 로.

## 3차 리뷰 대비 / vs 3rd Review
| 3차 지적 | 4차 상태 | 근거(현재 path:line) |
|---|---|---|
| H3 번들 안 `cosign.pub`(59th 수정) | 유지 | `app/cli/nexus.py:759-764` |
| L30 경로 chmod·디렉터리 0755 | 해결 | `:50-58` |
| L33 CLI 가 `RESTORE` 대신 입력·복원 기본 켜짐 | 해결(기본 끔) · 매니페스트 밖 파일(L17) | `:733-734`, `:805` |
| L22 동기 Valkey 반납 | 해결 | `app/services/mcp_modern.py:74-80` |
