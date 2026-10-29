"""统一错误格式（约定 2.3）。"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("pwb")

STATUS_CODES = {400: "invalid_params", 401: "unauthorized", 404: "not_found", 409: "conflict",
                503: "unavailable", 500: "internal"}


class ApiError(Exception):
    def __init__(self, status, message, details=None):
        super().__init__(message)
        self.status, self.message, self.details = status, message, details


def not_found(message):
    return ApiError(404, message)


def invalid(message, details=None):
    return ApiError(400, message, details)


def error_response(status, message, details=None):
    body = {"kind": "error", "schemaVersion": 1,
            "error": {"code": STATUS_CODES.get(status, "invalid_params" if status < 500 else "internal"),
                      "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status)


def install(app: FastAPI):
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(exc.status, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return error_response(400, "参数不合法", [str(e.get("msg")) + " @ " + ".".join(map(str, e.get("loc", ()))) for e in exc.errors()])

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        status = 400 if exc.status_code == 405 else exc.status_code
        message = "接口不存在：" + request.url.path if exc.status_code == 404 else str(exc.detail)
        return error_response(status, message)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("未处理的错误：%s %s", request.method, request.url.path)
        return error_response(500, "后端内部错误：{}".format(type(exc).__name__))
