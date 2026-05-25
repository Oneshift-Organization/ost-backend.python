class DomainError(Exception):
    """Base for any business-level error. Mapped to HTTP at the API boundary."""


class ValidationError(DomainError):
    """Input violates domain invariants (HTTP 400/422)."""


class NotFoundError(DomainError):
    """Requested resource does not exist for the given user (HTTP 404)."""


class UnauthorizedError(DomainError):
    """Auth missing or invalid (HTTP 401)."""


class ForbiddenError(DomainError):
    """Caller authenticated but lacks permission for the resource (HTTP 403)."""


class ConflictError(DomainError):
    """Operation conflicts with current state (HTTP 409)."""


class ExternalServiceError(DomainError):
    """Upstream dependency failed (HTTP 502/503)."""


class IngestionError(DomainError):
    """Generic file-ingestion failure."""


class UnsupportedFormatError(IngestionError):
    """No loader/parser registered for the file content (HTTP 415)."""


class ProviderDetectionError(IngestionError):
    """File parsed but cloud provider could not be identified (HTTP 400)."""
