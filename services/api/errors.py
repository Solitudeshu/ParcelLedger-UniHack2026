from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, **details):
        self.code, self.message, self.status, self.details = code, message, status, details


def response(request: Request, code: str, message: str, status: int, **details):
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": status >= 500,
                "correlation_id": getattr(request.state, "correlation_id", str(uuid4())),
                **details,
            }
        },
    )


def install_handlers(app):
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return response(request, exc.code, exc.message, exc.status, **exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Never echo request bodies, passwords, tokens or submitted signatures in errors.
        fields = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
        return response(
            request, "VALIDATION_ERROR", "Thông tin chưa đúng định dạng.", 422, fields=fields
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError):
        return response(request, "CONFLICT", "Dữ liệu đã tồn tại hoặc đã được cập nhật.", 409)
