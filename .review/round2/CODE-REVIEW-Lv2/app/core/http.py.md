> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [core_Abstract.md](core_Abstract.md) · 1차 리뷰: [http.py.md](../../../../CODE-REVIEW-Lv2/app/core/http.py.md)

### 06. app/core/http.py
**파일 요약 / File Summary**
- 역할 / Role: 아웃바운드 HTTP 클라이언트 팩토리(`async_client`·`sync_client`) — 요청 훅(event hook)으로 **egress 가드**(링크 로컬·IMDS 차단) + W3C traceparent 주입, 시험용 origin 별 transport 마운트. 2차에서 **클라이언트 콜백(푸시) URL 검사 `internal_url`**(사설·루프백·예약 차단)과 **웹훅 재시도 전송 `post_with_retry`** 가 추가되었다. in-process ASGI 스트리밍 전송(`StreamingASGITransport`)·동기 브리지(`SyncASGITransport`)는 그대로.
- 진입점 거리 / Distance: 2 (`app/api/mcp.py:557`·`app/api/admin.py:89` 등 API 가 `sync_client` 직접 사용, 서비스 경유 다수 — `app/services/a2a_gateway.py:90`, `app/services/providers.py:111`)
- LOC: 273 (21:03 스냅샷 `wc -l` — 리뷰 도중 L37 한 줄이 바뀌었으나 줄 수는 같음, 아래 스냅샷 고지) · 1차 225 → +48
- 주요 export: `register_mount`, `clear_mounts`, `EgressBlocked`, `blocked_url`, `internal_url`(신규), `post_with_retry`(신규), `async_client`, `sync_client`, `StreamingASGITransport`, `SyncASGITransport`, `_ASYNC_MOUNTS`(외부 직접 참조 — `app/services/mcp_gateway.py:570-573`)
- 의존(내부): `app.core.config.settings`
- 외부 패키지: httpx(0.28.1, `uv.lock:811-812`), opentelemetry(propagate), 표준 `ipaddress`·`socket`·`asyncio`
- 부수효과 / Side effects: 모듈 전역 마운트 레지스트리, DNS 판정 캐시 `_RESOLVED`(60초·4096 상한), 아웃바운드 네트워크 I/O, DNS 조회
- 동시성 / Concurrency: asyncio(비동기 DNS·백그라운드 Task+Queue), 스레드풀에서 동기 가드(`socket.getaddrinfo`), 전역 dict 를 락 없이 공유(경합은 판정 캐시 수준으로 무해)
- 테스트 커버리지 흔적: `test/test_review_fixes.py:57-81`(`test_egress_guard` — 169.254·IPv4-mapped·fe80·`fd00:ec2::254`·metadata 이름·이름 해석 결과 차단, 사설망 허용, 마운트 통과), `test/test_policy_hardening.py:46-63`(`test_push_url_guard` — `internal_url` 경유 푸시 URL 거부), `test/test_lv2_hardening.py:178-195`(`test_post_with_retry` — 503 재시도·400 즉시 포기·메타데이터 주소 시도 없음), `test/conftest.py:62-74`(모든 in-process 마운트)
- 2차 변경 요약 / Changes since 1st review: (1) L92-120 `_internal_ip`·`internal_url` 신규 — Epic 43(D17-④) 클라이언트 푸시 URL 검사(`test/impl/43rd/scenario.md:8`). (2) L156-170 `post_with_retry` 신규 — Epic 49(D19-②) 웹훅 재시도(`test/impl/49th/scenario.md:12`). (3) 그 밖의 줄(egress 가드 L34-89, 훅 L123-153, 팩토리 L173-180, 전송 L183-273)은 1차 인용 원문과 **동일**(줄 번호만 이동). 1차가 지적한 `start["error"]` 미사용·함수 내 `import asyncio` 도 그대로.
- 스냅샷 고지 / Snapshot note: 리뷰 도중 21:34 에 Epic 51(D19-④) 동시 작업이 **L37 한 줄**을 바꿨다(`100.100.100.200/32` 추가 — 아래 Alibaba 지적 대응). 이 보고서의 인용·판정은 21:03 스냅샷 기준이다 — 현재 파일에서 L37 만 되돌린 사본의 해시가 스냅샷 해시(`d416e02b…`, `scratchpad/snapshot-start.sha:18`)와 일치함을 확인했고, 줄 번호는 현재 디스크와 같다. 다른 파일(`app/services/{a2a_gateway,a2a_server,mcp_gateway,mcp_consent,llm}.py`)도 스냅샷 이후 바뀌어 교차 참조 줄 번호는 **스냅샷 기준**이며 현재 디스크에서는 몇 줄 밀렸을 수 있다. 스냅샷 이후 수정은 "(스냅샷 이후 진행 중)" 으로만 표시하고 상태 판정에 반영하지 않았다(시험 미실행).
- 스냅샷 고지 / Post-snapshot note: 이 보고서의 인용·줄 번호는 **21:03 스냅샷**(sha1 `d416e02b…`, `scratchpad/snapshot-start.sha` — 인용 블록을 이어 붙여 재구성한 해시가 일치함을 확인) 기준이다. 리뷰 도중 21:34 에 디스크의 이 파일이 바뀌었다(D19-④): L37 `_BLOCKED_NETS` 에 `100.100.100.200/32`(Alibaba IMDS) 추가 — 아래 [위험:중간] Alibaba 항목은 **기본 가드 기준으로 해소**(단 `_internal_ip` 의 100.64/10 나머지 대역은 그대로). 같은 시각 호출부도 바뀌어 푸시 URL 검사가 스레드풀에서 실행되고(`app/services/a2a_gateway.py:569`, `app/services/a2a_server.py:286`) **전달 직전 재검사**가 추가됐다(`app/services/a2a_gateway.py:853`, `app/services/a2a_server.py:117`) — 아래 "등록 시점에만 검사" [위험:중간] 은 **부분 해소**(재검사와 실제 연결 사이 재해석 TOCTOU·해석 실패 허용 L119-120 은 잔존). 등급·표는 스냅샷 기준으로 유지한다.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/http.py lines=1-31
"""아웃바운드 HTTP 클라이언트 팩토리 — W3C traceparent 전파 + 메타데이터 대역 차단 + (시험용) 호스트별 transport 마운트."""
import asyncio
import ipaddress
import socket
import time

import httpx
from opentelemetry import propagate

from app.core.config import settings

_ASYNC_MOUNTS: dict[str, httpx.AsyncBaseTransport] = {}
_SYNC_MOUNTS: dict[str, httpx.BaseTransport] = {}


def register_mount(prefix: str, async_transport: httpx.AsyncBaseTransport | None = None,
                   sync_transport: httpx.BaseTransport | None = None) -> None:
    """prefix 예: 'http://mcp-servers' — 해당 origin 요청을 주어진 transport 로 보낸다 (시험·in-process)."""
    key = prefix.rstrip("/")
    if "://" in key and not key.startswith("all://"):
        scheme, host = key.split("://", 1)
        key = f"{scheme}://{host}"
    if async_transport is not None:
        _ASYNC_MOUNTS[key] = async_transport
    if sync_transport is not None:
        _SYNC_MOUNTS[key] = sync_transport


def clear_mounts() -> None:
    _ASYNC_MOUNTS.clear()
    _SYNC_MOUNTS.clear()
```
**분석 / Analysis (Level 2)**
- L1: 이 모듈의 책임 요약. 2차에서 "콜백 URL 검사·웹훅 재시도" 책임이 늘었지만 docstring 은 갱신되지 않았다(사소).
- L4 `socket`: 동기 `getaddrinfo` 용 — 비동기 경로는 `loop.getaddrinfo`(L132)를 쓴다. 둘이 섞여 있다는 점이 뒤의 이벤트 루프 블로킹 지적의 배경이다.
- L12-13: origin → transport 매핑. 모듈 전역 가변 dict 이며, 팩토리는 생성 시점에 `dict(...)` 로 **복사**한다(L174·L179) — 클라이언트를 만든 뒤 등록한 마운트는 그 클라이언트에 반영되지 않는다(흔한 시험 실수: fixture 순서).
- L16-26 `register_mount`: L19 끝 슬래시 제거. **L20-22 는 사실상 no-op** — `split("://", 1)` 로 나눈 두 조각을 같은 구분자로 다시 붙이므로 문자열이 그대로다. 1차 분석("접두에서 경로를 떼어 정규화")은 코드와 다르다: `"http://x/y"` 를 넣으면 키가 `"http://x/y"` 로 남고, `_target`(L59)이 비교하는 origin(`scheme://netloc`)과 일치하지 않는다.
- L29-31: 시험 정리용.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L16-26·L60-61·L113-114: 운영 코드에서도 `register_mount` 를 막는 가드(`settings.app_env == "test"`)가 없고, 마운트된 origin 은 egress 가드·`internal_url` 을 **모두 통과**한다. 운영 코드가 실수로(또는 플러그인이) 마운트를 등록하면 그 origin 트래픽이 가로채지고 검사도 빠진다. 최소 수정: `register_mount` 에서 `app_env not in ("test", "local")` 이면 `RuntimeError`.
- [위험:낮음] L20-22: 경로가 포함된 prefix 는 조용히 매칭에 실패한다(시험 작성자 혼란). 최소 수정: `httpx.URL(prefix)` 로 파싱해 `f"{u.scheme}://{u.netloc.decode()}"` 로 키를 만든다.

```python path=app/core/http.py lines=34-53
# ── egress 가드 — 사용자가 넣은 URL(OpenAPI·카드·푸시·커넥터)로 클라우드 메타데이터를 읽지 못하게 (SSRF).
# 사내 사설망 업스트림은 제품 기능이라 막지 않는다 — 링크 로컬(169.254/16·fe80::/10)과 IMDS 이름·주소만.
_BLOCKED_NETS = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fe80::/10"),
                 ipaddress.ip_network("fd00:ec2::254/128"))
_BLOCKED_HOSTS = {"metadata", "metadata.google.internal", "metadata.goog"}
_RESOLVED: dict[str, tuple[float, bool]] = {}  # host → (시각, 차단 여부) 60초


class EgressBlocked(httpx.TransportError):
    """메타데이터·링크 로컬 대상으로의 아웃바운드 요청."""


def _blocked_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in _BLOCKED_NETS if net.version == ip.version)
```
**분석 / Analysis (Level 2)**
- L34-35 설계 결정: 팩토리를 쓰는 **모든** 아웃바운드 호출에 걸리는 기본 가드는 "클라우드 자격 증명 탈취"만 막는 최소 SSRF 방어다. 사설망(10/8·172.16/12·192.168/16)·루프백은 업스트림 등록 기능 때문에 허용한다. 더 엄격한 검사는 L102 `internal_url`(푸시 URL 전용)로 분리되었다.
- L36-37: 169.254/16 은 AWS·GCP·Azure·OCI 의 IMDS(169.254.169.254)와 일부 클라우드의 169.254.0.x 를 덮는다. `fd00:ec2::254` 는 AWS IPv6 IMDS.
- L38: GCP 메타데이터 이름. 이름 검사는 보조이고, 실제 방어는 해석된 IP 검사(L69)다(`metadata` 짧은 이름이 검색 도메인으로 169.254.169.254 로 풀려도 IP 로 걸린다).
- L42-43 `EgressBlocked(httpx.TransportError)`: 기존 `except httpx.HTTPError` 처리부가 그대로 "연결 실패" 로 다룬다. 반대로 **차단만 따로 처리하려면 `EgressBlocked` 를 `HTTPError` 보다 먼저** 잡아야 한다 — L164-167 이 그 순서를 지킨다.
- L46-53 `_blocked_ip`: IPv6 zone id(`fe80::1%eth0`)를 떼고, IPv4-mapped(`::ffff:169.254.169.254`)를 IPv4 로 풀어 비교한다. 문자열이 IP 리터럴이 아니면(`ValueError`) False — 이름은 L69 의 해석 결과로 판정된다.
**짚어볼 점 / Watch-outs**
- [위험:중간] L36-37: **Alibaba Cloud IMDS `100.100.100.200`(100.64.0.0/10 공유 주소 공간)** 이 차단 대역에 없다. L99 의 `is_private` 도 100.64/10 에 대해 False 를 돌려주므로(CPython 3.12 `ipaddress.py:1344` docstring "`is_private` is `False` for `100.64.0.0/10`", 런타임은 `Dockerfile:3` `python:3.12-slim`) `internal_url` 까지 통과한다. 시나리오: Alibaba Cloud 에 배포된 게이트웨이에서 `mcp:write` 를 가진 개발자가 OpenAPI 가져오기 URL(`app/api/mcp.py:557`)로 `http://100.100.100.200/latest/meta-data/ram/security-credentials/` 를 넣으면 인스턴스 RAM 자격 증명이 응답 본문으로 돌아온다. 같은 이유로 100.64/10 을 파드 대역으로 쓰는 클러스터(예: EKS 사용자 지정 네트워킹의 보조 CIDR)에서는 푸시 URL 검사(`internal_url`)가 파드 주소를 "공인" 으로 본다(가능성 — 배포 의존). 최소 수정: `_BLOCKED_NETS` 에 `100.100.100.200/32` 추가, `_internal_ip` 는 `not ip.is_global` 로 판정. (스냅샷 이후 진행 중: 현재 디스크 L37 에 `100.100.100.200/32` 가 추가됨 — `_internal_ip` 의 100.64/10 통과는 그대로.)
- [위험:낮음] NAT64(`64:ff9b::/96`)·6to4(`2002::/16`)처럼 IPv6 안에 IPv4 를 싣는 형식은 L51-52 가 풀지 않아 **기본 가드**는 `64:ff9b::a9fe:a9fe` 를 통과시킨다(NAT64 망 한정, 링크 로컬은 보통 NAT64 로 라우팅되지 않으므로 가능성 낮음). 반면 `internal_url` 은 `::/8` 이 `is_reserved`, `2002::/16` 이 `is_private` 라(CPython 3.12 `ipaddress.py` 의 IPv6 `_private_networks`·`_reserved_networks`) 두 형식 모두 거부한다. 최소 수정: 기본 가드에서 `ip.sixtofour`·NAT64 접두를 IPv4 로 풀어 재검사.

```python path=app/core/http.py lines=56-89
def _target(url: httpx.URL) -> tuple[str, bool | None]:
    """(정규화한 host, 판정) — 판정이 None 이면 이름 해석이 필요하다. 시험 마운트는 네트워크를 타지 않으므로 통과."""
    host = url.host.strip("[]").rstrip(".").lower()
    origin = f"{url.scheme}://{url.netloc.decode()}"
    if origin in _ASYNC_MOUNTS or origin in _SYNC_MOUNTS:
        return host, False
    if host in _BLOCKED_HOSTS or _blocked_ip(host):
        return host, True
    hit = _RESOLVED.get(host)
    return host, (hit[1] if hit and time.monotonic() - hit[0] < 60 else None)


def _remember(host: str, infos: list) -> bool:
    blocked = any(_blocked_ip(info[4][0]) for info in infos)  # 해석 실패(빈 목록)는 연결 단계에서 그대로 실패한다
    if len(_RESOLVED) > 4096:
        _RESOLVED.clear()
    _RESOLVED[host] = (time.monotonic(), blocked)
    return blocked


def _blocked_error(request: httpx.Request) -> EgressBlocked:
    return EgressBlocked(f"메타데이터·링크 로컬 주소로는 요청할 수 없습니다: {request.url.host}", request=request)


def blocked_url(url: str | httpx.URL) -> bool:
    """메타데이터·링크 로컬 대상이면 True (시험 마운트 제외) — 이 팩토리를 거치지 않는 클라이언트의 사전 점검용."""
    host, blocked = _target(httpx.URL(url))
    if blocked is None:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            infos = []
        blocked = _remember(host, infos)
    return blocked
```
**분석 / Analysis (Level 2)**
- L58: httpx 의 `URL.host` 는 IPv6 리터럴을 대괄호 없이 돌려주므로 `strip("[]")` 는 방어 코드다. 끝 점(`metadata.google.internal.`)·대소문자를 정규화해 이름 비교 우회를 막는다.
- L59-61: origin = `scheme://netloc`(기본 포트는 httpx 가 생략). 시험 마운트는 통과.
- L62-65: 차단 이름·리터럴 IP 면 즉시 차단, 아니면 60초 캐시, 캐시 없음이면 None(→ DNS 조회 필요).
- L68-73 `_remember`: 해석된 **모든** 주소(A·AAAA 여러 개) 중 하나라도 차단 대역이면 차단 — "여러 레코드 중 하나만 IMDS" 우회를 막는다(초점 항목 "다중 IP" 는 이 줄로 처리됨). 4096 초과 시 통째로 비운다(단순 상한).
- 숫자 표기 우회(초점 항목): httpx 0.28.1(`uv.lock:811-812`) 의 호스트 파서는 `^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$` 모양만 IPv4 로 보고 `ipaddress.IPv4Address` 로 검증한다(로컬 uv 캐시 사본 `httpx/_urlparse.py:154`·`:352-362` 확인). 그래서 (a) 0 으로 시작하는 8진 점 표기(`0251.0376.0251.0376`)는 IPv4Address 가 앞자리 0 을 거부해 `InvalidURL` — 요청 자체가 만들어지지 않는다(확인됨). (b) 10진(`http://2852039166/`)·16진(`0xa9fea9fe`)·축약(`169.254.43518`) 은 그 모양이 아니라 **일반 호스트 이름**으로 소문자화만 되어 통과하고(`_urlparse.py:379-386`), `_blocked_ip` 의 `ip_address()` 도 파싱하지 못해 L62 를 지난다. 이후 L85/L132 의 `getaddrinfo` 가 glibc `inet_aton` 규칙으로 169.254.169.254 로 풀어 돌려주면 L69 에서 차단된다(가능성 — 런타임 미실행·시험 없음). 즉 숫자 표기 방어는 **이름 해석 경로에 전적으로 의존**한다.
- L80-89 `blocked_url`: 팩토리 밖 클라이언트용 동기 사전 점검. 실제 호출부 `app/services/mcp_gateway.py:574`(스냅샷) 는 `run_in_threadpool` 로 감싸 루프를 막지 않지만 **hosted 클라이언트 생성 때 1회만** 부르고, 캐시된 httpx2 클라이언트(`:579-581`, 훅은 obo 일 때만)의 이후 요청에는 가드가 없다 — 등록 뒤 이름을 메타데이터 주소로 바꾸면 통과(교차 이슈, `.review/round2/VERIFY.md:11` SEC2-01 확인). docstring 의 "사전 점검용" 표현이 1회 검사로 충분하다는 오해를 부른다. (스냅샷 이후 진행 중: 현재 `app/services/mcp_gateway.py:560-564`·`:594` 에 요청마다 `_guard_hosted` 훅.)
**짚어볼 점 / Watch-outs**
- [위험:중간 · TOCTOU] L56-73 (1차 이월): 가드는 훅 시점에 이름을 해석하고, 실제 연결은 httpcore 가 **다시** 해석한다. 판정도 60초 캐시(L65)라 TTL 0 레코드로 첫 조회엔 공인 IP, 연결 때엔 169.254.169.254 를 주는 DNS 재바인딩이 통과한다. 39차 시나리오는 "클러스터 egress NetworkPolicy 가 최종 방어선"이라 적었지만(`test/impl/39th/scenario.md:120`), 차트가 배포하는 NetworkPolicy 는 모두 `policyTypes: [Ingress]` 뿐이다(`deploy/helm/agentnexus/templates/networkpolicy.yaml:14,28,48`) — 문서가 가정한 방어선이 기본 배포에는 없다. 최소 수정: 해석한 IP 로 직접 연결하는 커스텀 transport(SNI·Host 는 원래 이름 유지), 또는 차트에 169.254.0.0/16·100.100.100.200 을 `except` 한 egress NetworkPolicy 를 기본 포함.
- [위험:낮음] L85: 동기 `socket.getaddrinfo` — 비동기 코드에서 직접 부르면 루프가 DNS 시간만큼 멈춘다(현재 호출부는 스레드풀로 감쌈).
- L69 해석 실패는 False 로 캐시 — 연결 단계에서 어차피 실패하므로 기본 가드에서는 무해(주석의 근거 유효).

```python path=app/core/http.py lines=92-120
def _internal_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified or ip.is_multicast


def internal_url(url: str) -> bool:
    """사설·루프백·링크 로컬·예약 대상이면 True (시험 마운트 제외) — 클라이언트가 넣는 콜백(푸시) URL 검사용 (D17-④).
    업스트림 등록과 달리 콜백은 공인 수신처여야 한다 — 게이트웨이를 내부망 요청 발판으로 쓰지 못하게."""
    try:
        u = httpx.URL(url)
    except (httpx.InvalidURL, TypeError):
        return True
    if u.scheme not in ("http", "https") or not u.host:
        return True
    host = u.host.strip("[]").rstrip(".").lower()
    origin = f"{u.scheme}://{u.netloc.decode()}"
    if origin in _ASYNC_MOUNTS or origin in _SYNC_MOUNTS:
        return False
    if host in ("localhost",) or host in _BLOCKED_HOSTS or _internal_ip(host):
        return True
    try:
        return any(_internal_ip(info[4][0]) for info in socket.getaddrinfo(host, None))
    except OSError:
        return False  # 해석 안 되는 이름은 전송 단계에서 실패한다
```
**분석 / Analysis (Level 2)**
- L92-99 `_internal_ip` (신규): `ipaddress` 플래그 조합. `is_private` 는 10/8·172.16/12·192.168/16·127/8·169.254/16·fc00::/7(ULA)·::1 등을 포함하고, `is_unspecified` 가 `0.0.0.0`·`::` 를, `is_link_local` 이 fe80::/10 을, `is_reserved`·`is_multicast` 가 나머지를 덮는다. IPv4-mapped(`[::ffff:127.0.0.1]`)는 L97-98 로 풀린다 — 초점 항목의 IPv6 형식(`[::1]`·`::ffff:127.0.0.1`·fc00::/7·fe80::)은 모두 True 가 된다(`test/test_policy_hardening.py:50-52` 가 `[::1]`·127.0.0.1·10.x·192.168.x·localhost 를 확인).
- L105-110: 파싱 실패·http(s) 아님·host 없음은 **True(=거부)** — 형식 오류에 fail closed.
- L111-114: 시험 마운트는 통과(`test/test_policy_hardening.py:54-55`).
- L115: `localhost` 리터럴, IMDS 이름, IP 리터럴 검사.
- L117-118: 이름이면 해석된 모든 주소 중 하나라도 내부면 True.
- L119-120: 해석 실패는 **False(=허용)** — "전송 단계에서 실패한다" 는 근거.
- 호출처: `app/services/a2a_gateway.py:639-646` `push_url_problem`(운영 환경에서만 — `app_env not in ("local", "test")`), 그 호출처는 `app/services/a2a_gateway.py:568`(게이트웨이 경유 push_set)·`app/services/a2a_server.py:283`(플랫폼 서버, `app/api/a2a.py:222` 가 `push_check=push_url_problem` 주입).
**짚어볼 점 / Watch-outs**
- [위험:중간] **검사가 등록 시점에만 있고 전달 시점에는 없다.** 실제 전달은 `post_with_retry`(L156) → `async_client` 의 기본 가드(L128-137, 링크 로컬·IMDS 만)로 나간다(`app/services/a2a_gateway.py:835-836`, `app/services/a2a_server.py:119`). 시나리오: 인증된 사용자가 `tasks/pushNotificationConfig/set` 에 자기 도메인 `https://hook.attacker.example/x` 를 등록(그 순간 공인 IP 로 해석 → 통과)한 뒤 DNS 를 `10.0.0.5`(사내 관리 콘솔)로 바꾸면, Task 상태가 바뀔 때마다 게이트웨이가 내부 주소로 Task JSON 을 POST 한다. 등록 시 아직 해석되지 않는 이름도 L119-120 이 허용하므로 "나중에 레코드를 만든다" 로도 같은 결과다. 최소 수정: 콜백 전용 엄격 모드 — `post_with_retry` 가 `async_client(strict=True)` 같은 별도 훅으로 매 요청 `internal_url` 판정(가능하면 해석 IP 고정 연결)을 하게 하고, L119-120 은 콜백 용도에서 True(거부)로.
- [위험:낮음] L117-118: 동기 `socket.getaddrinfo` 를 **비동기 경로에서 직접** 부른다 — `_proxy`(`app/services/a2a_gateway.py:549` async def → L568)와 `A2AServer.dispatch`(`app/services/a2a_server.py:218` async def → L283)가 이 함수를 스레드풀 없이 호출하므로, 느린 DNS(최대 수 초) 동안 그 워커의 이벤트 루프 전체가 멈춘다. 최소 수정: `await run_in_threadpool(push_url_problem, url)` 또는 `loop.getaddrinfo` 사용.
- [위험:낮음] L115 `localhost` 정확 일치만 — `foo.localhost` 는 L118 의 해석 결과(시스템 리졸버가 127.0.0.1 로 푸는 경우)에 의존한다(가능성).

```python path=app/core/http.py lines=123-153
def _guard_sync(request: httpx.Request) -> None:
    if blocked_url(request.url):
        raise _blocked_error(request)


async def _guard_async(request: httpx.Request) -> None:
    host, blocked = _target(request.url)
    if blocked is None:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None)
        except OSError:
            infos = []
        blocked = _remember(host, infos)
    if blocked:
        raise _blocked_error(request)


async def _inject_async(request: httpx.Request) -> None:
    await _guard_async(request)
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    for k, v in carrier.items():
        request.headers.setdefault(k, v)


def _inject_sync(request: httpx.Request) -> None:
    _guard_sync(request)
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    for k, v in carrier.items():
        request.headers.setdefault(k, v)
```
**분석 / Analysis (Level 2)**
- L123-137: 동기·비동기 가드. 비동기 판은 `loop.getaddrinfo`(기본 스레드풀 executor 에서 실행)로 루프를 막지 않는다.
- L140-153: 요청 훅 — 가드가 먼저, 그다음 OTel 현재 컨텍스트를 `traceparent`(+`tracestate`)로 주입. `setdefault` 라 호출자가 넣은 헤더를 덮지 않는다.
- 리다이렉트(초점 항목): httpx 는 **요청 훅을 리다이렉트 홉마다 다시 실행**하지만, 앱 코드에서 `follow_redirects=True` 를 쓰는 곳은 없다(`grep` — 유일한 사용처 `app/cli/nexus.py:159` 는 `False`). httpx 기본값도 False 라 3xx 는 응답 그대로 돌아오고, `post_with_retry` 는 3xx 를 실패로 본다(L162-163). 리다이렉트를 이용한 가드 우회 경로는 현재 없다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L143: traceparent 가 **외부 수신처**(클라이언트 웹훅·외부 IdP·Slack 웹훅 `app/services/notify.py:40`)에도 붙는다 — trace id 가 제3자에게 노출된다(표준 관행이지만 외부 대상에서는 끄는 선택지 고려).

```python path=app/core/http.py lines=156-180
async def post_with_retry(url: str, attempts: int = 3, base_delay: float = 0.5, timeout: float = 5, **kw) -> bool:
    """웹훅(푸시) 전달 — 연결 오류·5xx·429 는 지수 백오프(0.5·1초 …)로 다시 보낸다. 다른 4xx·차단 대상은 바로 포기 (D19-②)."""
    for i in range(attempts):
        try:
            async with async_client(timeout=timeout) as http:
                res = await http.post(url, **kw)
            if res.status_code < 500 and res.status_code != 429:
                return res.status_code < 400
        except EgressBlocked:
            return False
        except httpx.HTTPError:
            pass
        if i < attempts - 1:
            await asyncio.sleep(base_delay * 2 ** i)
    return False


def async_client(timeout: float | None = None, **kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout or settings.upstream_timeout, mounts=dict(_ASYNC_MOUNTS) or None,
                             event_hooks={"request": [_inject_async]}, **kw)


def sync_client(timeout: float | None = None, **kw) -> httpx.Client:
    return httpx.Client(timeout=timeout or settings.upstream_timeout, mounts=dict(_SYNC_MOUNTS) or None,
                        event_hooks={"request": [_inject_sync]}, **kw)
```
**분석 / Analysis (Level 2)**
- L156-170 `post_with_retry` (신규): 시도마다 새 클라이언트(`async with` 로 확실히 닫힘). 2xx → True, 3xx·4xx(429 제외) → False 즉시 반환, 5xx·429·전송 오류(`HTTPError`) → 0.5·1초 백오프 후 재시도, `EgressBlocked` → 즉시 False. **`EgressBlocked` 를 `HTTPError` 보다 먼저 잡는 순서**가 핵심 — 바꾸면 차단 대상에 3회 재시도한다. 최악 소요 = 3×5초(timeout) + 1.5초.
- 호출처: `app/services/a2a_gateway.py:835-837`(게이트웨이 푸시 중계, 백그라운드 Task), `app/services/a2a_server.py:115-121`(플랫폼 서버 푸시, 백그라운드 Task).
- L173-180 팩토리: 타임아웃 기본값(`UPSTREAM_TIMEOUT` 30초, `app/core/config.py:46`), 마운트 복사, 훅. `**kw` 로 `verify`(SSLContext, `app/services/tls.py:93-94`) 등을 받는다.
- 프록시(초점 항목): httpx 기본 `trust_env=True` 라 `HTTP(S)_PROXY`·`NO_PROXY`·`SSL_CERT_FILE` 환경변수를 따른다(앱 코드에 `trust_env` 지정 없음 — `grep`). 프록시가 설정되면 가드는 **로컬** 리졸버로 판정하고 실제 연결 대상 해석은 **프록시**가 한다 — split-horizon DNS 에서는 두 판정이 다를 수 있다(가능성).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L162: 429 의 `Retry-After` 를 무시하고 고정 백오프로 재시도한다. L166 은 `httpx.HTTPError` 만 잡으므로 `httpx.InvalidURL`(HTTPError 하위가 아님) 등은 백그라운드 Task 밖으로 새어 "Task exception was never retrieved" 로그만 남는다(푸시 URL 은 등록 시 http(s) 검사를 거치므로 드묾). 최소 수정: `except Exception` 로 로그 후 False.
- [위험:낮음] L174 `timeout or settings.upstream_timeout` — `timeout=0` 이 기본값으로 바뀐다(1차 이월, 실제 호출부는 양수만). L175 `event_hooks` 를 `**kw` 로 넘기면 `TypeError`(1차 이월).
- 팩토리가 매번 새 연결 풀 — keep-alive 재사용이 없어 호출마다 TCP/TLS 핸드셰이크 + 가드 DNS(캐시 미스 시)가 생긴다(1차 이월, 성능).

```python path=app/core/http.py lines=183-214
class StreamingASGITransport(httpx.AsyncBaseTransport):
    """응답 본문을 도착하는 대로 흘리는 in-process ASGI 전송.

    httpx.ASGITransport 는 앱이 응답을 끝낼 때까지 모은 뒤 돌려주므로, SSE 응답 도중 서버가 클라이언트에게 요청을 보내고
    답을 기다리는 흐름(MCP sampling·elicitation)이 교착된다. 시험·in-process 마운트에서 이 전송을 쓴다.
    """

    def __init__(self, app, client: tuple[str, int] = ("127.0.0.1", 50000)) -> None:
        self.app = app
        self.client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import asyncio
        body = await request.aread()
        url = request.url
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": request.method,
            "scheme": url.scheme, "path": url.path, "raw_path": url.raw_path.split(b"?")[0],
            "query_string": url.query, "root_path": "", "client": self.client,
            "server": (url.host, url.port or (443 if url.scheme == "https" else 80)),
            "headers": [(k.lower(), v) for k, v in request.headers.raw],
        }
        disconnected = asyncio.Event()
        sent_body = False

        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}
```
**분석 / Analysis (Level 2)**
- L183-188: 존재 이유 — `httpx.ASGITransport` 는 본문을 모아서 돌려주므로 SSE 도중 역요청(sampling·elicitation)이 교착한다. 그래서 청크를 즉시 흘리는 전송을 직접 구현했다.
- L190-192: `client` 기본 `127.0.0.1` — 앱은 in-process 호출을 루프백 클라이언트로 본다. IP 기반 정책(로그인 허용 CIDR 등)을 시험할 때 이 값이 결과를 바꾼다는 점이 흔한 함정이다.
- L198-204: httpx Request → ASGI scope. `raw_path` 에서 쿼리를 떼고 `query_string` 은 bytes 로 따로 준다.
- L208-214 `receive`: 본문을 한 번 주고, 이후에는 클라이언트가 스트림을 닫을 때(`disconnected`)까지 기다렸다가 `http.disconnect` — 앱이 연결 종료를 감지할 수 있게.
**짚어볼 점 / Watch-outs**
- L195 함수 안 `import asyncio` 중복(모듈 L2 에 이미 있음, 1차 이월 — 삭제 후보).

```python path=app/core/http.py lines=216-241
        chunks: asyncio.Queue = asyncio.Queue()
        start: dict = {}
        started = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.start":
                start.update(status=message["status"], headers=message.get("headers", []))
                started.set()
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    await chunks.put(message["body"])
                if not message.get("more_body", False):
                    await chunks.put(None)

        async def run():
            try:
                await self.app(scope, receive, send)
            except Exception as exc:  # noqa: BLE001 — 시작 전 오류는 500 으로
                if not started.is_set():
                    start.update(status=500, headers=[], error=exc)
                    started.set()
            finally:
                await chunks.put(None)

        task = asyncio.create_task(run())
        await started.wait()
```
**분석 / Analysis (Level 2)**
- L216-228 `send`: 응답 시작(상태·헤더) 기록 + 이벤트, 본문 청크는 무제한 큐에 넣고 `more_body=False` 면 종료 표시(None).
- L230-238 `run`: 앱 실행. 응답 시작 **전** 예외면 500 으로 시작 처리, 어떤 경우든 마지막에 None 을 넣어 소비자가 끝나게 한다.
- L240-241: 앱을 백그라운드 Task 로 돌리고 응답 시작까지만 기다린다. 앱이 `http.response.start` 를 보내지 않고 영원히 대기하면 L241 도 영원히 기다린다(시험 전용이라 수용).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L233-236 (1차 이월): 응답 **시작 후** 예외는 아무 표시 없이 삼켜지고, 클라이언트는 잘린 본문을 정상 종료로 받는다(디버깅 난이도↑). 최소 수정: `logging.exception` 한 줄.
- L235 `error=exc` 는 저장만 하고 어디서도 읽지 않는다(1차 INDEX §4.4 삭제 후보 — 미해결).

```python path=app/core/http.py lines=243-256
        class _Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                while True:
                    chunk = await chunks.get()
                    if chunk is None:
                        break
                    yield chunk

            async def aclose(self):
                disconnected.set()
                if not task.done():
                    await asyncio.wait({task}, timeout=5)

        return httpx.Response(start["status"], headers=start["headers"], stream=_Stream(), request=request)
```
**분석 / Analysis (Level 2)**
- L243-249: httpx 가 본문을 읽을 때 큐에서 꺼내 흘린다. 첫 None 에서 종료 — `send` 와 `run` 이 None 을 두 번 넣을 수 있지만 첫 번째에서 멈추므로 무해하다.
- L251-254 `aclose`: disconnect 를 알리고 최대 5초 앱 종료를 기다린다.
- L256: 스트리밍 응답 반환.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L254 (1차 이월): 5초 뒤에도 끝나지 않은 Task 를 취소하지 않는다 — 시험 루프에 남아 "Task was destroyed but it is pending" 경고·교차 시험 간섭의 원인이 될 수 있다. 최소 수정: 대기 후 `task.cancel()`.

```python path=app/core/http.py lines=259-273
class SyncASGITransport(httpx.BaseTransport):
    """동기 클라이언트 → 같은 프로세스 ASGI 앱 (시험용). 호출 스레드(스레드풀)가 앱 이벤트 루프에 요청을 넘기고 기다린다 —
    루프 스레드에서 부르면 교착되므로 동기 엔드포인트(스레드풀)에서만 쓴다."""

    def __init__(self, app, loop_getter):
        self.app, self.loop_getter = app, loop_getter

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        req = httpx.Request(request.method, request.url, headers=request.headers, content=request.read())

        async def call():
            res = await httpx.ASGITransport(app=self.app).handle_async_request(req)
            return res.status_code, res.headers, await res.aread()
        status, headers, body = asyncio.run_coroutine_threadsafe(call(), self.loop_getter()).result(timeout=60)
        return httpx.Response(status, headers=headers, content=body, request=request)
```
**분석 / Analysis (Level 2)**
- L259-264: 동기 httpx 클라이언트(예: `app/core/auth.py:131` JWKS 조회, `app/services/policy.py:196` OPA)가 같은 프로세스 앱을 부를 때 쓰는 전송. 루프는 지연 조회(`test/conftest.py:74` 가 `orchestrator.LOOP` 를 넘김).
- L272: 스레드풀 스레드가 앱 루프에 코루틴을 넘기고 최대 60초 기다린다.
**짚어볼 점 / Watch-outs**
- (1차 이월) 루프 스레드에서 호출하면 자기 자신을 기다려 교착 → 60초 타임아웃. 2차에서 추가된 `internal_url`(L117)처럼 비동기 경로에서 동기 I/O 를 부르는 코드가 늘고 있어 같은 계열의 실수가 재발하기 쉽다.

## 파일 평가 / File Assessment
- 강점: (1) 모든 팩토리 호출에 egress 가드·traceparent 를 일관 적용하고, `EgressBlocked` 를 `TransportError` 하위로 만들어 기존 오류 처리에 합류. (2) 다중 레코드·IPv4-mapped·zone id·끝 점·대소문자 우회를 고려했고, 콜백 URL 용 엄격 검사(`internal_url`)를 별도 함수로 분리해 형식 오류에는 fail closed. (3) `post_with_retry` 가 차단 대상은 즉시 포기·전송 오류만 백오프 재시도하는 정확한 예외 순서.
- 위험: (1) [위험:중간] 콜백 URL 엄격 검사가 등록 시점에만 있고 전달(`post_with_retry`)은 기본 가드만 — DNS 변경·지연 해석으로 사설망 POST (L102-120·L156-170). (2) [위험:중간] DNS 재바인딩 TOCTOU·60초 판정 캐시(1차 이월) + 문서가 가정한 egress NetworkPolicy 미배포 (L56-73, `deploy/helm/agentnexus/templates/networkpolicy.yaml:14`). (3) [위험:중간] Alibaba IMDS(100.100.100.200)·100.64/10 미차단 (L36-37·L99). 그 밖에 [위험:낮음] 비동기 경로의 동기 DNS(L117), 운영 마운트 무가드(L16-26), 스트리밍 전송 잔재(L235·L254).
- 개선 권고(영향순): (1) 콜백 전용 엄격 클라이언트(매 요청 `internal_url` + 해석 IP 고정 연결)로 `post_with_retry` 전환, 해석 실패는 거부. (2) 차트에 메타데이터·공유 주소 대역을 `except` 한 egress NetworkPolicy 기본 포함, 또는 IP 고정 transport. (3) `_BLOCKED_NETS` 에 `100.100.100.200/32`, `_internal_ip` 를 `not is_global` 로. (4) `push_url_problem` 호출을 스레드풀로, `register_mount` 를 시험 환경 전용으로.
- 파일 위험 등급 / Risk grade: 높음 — 보안 경계 핵심 모듈(모든 아웃바운드의 SSRF 가드)에 미해결 [위험:중간] 3건.
- 재읽기 권고 레벨: 2 — 가드 로직은 짧지만 DNS·라이브러리 동작(재해석·trust_env·리다이렉트)에 대한 전제가 많아 줄 단위로 다시 읽을 가치가 있다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| L1-31 Watch-outs: 운영에서도 마운트가 매번 평가되고 마운트 origin 은 가드 통과 — 시험 전용 가드 없음 | 미해결 | `app/core/http.py:16-26`·`:60-61`·`:174` — 신규 `internal_url` 도 마운트 통과(`:113-114`) |
| L34-53 [위험:중간] 가드 범위가 링크 로컬·IMDS 로 좁아 내부 서비스 SSRF 잔존 | 부분 해결 | 푸시 URL 만 `internal_url` 로 사설·루프백 거부(`:102-120`, `app/services/a2a_gateway.py:639-646`). OpenAPI fetch(`app/api/mcp.py:557`)·카드(`app/services/a2a_gateway.py:90`)·hosted base_url(`app/services/mcp_gateway.py:574`)·푸시 **전달**(`:156-170`)은 여전히 기본 가드 |
| L34-53 루프백(127/8·::1) 허용 | 부분 해결 | 푸시 등록 시에만 거부(`:99`·`:115`), 기본 가드는 그대로 허용(`:36-37`) |
| L56-106 [위험:중간 · TOCTOU] DNS 재바인딩·60초 판정 캐시 | 미해결 | `:56-73`·`:128-137` 동일. 39차가 위험 수용(`test/impl/39th/scenario.md:120`)했으나 전제한 egress NetworkPolicy 가 차트에 없음(`deploy/helm/agentnexus/templates/networkpolicy.yaml:14,28,48`) |
| L69 해석 실패(빈 목록) False 캐시 — 무해 | 미해결(의도, 기본 가드에서 무해) | `:69`. 단 같은 규칙이 콜백 검사에도 쓰여(`:119-120`) 그쪽에서는 위험으로 바뀜 |
| L85 동기 `getaddrinfo` 가 비동기 코드에서 루프를 막음 | 미해결(악화) | `:85` 그대로 + 신규 `internal_url` 이 async 경로에서 직접 호출됨(`:117-118`, `app/services/a2a_gateway.py:568`, `app/services/a2a_server.py:283`) |
| 리다이렉트: 따르는 호출은 홉별 훅 재실행에 의존 | 해결(해당 경로 없음 확인) | 앱 내 `follow_redirects=True` 없음(`app/cli/nexus.py:159` 만 False), `post_with_retry` 는 3xx 를 실패 처리(`:162-163`) |
| L126 `timeout or` — 0 이 기본값으로 | 미해결 | `:174`·`:179` |
| 팩토리가 매번 새 연결 풀 — keep-alive 없음 | 미해결 | `:173-180`, `post_with_retry` 도 시도마다 새 클라이언트(`:160`) |
| `event_hooks` 를 `**kw` 로 넘기면 TypeError | 미해결 | `:175`·`:180` |
| L147 함수 내 `import asyncio` 중복 | 미해결 | `:195` |
| [위험:낮음] L185-188 시작 후 예외 조용히 삼킴 | 미해결 | `:233-238` |
| L187 `error=exc` 미사용(INDEX §4.4) | 미해결 | `:235` |
| L206 대기 후 미종료 Task 미취소 | 미해결 | `:254` |
| SyncASGITransport 루프 스레드 호출 시 교착 | 미해결(설계상 제약) | `:259-273` |
| 1차 개선 권고 (2) 해석 IP 직접 연결 transport | 미해결 | 커스텀 transport 없음(`:173-180`) |
