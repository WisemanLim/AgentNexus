> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [cli_Abstract.md](cli_Abstract.md)

### 04. app/cli/nexus.py
**파일 요약 / File Summary**
- 역할 / Role: `nexus` CLI(typer). 로그인(RFC 8628 디바이스 코드·PAT), 설치 사전 점검·compose 설치, 카탈로그·레지스트리·MCP·에이전트·KB·채팅(SSE)·승인·사용량·컨텍스트·감사 조회, 에어갭 번들 export/import.
- 진입점 거리 / Distance: 0 (`nexus` → `python -m app.cli` → `main()`)
- LOC: 752 — `max_lines_per_file`(600) 초과 → **논리 블록 단위로 분석**(모든 블록 커버, 원문 전체 인용)
- 주요 export: `app`(typer.Typer), `main`, `write_env`, `HTTP`(시험 주입점), 하위 앱 `catalog`·`registry`·`mcp`·`agents`·`approvals`·`bundle`·`kb`·`install_app`·`context`·`audit_app`
- 의존(내부): `app.db.SessionLocal`·`app.services.setup`(설정 코드 `--local`), `app.services.deploy.PROFILES` — 모두 함수 안 지연 import
- 외부 패키지: typer, httpx, (외부 명령) docker·docker compose·cosign·helm·kubectl·sysctl
- 부수효과 / Side effects: `~/.nexus/config.json`(토큰 저장, 0600), 현재 디렉터리에 `nexus-precheck.json`·`install.yaml`·`.env.prod` 생성, 하위 프로세스 실행, 원격 API 호출, 파일 이동(격리)
- 동시성 / Concurrency: none(동기 httpx·subprocess)
- 테스트 커버리지 흔적: `test/test_cli.py`, `test/test_cli_install.py`(install·context·audit·bundle)

## 블록 리뷰 / Block Walk (Level 2)

```python path=app/cli/nexus.py lines=1-33
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
```
**분석 / Analysis (Level 2)**
- L1-7: 사용법 요약 docstring — `nexus --help` 의 근거 문서 역할.
- L8-19: 표준 라이브러리(해시·JSON·subprocess·socket)와 typer·httpx.
- L21-31: 루트 앱 + 하위 명령 그룹 7개를 `add_typer` 로 연결. `no_args_is_help=True` 는 인자 없이 실행하면 도움말을 보여 준다. `add_completion=False` 로 셸 자동완성 설치 명령을 숨긴다.
- L33 `HTTP = None`: 시험에서 `httpx.Client` 호환 객체(TestClient)를 주입하는 seam(`test/test_cli.py:20`).
**짚어볼 점 / Watch-outs**
- 모듈 전역 `HTTP` 는 시험 간 공유 상태다 — `monkeypatch.setattr` 로만 바꿔야 원복된다(현재 시험은 그렇게 한다).

```python path=app/cli/nexus.py lines=36-61
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
    d.mkdir(parents=True, exist_ok=True)
    p = d / "config.json"
    p.write_text(json.dumps(cfg, indent=2))
    p.chmod(0o600)


def _url() -> str:
    return os.environ.get("NEXUS_URL") or _load().get("url") or "http://127.0.0.1:8800"


def _client() -> httpx.Client:
    return HTTP if HTTP is not None else httpx.Client(base_url=_url(), timeout=60)
```
**분석 / Analysis (Level 2)**
- L36-37: 설정 디렉터리 — `NEXUS_CONFIG_DIR` 우선, 없으면 `~/.nexus`.
- L40-42: 설정 읽기. 파일이 없으면 빈 dict.
- L45-48: 현재 컨텍스트(`current`)가 있으면 최상위 `url`·토큰 값을 `contexts[current]` 에 복사해 동기화한다(SCR-A11).
- L49-53: 디렉터리 생성 → JSON 쓰기 → 권한 0600.
- L56-57: 서버 주소 우선순위: 환경변수 `NEXUS_URL` > 설정 파일 > 기본값 `http://127.0.0.1:8800`.
- L60-61: 주입된 `HTTP` 가 없으면 매 호출마다 새 `httpx.Client` 생성(타임아웃 60초).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L52-53 경쟁 구간: `write_text` 로 파일을 umask 기본 권한(보통 0644)으로 만든 뒤 `chmod(0o600)` 한다. 그 사이 같은 호스트의 다른 사용자가 액세스·리프레시 토큰을 읽을 수 있다. `os.open(p, O_WRONLY|O_CREAT|O_TRUNC, 0o600)` 로 처음부터 0600 으로 열어야 한다(`app/core/keys.py:29` 가 올바른 예).
- [위험:낮음] L40-42 손상된 JSON 이면 `json.JSONDecodeError` 가 그대로 터져 모든 명령이 실패한다 — 사용자 안내 메시지가 없다.
- [위험:낮음] L60-61 자원 누수: `_client()` 가 만든 `httpx.Client` 는 `_req`(L73) 에서 `with` 없이 쓰이고 닫히지 않는다. CLI 는 단명 프로세스라 실제 피해는 작지만, 연결 풀·소켓이 GC 에 맡겨진다.
- L45-48 은 `context add` 버그의 원인이 된다(아래 L565-570 참조).

```python path=app/cli/nexus.py lines=64-86
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
```
**분석 / Analysis (Level 2)**
- L64-69: 인증 헤더 — `NEXUS_TOKEN` 환경변수 > 저장된 access_token. 없으면 종료 코드 2(사용 오류 관례).
- L72-82: 공통 요청 함수 — 4xx/5xx 면 problem+json 의 `detail` 또는 OpenAI 형식 `error.message` 를 꺼내 표시하고 종료 코드 1. `res.json() if res.content else {}` 로 빈 본문(204)도 처리.
- L85-86: `--json` 이면 들여쓰기 JSON, 아니면 사람이 읽는 문자열. `default=str` 로 datetime 등 직렬화 실패를 피한다.
**짚어볼 점 / Watch-outs**
- L73 `headers={**_auth(), **kw.pop(...)}` — 호출자가 Authorization 을 넘기면 덮어쓴다(의도된 우선순위).
- 액세스 토큰 만료 시 refresh_token 으로 갱신하는 로직이 없다 — `login` 은 refresh_token 을 저장하지만(L113) 어디서도 쓰지 않는다(죽은 데이터). 만료 후엔 401 → 재로그인 필요.

```python path=app/cli/nexus.py lines=89-141
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
```
**분석 / Analysis (Level 2)**
- L90-93: `login` 옵션 — `--device/--no-device` 불리언 쌍, `--token` 으로 PAT 저장, `--url` 로 서버 지정.
- L96-98: `--url` 을 설정에 저장하고 같은 프로세스의 `_client()` 가 새 주소를 쓰도록 환경변수도 설정(부수효과).
- L99-103: PAT 직접 저장 경로 — 서버 검증 없이 저장만 한다.
- L104-118: RFC 8628 디바이스 흐름 — `/idp/device/code` 로 코드 발급 → 사용자에게 `user_code`·검증 URL 출력 → `interval` 마다 `/idp/token` 폴링 → 성공 시 토큰 저장 후 `/api/v1/me` 로 사용자 확인.
- L119-123: `slow_down` 이면 간격 +5초(RFC 8628 §3.5 준수), `authorization_pending` 외 오류는 실패.
- L128-134: 로그아웃 — 로컬 토큰만 삭제. L137-141: `whoami`.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L105 응답 상태 확인 없이 `.json()` 후 `dc['user_code']` — 서버 오류(4xx/5xx·HTML) 시 `KeyError`/`JSONDecodeError` 트레이스백이 사용자에게 노출된다.
- L99-103 PAT 를 저장할 때 형식(`anx_pat_` 접두)·유효성 확인이 없다 — 잘못된 토큰은 다음 명령에서야 401 로 드러난다.
- L128-134 서버 측 refresh token 폐기(`/idp/revoke`)를 호출하지 않는다 — 웹 콘솔(`web/src/lib/auth.tsx:114`)과 달리 CLI 로그아웃 후에도 refresh token 이 서버에서 유효하다.

```python path=app/cli/nexus.py lines=144-212
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
```
**분석 / Analysis (Level 2)**
- L145-148: 포트 점유 확인 — `connect_ex` 가 0 이면 누군가 듣고 있음. `with socket.socket()` 으로 소켓을 확실히 닫는다.
- L151-154: `install` 서브앱, 배포 프로파일 5종, 외부 접근 점검 대상 3곳.
- L157-161: HEAD 요청으로 외부 도달성 판정(상태 코드 600 미만이면 응답이 왔다는 뜻).
- L164-172: 메모리 — Linux 는 `/proc/meminfo`, macOS 는 `sysctl -n hw.memsize`. 인자 리스트 형태 `subprocess.run([...])` 이라 셸 인젝션 위험이 없다.
- L175-212: A01 사전 점검 — 자원·디스크·docker·compose v2·env 파일·포트·OTLP 포트·helm/kubectl·번들 디렉터리·DNS·시간·외부 접근을 `items` 로 모은다. 레벨: ok/fail/warn, `fail` 이 하나라도 있으면 설치를 거부한다.
**짚어볼 점 / Watch-outs**
- L195 `_healthy(port)` 는 L367 에서 정의된다 — 함수 호출 시점에 이름이 해석되므로 문제없지만 읽는 사람은 아래로 내려가야 한다.
- L208 `time` 항목은 항상 ok — 실제 NTP 오차를 재지 않는다(주석이 인정). 결과 보고서에서 "통과"로 오해할 수 있다.
- L209 외부 3곳을 순차 HEAD(각 2.5초 타임아웃) — 에어갭 환경에선 점검이 최대 ~7.5초 걸린다.

```python path=app/cli/nexus.py lines=215-259
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
```
**분석 / Analysis (Level 2)**
- L215-224: `@install_app.callback()` + `invoke_without_command=True` — `nexus install` 만 입력하면 점검이 돌고, `nexus install compose` 처럼 하위 명령이 있으면 L226-227 에서 바로 반환한다.
- L218: 기본 포트를 **함수 정의 시점**의 `API_PORT` 환경변수로 계산한다(typer 옵션 기본값은 import 시 1회 평가).
- L231-233: 외부 접근이 막혀 있으면 `airgap=True` 로 자동 권장.
- L234-237: `--interactive` 이면 프롬프트로 선택.
- L243-245: 보고서·`install.yaml` 저장. L249-257: 기호(✔✘⚠)로 결과 출력.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L245 `install.yaml` 을 f-string 으로 직접 만든다 — `mode` 는 검증하지 않으므로 대화형 입력에 개행·콜론이 들어가면 YAML 구조가 깨진다(`profile` 은 L238 에서 검증). 로컬 사용자 입력이라 보안 영향은 낮다.
- L231 `next(...)` 에 기본값이 없지만 `external` 항목은 `_precheck` 가 항상 추가하므로(L210) StopIteration 은 나지 않는다.

```python path=app/cli/nexus.py lines=262-292
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
    n, lines = 0, []
    for line in example.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#") and line.split("=", 1)[1].strip() == "CHANGE_ME":
            k = line.split("=", 1)[0]
            if k == "ENCRYPTION_KEY":
                import base64
                val = base64.urlsafe_b64encode(pysecrets.token_bytes(32)).decode()
            else:
                val = pysecrets.token_urlsafe(32)
            lines.append(f"{k}={val}")
            n += 1
        else:
            lines.append(line)
    target.write_text("\n".join(lines) + "\n")
    target.chmod(0o600)
    return n
```
**분석 / Analysis (Level 2)**
- L262-271: `install.yaml` 을 YAML 파서 없이 `key: value` 줄 단위로 읽는다 — pyyaml 의존을 피하려는 최소 파서. `true/false` 만 불리언 변환.
- L274-292: `.env.example` → 운영 env 생성. `CHANGE_ME` 값만 `secrets.token_urlsafe(32)`(ENCRYPTION_KEY 는 Fernet 형식 32바이트 urlsafe base64)로 채우고 값은 출력하지 않는다(MSG-018). 0600 권한.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L290-291 도 `write_text` 후 `chmod` — 생성 직후 잠깐 umask 권한으로 열려 DB 비밀번호·서명 키가 노출될 수 있다(L52-53 과 같은 문제).
- [위험:높음 · 기능 버그] L279-286 은 `ENCRYPTION_KEY` 만 특수 처리하고 나머지 `CHANGE_ME` 는 전부 `token_urlsafe(32)` 로 채운다. 실제 `.env.example` 에는 `SIGNING_KEY_PEM=CHANGE_ME` 가 있으므로, 생성된 `.env.prod` 의 `SIGNING_KEY_PEM` 은 PEM 이 아닌 난수 문자열이 된다. 앱의 `keys._configured()`(`app/core/keys.py:43-46`)는 이 값을 "설정됨"으로 보고 `serialization.load_pem_private_key`(`app/core/keys.py:51-52`)에서 `ValueError` 를 던지며, entrypoint 의 `python -m app.seed`(`app/seed.py:371` `keys.ensure_keys()`)가 `set -e` 로 실패해 api 컨테이너가 기동하지 못한다. 시험(`test/test_cli_install.py:52`)은 `SIGNING_KEY_PEM` 이 없는 축약 예시만 써서 이 경로를 잡지 못한다. `SIGNING_KEY_PEM` 은 비워 두고(앱이 data_dir/keys 에 생성) 또는 RSA PEM 을 생성해 넣어야 한다.
- `BOOTSTRAP_ADMIN_PASSWORD` 도 난수로 채워지고 값은 출력되지 않는다(MSG-018) — 운영자는 0600 파일을 직접 열어야 첫 관리자 비밀번호를 안다. 안내 메시지에 이 사실을 넣는 것이 좋다.

```python path=app/cli/nexus.py lines=295-347
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
            f"COMPOSE_PROFILES={cprof} {' '.join(dc)} up -d --build", f"헬스 대기 {wait}s → GET /health",
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
    subprocess.run([*dc, "up", "-d", "--build"], env=env, check=True)
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
- L302-308: 설치 선택(`install.yaml`)·compose 파일 목록(`compose/*.yml` 전부 포함)·COMPOSE_PROFILES 계산·기본 `docker compose` 명령 배열.
- L309-315: `--dry-run` 이면 실행 계획만 출력(값 미노출).
- L316-318: 서명 검증 키가 없으면 기본 거부 — `--allow-unsigned` 로만 우회(MSG-020). 공급망 보안 기본값이 안전 쪽이다.
- L319-324: env 생성 후 `CHANGE_ME` 잔존 시 중단.
- L326-328: 프로파일에 맞는 `AGENTNEXUS_COMPONENTS` 주입(`app/services/deploy.py:21-27`).
- L329-336: `docker compose config --images` 로 이미지 목록 → 각 이미지 `cosign verify`.
- L337-347: `up -d --build` → 헬스 대기 → 실패 시 로그 20줄 → 성공 시 컨테이너 안에서 설정 코드 발급.
**짚어볼 점 / Watch-outs**
- [위험:중간] L337 `up -d --build` 는 **로컬에서 이미지를 다시 빌드**한다 — L329-336 의 cosign 검증은 레지스트리 이미지 서명을 확인하지만, 빌드로 생긴 로컬 이미지는 서명 대상이 아니므로 검증과 실제 기동 이미지가 다를 수 있다(검증 우회 가능성). 서명 검증을 쓰는 경로에서는 `--build` 를 빼고 digest 고정(`image@sha256:`)으로 기동해야 일관된다.
- L330 `subprocess.run(...).stdout` — 실패(returncode≠0) 여부를 보지 않는다. compose 설정 오류 시 이미지 목록이 비어 "서명 검증 0/0" 으로 통과한다.
- L338 `API_PORT=` 값이 숫자가 아니면 `ValueError` 트레이스백.

```python path=app/cli/nexus.py lines=350-371
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
```
**분석 / Analysis (Level 2)**
- L350-364: 일회용 설정 코드 재발급 — `--local` 이면 API 를 거치지 않고 DB 에 직접(부트스트랩: 아직 로그인 계정이 없을 때). `with SessionLocal() as db` 로 세션을 닫는다.
- L364 `exp[:19]` — ISO 문자열 앞 19자(초 단위)만 출력.
- L367-371: `/health` 200 이면 healthy.
**짚어볼 점 / Watch-outs**
- L360 `S.new_code` 는 `(code, exp_iso_str)` 를 반환하므로(`app/services/setup.py:78-84`) L364 `exp[:19]` 는 두 경로 모두 안전하다.
- `--local` 은 컨테이너 안(DB 접근 가능)에서만 의미가 있다 — 호스트에서 실행하면 기본 `DATABASE_URL`(`sqlite:////app/data/app.db`, `app/core/config.py:19`)로 엉뚱한 DB 를 열 수 있다.

```python path=app/cli/nexus.py lines=374-445
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
```
**분석 / Analysis (Level 2)**
- L375-383: 카탈로그 목록 — 비어 있지 않은 필터만 쿼리로.
- L386-390: 즉시 사용 등록(FR-W04) — 서버가 도구 조회·기본 규칙까지 수행.
- L393-400: `server.json` 게시 — typer `exists=True, readable=True` 가 파일 존재·권한을 미리 검사.
- L404-423: Custom MCP 서버 템플릿 생성 — 서버에서 `{상대경로: 내용}` 을 받아 디렉터리에 쓴다.
- L426-445: MCP 서버 목록·Inspector(게이트웨이 경로로 tools/list 또는 tools/call).
**짚어볼 점 / Watch-outs**
- [위험:중간] L419-420 신뢰하지 않은 입력 → 파일 쓰기 싱크: 서버 응답의 키 `rel` 을 검증 없이 `target / rel` 에 쓴다. `rel` 이 `../../.bashrc` 이거나 절대 경로(`/etc/x` — `Path / "/abs"` 는 절대 경로로 대체됨)이면 대상 디렉터리 밖에 쓴다. `NEXUS_URL` 은 사용자가 바꿀 수 있는 값이라(L57) 악성·탈취된 서버가 로컬 파일을 덮어쓸 수 있다. `(target / rel).resolve().is_relative_to(target.resolve())` 검사와 `parent.mkdir(parents=True)` 가 필요하다(현재는 하위 디렉터리가 있는 키면 `FileNotFoundError`).
- L441 `json.loads(args)` 오류 시 트레이스백 — 사용자 메시지로 감싸면 좋다.

```python path=app/cli/nexus.py lines=448-529
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
```
**분석 / Analysis (Level 2)**
- L449-462: 에이전트 프로파일 목록·시험 실행.
- L465-500: 지식 베이스 목록·생성·문서 추가·검색. L484-489: PDF·docx·xlsx 는 채팅 첨부 API 로 올려 서버가 텍스트를 추출, 그 외는 로컬에서 `read_text()` 로 읽어 본문 전송.
- L503-529: 채팅 — 세션 생성 → 메시지 POST 를 **스트리밍**으로 받으며 SSE `event:`/`data:` 줄을 직접 파싱해 이벤트별 출력.
**짚어볼 점 / Watch-outs**
- L485-486 `with path.open("rb")` 로 파일을 닫는다(좋음).
- L489 `read_text()` 는 기본 인코딩(로캘) 의존 — Windows·비UTF-8 로캘에서 한글 문서가 깨질 수 있다. `encoding="utf-8"` 명시 권장.
- [위험:낮음] L508 스트림 응답의 상태 코드를 확인하지 않는다 — 4xx 이면 SSE 가 아니라 problem+json 이 와서 아무것도 출력되지 않고 정상 종료(종료 코드 0)한다.
- L508 `_client().stream(...)` 의 클라이언트 타임아웃 60초(L61)는 청크 간 읽기 타임아웃으로 적용된다 — 사람 승인 대기처럼 이벤트 간격이 60초를 넘는 실행은 `ReadTimeout` 으로 끊길 수 있다(서버 하트비트 주기에 의존, 이 리뷰 범위에서 미확인).
- SSE 사양상 `data:` 가 여러 줄일 수 있으나 L514 는 한 줄만 처리한다.

```python path=app/cli/nexus.py lines=532-556
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
```
**분석 / Analysis (Level 2)**
- L533-538: 승인 대기 목록. L542-545: 승인/거부(`--reject`) 한 명령으로 처리. L549-556: 사용량·비용 집계 출력.
**짚어볼 점 / Watch-outs**
- L555 `${r['cost_usd']:.6f}` — f-string 안 `$` 는 리터럴 문자(셸 변수 아님). 통화 표기용.

```python path=app/cli/nexus.py lines=559-602
context = typer.Typer(help="연결 컨텍스트 — 환경(dev·prod) 전환 (SCR-A11)", no_args_is_help=True)
app.add_typer(context, name="context")
audit_app = typer.Typer(help="감사 로그 조회", no_args_is_help=True)
app.add_typer(audit_app, name="audit")


@context.command("add")
def context_add(name: str, url: str):
    cfg = _load()
    cfg.setdefault("contexts", {})[name] = {**cfg.get("contexts", {}).get(name, {}), "url": url.rstrip("/")}
    _save({**cfg, "current": cfg.get("current")} if cfg.get("current") != name else cfg)
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
```
**분석 / Analysis (Level 2)**
- L559-562: `context`·`audit` 서브앱.
- L565-570: 컨텍스트 추가 — `contexts[name].url` 갱신 후 저장.
- L573-586: 컨텍스트 전환 — 현재 최상위 값을 이전 컨텍스트에 보관 → 최상위 키 제거 → 새 컨텍스트 값으로 교체.
- L589-593: 목록(`*` = 현재).
- L596-602: `1h·30m·2d` 를 UTC ISO 시각으로 변환, 형식이 다르면 그대로(ISO 로 간주).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 기능 버그] L569 의 조건식은 두 분기가 사실상 같은 `cfg` 를 넘긴다. 그런데 `name` 이 **현재 컨텍스트**이면 `_save`(L46-48)가 `contexts[current]` 를 최상위의 **이전** `url` 로 다시 덮어써서, `nexus context add <현재이름> <새URL>` 의 새 URL 이 저장되지 않는다. 최상위 `cfg["url"]` 도 함께 갱신하거나 `_save` 의 동기화를 `context_use` 에서만 하도록 바꿔야 한다.

```python path=app/cli/nexus.py lines=605-623
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
```
**분석 / Analysis (Level 2)**
- L606-614: 감사 이벤트 조회 — 빈 값 필터 제거. `limit=100` 은 정수라 항상 포함.
- L617-623: 해시 체인 검증 결과 출력, 손상이면 종료 코드 1(스크립트·CI 연계용).
**짚어볼 점 / Watch-outs**
- L609 `if v` 필터는 `limit=0` 을 제거한다 — 의도된 동작이면 무방.

```python path=app/cli/nexus.py lines=626-672
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
```
**분석 / Analysis (Level 2)**
- L627: 기본 번들 이미지 3개.
- L630-635: 1 MiB 청크로 SHA-256 — 대용량 tar 도 메모리에 다 올리지 않는다(좋은 습관).
- L638-672: 오프라인 번들 — `docker save` → 동봉 파일 복사 → 서명 구성 백업(`include_secrets: False`) → 파일별 SHA-256 매니페스트.
**짚어볼 점 / Watch-outs**
- L657 이미지 이름의 `/`·`:` 만 치환 — `@sha256:` digest 표기 이미지는 `@` 가 파일명에 남지만 동작에는 문제없다.
- 매니페스트 자체는 서명되지 않는다 — 번들 위변조 방지는 `bundle import --verify`(tar 별 cosign 서명)에 의존하고, 매니페스트·config-backup 은 서명 대상이 아니다(config-backup 은 서버 측 서명 검증이 있음, L736).

```python path=app/cli/nexus.py lines=675-743
SEPARATE_LICENSES = ("agpl", "elv2", "elastic", "sspl", "busl", "ee/")


@bundle.command("import")
def bundle_import(path: Path = typer.Argument(Path("agentnexus-airgap")), restore_config: bool = True,
                  registry_host: str | None = typer.Option(None, "--registry", help="사설 레지스트리로 적재 (docker tag·push)"),
                  verify: bool = typer.Option(False, "--verify", help="cosign 서명 검증 (번들의 cosign.pub·*.sig)"),
                  apply_airgap: bool = typer.Option(False, "--apply-airgap", help="적재 후 에어갭 프로파일 적용"),
                  dry_run: bool = False):
    """번들 반입(SCR-A04) — SHA-256·서명 검증 → 불일치는 격리·제외 → docker load → 사설 레지스트리 적재 → 구성 복원 → 에어갭."""
    manifest = json.loads((path / "manifest.json").read_text())
    bad = [n for n, h in manifest["files"].items() if not (path / n).exists() or _sha256(path / n) != h]
    lic = {n: v for n, v in (manifest.get("licenses") or {}).items() if any(x in str(v).lower() for x in SEPARATE_LICENSES)}
    if lic:
        typer.secho("라이선스 분리 배포 대상 — 기본 설치에서 제외 (MSG-023): " + ", ".join(f"{n}({v})" for n, v in lic.items()),
                    fg="yellow")
    if verify and not dry_run:
        pub = path / "cosign.pub"
        if not pub.exists() or shutil.which("cosign") is None:
            typer.secho("서명 검증 불가 — 번들에 cosign.pub 가 없거나 cosign 미설치 (MSG-020).", fg="red", err=True)
            raise typer.Exit(1)
        for n in [x for x in manifest["files"] if x.endswith(".tar") and x not in bad]:
            sig = path / f"{n}.sig"
            ok = sig.exists() and subprocess.run(["cosign", "verify-blob", "--key", str(pub), "--signature", str(sig),
                                                  str(path / n)], capture_output=True, check=False).returncode == 0
            if not ok:
                bad.append(n)
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
- L675: 분리 배포가 필요한 라이선스 키워드(AGPL·ELv2·SSPL·BUSL 등).
- L685-687: 매니페스트 로드 → 체크섬 불일치·누락 목록 `bad` → 라이선스 분리 대상 `lic`.
- L691-701: `--verify` 이면 tar 별 `cosign verify-blob`.
- L702-709: 실패 파일을 `quarantine/` 로 이동(격리).
- L710-721: 적재 대상 계산, `--dry-run` 은 계획만.
- L724-733: `docker load` → (선택) 사설 레지스트리로 `tag`·`push`.
- L734-743: 구성 복원(명시 동의 `confirm: RESTORE`) → 에어갭 프로파일 적용 → 부분 실패면 종료 코드 1.
**짚어볼 점 / Watch-outs**
- [위험:중간] L686·L696·L707-709 신뢰하지 않은 입력 → 파일 시스템 싱크: 매니페스트의 파일 이름 `n` 을 그대로 `path / n` 으로 쓴다. 악성 번들이 `"../../../home/u/.ssh/id_rsa": "0"` 같은 항목을 넣으면 L686 이 해시 불일치로 `bad` 에 넣고, L709 `shutil.move` 가 번들 밖의 파일을 `quarantine/` 로 **옮겨 버린다**(원래 위치에서 사라짐). `n` 이 단순 파일명인지(`Path(n).name == n`) 검증해야 한다.
- L725 `docker load` 는 `check=True` — 손상 tar 하나로 나머지 적재가 중단된다(부분 실패 정책 L722 과 불일치).
- L730 `img.split('/')[-1]` 로 레지스트리 호스트·네임스페이스를 버린다 — 서로 다른 네임스페이스의 동명 이미지가 충돌한다.
- L713 `extra` 는 `lic`(라이선스 제외) 파일도 포함해 "동봉 파일" 로 안내한다.

```python path=app/cli/nexus.py lines=746-752
def main() -> None:
    app(prog_name="nexus")


if __name__ == "__main__":  # pragma: no cover
    main()
    sys.exit(0)
```
**분석 / Analysis (Level 2)**
- L746-747: 진입 함수. L750-752: 직접 실행 시. `# pragma: no cover` 로 커버리지 제외.
**짚어볼 점 / Watch-outs**
- L752 `sys.exit(0)` 은 `app()` 이 이미 `SystemExit` 를 던지므로 도달하지 않는 코드다(typer 기본 `standalone_mode`).

## 파일 평가 / File Assessment
- 강점: (1) 설치 경로의 안전 기본값 — 서명 키 없으면 기본 거부, 시크릿 값 미출력, 0600. (2) 외부 명령을 인자 리스트로 실행해 셸 인젝션이 없다. (3) RFC 8628 폴링 규칙(slow_down)을 지킨다.
- 위험: (1) [중간] 서버 응답·번들 매니페스트의 파일 이름을 경로 검증 없이 사용(L419-420, L686-709). (2) [중간] cosign 검증 후 `--build` 로 다른 이미지가 기동될 수 있다(L337). (3) [낮음] 토큰·시크릿 파일 생성 시 chmod 경쟁, `context add` 기능 버그, refresh token 미사용.
- 개선 권고(영향순): (1) 경로 정규화·`is_relative_to` 검사 추가. (2) 서명 검증 경로에서 `--build` 제거·digest 고정. (3) `os.open(..., 0o600)` 로 원자적 권한 생성, `context add` 동기화 수정, 만료 시 refresh 사용.
- 재읽기 권고 레벨: 2 — 하위 프로세스·파일 시스템 경계가 많아 줄 단위로 다시 확인할 가치가 있다.
