"""FastAPI exception handlers."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from smarthris.core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    StorageError,
    ValidationError,
)


def register_exception_handlers(app: FastAPI) -> None:
    """Map domain errors to HTTP responses without leaking DB internals."""

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(ConflictError)
    async def conflict_handler(_request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(PayloadTooLargeError)
    async def payload_too_large_handler(
        _request: Request,
        exc: PayloadTooLargeError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(StorageError)
    async def storage_error_handler(_request: Request, exc: StorageError) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(ValidationError)
    async def validation_handler(_request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(ServiceUnavailableError)
    async def service_unavailable_handler(
        _request: Request,
        exc: ServiceUnavailableError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(_request: Request, _exc: IntegrityError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "A record with the same unique value already exists",
                "code": "conflict",
            },
        )
