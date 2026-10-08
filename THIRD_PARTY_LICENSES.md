# THIRD_PARTY_LICENSES

> 자동 생성 — `make licenses` (prod 이미지의 설치 메타데이터 + 콘솔 prod 의존성). 직접 고치지 말고 다시 생성한다.
> 저장소 자체 라이선스는 `LICENSE`(Apache-2.0), 고지는 `NOTICE`.

## 검토 대상 (카피레프트·소스 공개형)

| 패키지 | 버전 | 라이선스 | 구분 | 판정 |
|---|---|---|---|---|
| certifi | 2026.7.22 | MPL-2.0 | python | MPL-2.0 — 파일 단위 카피레프트, 수정 없이 사용(CA 번들 데이터). 수정 시 해당 파일 소스 공개 |
| psycopg | 3.3.6 | LGPL-3.0-only | python | LGPL-3.0-only — 수정 없이 동적 연결(파이썬 import)로만 사용, 교체 가능(SQLAlchemy 드라이버). 배포 시 라이선스 사본·출처 고지 |
| psycopg-binary | 3.3.6 | LGPL-3.0-only | python | LGPL-3.0-only — psycopg 과 같음(빌드된 libpq·OpenSSL 동봉 휠) |

## Python 런타임 (109)

| 패키지 | 버전 | 라이선스 |
|---|---|---|
| a2a-sdk | 1.2.2 | Apache-2.0 |
| aiofile | 3.12.3 | Apache-2.0 |
| aiologic | 0.17.1 | ISC License (ISCL) |
| alembic | 1.20.0 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| attrs | 26.1.0 | MIT |
| Authlib | 1.8.0 | BSD-3-Clause |
| beartype | 0.22.9 | MIT License |
| cachetools | 7.2.1 | MIT |
| caio | 0.12.9 | Apache-2.0 |
| certifi | 2026.7.22 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| culsans | 0.11.0 | ISC License (ISCL) |
| cyclopts | 5.1.1 | Apache-2.0 |
| dnspython | 2.8.0 | ISC |
| docstring_parser | 0.18.0 | MIT |
| email-validator | 2.3.0 | Unlicense |
| exceptiongroup | 1.3.1 | MIT License |
| fastapi | 0.142.2 | MIT |
| fastmcp | 4.0.11 | Apache-2.0 |
| fastmcp-slim | 4.0.11 | Apache-2.0 |
| google-api-core | 2.41.0 | Apache 2.0 |
| google-auth | 2.60.0 | Apache 2.0 |
| googleapis-common-protos | 1.75.5 | Apache-2.0 |
| griffelib | 2.3.0 | ISC |
| grpcio | 1.84.0 | Apache-2.0 |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.13.1 | BSD-3-Clause |
| httptools | 0.8.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| httpx2 | 2.13.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| jaraco.classes | 3.4.0 | MIT License |
| jaraco.context | 6.1.2 | MIT |
| jaraco.functools | 4.6.0 | MIT |
| jeepney | 0.9.0 | MIT |
| joserfc | 1.7.5 | BSD-3-Clause |
| json-rpc | 1.15.0 | MIT |
| jsonref | 1.1.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-path | 0.5.0 | Apache-2.0 |
| jsonschema-specifications | 2025.9.1 | MIT |
| keyring | 25.7.0 | MIT |
| Mako | 1.4.3 | MIT |
| markdown-it-py | 4.2.0 | MIT License |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| mcp | 2.3.0 | MIT |
| mcp-types | 2.3.0 | MIT |
| mdurl | 0.1.2 | MIT License |
| more-itertools | 11.1.0 | MIT |
| openapi-pydantic | 0.6.0 | MIT |
| opentelemetry-api | 1.45.0 | Apache-2.0 |
| opentelemetry-exporter-http-transport | 0.66b0 | Apache-2.0 |
| opentelemetry-exporter-otlp-common | 0.66b0 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-common | 1.45.0 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-http | 1.45.0 | Apache-2.0 |
| opentelemetry-proto | 1.45.0 | Apache-2.0 |
| opentelemetry-sdk | 1.45.0 | Apache-2.0 |
| opentelemetry-semantic-conventions | 0.66b0 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pathable | 0.6.0 | Apache-2.0 |
| platformdirs | 4.12.3 | MIT |
| prometheus_client | 0.26.0 | Apache-2.0 AND BSD-2-Clause |
| proto-plus | 1.29.0 | Apache 2.0 |
| protobuf | 7.36.2 | 3-Clause BSD License |
| psycopg | 3.3.6 | LGPL-3.0-only |
| psycopg-binary | 3.3.6 | LGPL-3.0-only |
| py-key-value-aio | 0.4.6 | Apache-2.0 |
| pyasn1 | 0.6.4 | BSD-2-Clause |
| pyasn1_modules | 0.4.2 | BSD |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| pydantic_core | 2.46.5 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| PyJWT | 2.15.1 | MIT |
| pyperclip | 1.11.0 | BSD |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT |
| redis | 8.1.0 | MIT |
| referencing | 0.37.0 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| rich | 15.0.0 | MIT |
| rich-rst | 2.2.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| SecretStorage | 3.5.0 | BSD-3-Clause |
| shellingham | 1.5.4 | ISC License |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| SQLAlchemy | 2.1.3 | MIT |
| sse-starlette | 3.5.0 | BSD-3-Clause |
| starlette | 1.7.0 | BSD-3-Clause |
| truststore | 0.10.4 | MIT |
| typer | 0.27.2 | MIT |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uncalled-for | 0.4.0 | MIT License |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| uvloop | 0.23.0 | MIT License |
| watchfiles | 1.3.0 | MIT |
| websockets | 17.2 | BSD-3-Clause |
| wrapt | 2.5.0 | BSD-2-Clause |

## 콘솔 JS (prod, 95)

| 패키지 | 버전 | 라이선스 |
|---|---|---|
| @radix-ui/primitive | 1.1.7 | MIT |
| @radix-ui/react-compose-refs | 1.1.5 | MIT |
| @radix-ui/react-context | 1.2.2 | MIT |
| @radix-ui/react-dialog | 1.2.0 | MIT |
| @radix-ui/react-dismissable-layer | 1.1.20 | MIT |
| @radix-ui/react-focus-guards | 1.1.6 | MIT |
| @radix-ui/react-focus-scope | 1.2.0 | MIT |
| @radix-ui/react-id | 1.1.4 | MIT |
| @radix-ui/react-portal | 1.1.18 | MIT |
| @radix-ui/react-presence | 1.1.11 | MIT |
| @radix-ui/react-primitive | 2.1.11 | MIT |
| @radix-ui/react-slot | 1.4.0 | MIT |
| @radix-ui/react-use-callback-ref | 1.1.4 | MIT |
| @radix-ui/react-use-controllable-state | 1.2.6 | MIT |
| @radix-ui/react-use-effect-event | 0.0.5 | MIT |
| @radix-ui/react-use-layout-effect | 1.1.4 | MIT |
| @reduxjs/toolkit | 2.13.0 | MIT |
| @standard-schema/spec | 1.1.0 | MIT |
| @standard-schema/utils | 0.3.0 | MIT |
| @tanstack/query-core | 5.104.1 | MIT |
| @tanstack/react-query | 5.104.1 | MIT |
| @tanstack/react-table | 8.21.3 | MIT |
| @tanstack/react-virtual | 3.14.13 | MIT |
| @tanstack/table-core | 8.21.3 | MIT |
| @tanstack/virtual-core | 3.17.11 | MIT |
| @types/d3-array | 3.2.2 | MIT |
| @types/d3-color | 3.1.3 | MIT |
| @types/d3-drag | 3.0.7 | MIT |
| @types/d3-ease | 3.0.2 | MIT |
| @types/d3-interpolate | 3.0.4 | MIT |
| @types/d3-path | 3.1.1 | MIT |
| @types/d3-scale | 4.0.9 | MIT |
| @types/d3-selection | 3.0.12 | MIT |
| @types/d3-shape | 3.2.0 | MIT |
| @types/d3-time | 3.0.4 | MIT |
| @types/d3-timer | 3.0.2 | MIT |
| @types/d3-transition | 3.0.9 | MIT |
| @types/d3-zoom | 3.0.9 | MIT |
| @types/react | 19.3.0 | MIT |
| @types/react-dom | 19.3.0 | MIT |
| @types/use-sync-external-store | 0.0.6 | MIT |
| @xyflow/react | 12.12.0 | MIT |
| @xyflow/system | 0.0.83 | MIT |
| aria-hidden | 1.2.6 | MIT |
| classcat | 5.0.5 | MIT |
| clsx | 2.1.1 | MIT |
| cmdk | 1.1.1 | MIT |
| cookie | 1.1.1 | MIT |
| csstype | 3.2.3 | MIT |
| d3-array | 3.2.4 | ISC |
| d3-color | 3.1.0 | ISC |
| d3-dispatch | 3.0.1 | ISC |
| d3-drag | 3.0.0 | ISC |
| d3-ease | 3.0.1 | BSD-3-Clause |
| d3-format | 3.1.2 | ISC |
| d3-interpolate | 3.0.1 | ISC |
| d3-path | 3.1.0 | ISC |
| d3-scale | 4.0.2 | ISC |
| d3-selection | 3.0.0 | ISC |
| d3-shape | 3.2.0 | ISC |
| d3-time | 3.1.0 | ISC |
| d3-time-format | 4.1.0 | ISC |
| d3-timer | 3.0.1 | ISC |
| d3-transition | 3.0.1 | ISC |
| d3-zoom | 3.0.0 | ISC |
| decimal.js-light | 2.5.1 | MIT |
| detect-node-es | 1.1.0 | MIT |
| es-toolkit | 1.52.0 | MIT |
| eventemitter3 | 5.0.4 | MIT |
| get-nonce | 1.0.1 | MIT |
| immer | 11.1.21 | MIT |
| internmap | 2.0.3 | ISC |
| pretendard | 1.3.9 | OFL-1.1 |
| react | 19.3.0 | MIT |
| react-dom | 19.3.0 | MIT |
| react-is | 17.0.2 | MIT |
| react-redux | 9.3.0 | MIT |
| react-remove-scroll | 2.7.2 | MIT |
| react-remove-scroll-bar | 2.3.8 | MIT |
| react-router | 7.18.4 | MIT |
| react-style-singleton | 2.2.3 | MIT |
| recharts | 3.10.1 | MIT |
| redux | 5.0.1 | MIT |
| redux-thunk | 3.1.0 | MIT |
| reselect | 5.2.0 | MIT |
| scheduler | 0.28.0 | MIT |
| set-cookie-parser | 2.7.2 | MIT |
| tiny-invariant | 1.3.3 | MIT |
| tslib | 2.8.1 | 0BSD |
| use-callback-ref | 1.3.3 | MIT |
| use-sidecar | 1.1.3 | MIT |
| use-sync-external-store | 1.7.0 | MIT |
| victory-vendor | 37.3.6 | MIT AND ISC |
| yaml | 2.9.1 | ISC |
| zustand | 4.5.7 | MIT |

## 폰트

| 이름 | 라이선스 | 사본 |
|---|---|---|
| Pretendard | OFL-1.1 | `web/public/licenses/Pretendard-OFL-1.1.txt` (콘솔 번들 `/licenses/` 에 포함) |
