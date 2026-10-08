"""nexus — AgentNexus CLI (FR-P03·S03, SCR-A01·A04·A11·E04·F05).

설치·로그인: `nexus install --check` · `nexus login --device` (RFC 8628) · `nexus login --token anx_pat_…`
사용: `nexus chat <profile> "<요청>"` · `nexus catalog list` · `nexus registry publish server.json`
· `nexus mcp scaffold <name>` · `nexus approvals approve <id>` · `nexus bundle export|import` (에어갭)
설정: ~/.nexus/config.json (NEXUS_CONFIG_DIR) · 서버 주소 NEXUS_URL (기본 http://127.0.0.1:8800)
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import typer

app = typer.Typer(help="AgentNexus CLI — MCP + AI Gateway + A2A 플랫폼", no_args_is_help=True, add_completion=False)
catalog = typer.Typer(help="통합 카탈로그", no_args_is_help=True)
registry = typer.Typer(help="MCP 레지스트리 게시", no_args_is_help=True)
mcp = typer.Typer(help="MCP 서버", no_args_is_help=True)
agents = typer.Typer(help="에이전트 프로파일", no_args_is_help=True)
approvals = typer.Typer(help="사람 승인", no_args_is_help=True)
bundle = typer.Typer(help="에어갭 오프라인 번들", no_args_is_help=True)
kb = typer.Typer(help="지식 베이스 (RAG 내장 벡터 색인)", no_args_is_help=True)
for sub, name in ((catalog, "catalog"), (registry, "registry"), (mcp, "mcp"), (agents, "agents"),
                  (approvals, "approvals"), (bundle, "bundle"), (kb, "kb")):
    app.add_typer(sub, name=name)

HTTP = None  # 시험에서 httpx.Client 호환 객체 주입


def _config_dir() -> Path:
    return Path(os.environ.get("NEXUS_CONFIG_DIR", Path.home() / ".nexus"))


def _load() -> dict:
    p = _config_dir() / "config.json"
    return json.loads(p.read_text()) if p.exists() else {}


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


def _url() -> str:
    return os.environ.get("NEXUS_URL") or _load().get("url") or "http://127.0.0.1:8800"


def _client() -> httpx.Client:
    return HTTP if HTTP is not None else httpx.Client(base_url=_url(), timeout=60)


def _auth() -> dict:
    tok = os.environ.get("NEXUS_TOKEN") or _load().get("access_token")
    if not tok:
        typer.secho("로그인이 필요합니다: nexus login --device", fg="red", err=True)
        raise typer.Exit(2)
    return {"Authorization": f"Bearer {tok}"}


def _req(method: str, path: str, **kw) -> dict:
    res = _client().request(method, path, headers={**_auth(), **kw.pop("headers", {})}, **kw)
    if res.status_code >= 400:
        try:
            body = res.json()
            msg = body.get("detail") or body.get("error", {}).get("message") or body
        except ValueError:
            msg = res.text
        typer.secho(f"오류 {res.status_code}: {msg}", fg="red", err=True)
        raise typer.Exit(1)
    return res.json() if res.content else {}


def _out(data, as_json: bool) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str) if as_json else data)


# ───────────────────────── 로그인 ─────────────────────────
@app.command()
def login(device: bool = typer.Option(True, "--device/--no-device", help="Device Authorization Grant(브라우저 확인)"),
          token: str | None = typer.Option(None, help="PAT(anx_pat_…) 직접 저장"),
          url: str | None = typer.Option(None, help="AgentNexus 주소")):
    """로그인 (SCR-A11) — 브라우저에서 디바이스 코드를 확인하면 토큰을 저장한다."""
    cfg = _load()
    if url:
        cfg["url"] = url.rstrip("/")
        os.environ["NEXUS_URL"] = cfg["url"]
    if token:
        cfg["access_token"] = token
        _save(cfg)
        typer.echo("토큰을 저장했습니다.")
        return
    c = _client()
    dc = c.post("/idp/device/code", data={"client_id": "nexus-cli", "scope": "openid profile"}).json()
    typer.echo(f"브라우저에서 코드 입력: {dc['user_code']}\n  {dc['verification_uri_complete']}")
    interval, deadline = dc.get("interval", 5), time.time() + dc.get("expires_in", 600)
    while time.time() < deadline:
        time.sleep(interval)
        res = c.post("/idp/token", data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                         "device_code": dc["device_code"], "client_id": "nexus-cli"}).json()
        if "access_token" in res:
            cfg.update(access_token=res["access_token"], refresh_token=res.get("refresh_token"),
                       expires_at=int(time.time()) + res.get("expires_in", 3600))
            _save(cfg)
            me = c.get("/api/v1/me", headers={"Authorization": f"Bearer {res['access_token']}"}).json()
            typer.secho(f"로그인 완료: {me.get('name')} ({', '.join(me.get('roles', []))})", fg="green")
            return
        if res.get("error") == "slow_down":
            interval += 5
        elif res.get("error") not in ("authorization_pending",):
            typer.secho(f"로그인 실패: {res.get('error_description') or res.get('error')}", fg="red", err=True)
            raise typer.Exit(1)
    typer.secho("코드가 만료되었습니다.", fg="red", err=True)
    raise typer.Exit(1)


@app.command()
def logout():
    cfg = _load()
    cfg.pop("access_token", None)
    cfg.pop("refresh_token", None)
    _save(cfg)
    typer.echo("로그아웃했습니다.")


@app.command()
def whoami(as_json: bool = typer.Option(False, "--json")):
    me = _req("GET", "/api/v1/me")
    _out(me if as_json else f"{me['name']} <{me.get('email')}> roles={','.join(me['roles'])} team={me.get('team')}",
         as_json)


# ───────────────────────── 설치 점검 (SCR-A01) ─────────────────────────
def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


install_app = typer.Typer(help="설치 — 사전 점검(A01)·compose 설치(A02)·설정 코드", invoke_without_command=True)
app.add_typer(install_app, name="install")
PROFILES = ("full", "gateway-only", "registry-only", "mcp-servers-only", "a2a-only")
EXTERNAL = ("https://ghcr.io", "https://registry-1.docker.io", "https://registry.modelcontextprotocol.io")


def _reachable(url: str) -> bool:
    try:
        return httpx.head(url, timeout=2.5, follow_redirects=False).status_code < 600
    except httpx.HTTPError:
        return False


def _mem_gib() -> float | None:
    try:
        if Path("/proc/meminfo").exists():
            kb = int(next(x for x in Path("/proc/meminfo").read_text().splitlines() if x.startswith("MemTotal")).split()[1])
            return round(kb / 1024 / 1024, 1)
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3, check=False)
        return round(int(out.stdout.strip()) / 1024 ** 3, 1) if out.returncode == 0 and out.stdout.strip().isdigit() else None
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
        return None


def _precheck(mode: str, port: int, domain: str | None) -> list[dict]:
    """A01 사전 점검 — level: ok | fail | warn. fail 이 하나라도 있으면 설치 명령을 거부한다."""
    def run(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    items: list[dict] = []

    def add(key, ok, detail="", warn=False, fix=None):
        items.append({"key": key, "level": "ok" if ok else ("warn" if warn else "fail"), "detail": detail,
                      **({"fix": fix} if fix and not ok else {})})
    cpu, mem = os.cpu_count() or 0, _mem_gib()
    add("resources", cpu >= 4 and (mem is None or mem >= 8), f"{cpu} vCPU · {mem if mem is not None else '?'} GiB", warn=True,
        fix="권장: 4 vCPU · 8 GiB 이상")
    free = shutil.disk_usage(".").free / 1024 ** 3
    add("disk", free >= 20, f"여유 {free:.0f} GiB", warn=True, fix="권장: 20 GiB 이상 (이미지·DB·모델)")
    add("docker", shutil.which("docker") is not None and run(["docker", "info"]), fix="Docker Engine 설치·기동")
    add("compose_v2", run(["docker", "compose", "version"]), fix="Docker Compose v2 플러그인")
    add("env_files", all(Path(f).exists() for f in (".env.local", ".env.prod")), fix="nexus install compose 가 .env.prod 를 만든다")
    add(f"port_{port}", _port_free(port) or _healthy(port), f"API_PORT={port}", fix="다른 포트(API_PORT) 지정 또는 점유 해제")
    add("port_4317", _port_free(4317), "OTLP gRPC", warn=True, fix="기존 OTel Collector 가 쓰는 중 — 연동(SCR-A08) 권장")
    if mode == "helm":
        add("helm", shutil.which("helm") is not None, fix="helm 3+ 설치")
        add("kubectl", shutil.which("kubectl") is not None, fix="kubectl 설치·컨텍스트 설정")
    if mode == "airgap":
        add("bundle_dir", Path("agentnexus-airgap").exists(), fix="nexus bundle import ./agentnexus-airgap")
    if domain:
        try:
            socket.getaddrinfo(domain, 443)
            add("dns", True, domain)
        except OSError:
            add("dns", False, domain, fix="DNS 레코드 등록 또는 /etc/hosts")
    add("time", True, time.strftime("%Y-%m-%d %H:%M:%S %Z"), warn=True)  # NTP 오차는 호스트 chrony·timedatectl 로 확인
    blocked = [u for u in EXTERNAL if not _reachable(u)]
    add("external", not blocked, "외부 레지스트리 접근" + (f" 차단: {', '.join(blocked)}" if blocked else " 가능"), warn=True,
        fix="에어갭 번들 설치 권장 (nexus bundle import, SCR-A04) — install.yaml airgap: true")
    return items


@install_app.callback()
def install(ctx: typer.Context, check: bool = typer.Option(True, "--check/--no-check"),
            mode: str = typer.Option("compose", help="compose|helm|airgap"),
            port: int = typer.Option(int(os.environ.get("API_PORT", "8800")), help="게시 포트"),
            domain: str | None = typer.Option(None, help="콘솔 도메인 (DNS 점검)"),
            profile: str = typer.Option("full", help="|".join(PROFILES)), airgap: bool = False,
            interactive: bool = typer.Option(False, "--interactive", help="배포 방식·에어갭·프로파일을 묻고 install.yaml 저장"),
            save: bool = typer.Option(False, "--save", help="선택을 install.yaml 로 저장"),
            report: Path = typer.Option(Path("nexus-precheck.json"), help="점검 보고서(JSON)"),
            as_json: bool = typer.Option(False, "--json")):
    """설치 사전 점검(SCR-A01) — 자원·런타임·포트·DNS·시간·외부 접근 → 보고서 · install.yaml (실패 잔존 시 설치 거부)."""
    if ctx.invoked_subcommand:
        return
    items = _precheck(mode, port, domain)
    checks = {i["key"]: i["level"] != "fail" for i in items}
    ok = all(checks.values())
    ext = next(i for i in items if i["key"] == "external")
    if ext["level"] != "ok" and not airgap:
        airgap = True  # 외부 접근 차단 감지 → 에어갭 자동 권장 (A01 ③)
    if interactive:
        mode = typer.prompt("배포 방식 (compose|helm)", default=mode)
        airgap = typer.confirm("에어갭(외부 SaaS 없이) 설치", default=airgap)
        profile = typer.prompt(f"기동 프로파일 ({'|'.join(PROFILES)})", default=profile)
    if profile not in PROFILES:
        typer.secho(f"알 수 없는 프로파일: {profile}", fg="red", err=True)
        raise typer.Exit(2)
    result = {"mode": mode, "ok": ok, "checks": checks, "items": items, "airgap_recommended": ext["level"] != "ok",
              "selection": {"mode": mode, "airgap": airgap, "profile": profile}}
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    if interactive or save:
        Path("install.yaml").write_text(f"mode: {mode}\nairgap: {str(airgap).lower()}\nprofile: {profile}\n")
    if as_json:
        _out(result, True)
    else:
        sym = {"ok": "✔", "fail": "✘", "warn": "⚠"}
        for i in items:
            typer.echo(f"  {sym[i['level']]} {i['key']:<12} {i['detail']}" + (f"  → {i['fix']}" if i.get("fix") else ""))
        n = {lv: sum(1 for i in items if i["level"] == lv) for lv in sym}
        typer.echo(f"결과: 통과 {n['ok']} · 실패 {n['fail']} · 경고 {n['warn']} · 보고서 {report}")
        if interactive or save:
            typer.echo("install.yaml 저장 — 다음: nexus install compose")
        typer.secho("점검 통과 — make local-all 로 기동하세요." if ok else "점검 실패 항목을 해결하세요.",
                    fg="green" if ok else "red")
    if not ok:
        raise typer.Exit(1)


def _install_yaml() -> dict:
    p = Path("install.yaml")
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = {"true": True, "false": False}.get(v.strip(), v.strip())
    return out


def write_env(example: Path, target: Path) -> int:
    """`.env.example` → 운영 env — CHANGE_ME 값을 난수로 채워 0600 으로 쓴다. 값은 출력하지 않는다(MSG-018)."""
    import secrets as pysecrets
    n, lines, gen = 0, [], {}
    for line in example.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#") and line.split("=", 1)[1].strip() == "CHANGE_ME":
            k = line.split("=", 1)[0]
            if k == "ENCRYPTION_KEY":
                import base64
                val = base64.urlsafe_b64encode(pysecrets.token_bytes(32)).decode()
            elif k == "SIGNING_KEY_PEM":
                val = ""  # 난수 문자열은 PEM 이 아니다 — 비우면 data/keys 에 RS256 키를 자동 생성
            else:
                val = pysecrets.token_urlsafe(32)
            gen[k] = val
            lines.append(f"{k}={val}")
            n += 1
        else:
            lines.append(line)
    if gen.get("DB_PASSWORD"):  # 접속 문자열 안의 CHANGE_ME 도 같은 비밀번호로 — 다르면 api 가 DB 에 붙지 못한다
        lines = [ln.replace(":CHANGE_ME@", f":{gen['DB_PASSWORD']}@") if ln.startswith("DATABASE_URL=") else ln for ln in lines]
    _write_private(target, "\n".join(lines) + "\n")
    return n


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
            "서명 검증: " + (f"{' '.join(dc)} pull → 받은 다이제스트(repo@sha256)마다 cosign verify --key {cosign_key}" if cosign_key
                           else ("생략(--allow-unsigned)" if allow_unsigned else "키 없음 → 중단")),
            f"COMPOSE_PROFILES={cprof} {' '.join(dc)} up -d{' --no-build --pull never' if cosign_key else ' --build'}", f"헬스 대기 {wait}s → GET /health",
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
    if cosign_key:  # 레지스트리에서 받은 다이제스트를 검증하고 그 이미지로만 기동 — 같은 태그의 로컬 빌드·이미지로 바뀌지 않게 (59th)
        imgs = subprocess.run([*dc, "config", "--images"], capture_output=True, text=True, env=env, check=False).stdout.split()
        if not imgs or subprocess.run([*dc, "pull"], env=env, check=False).returncode != 0:
            typer.secho("이미지를 레지스트리에서 받지 못했습니다 — 서명 검증은 레지스트리 이미지(repo@sha256)만 가능합니다 (MSG-020).",
                        fg="red", err=True)
            raise typer.Exit(1)

        def _digest(img: str) -> str:
            out = subprocess.run(["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", img],
                                 capture_output=True, text=True, check=False)
            return out.stdout.strip() if out.returncode == 0 and "@sha256:" in out.stdout else ""
        refs = {i: _digest(i) for i in imgs}
        bad = [i for i, ref in refs.items() if not ref or subprocess.run(["cosign", "verify", "--key", str(cosign_key), ref],
                                                                         capture_output=True, check=False).returncode != 0]
        if bad:
            typer.secho(f"서명 검증 실패 {len(bad)}/{len(imgs)}: {', '.join(bad)} — 기동하지 않습니다 (MSG-020).", fg="red", err=True)
            raise typer.Exit(1)
        typer.echo(f"✔ 서명 검증 {len(imgs)}/{len(imgs)} (다이제스트)")
    subprocess.run([*dc, "up", "-d", *(["--no-build", "--pull", "never"] if cosign_key else ["--build"])], env=env,
                   check=True)  # 검증한 이미지를 그대로 기동 (D20-③)
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


@install_app.command("code")
def install_code(local: bool = typer.Option(False, "--local", help="api 컨테이너 안에서 DB 로 직접 (부트스트랩)")):
    """첫 실행 일회용 설정 코드 재발급 (30분) — 코드 없이는 /setup 진입 불가."""
    if local:
        from app.db import SessionLocal
        from app.services import setup as S
        with SessionLocal() as db:
            if S.completed(db):
                typer.echo("첫 실행이 이미 끝났습니다.")
                return
            code, exp = S.new_code(db, "cli")
    else:
        r = _req("POST", "/api/v1/setup/code")
        code, exp = r["code"], r["expires_at"]
    typer.echo(f"일회용 설정 코드: {code}  (만료 {exp[:19]} UTC · 1회 사용)")


def _healthy(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


# ───────────────────────── 카탈로그·레지스트리 ─────────────────────────
@catalog.command("list")
def catalog_list(type_: str | None = typer.Option(None, "--type"), q: str | None = None,
                 as_json: bool = typer.Option(False, "--json")):
    params = {k: v for k, v in (("type", type_), ("q", q)) if v}
    data = _req("GET", "/v1/catalog", params=params)
    if as_json:
        return _out(data, True)
    for i in data["items"]:
        typer.echo(f"{i['id']}  {i['type']:<13} {i['status']:<9} {i['name']}  ({i['version']})")


@catalog.command("register")
def catalog_register(item_id: str, as_json: bool = typer.Option(False, "--json")):
    """즉시 사용 — 승인된 항목을 등록하고 도구·기본 정책을 적용한다 (FR-W04)."""
    data = _req("POST", f"/v1/catalog/{item_id}/register", json={})
    _out(data if as_json else f"등록 완료: {data['instance']['id']} → {data['instance'].get('gateway_path')}", as_json)


@registry.command("publish")
def registry_publish(path: Path = typer.Argument(..., exists=True, readable=True),
                     as_json: bool = typer.Option(False, "--json")):
    """server.json 게시 → 운영자 승인 대기 (SCR-E04)."""
    doc = json.loads(path.read_text())
    data = _req("POST", "/v0/publish", json=doc)
    ver = data["version"]
    _out(data if as_json else f"게시: {data['item']['name']}@{ver['version']} — 상태 {ver['status']}", as_json)


# ───────────────────────── MCP ─────────────────────────
@mcp.command("scaffold")
def mcp_scaffold(name: str, directory: Path = typer.Option(None, "--dir"), title: str | None = None,
                 description: str | None = None, public_url: str | None = None, force: bool = False,
                 include: list[str] = typer.Option(None, "--include", help="docker·register·readme 반복 · none = 선택 항목 없음 (생략 = 전부)")):
    """Custom MCP 서버 템플릿 생성 (FR-S03, SCR-F05)."""
    params = {k: v for k, v in (("name", name), ("title", title), ("description", description),
                                ("public_url", public_url)) if v}
    if include:
        params["include"] = include
    files = _req("GET", "/api/v1/mcp-servers/templates/custom", params=params)["files"]
    target = directory or Path(name)
    if target.exists() and any(target.iterdir()) and not force:
        typer.secho(f"{target} 가 비어 있지 않습니다 (--force).", fg="red", err=True)
        raise typer.Exit(1)
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    unsafe = [rel for rel in files if not (target / rel).resolve().is_relative_to(root)]
    if unsafe:  # 서버 응답의 파일 이름이 대상 밖(../·절대 경로)을 가리키면 쓰지 않는다
        typer.secho(f"대상 디렉터리 밖 경로가 포함돼 중단합니다: {unsafe}", fg="red", err=True)
        raise typer.Exit(1)
    for rel, content in files.items():
        (target / rel).write_text(content)
    typer.echo(f"생성: {target}/ ({', '.join(sorted(files))})\n"
               f"다음: cd {target} && uv sync && uv run uvicorn server:app\n"
               f"게시: nexus registry publish {target}/server.json")


@mcp.command("list")
def mcp_list(as_json: bool = typer.Option(False, "--json")):
    data = _req("GET", "/api/v1/mcp-servers")
    if as_json:
        return _out(data, True)
    for s in data["items"]:
        typer.echo(f"{s['id']:<16} {s['health']:<10} tools={s['tools_count']:<3} {s['gateway_path']}")


@mcp.command("inspect")
def mcp_inspect(server: str, tool: str | None = typer.Argument(None, help="생략 시 tools/list"),
                args: str = typer.Option("{}", help="JSON 인자"),
                approval: str | None = typer.Option(None, help="승인된 approval_id 로 재호출"),
                as_json: bool = typer.Option(True, "--json/--text")):
    """MCP Inspector (SCR-F06) — 게이트웨이 경로로 tools/list 또는 tools/call."""
    body = {"method": "tools/call", "params": {"name": tool, "arguments": json.loads(args)}} if tool else \
        {"method": "tools/list", "params": {}}
    if approval:
        body["approval_id"] = approval
    _out(_req("POST", f"/api/v1/mcp-servers/{server}/inspect", json=body), as_json)


# ───────────────────────── 에이전트·채팅·승인 ─────────────────────────
@agents.command("list")
def agents_list(as_json: bool = typer.Option(False, "--json")):
    data = _req("GET", "/api/v1/agents")
    if as_json:
        return _out(data, True)
    for a in data["items"]:
        tag = "[템플릿] " if a["template"] else ""
        typer.echo(f"{a['id']:<22} v{a['version']:<3} {a['status']:<10} {tag}{a['name']}")


@agents.command("test")
def agents_test(profile: str, request: str, as_json: bool = typer.Option(False, "--json")):
    data = _req("POST", f"/api/v1/agents/{profile}/test", json={"request": request})
    _out(data if as_json else f"[{data['status']}] {data['run'].get('result') or ''}", as_json)


@kb.command("list")
def kb_list(as_json: bool = typer.Option(False, "--json")):
    data = _req("GET", "/api/v1/knowledge-bases")
    if as_json:
        return _out(data, True)
    for k in data["items"]:
        typer.echo(f"{k['id']:<22} 문서 {k['documents']:<4} 청크 {k['chunks']:<5} {k['data_class']:<12} {k['name']}")


@kb.command("create")
def kb_create(kb_id: str, name: str, team: str | None = None, data_class: str = "internal",
              embedding_model: str = "embed-local"):
    _out(_req("POST", "/api/v1/knowledge-bases", json={"id": kb_id, "name": name, "team": team, "data_class": data_class,
                                                      "embedding_model": embedding_model}), True)


@kb.command("add")
def kb_add(kb_id: str, path: Path = typer.Argument(..., exists=True, dir_okay=False), title: str | None = None):
    """문서 추가 — 텍스트 파일은 바로, PDF·docx·xlsx 는 첨부로 올려 텍스트를 추출한 뒤 색인한다."""
    if path.suffix.lower() in (".pdf", ".docx", ".xlsx"):
        with path.open("rb") as f:
            att = _req("POST", "/api/v1/chat/attachments", files={"file": (path.name, f)})
        body = {"title": title or path.stem, "attachment_id": att["id"]}
    else:
        body = {"title": title or path.stem, "text": path.read_text(), "source": f"file:{path.name}"}
    d = _req("POST", f"/api/v1/knowledge-bases/{kb_id}/documents", json=body)
    typer.echo(f"색인: {d['title']} — 청크 {d['chunks']}개")


@kb.command("search")
def kb_search(kb_id: str, query: str, top_k: int = 5, as_json: bool = typer.Option(False, "--json")):
    data = _req("POST", f"/api/v1/knowledge-bases/{kb_id}/search", json={"query": query, "top_k": top_k})
    if as_json:
        return _out(data, True)
    for h in data["items"]:
        typer.echo(f"{h['score']:.3f}  {h['title']}  — {h['text'][:80]}")


@app.command()
def chat(profile: str, message: str, data_class: str | None = typer.Option(None, "--data-class")):
    """자연어 요청 — 실행 이벤트를 스트리밍 출력 (FR-A01)."""
    sid = _req("POST", "/api/v1/sessions", json={"profile_id": profile})["id"]
    body = {"content": message, **({"data_class": data_class} if data_class else {})}
    with _client().stream("POST", f"/api/v1/sessions/{sid}/messages", json=body,
                          headers={**_auth(), "Accept": "text/event-stream"}) as res:
        event = None
        for line in res.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event:
                data = json.loads(line[6:])
                if event == "delta":
                    typer.echo(data.get("text", ""), nl=False)
                elif event == "step.completed":
                    typer.secho(f"\n· {data['step']} 완료 ({data['node']})", fg="blue", err=True)
                elif event == "approval.required":
                    typer.secho(f"\n⏸ 승인 필요: {data.get('tool')} → nexus approvals approve {data['approval_id']}",
                                fg="yellow")
                elif event == "run.completed":
                    typer.echo(f"\n\n[{data['status']}] 인용 {len(data.get('citations') or [])}건")
                elif event == "run.waiting":
                    typer.echo(f"\n[waiting_approval] 승인 후 재개됩니다: {', '.join(data.get('approvals', []))}")
                elif event == "run.failed":
                    typer.secho(f"\n[failed] {data.get('error')}", fg="red", err=True)
                    raise typer.Exit(1)


@approvals.command("list")
def approvals_list(status: str = "pending", as_json: bool = typer.Option(False, "--json")):
    data = _req("GET", "/api/v1/approvals", params={"status": status})
    if as_json:
        return _out(data, True)
    for a in data["items"]:
        typer.echo(f"{a['id']}  {a['target']:<30} risk={a['risk']:<6} requester={a['requester']}")


@approvals.command("approve")
def approvals_approve(approval_id: str, note: str | None = None, reject: bool = False):
    action = "reject" if reject else "approve"
    data = _req("POST", f"/api/v1/approvals/{approval_id}/{action}", json={"note": note})
    typer.echo(f"{approval_id}: {data['status']}")


@app.command()
def usage(group_by: str = "model", as_json: bool = typer.Option(False, "--json")):
    data = _req("GET", "/api/v1/usage", params={"group_by": group_by})
    if as_json:
        return _out(data, True)
    for r in data["items"]:
        tokens = r["tokens_in"] + r["tokens_out"]
        typer.echo(f"{r['key']:<24} calls={r['calls']:<5} tokens={tokens:<8} ${r['cost_usd']:.6f}")
    typer.echo(f"합계 ${data['total_cost_usd']:.6f} ({data['scope']})")


context = typer.Typer(help="연결 컨텍스트 — 환경(dev·prod) 전환 (SCR-A11)", no_args_is_help=True)
app.add_typer(context, name="context")
audit_app = typer.Typer(help="감사 로그 조회", no_args_is_help=True)
app.add_typer(audit_app, name="audit")
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


@context.command("add")
def context_add(name: str, url: str):
    cfg = _load()
    cfg.setdefault("contexts", {})[name] = {**cfg.get("contexts", {}).get(name, {}), "url": url.rstrip("/")}
    if cfg.get("current") == name:  # 현재 컨텍스트면 최상위 값도 — _save 가 최상위 값으로 contexts[current] 를 다시 쓴다
        cfg["url"] = url.rstrip("/")
    _save(cfg)
    typer.echo(f"컨텍스트 추가: {name} → {url} (전환: nexus context use {name})")


@context.command("use")
def context_use(name: str):
    cfg = _load()
    ctxs = cfg.get("contexts", {})
    if name not in ctxs:
        typer.secho(f"없는 컨텍스트: {name} — nexus context add {name} --url …", fg="red", err=True)
        raise typer.Exit(2)
    if cfg.get("current"):
        ctxs[cfg["current"]] = {k: cfg.get(k) for k in ("url", "access_token", "refresh_token", "expires_at") if cfg.get(k)}
    for k in ("url", "access_token", "refresh_token", "expires_at"):
        cfg.pop(k, None)
    cfg.update({**ctxs[name], "current": name, "contexts": ctxs})
    _save(cfg)
    typer.echo(f"현재 컨텍스트: {name} ({cfg.get('url')}){'' if cfg.get('access_token') else ' — 로그인 필요: nexus login --device'}")


@context.command("list")
def context_list():
    cfg = _load()
    for n, c in (cfg.get("contexts") or {}).items():
        typer.echo(f"{'*' if n == cfg.get('current') else ' '} {n:<12} {c.get('url', '')}  {'로그인됨' if c.get('access_token') else '-'}")


def _since(v: str) -> str:
    import datetime as _dt
    unit = {"m": 60, "h": 3600, "d": 86400}
    if v and v[-1] in unit and v[:-1].isdigit():
        t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=int(v[:-1]) * unit[v[-1]])
        return t.isoformat()
    return v


@audit_app.command("list")
def audit_list(since: str = typer.Option("1h", help="1h · 30m · 2d 또는 ISO 시각"), action: str | None = None,
               limit: int = 100, as_json: bool = typer.Option(False, "--json")):
    """감사 이벤트 (SCR-A11 · GET /api/v1/audit/events) — --json 으로 jq 연계."""
    data = _req("GET", "/api/v1/audit/events", params={k: v for k, v in {"since": _since(since), "action": action,
                                                                          "limit": limit}.items() if v})
    if as_json:
        return _out(data, True)
    for e in data.get("items", []):
        typer.echo(f"{str(e.get('ts'))[:19]}  {e.get('action'):<28} {e.get('actor') or '-':<24} {e.get('target') or ''}")


@app.command("audit-verify")
def audit_verify():
    data = _req("GET", "/api/v1/audit/verify")
    typer.secho(f"해시 체인 {'유효' if data['valid'] else '손상'} — 검사 {data['checked']}건",
                fg="green" if data["valid"] else "red")
    if not data["valid"]:
        raise typer.Exit(1)


# ───────────────────────── 에어갭 번들 (SCR-A04) ─────────────────────────
DEFAULT_IMAGES = ["wise-agentnexus-api:prod", "postgres:17-alpine", "valkey/valkey:8-alpine"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@bundle.command("export")
def bundle_export(out: Path = typer.Option(Path("agentnexus-airgap"), "--out"),
                  images: list[str] = typer.Option(None, "--image", help="반복 지정 (기본: api·postgres·valkey)"),
                  with_images: bool = typer.Option(True, "--with-images/--no-images"),
                  with_config: bool = typer.Option(True, "--with-config/--no-config"),
                  include: list[Path] = typer.Option(None, "--include", exists=True, dir_okay=False,
                                                     help="동봉할 파일 (반복) — helm package 결과 tgz·compose 파일 등"),
                  dry_run: bool = False):
    """오프라인 번들 — 컨테이너 이미지(docker save) + 서명 구성 백업 + 동봉 파일 + SHA-256 매니페스트."""
    imgs = images or DEFAULT_IMAGES
    plan = [f"docker save {i}" for i in imgs] if with_images else []
    plan += [f"copy {p}" for p in include or []]
    if dry_run:
        typer.echo("\n".join(plan + (["GET /api/v1/admin/backup"] if with_config else [])) or "(nothing)")
        return
    out.mkdir(parents=True, exist_ok=True)
    files = []
    if with_images:
        for img in imgs:
            tar = out / (img.replace("/", "_").replace(":", "__") + ".tar")
            subprocess.run(["docker", "save", "-o", str(tar), img], check=True)
            files.append(tar)
    for p in include or []:
        dst = out / p.name
        shutil.copy2(p, dst)
        files.append(dst)
    if with_config:
        bk = _req("POST", "/api/v1/admin/backup", json={"include_secrets": False})
        cfg = out / "config-backup.json"
        cfg.write_text(json.dumps(bk, ensure_ascii=False))
        files.append(cfg)
    manifest = {"format": "agentnexus-airgap/1", "created_at": int(time.time()), "images": imgs if with_images else [],
                "files": {f.name: _sha256(f) for f in files}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    typer.echo(f"번들 생성: {out}/ ({len(files)} files)")


SEPARATE_LICENSES = ("agpl", "elv2", "elastic", "sspl", "busl", "ee/")


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
    lic = {n: v for n, v in (manifest.get("licenses") or {}).items() if any(x in str(v).lower() for x in SEPARATE_LICENSES)}
    if lic:
        typer.secho("라이선스 분리 배포 대상 — 기본 설치에서 제외 (MSG-023): " + ", ".join(f"{n}({v})" for n, v in lic.items()),
                    fg="yellow")
    def _blob_ok(pub: Path, f: Path) -> bool:
        sig = f.with_name(f.name + ".sig")
        return sig.exists() and subprocess.run(["cosign", "verify-blob", "--key", str(pub), "--signature", str(sig), str(f)],
                                               capture_output=True, check=False).returncode == 0
    if verify and not dry_run:
        pub = cosign_key
        if pub is None or not pub.exists() or pub.resolve().is_relative_to(path.resolve()) or shutil.which("cosign") is None:
            typer.secho("서명 검증 불가 — 번들 밖에서 받은 공개키를 --cosign-key 로 지정하세요(번들 안의 키는 쓰지 않음) "
                        "또는 cosign 미설치 (MSG-020). PoC 면 --allow-unsigned.", fg="red", err=True)
            raise typer.Exit(1)
        if not _blob_ok(pub, path / "manifest.json"):  # 매니페스트가 파일 해시 전체를 묶는다 — 서명이 없으면 해시도 믿을 수 없다
            typer.secho("매니페스트 서명(manifest.json.sig) 검증 실패 — 파일 해시 목록을 믿을 수 없어 중단 (MSG-020).", fg="red", err=True)
            raise typer.Exit(1)
        for n in [x for x in manifest["files"] if x.endswith(".tar") and x not in bad and (path / f"{x}.sig").exists()]:
            if not _blob_ok(pub, path / n):
                bad.append(n)
    elif not verify:
        typer.secho("서명 검증 생략(--allow-unsigned) — 체크섬만 확인합니다. PoC·시험 전용.", fg="yellow")
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
    if restore_config and "config-backup.json" in ok_files:  # 서명된 매니페스트에 있고 해시가 맞는 백업만 (65th)
        bk = json.loads((path / "config-backup.json").read_text())
        res = _req("POST", "/api/v1/admin/restore", json={"bundle": bk["bundle"], "signature": bk["signature"],
                                                            "confirm": "RESTORE"})  # --restore-config 가 명시 동의
        typer.echo(f"구성 복원: {sum(res['restored'].values())} rows")
    if apply_airgap:
        r = _req("POST", "/api/v1/admin/profiles/apply", json={"profile": "full", "airgap": True, "confirm": True})
        typer.echo(f"에어갭 프로파일 적용 — {r['commands']['compose']}")
    if bad:
        raise typer.Exit(1)


def main() -> None:
    app(prog_name="nexus")


if __name__ == "__main__":  # pragma: no cover
    main()
    sys.exit(0)
