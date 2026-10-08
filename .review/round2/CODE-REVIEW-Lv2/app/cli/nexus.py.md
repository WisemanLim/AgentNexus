> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [cli_Abstract.md](cli_Abstract.md) · 1차 리뷰: [nexus.py.md](../../../../CODE-REVIEW-Lv2/app/cli/nexus.py.md)

### 02. app/cli/nexus.py
**파일 요약 / File Summary**
- 역할 / Role: `nexus` CLI(typer). 로그인(RFC 8628 디바이스 코드·PAT), 설치 사전 점검·compose 설치·설정 코드, 카탈로그·레지스트리·MCP·에이전트·KB·채팅(SSE)·승인·사용량·컨텍스트·감사 조회, 에어갭 번들 export/import.
- 진입점 거리 / Distance: 0 (`/usr/local/bin/nexus` → `python -m app.cli`(`Dockerfile:8`) → `app/cli/__main__.py:1-3` → `main()`)
- LOC: 768 (현재 `wc -l`) · 1차 752 → +16 — `max_lines_per_file`(600) 초과 → **논리 블록 단위로 분석(모든 블록 커버)**. 40줄 이하 블록은 전체 인용, 초과 블록은 핵심 줄 원문 + `# … (L<a>-<b> 생략 — 요약)` 표시.
- 주요 export: `app`(typer.Typer), `main`, `write_env`, `HTTP`(시험 주입점), 하위 앱 `catalog`·`registry`·`mcp`·`agents`·`approvals`·`bundle`·`kb`·`install_app`·`context`·`audit_app`
- 의존(내부): `app.db.SessionLocal`·`app.services.setup`(`install code --local`), `app.services.deploy.PROFILES` — 모두 함수 안 지연 import
- 외부 패키지: typer, httpx, (외부 명령) docker·docker compose·cosign·sysctl
- 부수효과 / Side effects: `~/.nexus/config.json`(토큰, 0600), 현재 디렉터리에 `nexus-precheck.json`·`install.yaml`·`.env.prod` 생성, 하위 프로세스 실행, 원격 API 호출, 번들 파일 이동(격리)
- 동시성 / Concurrency: 없음(동기 httpx·subprocess, 단일 프로세스)
- 테스트 커버리지 흔적: `test/test_cli.py:30-160`(로그인·scaffold·채팅·사용량·install check·번들 왕복), `test/test_cli_install.py:30-121`(사전 점검·compose 계획·서명 게이트·설정 코드·컨텍스트·번들 격리), `test/test_review_fixes.py:297-305`(C39-14 write_env), `:309-326`(C39-15 경로 탈출 2종), `:483-494`(C39-26 context add)
- 2차 변경 요약 / Changes since 1st review (Epic 39 — 품질 검토 반영): (1) `write_env` 가 `SIGNING_KEY_PEM` 을 비워 앱이 RSA 키를 자동 생성하게 하고(L284-285), `DATABASE_URL` 안의 `:CHANGE_ME@` 를 생성한 `DB_PASSWORD` 로 맞춘다(L293-294). (2) `mcp scaffold` 가 서버 응답 파일 이름이 대상 밖이면 중단(L424-428). (3) `bundle import` 가 매니페스트 이름이 번들 밖이면 중단(L698-701). (4) `context add` 가 현재 컨텍스트면 최상위 `url` 도 갱신(L579-580). 그 밖의 블록은 1차 인용과 동일.

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
- L1-7: 사용법 docstring(`nexus --help` 근거).
- L8-19: 표준 라이브러리 + typer·httpx. `subprocess` 는 이 파일 전체에서 **인자 리스트 형태로만** 쓰인다(`shell=True`·`os.system`·`Popen` 검색 0건) — 셸 인젝션 경로가 없다.
- L21-31: 루트 앱 + 하위 그룹 7개. `no_args_is_help=True` 는 인자 없으면 도움말, `add_completion=False` 는 자동완성 설치 명령 숨김.
- L33 `HTTP`: 시험에서 TestClient 를 주입하는 seam — 모듈 전역이라 `monkeypatch.setattr` 로만 바꿔야 원복된다.

```python path=app/cli/nexus.py lines=34-62


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
- L36-42: 설정 디렉터리(`NEXUS_CONFIG_DIR` > `~/.nexus`)·설정 읽기(없으면 빈 dict).
- L45-48: 현재 컨텍스트가 있으면 최상위 `url`·토큰을 `contexts[current]` 로 복사(SCR-A11). 이 동기화 때문에 "최상위 값이 진실" 이고, 컨텍스트만 바꾸면 덮어써진다(2차 `context add` 수정의 배경, L579-580).
- L49-53: 디렉터리 생성 → JSON 쓰기 → 0600.
- L56-61: 서버 주소 우선순위 `NEXUS_URL` > 설정 > 기본. 주입된 `HTTP` 가 없으면 **호출마다 새 `httpx.Client`**.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L52-53 생성 후 권한 변경(write-then-chmod) 경쟁(1차 지적 그대로): `write_text` 가 umask 기본 권한(보통 0644)으로 파일을 만든 뒤 `chmod(0o600)` 하므로, 그 사이 같은 호스트의 다른 사용자가 액세스·리프레시 토큰을 읽을 수 있다. L50 디렉터리도 기본 0755. 최소 수정: `d.mkdir(mode=0o700, ...)` + `os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)` 로 처음부터 0600(`app/core/keys.py:24-34` `_read_or_create` 의 `os.open(..., O_CREAT | O_EXCL, 0o600)` 이 같은 저장소의 올바른 예).
- [위험:낮음] L40-42·L60-61 견고성(1차 지적 그대로): 손상된 JSON 이면 모든 명령이 `JSONDecodeError` 트레이스백으로 실패하고, `_req`(L73)가 만든 `Client` 는 닫히지 않는다(단명 프로세스라 실피해는 작음). 최소 수정: `try/except ValueError` 로 안내 메시지, `with _client() as c:` 또는 모듈 단일 클라이언트.
- [위험:낮음] L56-61 서버 주소가 `http://` 여도 경고 없이 Bearer 토큰을 보낸다. `NEXUS_URL=http://nexus.corp.example` 처럼 원격 평문 주소를 쓰면 액세스 토큰·PAT 가 평문으로 네트워크에 흐른다. 최소 수정: 호스트가 `127.0.0.1`/`localhost` 가 아닌 `http://` 이면 경고(또는 `--insecure` 요구).

```python path=app/cli/nexus.py lines=63-87

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
- L64-69: 인증 헤더 — `NEXUS_TOKEN` > 저장 토큰, 없으면 종료 코드 2(사용 오류 관례).
- L72-82: 공통 요청 — 4xx/5xx 면 problem+json `detail` 또는 OpenAI 형식 `error.message` 를 꺼내 표시하고 종료 코드 1. 호출자가 `headers` 로 Authorization 을 넘기면 덮어쓴다(의도된 우선순위). 빈 본문(204)은 `{}`.
- L85-86: `--json` 이면 들여쓰기 JSON(`default=str` 로 datetime 직렬화), 아니면 문자열.
- 액세스 토큰 만료 시 refresh 로 갱신하는 경로가 없다 — 만료 후엔 401 → 재로그인(아래 L113 참조).

```python path=app/cli/nexus.py lines=88-126

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

```
**분석 / Analysis (Level 2)**
- L96-98: `--url` 을 저장하고 같은 프로세스의 `_client()` 가 새 주소를 쓰도록 환경변수도 바꾼다(프로세스 부수효과).
- L99-103: PAT 직접 저장 — 형식·유효성 확인 없음(잘못된 토큰은 다음 명령에서 401).
- L104-118: RFC 8628 — 코드 발급 → `user_code`·검증 URL 출력 → `interval` 마다 토큰 폴링 → 성공 시 저장 후 `/api/v1/me` 확인.
- L119-123: `slow_down` 이면 간격 +5초(RFC 8628 §3.5), `authorization_pending` 외 오류는 실패.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L105-106·L110-111 응답 상태 확인 없이 `.json()` 후 키 접근(1차 지적 그대로) — 서버 4xx/5xx·HTML 오류 페이지면 `KeyError`/`JSONDecodeError` 트레이스백이 사용자에게 노출된다. 최소 수정: `res.raise_for_status()` 또는 `_req` 와 같은 오류 출력.
- [위험:낮음] L113 `refresh_token` 을 디스크에 저장하지만 CLI 어디서도 쓰지 않고(L47·L593 은 복사만), `logout`(L128-134)은 로컬 삭제만 하고 서버 폐기(`/idp/revoke`)를 호출하지 않는다(1차 지적 그대로). 결과: 수명이 긴 refresh 토큰이 이득 없이 디스크·서버에 살아 있다. 최소 수정: 저장하지 않거나, 만료 시 갱신에 쓰고 로그아웃 때 폐기.

```python path=app/cli/nexus.py lines=127-143

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
- L128-134: 최상위 토큰 삭제 후 `_save` — `current` 가 있으면 `_save`(L46-48)가 `contexts[current]` 도 토큰 없는 값으로 다시 쓰므로 현재 컨텍스트의 토큰도 지워진다(다른 컨텍스트 토큰은 남음).
- L137-141: `whoami` — `/api/v1/me`.

```python path=app/cli/nexus.py lines=144-174
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


```
**분석 / Analysis (Level 2)**
- L145-148: 포트 점유 확인 — `connect_ex` 가 0 이면 누군가 듣고 있음. `with socket.socket()` 으로 소켓을 확실히 닫는다.
- L151-154: `install` 서브앱, 배포 프로파일 5종(서버 `app/services/deploy.py:21-27` 와 같은 이름), 외부 접근 점검 대상 3곳.
- L157-161: HEAD 로 도달성 판정(상태 코드가 오면 연결 가능).
- L164-172: 메모리 — Linux `/proc/meminfo`, macOS `sysctl`(인자 리스트·`timeout=3`·`check=False`).

```python path=app/cli/nexus.py lines=175-214
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
- L177-181: 외부 명령 실행 헬퍼 — 인자 리스트·10초 타임아웃·실패를 False 로.
- L184-186: 항목 레벨 ok/warn/fail, 실패일 때만 `fix` 안내.
- L187-211: 자원·디스크·docker·compose v2·env 파일·포트(이미 우리 서비스가 healthy 면 통과)·OTLP 포트·helm/kubectl·번들 디렉터리·DNS·시간·외부 접근.
- L195 `_healthy` 는 L372 에서 정의 — 호출 시점에 이름을 찾으므로 정의 순서는 무관.
- L208 `time` 은 항상 ok(실제 NTP 오차를 재지 않음, 주석이 인정). L209 외부 3곳 순차 HEAD(에어갭이면 최대 약 7.5초) — 둘 다 1차 지적 그대로(기능 한계, 등급 없음).

```python path=app/cli/nexus.py lines=215-261
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
    # … (L246-257 생략 — --json 이면 결과 JSON, 아니면 항목별 ✔✘⚠ 기호·통과/실패/경고 개수·보고서 경로·다음 단계 안내 출력)
    if not ok:
        raise typer.Exit(1)
```
**분석 / Analysis (Level 2)**
- L215-227: `@install_app.callback()` + `invoke_without_command=True` — `nexus install` 만 치면 점검이 돌고, `nexus install compose` 처럼 하위 명령이 있으면 바로 반환.
- L218: 기본 포트를 **import 시점** `API_PORT` 로 계산(typer 옵션 기본값은 정의 시 1회 평가).
- L231 `next(...)` 에 기본값이 없지만 `external` 항목은 `_precheck` 가 항상 추가(L210)하므로 `StopIteration` 은 나지 않는다.
- L232-237: 외부 접근이 막히면 에어갭 자동 권장, `--interactive` 면 프롬프트.
- L238-240: 프로파일 검증(종료 코드 2).
- L241-245: 보고서·`install.yaml` 저장.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L235·L245 `mode` 는 검증 없이 f-string 으로 YAML 에 쓴다(1차 지적 그대로, `profile` 만 L238 검증). 대화형 입력에 개행이 들어가면 `install.yaml` 에 임의 키(`airgap: false` 등)를 끼워 넣을 수 있다 — 로컬 사용자 입력이라 보안 영향은 낮지만 L262-271 의 최소 파서가 그대로 읽는다. 최소 수정: `mode not in ("compose","helm","airgap")` 이면 거부.

```python path=app/cli/nexus.py lines=262-299
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
    target.write_text("\n".join(lines) + "\n")
    target.chmod(0o600)
    return n


```
**분석 / Analysis (Level 2)**
- L262-271: `install.yaml` 을 YAML 파서 없이 `key: value` 줄 단위로 읽는 최소 파서(pyyaml 의존 회피). `true/false` 만 불리언.
- L274-297: `.env.example` → 운영 env. 값이 정확히 `CHANGE_ME` 인 줄만 바꾼다.
  - L281-283: `ENCRYPTION_KEY` 는 Fernet 형식(32바이트 urlsafe base64).
  - L284-285 (2차): `SIGNING_KEY_PEM` 은 **빈 값** — 앱이 `data_dir/keys/signing.pem` 에 RSA 키를 자동 생성(`app/core/keys.py:43-46·51`). 앱 쪽 `_configured` 도 `CHANGE_ME` 를 미설정으로 보므로 이중 방어다(1차 [위험:높음] 기동 실패 해결).
  - L287: 나머지는 `token_urlsafe(32)` — 문자 집합이 `[A-Za-z0-9_-]` 라 URL userinfo 에 넣어도 퍼센트 인코딩이 필요 없다(L294 치환이 안전한 이유).
  - L293-294 (2차): `DATABASE_URL` 안의 `:CHANGE_ME@` 를 같은 `DB_PASSWORD` 로 — compose 의 PostgreSQL 은 `DB_PASSWORD` 로 초기화되므로(`docker-compose.prod.yml:21`) 둘이 다르면 api 가 붙지 못한다. 현재 `.env.example` 에서 값 내부에 `CHANGE_ME` 가 있는 줄은 `DATABASE_URL`(L11) 하나뿐이다(검색 확인).
  - 값은 출력하지 않는다(MSG-018). 반환값은 생성 개수.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L295-296 도 write-then-chmod(1차 지적 그대로) — 생성 직후 잠깐 umask 권한으로 열려 DB 비밀번호·암호화 키·부트스트랩 관리자 비밀번호가 같은 호스트 다른 사용자에게 노출될 수 있다. 최소 수정: L52-53 과 같이 `os.open(..., 0o600)`.

```python path=app/cli/nexus.py lines=300-354
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
    # … (L314-317 생략 — dry-run 용 실행 계획 문자열 목록: .env 생성 여부·서명 검증 방식·up 명령·헬스 대기·설정 코드)
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
    # … (L344-351 생략 — /health 가 200 이 될 때까지 3초 간격 대기, 시간 초과면 api 로그 20줄 출력 후 종료 코드 1, 성공 시 콘솔 주소 출력)
    subprocess.run([*dc, "exec", "-T", "api", "nexus", "install", "code", "--local"], env=env, check=False)
```
**분석 / Analysis (Level 2)**
- L307-313: 설치 선택(`install.yaml`)·compose 파일 목록(`compose/*.yml` 전부 추가)·COMPOSE_PROFILES·기본 명령 배열. 모든 인자는 리스트 원소로 전달돼 `project`·`env_file` 에 공백·`;` 가 있어도 셸 해석이 없다.
- L318-320: `--dry-run` 이면 계획만(값 미노출).
- L321-323: 서명 키가 없으면 **기본 거부** — `--allow-unsigned` 로만 우회(MSG-020). 공급망 기본값이 안전 쪽이다.
- L324-329: env 생성 후 `CHANGE_ME` 잔존 시 중단.
- L330-333: 프로파일별 `AGENTNEXUS_COMPONENTS`(`app/services/deploy.py:21-27`).
- L334-341: `docker compose config --images` → 이미지마다 `cosign verify`.
- L342-352: 기동 → 헬스 대기 → 컨테이너 안에서 설정 코드 발급.
- L343 `API_PORT=` 값이 숫자가 아니면 `ValueError` 트레이스백(1차 지적 그대로, 등급 없음).
**짚어볼 점 / Watch-outs**
- [위험:중간] L342 `up -d --build` 는 서명 검증(L334-341) **뒤에 로컬에서 이미지를 다시 빌드**한다(1차 지적 그대로). `build:` 가 있는 서비스는 cosign 이 확인한 레지스트리 이미지가 아니라 현재 작업 트리로 빌드한 서명 없는 이미지로 기동된다 — 검증한 것과 실행하는 것이 다르다. 또 L335 는 `config --images` 의 반환 코드를 보지 않아 compose 설정 오류 시 이미지 목록이 비고 "서명 검증 0/0" 으로 통과한다. 시나리오: 작업 트리(또는 `compose/*.yml` 추가 파일)가 변조된 호스트에서 운영자가 `--cosign-key` 로 "검증된 설치" 를 했다고 믿지만 실제로는 변조된 소스가 빌드·기동된다. 최소 수정: `cosign_key` 경로에서는 `--build` 를 빼고 `--pull always`·digest 고정(`image@sha256:`) 이미지로 기동, `config --images` 실패·0개면 중단.

```python path=app/cli/nexus.py lines=355-378
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
- L358-365: `--local` 이면 API 를 거치지 않고 DB 에 직접(아직 로그인 계정이 없는 부트스트랩). 지연 import 라 CLI 의 다른 명령은 DB 의존 없이 동작한다. `S.new_code` 는 `(code, exp_iso)` 를 돌려주고 내부에서 commit 한다(`app/services/setup.py:78-84`).
- `--local` 을 호스트에서 실행하면 기본 `DATABASE_URL`(`sqlite:////app/data/app.db`, `app/core/config.py:19`)로 엉뚱한 DB 를 열 수 있다(1차 지적 그대로 — 컨테이너 안 전용 설계).
- L372-376: `/health` 200 이면 healthy(2초 타임아웃).

```python path=app/cli/nexus.py lines=379-407
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


```
**분석 / Analysis (Level 2)**
- L380-388: 비어 있지 않은 필터만 쿼리 파라미터로(httpx 가 URL 인코딩).
- L391-395: 즉시 사용 등록(FR-W04). `item_id` 를 경로에 f-string 으로 넣는다 — `/` 가 들어가면 다른 경로가 되지만 사용자 자신의 입력·자신의 권한이라 보안 영향은 없다.
- L398-405: `server.json` 게시 — typer `exists=True, readable=True` 가 파일을 미리 검사.

```python path=app/cli/nexus.py lines=408-435
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


```
**분석 / Analysis (Level 2)**
- L414-418: 서버에서 `{상대경로: 내용}` 템플릿을 받는다(`include` 는 반복 쿼리 파라미터).
- L419-423: 비어 있지 않은 디렉터리는 `--force` 없이 거부.
- L424-428 (2차): **신뢰하지 않는 서버 응답의 파일 이름**을 쓰기 전에 검증 — `(target / rel).resolve()` 는 `..` 를 정리하고 **심볼릭 링크도 따라간** 실제 경로라서, `../x`·절대 경로(`Path / "/abs"` 는 절대 경로로 대체됨)·대상 안에 미리 심어 둔 바깥 링크(`--force` 로 비어 있지 않은 디렉터리에 쓸 때)까지 모두 `is_relative_to(root)` 에서 걸린다. 하나라도 있으면 아무 파일도 쓰지 않고 중단(부분 쓰기 없음). 1차 [위험:중간] 해결(`test/test_review_fixes.py:309-317`).
- L429-430: 하위 디렉터리를 만드는 `parent.mkdir` 는 여전히 없다 — 다만 현재 서버 템플릿 키는 모두 평면(`server.py`·`anx_auth.py`·`telemetry.py`·`pyproject.toml`·`server.json`·선택 파일, `app/services/mcp_template.py:290-298`)이라 실패 경로가 없다.
- 검증(L425)과 쓰기(L430) 사이에 같은 사용자 권한의 다른 프로세스가 링크를 바꿔 끼우는 TOCTOU 는 이론상 가능하지만 로컬 단일 사용자 도구라 범위 밖.

```python path=app/cli/nexus.py lines=436-457
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
- L436-442: MCP 서버 목록. L445-455: Inspector — 게이트웨이 경로로 `tools/list`/`tools/call`, 승인 후 재호출은 `approval_id` 로. 정책·승인 판정은 서버 책임.
- L451 `json.loads(args)` 오류는 트레이스백(1차 지적 그대로, 등급 없음 — 사용자 메시지로 감싸면 좋다).

```python path=app/cli/nexus.py lines=458-512
# ───────────────────────── 에이전트·채팅·승인 ─────────────────────────
@agents.command("list")
def agents_list(as_json: bool = typer.Option(False, "--json")):
    # … (L461-488 생략 — agents list 출력 · agents test(POST /api/v1/agents/{profile}/test) · kb list · kb create(POST /api/v1/knowledge-bases) — 모두 _req 위임 후 출력)
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
    # … (L502-510 생략 — kb search: POST /api/v1/knowledge-bases/{kb_id}/search 결과를 점수·제목·본문 80자로 출력)
```
**분석 / Analysis (Level 2)**
- L459-488: 에이전트 목록·시험 실행, KB 목록·생성 — 모두 `_req` 위임(서버가 권한·검증).
- L491-501: 문서 추가 — PDF·docx·xlsx 는 채팅 첨부 API 로 올려 서버가 텍스트 추출(`with path.open("rb")` 로 파일을 닫음), 그 외는 로컬 `read_text()` 로 본문 전송.
- L499 `read_text()` 는 로캘 기본 인코딩 의존 — 비UTF-8 로캘(Windows 등)에서 한글 문서가 깨질 수 있다(1차 지적 그대로, `encoding="utf-8"` 권장, 등급 없음).
- L504-510: 검색 결과 출력.

```python path=app/cli/nexus.py lines=513-541
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
- L516-517: 세션 생성 → 메시지 본문(데이터 등급 선택).
- L518-539: `stream()` 컨텍스트로 응답을 스트리밍 — SSE `event:`/`data:` 줄을 직접 파싱해 이벤트별 출력. `with` 로 응답(연결)은 닫힌다. `--data-class` 는 서버 정책 판정 입력이며 서버가 최종 등급을 결정한다.
- L518 의 클라이언트 타임아웃 60초(L61)는 청크 간 읽기 타임아웃으로 적용된다 — 승인 대기처럼 이벤트 간격이 60초를 넘으면 `ReadTimeout` 가능(서버 하트비트 주기 의존, 1차와 같이 미확인). SSE 의 여러 줄 `data:` 는 처리하지 않는다(1차 지적 그대로).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L518-519 스트림 응답의 상태 코드를 확인하지 않는다(1차 지적 그대로) — 4xx(권한 없음·한도 초과)면 SSE 가 아니라 problem+json 이 와서 아무것도 출력하지 않고 **종료 코드 0** 으로 끝난다. 스크립트·CI 연계에서 실패를 성공으로 오인. 최소 수정: `if res.status_code >= 400: res.read(); 오류 출력; raise typer.Exit(1)`.

```python path=app/cli/nexus.py lines=542-568
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
- L542-548: 승인 대기 목록. L551-555: 승인/거부(`--reject`) — 거부 사유 필수 여부 등은 서버가 판정. L558-566: 사용량·비용 집계(`app/api/observe.py:331-367` 호출 — 서버가 `cost:read` 없으면 본인 범위로 제한). f-string 의 `$` 는 리터럴 통화 기호.

```python path=app/cli/nexus.py lines=569-607
context = typer.Typer(help="연결 컨텍스트 — 환경(dev·prod) 전환 (SCR-A11)", no_args_is_help=True)
app.add_typer(context, name="context")
audit_app = typer.Typer(help="감사 로그 조회", no_args_is_help=True)
app.add_typer(audit_app, name="audit")


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


```
**분석 / Analysis (Level 2)**
- L575-582 (2차): 컨텍스트 추가 — 대상이 **현재 컨텍스트**면 최상위 `url` 도 함께 바꾼다(L579-580). `_save`(L46-48)가 최상위 값으로 `contexts[current]` 를 다시 쓰기 때문에, 이 줄이 없으면 새 URL 이 이전 URL 로 덮어써졌다(1차 기능 버그 해결, `test/test_review_fixes.py:483-494`).
- L585-598: 전환 — 현재 최상위 값을 이전 컨텍스트로 보관 → 최상위 키 제거 → 새 컨텍스트 값으로 교체. 모든 환경의 토큰이 한 파일(0600)에 모인다.
- L601-605: 목록(`*` = 현재).

```python path=app/cli/nexus.py lines=608-637
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


```
**분석 / Analysis (Level 2)**
- L608-614: `1h·30m·2d` → UTC ISO 시각(tz-aware), 형식이 다르면 그대로(ISO 로 간주).
- L617-626: 감사 이벤트 — 빈 값 필터 제거(`if v` 라 `limit=0` 도 제거됨, 1차 지적 그대로·의도 가능).
- L629-635: 해시 체인 검증, 손상이면 종료 코드 1(스크립트·CI 연계용).

```python path=app/cli/nexus.py lines=638-686
# ───────────────────────── 에어갭 번들 (SCR-A04) ─────────────────────────
DEFAULT_IMAGES = ["wise-agentnexus-api:prod", "postgres:17-alpine", "valkey/valkey:8-alpine"]
# … (L640-647 생략 — _sha256: 파일을 1 MiB 청크로 읽어 SHA-256 hex, with 로 파일 닫음)
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
- L639: 기본 번들 이미지 3개. L642-647 `_sha256`: 1 MiB 청크 해시 — 대용량 tar 도 메모리에 다 올리지 않는다(좋은 습관).
- L659-664: 계획·dry-run.
- L667-671: `docker save` — 이미지 이름의 `/`·`:` 만 치환해 파일명으로(경로 구분자가 사라지므로 `--image ../../x` 같은 값도 번들 밖으로 못 나간다). `@sha256:` digest 표기는 `@` 가 파일명에 남지만 동작 문제는 없다.
- L672-675: `--include` 파일을 이름만 따서 복사 — 같은 이름 두 개면 뒤의 것이 덮어쓴다(사용자 입력, 영향 낮음).
- L676-680: 서버 서명 구성 백업(`include_secrets: False`). 서명은 서버 키로 만들어지고 복원 시 서버가 같은 키로 검증한다(`app/api/admin.py:295-302`).
- L681-683: 파일별 SHA-256 매니페스트 — **매니페스트 자체는 서명되지 않는다**(아래 import 블록 Watch-out 의 전제).

```python path=app/cli/nexus.py lines=687-737
SEPARATE_LICENSES = ("agpl", "elv2", "elastic", "sspl", "busl", "ee/")


@bundle.command("import")
def bundle_import(path: Path = typer.Argument(Path("agentnexus-airgap")), restore_config: bool = True,
                  registry_host: str | None = typer.Option(None, "--registry", help="사설 레지스트리로 적재 (docker tag·push)"),
                  verify: bool = typer.Option(False, "--verify", help="cosign 서명 검증 (번들의 cosign.pub·*.sig)"),
                  apply_airgap: bool = typer.Option(False, "--apply-airgap", help="적재 후 에어갭 프로파일 적용"),
                  dry_run: bool = False):
    """번들 반입(SCR-A04) — SHA-256·서명 검증 → 불일치는 격리·제외 → docker load → 사설 레지스트리 적재 → 구성 복원 → 에어갭."""
    manifest = json.loads((path / "manifest.json").read_text())
    unsafe = [n for n in manifest["files"] if not (path / n).resolve().is_relative_to(path.resolve())]
    if unsafe:  # 매니페스트 이름이 번들 밖을 가리키면 격리 이동이 번들 밖 파일을 옮기게 된다
        typer.secho(f"번들 밖 경로가 매니페스트에 있어 중단합니다: {unsafe}", fg="red", err=True)
        raise typer.Exit(1)
    bad = [n for n, h in manifest["files"].items() if not (path / n).exists() or _sha256(path / n) != h]
    # … (L703-706 생략 — 매니페스트 licenses 에서 AGPL·SSPL 등 분리 배포 대상을 골라 lic 로 두고 노란색 경고 출력)
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
    # … (L732-737 생략 — dry-run 이면 docker load·restore 계획만 출력하고, 실패가 있으면 종료 코드 1 로 반환)
```
**분석 / Analysis (Level 2)**
- L697-701 (2차): 매니페스트의 파일 이름(번들 작성자가 정한 **신뢰하지 않는 입력**)이 번들 밖을 가리키면 아무것도 하지 않고 중단. `resolve()` 가 `..`·절대 경로·심볼릭 링크를 실제 경로로 풀기 때문에, 1차가 지적한 "격리 이동(L725)이 번들 밖 파일을 옮기는" 경로가 막혔다(`test/test_review_fixes.py:318-326` — `../victim.txt` 가 그대로 남는지 확인).
- L702: 존재하지 않거나 해시가 다른 파일을 `bad` 로.
- L707-717: `--verify` 면 `.tar` 마다 `cosign verify-blob`(인자 리스트 — `path / n` 은 항상 번들 디렉터리 접두를 가져 `-` 로 시작하는 옵션 주입이 불가능).
- L718-725: 실패 파일을 `quarantine/` 로 이동(격리). `n` 이 하위 경로(`a/b.tar`)면 `q / n` 의 부모 디렉터리가 없어 `shutil.move` 가 실패한다(드문 경우).
- L726-731: 적재 대상 계산·동봉 파일 안내.
**짚어볼 점 / Watch-outs**
- [위험:중간] L693 `--verify` **기본값이 False** — 기본 `nexus bundle import` 는 서명을 전혀 확인하지 않고, 같은 번들 안에 있는 **서명 없는 매니페스트의 SHA-256**(L681-683 에서 만든 것)만 대조한다. 번들을 바꿀 수 있는 사람은 매니페스트도 바꿀 수 있으므로 이 대조는 전송 손상 검출일 뿐 진위 확인이 아니다. 그 뒤 L740-749 가 이미지를 `docker load` 하고 `--registry` 면 사설 레지스트리에 **push** 까지 한다. 또 `--verify` 를 줘도 검증 대상은 `.tar` 뿐(L712)이라 동봉 helm 차트 tgz 등 `extra`(L729-731 — "helm install … 하라" 고 안내하는 바로 그 파일)는 서명 확인이 없다. 대조: `install compose` 는 서명 키가 없으면 기본 거부한다(L321-323) — 같은 CLI 안에서 공급망 기본값이 반대다. 시나리오: 에어갭 반입 매체(USB 등)를 중간에 바꿔치기한 공격자가 api 이미지 tar 와 매니페스트 해시를 함께 교체 → 운영자가 문서대로 `nexus bundle import --registry reg.local` → 변조 이미지가 사설 레지스트리에 올라가 전 클러스터에 배포. 최소 수정: `--verify` 기본 True + `--allow-unsigned` 명시 우회, 매니페스트 자체 서명(`manifest.json.sig`) 검증 후 모든 파일(tar 외 포함) 해시 신뢰.
- [위험:낮음] L691 `restore_config: bool = True` 가 기본이고 L752-753 이 `"confirm": "RESTORE"` 를 자동으로 보낸다 — 주석은 "`--restore-config` 가 명시 동의" 라고 하지만 실제로는 **기본 동작**이다. 이미지만 반입하려던 운영자가 서버 구성(정책·보존·설정 행)을 번들 내용으로 덮어쓸 수 있다(서버가 자기 서명 키로 서명을 검증하므로 `app/api/admin.py:295-302`, 다른 설치본에서 만든 번들이면 복원이 실패해 이미지 적재 후 종료 코드 1 로 끝날 가능성). 최소 수정: 기본 False(`--restore-config` 를 줄 때만), 또는 확인 프롬프트.
- [위험:낮음] L729 `extra` 는 라이선스 분리 대상(`lic`)·격리된 파일도 "동봉 파일" 로 안내한다(1차 지적 그대로). 최소 수정: `n not in lic and n not in bad` 조건 추가.

```python path=app/cli/nexus.py lines=738-768
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


def main() -> None:
    app(prog_name="nexus")


if __name__ == "__main__":  # pragma: no cover
    main()
    sys.exit(0)
```
**분석 / Analysis (Level 2)**
- L738-739: 부분 실패 정책(ST-05) — 성공분은 적재하고 마지막(L758-759)에 종료 코드 1.
- L740-749: `docker load` → `--registry` 면 `Loaded image:` 줄에서 이미지 이름을 꺼내 `tag`·`push`. 이미지 참조는 docker 가 형식을 검증한 값이라 `-` 로 시작하는 옵션 주입은 불가능하다.
- L750-754: 구성 복원 — 서버가 서명 검증(`app/api/admin.py:295-302`).
- L755-757: 에어갭 프로파일 적용.
- L762-763: 진입 함수(`app/cli/__main__.py:1-3`). L766-768: 직접 실행 시.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L741·L746 적재 루프 견고성(1차 지적 그대로): `docker load` 가 `check=True` 라 손상 tar 하나에 `CalledProcessError` 트레이스백으로 나머지 적재가 멈춘다(L738 의 부분 실패 정책과 불일치). `img.split('/')[-1]` 은 레지스트리 호스트·네임스페이스를 버려 `org-a/api:1` 과 `org-b/api:1` 이 같은 `reg/api:1` 로 충돌·덮어쓰기된다. 최소 수정: `check=False` + 실패 tar 를 `bad` 에 추가, 대상 이름은 호스트만 제거하고 네임스페이스 유지.
- [위험:낮음] L768 `sys.exit(0)` 은 도달하지 않는다(1차 지적 그대로 — typer 의 `standalone_mode` 가 `app()` 안에서 `SystemExit` 를 던짐). 동작 영향은 없고 정리 대상. 최소 수정: 줄 삭제.

## 파일 평가 / File Assessment
- 강점: (1) 외부 명령을 모두 인자 리스트로 실행 — 셸 인젝션 경로 없음(`shell=True` 0건). (2) 2차에서 신뢰하지 않는 파일 이름(서버 템플릿·번들 매니페스트)을 `resolve().is_relative_to()` 로 쓰기·이동 전에 차단하고, `write_env` 의 서명 키·DB 비밀번호 불일치를 고쳤다(시험 고정). (3) `install compose` 는 서명 키 없으면 기본 거부·시크릿 값 미출력·0600.
- 위험: (1) [위험:중간] `bundle import` 서명 검증 기본 꺼짐·매니페스트 미서명 → 변조 이미지 적재·push — `app/cli/nexus.py:693,712,740-749`. (2) [위험:중간] cosign 검증 후 `--build` 로 다른 이미지 기동 — `app/cli/nexus.py:335,342`. (3) [위험:낮음] 토큰·시크릿 파일 write-then-chmod 경쟁, refresh 토큰 무용 저장, `restore_config` 기본 켜짐 — `app/cli/nexus.py:52-53,295-296,113,691`.
- 개선 권고(영향순): (1) `bundle import --verify` 기본화 + 매니페스트 서명. (2) 서명 경로에서 `--build` 제거·digest 고정. (3) `os.open(..., 0o600)`·디렉터리 0700, refresh 토큰 활용 또는 미저장, `restore_config` 기본 False. (4) 스트림·디바이스 코드 응답 상태 확인.
- 파일 위험 등급 / Risk grade: 중간 — 미해결 [위험:중간] 2건(공급망 경계), 1차 [위험:높음] 1건·[위험:중간] 2건은 해결.
- 재읽기 권고 레벨: 2 — 하위 프로세스·파일 시스템·공급망 경계가 많아 줄 단위 재확인 가치가 있다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| L33 전역 `HTTP` 시험 간 공유 상태 | 미해결(시험 seam, 실피해 없음) | L33 |
| [낮음] L52-53 `write_text` 후 `chmod` 경쟁(토큰) | 미해결 | L52-53 |
| [낮음] L40-42 손상 JSON 이면 모든 명령 실패 | 미해결 | L40-42 |
| [낮음] L60-61 `httpx.Client` 미닫힘 | 미해결 | L60-61·L73 |
| refresh_token 저장만 하고 미사용 | 미해결 | L113 (사용처 없음) |
| [낮음] L105 상태 확인 없이 `.json()` | 미해결 | L105-106·L110-111 |
| PAT 형식·유효성 확인 없음 | 미해결 | L99-103 |
| logout 이 서버 refresh 폐기 안 함 | 미해결 | L128-134 |
| L208 `time` 항목 항상 ok | 미해결(기능 한계) | L208 |
| L209 외부 3곳 순차 HEAD | 미해결(기능 한계) | L209 |
| [낮음] L245 `install.yaml` 의 `mode` 미검증 | 미해결 | L235·L245 |
| [낮음] L290-291 `write_env` chmod 경쟁 | 미해결 | L295-296 |
| [높음] `SIGNING_KEY_PEM` 이 난수로 채워져 api 기동 실패 | 해결 | L284-285 (+ `app/core/keys.py:43-46`); `test/test_review_fixes.py:297-305` |
| `BOOTSTRAP_ADMIN_PASSWORD` 미표시 안내 부족 | 미해결 | L326 |
| [중간] L337 cosign 검증 후 `up --build` | 미해결 | L342 |
| L330 `config --images` 반환 코드 미확인 | 미해결 | L335 |
| L338 `API_PORT` 비숫자면 ValueError | 미해결 | L343 |
| `--local` 호스트 실행 시 엉뚱한 DB | 미해결(컨테이너 전용 설계) | L358-365 |
| [중간] L419-420 scaffold 서버 응답 파일 이름 경로 탈출 | 해결 | L424-428; `test/test_review_fixes.py:309-317` |
| scaffold 하위 디렉터리 `parent.mkdir` 없음 | 미해결(현재 템플릿은 평면 키라 무해) | L429-430; `app/services/mcp_template.py:290-298` |
| L441 `json.loads(args)` 트레이스백 | 미해결 | L451 |
| L489 `read_text()` 로캘 인코딩 | 미해결 | L499 |
| [낮음] L508 스트림 상태 코드 미확인(실패가 종료 코드 0) | 미해결 | L518-519 |
| 스트림 60초 읽기 타임아웃 | 미해결(가능성, 서버 하트비트 미확인) | L61·L518 |
| SSE 여러 줄 `data:` 미처리 | 미해결 | L521-525 |
| [낮음] L569 `context add` 현재 컨텍스트 URL 미반영 | 해결 | L579-580; `test/test_review_fixes.py:483-494` |
| L609 `if v` 가 `limit=0` 제거 | 미해결(의도 가능) | L621-622 |
| L657 digest 이미지 파일명 `@` | 미해결(무해) | L669 |
| 매니페스트 미서명 — 위변조 방지는 `--verify` 의존 | 미해결(2차 [위험:중간] 으로 상향 — `--verify` 기본 꺼짐·tar 외 미검증) | L693·L712 |
| [중간] L686·L696·L707-709 매니페스트 이름 경로 탈출 → 번들 밖 파일 격리 이동 | 해결 | L698-701; `test/test_review_fixes.py:318-326` |
| L725 `docker load` `check=True` 가 부분 실패 정책과 불일치 | 미해결 | L741 |
| L730 `img.split('/')[-1]` 네임스페이스 충돌 | 미해결 | L746 |
| L713 `extra` 에 라이선스 제외 파일 포함 | 미해결 | L729 |
| L752 `sys.exit(0)` 도달 불가 | 미해결 | L768 |
