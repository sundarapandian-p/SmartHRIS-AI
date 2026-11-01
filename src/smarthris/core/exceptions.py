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
