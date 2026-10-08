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


def problem(status: int, code: str, detail: str, title: str | None = None, headers: dict | None = None,
            extra: dict | None = None) -> JSONResponse:
    body = {"type": f"https://agentnexus.dev/problems/{code}", "title": title or code, "status": status,
            "detail": detail, "code": code, **(extra or {})}
    return JSONResponse(body, status_code=status, headers=headers, media_type="application/problem+json")


def _is_openai_path(path: str) -> bool:
    return path.startswith("/v1/chat/") or path == "/v1/models" or path.startswith("/v1/embeddings")


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
