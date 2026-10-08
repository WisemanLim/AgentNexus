> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: [round2 nexus.py.md](../../../../round2/CODE-REVIEW-Lv2/app/cli/nexus.py.md)

### 12. app/cli/nexus.py
**파일 요약 / File Summary**
- 역할 / Role: 운영 CLI(`nexus`) — 로그인·컨텍스트, 설치(compose), 레지스트리 게시, 감사 검증, 에어갭 번들 내보내기·반입, 그리고 12차에 추가된 키 회전 명령. 이번 범위: D20-③ ① 서명 검증한 이미지로 설치(`--build` 생략, L347) ② 번들 반입 서명 검증 기본(L721-723, L741-751). D21-③ 키 명령(L578-600). D21-⑤ 토큰·시크릿 파일 0600 생성(L54-59).
- 진입점 거리 / Distance: 0 — 콘솔 스크립트 `nexus`(`Dockerfile:8`, `app.cli`), 컨테이너 안 `nexus install code --local`.
- LOC: 804 (2차 스냅샷 768 → +36)
- 주요 export(범위 안): `_save`·`_write_private`, `write_env`, `install_compose`, `keys_status`·`keys_rotate`·`keys_reencrypt`, `bundle_import`
- 의존(내부): API(HTTP), `app.services.deploy.PROFILES`, (`--local`) `app.db`·`app.services.setup`
- 외부 패키지: typer, httpx, 외부 명령 `docker`·`docker compose`·`cosign`
- 부수효과 / Side effects: 설정 파일·`.env.prod` 쓰기, `docker compose up`, `docker load`·`tag`·`push`, 구성 복원 API 호출.
- 동시성 / Concurrency: 없음(동기 CLI).
- 테스트 커버리지 흔적: `test/test_deploy_hardening.py:62-90`(C54-04 번들 반입 서명 기본), `:93-100`(C54-05 서명 검증 설치 = `up -d`), `test/test_key_rotation.py:93-97`(C58-05 키 명령), `test/test_remaining_items.py:80-91`(C60-06 0600).
- 3차 변경 요약: 위 네 가지.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/cli/nexus.py lines=45-59
def _save(cfg: dict) -> None:
    if cfg.get("current"):  # 현재 컨텍스트에도 반영 (SCR-A11 context use)
        cfg.setdefault("contexts", {})[cfg["current"]] = {k: cfg.get(k) for k in ("url", "access_token", "refresh_token",
                                                                                   "expires_at") if cfg.get(k)}
    d = _config_dir()
    d.mkdir(parents=True, exist_ok=True)
    _write_private(d / "config.json", json.dumps(cfg, indent=2))


def _write_private(path: Path, text: str) -> None:
    """토큰·시크릿 파일 — 처음부터 0600 으로 만든다(쓰고 나서 권한을 바꾸면 그 사이 읽힐 수 있다, D21-⑤)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)  # 이미 있던 파일의 권한도
    with os.fdopen(fd, "w") as f:
        f.write(text)
```
**분석 / Analysis (Level 2)**
- L54-59 `_write_private`(D21-⑤): 토큰·시크릿 파일을 **처음부터** 0600 으로 연다(`os.open(..., 0o600)`). 쓰고 나서 `chmod` 하면 그 사이 다른 사용자가 읽을 수 있다(2차 보고서 L96, C60-06). L57 은 이미 있던 파일(예전에 0644 로 만든 것)의 권한도 바로잡는다 — `O_CREAT` 의 모드는 **새로 만들 때만** 적용되기 때문.
- `O_TRUNC` 로 먼저 비우므로 L57 의 `chmod` 전에도 옛 내용이 노출되지 않는다.
- L49-50: 설정 디렉터리는 여전히 기본 권한(보통 0755)으로 만든다 — 파일이 0600 이라 내용은 보호되지만, 2차 보고서 L96 이 함께 지적한 부분(파일 이름·존재 노출)은 남았다. `mkdir(mode=0o700)`.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L30** L57 은 경로 기반 `os.chmod` 다. 연 파일 디스크립터에 대한 `os.fchmod(fd, 0o600)` 가 경로 바꿔치기(심볼릭 링크 교체) 경쟁이 없어 정확하다. 사용자 홈 아래 파일이라 실해는 작다.

```python path=app/cli/nexus.py lines=296-302
            n += 1
        else:
            lines.append(line)
    if gen.get("DB_PASSWORD"):  # 접속 문자열 안의 CHANGE_ME 도 같은 비밀번호로 — 다르면 api 가 DB 에 붙지 못한다
        lines = [ln.replace(":CHANGE_ME@", f":{gen['DB_PASSWORD']}@") if ln.startswith("DATABASE_URL=") else ln for ln in lines]
    _write_private(target, "\n".join(lines) + "\n")
    return n
```
**분석 / Analysis (Level 2)**
- `write_env` 가 `.env.prod`(DB 비밀번호·암호화 키 포함)를 같은 함수로 0600 생성 — D21-⑤ 의 실제 적용 지점.

```python path=app/cli/nexus.py lines=305-357
@install_app.command("compose")
def install_compose(profile: str | None = None, env_file: Path = typer.Option(Path(".env.prod"), "--env-file"),
                    project: str = "wise-agentnexus-prod", wait: int = 180,
                    cosign_key: Path | None = typer.Option(None, help="이미지 서명 검증 공개키 (cosign)"),
                    allow_unsigned: bool = typer.Option(False, help="서명 검증 없이 진행 (PoC 전용)"),
                    dry_run: bool = False):
    """Docker Compose 설치 (SCR-A02) — install.yaml → .env·시크릿 생성 → 서명 검증 → 기동 → 헬스 대기 → 설정 코드."""
    sel = _install_yaml()
    profile = profile or sel.get("profile") or "full"
    airgap = bool(sel.get("airgap"))
    files = ["-f", "docker-compose.yml", "-f", "docker-compose.prod.yml"] + sum((["-f", str(f)] for f in sorted(
        Path("compose").glob("*.yml"))), [])
    cprof = ",".join(x for x in ("demo" if profile in ("full", "mcp-servers-only") else "", "ollama" if airgap else "") if x)
    dc = ["docker", "compose", "-p", project, *files, "--env-file", str(env_file)]
    plan = [f".env: {'유지' if env_file.exists() else f'{env_file} 생성 (.env.example 의 CHANGE_ME → 난수, 0600)'}",
            f"서명 검증: {'cosign verify --key ' + str(cosign_key) if cosign_key else ('생략(--allow-unsigned)' if allow_unsigned else '키 없음 → 중단')}",
            f"COMPOSE_PROFILES={cprof} {' '.join(dc)} up -d{'' if cosign_key else ' --build'}", f"헬스 대기 {wait}s → GET /health",
            "설정 코드: docker compose exec api nexus install code --local"]
    if dry_run:
        typer.echo("\n".join(plan))
        return
    if not cosign_key and not allow_unsigned:
        typer.secho("이미지 서명 검증 키가 없습니다 — --cosign-key 로 검증하거나 PoC 면 --allow-unsigned (MSG-020).", fg="red", err=True)
        raise typer.Exit(1)
    if not env_file.exists():
        n = write_env(Path(".env.example"), env_file)
        typer.echo(f"✔ {env_file} 생성 · 시크릿 {n}개 (값은 표시하지 않음, 0600)")
    if "CHANGE_ME" in env_file.read_text():
        typer.secho(f"{env_file} 에 CHANGE_ME 가 남아 있습니다 — 값을 채우거나 파일을 지우고 다시 실행하세요.", fg="red", err=True)
        raise typer.Exit(1)
    env = {**os.environ, "COMPOSE_PROFILES": cprof}
    if profile != "full":
        from app.services.deploy import PROFILES as DP
        env["AGENTNEXUS_COMPONENTS"] = ",".join(DP[profile][0])
    if cosign_key:
        imgs = subprocess.run([*dc, "config", "--images"], capture_output=True, text=True, env=env, check=False).stdout.split()
        bad = [i for i in imgs if subprocess.run(["cosign", "verify", "--key", str(cosign_key), i], capture_output=True,
                                                 check=False).returncode != 0]
        if bad:
            typer.secho(f"서명 검증 실패 {len(bad)}/{len(imgs)}: {', '.join(bad)} — 기동하지 않습니다 (MSG-020).", fg="red", err=True)
            raise typer.Exit(1)
        typer.echo(f"✔ 서명 검증 {len(imgs)}/{len(imgs)}")
    subprocess.run([*dc, "up", "-d", *([] if cosign_key else ["--build"])], env=env, check=True)  # 검증한 이미지를 그대로 기동 (D20-③)
    port = int(next((ln.split("=", 1)[1] for ln in env_file.read_text().splitlines() if ln.startswith("API_PORT=")), "8800"))
    t0 = time.time()
    while time.time() - t0 < wait and not _healthy(port):
        time.sleep(3)
    if not _healthy(port):
        subprocess.run([*dc, "logs", "--tail", "20", "api"], env=env, check=False)
        typer.secho(f"헬스 실패 ({wait}s) — 위 로그 20줄 확인 후 실패 서비스만 재실행하세요.", fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(f"✔ healthy ({time.time() - t0:.0f}s) · 콘솔: http://127.0.0.1:{port}/setup")
    subprocess.run([*dc, "exec", "-T", "api", "nexus", "install", "code", "--local"], env=env, check=False)
```
**분석 / Analysis (Level 2)**
- L326-328: 서명 키도 `--allow-unsigned` 도 없으면 중단(기본이 안전).
- L339-346: `--cosign-key` 면 `compose config --images` 로 이미지 목록을 뽑아 각각 `cosign verify`.
- L347(D20-③): 서명을 검증했으면 `up -d`(빌드 없이), 미검증만 `--build`. 2차 M55 "검증 뒤 `up -d --build` 로 미검증 이미지 기동" 을 고치려는 변경.
**짚어볼 점 / Watch-outs**
- [위험:중간] **M11(INDEX §5) — 검증한 것과 실행하는 것이 같다는 보장이 없다.** ① 검증은 **태그**로, 실행은 **로컬 이미지**로 한다. compose 의 앱 이미지 이름은 `wise-agentnexus-api:${APP_ENV:-local}` 이고 `build:` 절이 함께 있다(`docker-compose.yml:4-6`, 아래 인용). `cosign verify <이름:태그>` 는 레지스트리에서 그 태그가 가리키는 다이제스트를 검증하지만, L347 `up -d` 는 기본 pull 정책(`missing`)이라 **같은 태그의 로컬 이미지가 있으면 그것을 쓰고**(예: 이전에 `make prod-build` 로 만든 미서명 이미지), 없으면 `build:` 절로 **로컬 소스에서 빌드**한다(`--build` 를 빼도 이미지가 없으면 compose 는 빌드한다). 어느 경우든 서명 검증 대상이 아닌 이미지가 뜬다. ② 이 이름은 레지스트리 접두가 없는 Docker Hub 공식 이미지 형식이라 실제 서명된 이미지를 그 이름으로 둘 수 없다 — 즉 `--cosign-key` 경로가 사실상 쓸 수 없거나 엉뚱한 대상을 검증한다. ③ L340 `config --images` 가 실패하면(`check=False`) 목록이 비고 L346 이 "서명 검증 0/0" 으로 통과한다. 시험은 명령 문자열만 본다(`test/test_deploy_hardening.py:93-100`). 최소 수정: 검증 결과에서 다이제스트를 뽑아 오버라이드 파일(`image: <레지스트리>/<이름>@sha256:…`, `build:` 제거)을 만들어 `up -d --pull always --no-build` 로 기동, 이미지 목록이 비거나 명령이 실패하면 중단, 앱 이미지 이름을 `${IMAGE_REPO}/wise-agentnexus-api` 로 설정 가능하게.

```text path=docker-compose.yml lines=4-6
x-app: &app
  build: { context: ., dockerfile: Dockerfile, target: runtime }
  image: wise-agentnexus-api:${APP_ENV:-local}
```
**분석 / Analysis (Level 2)**
- (M11 근거) 모든 앱 서비스가 이 앵커를 공유한다 — 빌드 정의와 로컬 태그가 한 몸이다.

```python path=app/cli/nexus.py lines=578-600
keys_app = typer.Typer(help="서명·암호화 키 회전 (D21-③ — 관리자 수동)", no_args_is_help=True)
app.add_typer(keys_app, name="keys")


@keys_app.command("status")
def keys_status():
    """현재 서명 kid·JWKS 에 남은 kid·암호화 키 수·env 주입 여부."""
    _out(_req("GET", "/api/v1/admin/keys"), True)


@keys_app.command("rotate")
def keys_rotate(kind: str = typer.Option(..., "--kind", help="signing | encryption"),
                yes: bool = typer.Option(False, "--yes", help="확인 없이 실행")):
    """키 회전 — signing: 새 kid(직전 키는 토큰 수명 동안 유지) · encryption: 새 키 + 저장 암호문 재암호화."""
    if not yes and not typer.confirm(f"{kind} 키를 회전합니다. 계속할까요?"):
        raise typer.Exit(1)
    _out(_req("POST", "/api/v1/admin/keys/rotate", json={"kind": kind, "confirm": "ROTATE"}), True)


@keys_app.command("reencrypt")
def keys_reencrypt(drop_previous: bool = typer.Option(False, "--drop-previous", help="실패 0 이면 파일의 이전 암호화 키 삭제")):
    """저장 암호문을 현재 키로 재암호화 (env 모드 회전 뒤에 실행)."""
    _out(_req("POST", "/api/v1/admin/keys/reencrypt", params={"drop_previous": str(drop_previous).lower()}), True)
```
**분석 / Analysis (Level 2)**
- D21-③ 키 명령: 상태(`GET /api/v1/admin/keys`), 회전(확인 프롬프트 → `confirm: "ROTATE"` 를 CLI 가 채워 보냄), 재암호화(`--drop-previous`). 권한·확인 검증은 서버가 한다([admin.py.md](../api/admin.py.md)).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L594 CLI 가 확인 문구를 대신 채우므로 서버의 "ROTATE 입력" 은 콘솔 실수 방지 장치일 뿐 CLI 에는 의미가 없다 — `--yes` 와 함께면 스크립트에서 묻지 않고 회전된다. 의도된 동작이라면 문서에, 아니면 `--confirm ROTATE` 를 사용자가 입력하게. 그리고 `keys reencrypt --drop-previous` 의 도움말이 M4(웹훅 토큰 재암호화 누락)를 모르는 채 "실패 0 이면 안전" 이라고 안내한다(L598).

```python path=app/cli/nexus.py lines=718-753
@bundle.command("import")
def bundle_import(path: Path = typer.Argument(Path("agentnexus-airgap")), restore_config: bool = True,
                  registry_host: str | None = typer.Option(None, "--registry", help="사설 레지스트리로 적재 (docker tag·push)"),
                  verify: bool = typer.Option(True, "--verify/--allow-unsigned",
                                              help="cosign 서명 검증(기본) — 번들의 cosign.pub 로 manifest.json.sig(파일 해시 전체)·*.tar.sig. "
                                                   "PoC 만 --allow-unsigned (D20-③)"),
                  apply_airgap: bool = typer.Option(False, "--apply-airgap", help="적재 후 에어갭 프로파일 적용"),
                  dry_run: bool = False):
    """번들 반입(SCR-A04) — SHA-256·서명 검증 → 불일치는 격리·제외 → docker load → 사설 레지스트리 적재 → 구성 복원 → 에어갭."""
    manifest = json.loads((path / "manifest.json").read_text())
    unsafe = [n for n in manifest["files"] if not (path / n).resolve().is_relative_to(path.resolve())]
    if unsafe:  # 매니페스트 이름이 번들 밖을 가리키면 격리 이동이 번들 밖 파일을 옮기게 된다
        typer.secho(f"번들 밖 경로가 매니페스트에 있어 중단합니다: {unsafe}", fg="red", err=True)
        raise typer.Exit(1)
    bad = [n for n, h in manifest["files"].items() if not (path / n).exists() or _sha256(path / n) != h]
    lic = {n: v for n, v in (manifest.get("licenses") or {}).items() if any(x in str(v).lower() for x in SEPARATE_LICENSES)}
    if lic:
        typer.secho("라이선스 분리 배포 대상 — 기본 설치에서 제외 (MSG-023): " + ", ".join(f"{n}({v})" for n, v in lic.items()),
                    fg="yellow")
    def _blob_ok(pub: Path, f: Path) -> bool:
        sig = f.with_name(f.name + ".sig")
        return sig.exists() and subprocess.run(["cosign", "verify-blob", "--key", str(pub), "--signature", str(sig), str(f)],
                                               capture_output=True, check=False).returncode == 0
    if verify and not dry_run:
        pub = path / "cosign.pub"
        if not pub.exists() or shutil.which("cosign") is None:
            typer.secho("서명 검증 불가 — 번들에 cosign.pub 가 없거나 cosign 미설치 (MSG-020). PoC 면 --allow-unsigned.", fg="red", err=True)
            raise typer.Exit(1)
        if not _blob_ok(pub, path / "manifest.json"):  # 매니페스트가 파일 해시 전체를 묶는다 — 서명이 없으면 해시도 믿을 수 없다
            typer.secho("매니페스트 서명(manifest.json.sig) 검증 실패 — 파일 해시 목록을 믿을 수 없어 중단 (MSG-020).", fg="red", err=True)
            raise typer.Exit(1)
        for n in [x for x in manifest["files"] if x.endswith(".tar") and x not in bad and (path / f"{x}.sig").exists()]:
            if not _blob_ok(pub, path / n):
                bad.append(n)
    elif not verify:
        typer.secho("서명 검증 생략(--allow-unsigned) — 체크섬만 확인합니다. PoC·시험 전용.", fg="yellow")
```
**분석 / Analysis (Level 2)**
- L721-723(D20-③): 서명 검증이 기본(`--verify`), 생략은 `--allow-unsigned`(경고).
- L728-731: 매니페스트의 파일 이름이 번들 밖을 가리키면 중단(경로 탈출 방지).
- L732: 체크섬 불일치·누락 목록.
- L741-751: cosign 이 있고 번들에 `cosign.pub` 가 있어야 하며, `manifest.json.sig` 로 매니페스트(파일 해시 전체를 묶는 목록)를 검증, `.tar.sig` 가 있으면 그 tar 도.
**짚어볼 점 / Watch-outs**
- [위험:높음] **H3(INDEX §5) — 신뢰의 뿌리(공개키)가 검증 대상 번들 안에 있다.** L742 `pub = path / "cosign.pub"` — 검증 키를 **번들에서** 꺼낸다. 번들을 바꿔치기할 수 있는 공격자(에어갭 반입 경로: USB·파일 서버·전송 중)라면 자기 키쌍을 만들어 `cosign.pub` 를 바꾸고 `manifest.json` 을 자기 키로 서명(`manifest.json.sig`)하면 L746 검증이 **통과**한다. 그러면 L776-785 가 공격자 이미지를 `docker load` → 사설 레지스트리로 `tag`·`push`(운영 클러스터가 그 이미지를 받아 실행), L786-790 이 번들 안 구성 백업을 기본값(`restore_config=True`, L719)으로 복원 요청한다(백업 서명은 서버가 플랫폼 키로 따로 검증하므로 이 부분은 막힌다 — 단 [keys.py.md](../core/keys.py.md) L121-122 의 폐기 불가 보관 키 참고). D20-③ 이 막으려던 2차 M56 "번들 바꿔치기 이미지가 사설 레지스트리로 push" 가 **기본값 검증을 켠 상태에서도** 그대로 가능하다 — 이 검증은 우발적 손상만 잡는다. 시험도 번들 안 `cosign.pub` 를 전제로 한다(`test/test_deploy_hardening.py:84`). 최소 수정: 검증 키는 번들 밖에서만 — `--cosign-key <운영자 보관 경로>` 필수, 또는 `install.yaml`·환경 변수(`COSIGN_PUBLIC_KEY` — 이미 ConfigMap 에 있음, `deploy/helm/agentnexus/templates/configmap.yaml:41`)의 키 지문과 `cosign.pub` 지문이 같을 때만 사용. 번들 안 키는 무시하거나 지문 비교용으로만.

```python path=app/cli/nexus.py lines=754-795
    if bad:
        typer.secho(f"체크섬 불일치·누락·서명 실패 {len(bad)}건 → 격리, 적재 제외: {bad}", fg="red", err=True)
        if not dry_run:
            q = path / "quarantine"
            q.mkdir(exist_ok=True)
            for n in bad:
                if (path / n).exists():
                    shutil.move(str(path / n), str(q / n))
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
- L754-761: 불일치·서명 실패 파일은 `quarantine/` 으로 격리하고 적재에서 제외.
- L774-785: 부분 실패여도 나머지는 적재하고 종료 코드로 알린다(ST-05). `--registry` 면 `Loaded image:` 줄을 파싱해 사설 레지스트리로 다시 태그·push.
- L786-790: 구성 백업 복원 — `--restore-config` 가 기본 True 라 "명시 동의" 주석과 달리 **옵션을 주지 않아도** 복원한다(L719, L789 주석).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L749 `.tar.sig` 가 **없는** tar 는 개별 서명 검증을 건너뛴다. 매니페스트 서명이 해시를 묶으므로 H3 를 고친 뒤에는 문제없지만, 지금은 H3 와 겹쳐 개별 서명 기능도 의미가 없다.
- [위험:낮음] L719·L789: 기본값 복원은 운영 데이터를 덮어쓰는 작업이다(복원 API 자체가 `RESTORE` 입력을 요구하는 이유 — `app/api/admin.py:297-299`). CLI 가 그 확인을 대신 채워 보내므로 반입 한 번이 곧 복원이 된다. 기본값을 False 로 바꾸거나 대화형 확인.

## 파일 평가 / File Assessment
- 강점: (1) 0600 생성으로 토큰·`.env.prod` 노출 구간을 없앴다(`app/cli/nexus.py:54-59`, `:301`). (2) 번들 반입이 경로 탈출 차단·격리·부분 실패 보고 등 방어적 흐름을 갖췄다(`app/cli/nexus.py:728-731`, `:754-761`, `:774-775`). (3) 서명 없는 설치·반입을 기본으로 막고 명시 우회만 허용(`app/cli/nexus.py:326-328`, `:721-723`).
- 위험: (1) [위험:높음] 번들 서명 검증의 공개키가 번들 안 — 바꿔치기 번들이 기본 검증을 통과(`app/cli/nexus.py:742-748`). (2) [위험:중간] 설치가 태그 검증 뒤 로컬 이미지·로컬 빌드로 기동할 수 있음(`app/cli/nexus.py:339-347` + `docker-compose.yml:4-6`). (3) [위험:낮음] 복원 기본값·CLI 의 확인 문구 대리 입력(`app/cli/nexus.py:719`, `:789`, `:594`).
- 개선 권고(영향순): (1) 검증 키는 번들 밖(운영자 지정·지문 고정). (2) 다이제스트 고정 오버라이드 + `--pull always --no-build`. (3) 위험 동작(복원·회전)의 확인은 사용자가 입력.
- 파일 위험 등급 / Risk grade: 높음 — 공급망 경계에 [위험:높음] 1건 + [위험:중간] 1건.
- 재읽기 권고 레벨: 2 — 외부 명령 호출의 입력·출력이 무엇을 신뢰하는지(태그·다이제스트·키 위치)를 줄 단위로 확인해야 한다.

## 2차 리뷰 대비 / vs 2nd Review
| 2차 지적 (2차 INDEX §5) | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| M55 cosign 검증 뒤 `up -d --build` 로 미검증 이미지 기동, `config --images` 실패 시 0/0 통과 (`nexus.py:342`·`:335`) | 부분 해결 | `--build` 제거 `app/cli/nexus.py:347`. 태그 검증 vs 로컬 이미지·로컬 빌드, 0/0 통과 남음(M11) |
| M56 `bundle import --verify` 기본 False — 바꿔치기 번들 이미지 push (`nexus.py:693`) | 형식상 해결 · 실질 미해결 | 기본 검증 `:721-723`, 그러나 공개키가 번들 안 `:742`(H3) |
| (2차 nexus.py.md L96) 파일을 쓰고 나서 chmod(write-then-chmod), 디렉터리 0755 | 해결(파일) · 미해결(디렉터리) | `:54-59` · 시험 `test/test_remaining_items.py:80-91` |
