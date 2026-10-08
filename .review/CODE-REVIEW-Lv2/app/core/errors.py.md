> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [core_Abstract.md](core_Abstract.md)

### 15. app/core/errors.py
**파일 요약 / File Summary**
- 역할 / Role: 공통 예외 `ApiError` 와 생성 헬퍼, 예외 처리기 설치 — 관리 API 는 RFC 7807 `application/problem+json`, OpenAI 호환 경로는 `{"error": {...}}` 형식.
- 진입점 거리 / Distance: 1 (`app/main.py:29-30,124`)
- LOC: 75
- 주요 export: `ApiError`, `bad_request`, `unauthorized`, `forbidden`, `not_found`, `conflict`, `too_many`, `problem`, `install`
- 의존(내부): 없음
- 외부 패키지: fastapi, starlette
- 부수효과 / Side effects: `install(app)` 이 앱에 처리기 등록
- 동시성 / Concurrency: none
- 테스트 커버리지 흔적: `test/test_foundation.py`(공통 오류 — docstring), `test/test_contract.py`

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/errors.py lines=1-17
"""공통 오류 — 관리 API 는 RFC 7807 problem+json, 게이트웨이 경로는 프로토콜별 형식(OpenAI·JSON-RPC)."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str = "", *, title: str | None = None,
                 headers: dict | None = None, extra: dict | None = None):
        super().__init__(detail or code)
        self.status = status
        self.code = code
        self.detail = detail
        self.title = title or code.replace("_", " ")
        self.headers = headers or {}
        self.extra = extra or {}
```
**분석 / Analysis (Level 2)**
- L8-17 `ApiError`: HTTP 상태·기계 판독 코드·사람용 상세·제목·응답 헤더·추가 필드. `super().__init__(detail or code)` 로 `str(exc)` 가 의미 있는 문자열이 되게 한다. 키워드 전용 인자(`*`)로 위치 인자 실수를 막는다.
- L15 `title`: 기본값은 코드의 밑줄을 공백으로.

```python path=app/core/errors.py lines=20-41
def bad_request(detail: str, code: str = "bad_request", **extra) -> ApiError:
    return ApiError(400, code, detail, extra=extra)


def unauthorized(detail: str = "인증이 필요합니다.", headers: dict | None = None) -> ApiError:
    return ApiError(401, "unauthorized", detail, headers=headers or {"WWW-Authenticate": "Bearer"})


def forbidden(detail: str = "권한이 없습니다.", code: str = "forbidden", **extra) -> ApiError:
    return ApiError(403, code, detail, extra=extra)


def not_found(what: str = "resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} 을(를) 찾을 수 없습니다.")


def conflict(detail: str, code: str = "conflict") -> ApiError:
    return ApiError(409, code, detail)


def too_many(detail: str, retry_after: int = 1, **extra) -> ApiError:
    return ApiError(429, "rate_limited", detail, headers={"Retry-After": str(retry_after)}, extra=extra)
```
**분석 / Analysis (Level 2)**
- L20-41: 자주 쓰는 상태별 팩토리. `unauthorized` 는 기본 `WWW-Authenticate: Bearer` 헤더(RFC 6750), `too_many` 는 `Retry-After`.
**짚어볼 점 / Watch-outs**
- L32-33 `not_found(what)` 의 메시지에 리소스 종류를 넣는다 — 존재 여부를 숨겨야 하는 곳(남의 Task 등)은 일부러 같은 404 를 쓰고 있어(`app/api/a2a.py:658-659`) 열거 공격에 일관된 응답을 준다(좋음).

```python path=app/core/errors.py lines=44-52
def problem(status: int, code: str, detail: str, title: str | None = None, headers: dict | None = None,
            extra: dict | None = None) -> JSONResponse:
    body = {"type": f"https://agentnexus.dev/problems/{code}", "title": title or code, "status": status,
            "detail": detail, "code": code, **(extra or {})}
    return JSONResponse(body, status_code=status, headers=headers, media_type="application/problem+json")


def _is_openai_path(path: str) -> bool:
    return path.startswith("/v1/chat/") or path == "/v1/models" or path.startswith("/v1/embeddings")
```
**분석 / Analysis (Level 2)**
- L44-48 `problem`: RFC 7807 본문(`type`·`title`·`status`·`detail`) + 확장 `code`·추가 필드. `type` URI 는 문서용 가상 도메인.
- L51-52: OpenAI 형식으로 응답할 경로 판별 — `/v1/chat/*`·`/v1/models`·`/v1/embeddings*`.
**짚어볼 점 / Watch-outs**
- L47 `**(extra or {})` 가 표준 필드(`status`·`detail`)와 같은 키를 가지면 덮어쓴다 — 호출부가 `extra={"status": ...}` 를 넘기지 않도록 주의.

```python path=app/core/errors.py lines=55-75
def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        if _is_openai_path(request.url.path):
            return JSONResponse({"error": {"message": exc.detail, "type": exc.code, "code": exc.code, **exc.extra}},
                                status_code=exc.status, headers=exc.headers)
        return problem(exc.status, exc.code, exc.detail, exc.title, exc.headers, exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized", 403: "forbidden"}.get(
            exc.status_code, "http_error")
        return problem(exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        if _is_openai_path(request.url.path):
            return JSONResponse({"error": {"message": "invalid request", "type": "invalid_request_error",
                                           "code": "validation_error", "details": errors}}, status_code=422)
        return problem(422, "validation_error", "필수 항목 누락 또는 형식 오류입니다.", extra={"errors": errors})
```
**분석 / Analysis (Level 2)**
- L56-61: `ApiError` → 경로에 따라 OpenAI 형식 또는 problem+json.
- L63-67: Starlette HTTPException(404·405 등 라우팅 오류)도 problem+json 으로.
- L69-75: pydantic 검증 오류(422) — 위치·메시지·타입만 추려 노출(원본 입력값은 싣지 않음 — 시크릿 반사 방지).
**짚어볼 점 / Watch-outs**
- [위험:낮음] 일반 `Exception` 처리기가 없다 — 처리되지 않은 예외는 Starlette 기본 `500 Internal Server Error`(text/plain)로 나간다. 관리 API 클라이언트(`web/src/lib/api.ts:54-65`)는 JSON 파싱 실패를 무시하고 statusText 로 대체하므로 화면은 유지되지만, JSON-RPC·OpenAI 클라이언트는 형식 계약이 깨진다. `@app.exception_handler(Exception)` 에서 trace id 와 함께 problem+json(상세 숨김)으로 바꾸는 것을 권장.
- L74 OpenAI 경로 검증 오류는 `status_code=422` — OpenAI 실제 API 는 400 을 쓴다. SDK 호환성에 영향(재시도 판단 등)이 있을 수 있다.

## 파일 평가 / File Assessment
- 강점: (1) 오류 형식이 경로별 프로토콜 계약(problem+json·OpenAI)을 따른다. (2) 검증 오류에서 입력값을 반사하지 않는다. (3) 팩토리로 일관된 코드 체계.
- 위험: (1) 일반 예외 처리기 부재(비정형 500). (2) OpenAI 경로 검증 오류 상태 코드 차이.
- 개선 권고(영향순): (1) 최상위 `Exception` 처리기 + trace id. (2) OpenAI 경로 400 으로 맞춤. (3) `extra` 가 표준 키를 덮지 않게 순서 변경.
- 재읽기 권고 레벨: 4 — 작고 안정적인 모듈.
