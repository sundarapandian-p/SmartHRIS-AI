"""Application-level errors mapped to HTTP responses in handlers."""


class AppError(Exception):
    """Base error with a stable client-facing code and message."""

    def __init__(self, message: str, *, code: str = "app_error") -> None:
        self.message = message
        self.code = code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, code="not_found")


class ConflictError(AppError):
    def __init__(self, message: str = "Resource already exists") -> None:
        super().__init__(message, code="conflict")


class ValidationError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="validation_error")


class PayloadTooLargeError(AppError):
    def __init__(self, message: str = "Uploaded file exceeds the maximum allowed size") -> None:
        super().__init__(message, code="payload_too_large")


class StorageError(AppError):
    def __init__(self, message: str = "Failed to store uploaded file") -> None:
        super().__init__(message, code="storage_error")


class ServiceUnavailableError(AppError):
    def __init__(
        self,
        message: str = "Service temporarily unavailable",
        *,
        code: str = "service_unavailable",
    ) -> None:
        super().__init__(message, code=code)


class UnauthenticatedError(AppError):
    def __init__(
        self,
        message: str = "Invalid email or password",
        *,
        code: str = "unauthenticated",
    ) -> None:
        super().__init__(message, code=code)


class ForbiddenError(AppError):
    def __init__(
        self,
        message: str = "You do not have permission to perform this action",
        *,
        code: str = "forbidden",
    ) -> None:
        super().__init__(message, code=code)
