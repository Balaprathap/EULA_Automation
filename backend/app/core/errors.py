"""Consistent API error envelope and the application exception hierarchy."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base error. Every subclass maps to one HTTP status and one machine code."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}
        super().__init__(self.message)

    def envelope(self, request_id: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
                "request_id": request_id,
            }
        }
        if self.details:
            body["error"]["details"] = self.details
        return body


class ValidationFailed(AppError):
    status_code = 400
    code = "INVALID_REQUEST"
    message = "The request was invalid."


class Unauthenticated(AppError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"
    message = "Authentication is required."


class Forbidden(AppError):
    status_code = 403
    code = "ACCESS_DENIED"
    message = "You do not have access to this resource."


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "The requested resource was not found."


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "The resource is in a conflicting state."


class FileTooLarge(AppError):
    status_code = 413
    code = "FILE_TOO_LARGE"
    message = "The uploaded file exceeds the maximum allowed size."


class UnsupportedMediaType(AppError):
    status_code = 415
    code = "UNSUPPORTED_FILE_TYPE"
    message = "Only PDF, DOCX, and TXT files are supported."


class Unprocessable(AppError):
    status_code = 422
    code = "DOCUMENT_NOT_ANALYZABLE"
    message = "This document cannot be analyzed."


class ScannedDocument(Unprocessable):
    code = "SCANNED_PDF_UNSUPPORTED"
    message = (
        "This PDF appears to be a scan or image with no selectable text. "
        "Upload a PDF that contains selectable text, or paste the agreement text directly."
    )


class EncryptedDocument(Unprocessable):
    code = "ENCRYPTED_PDF_UNSUPPORTED"
    message = "This PDF is password protected. Remove the protection and upload it again."


class EmptyDocument(Unprocessable):
    code = "EMPTY_DOCUMENT"
    message = "No readable text was found in this document."


class RateLimited(AppError):
    status_code = 429
    code = "RATE_LIMIT_EXCEEDED"
    message = "Rate limit exceeded. Please retry later."

    def __init__(self, message: str | None = None, *, retry_after: int = 60) -> None:
        super().__init__(message, details={"retry_after_seconds": retry_after})
        self.retry_after = retry_after


class ProviderUnavailable(AppError):
    status_code = 503
    code = "PROVIDER_UNAVAILABLE"
    message = "The AI provider is temporarily unavailable. Please retry shortly."


class ProviderRateLimited(ProviderUnavailable):
    code = "PROVIDER_RATE_LIMITED"
    message = "The AI provider rate limit was reached. Please retry shortly."


class InvalidModelOutput(AppError):
    """Raised when the model returns output that fails schema validation twice."""

    status_code = 502
    code = "INVALID_MODEL_OUTPUT"
    message = "The model returned output that did not match the required schema."
