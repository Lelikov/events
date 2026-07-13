class DomainError(Exception):
    """Base for domain errors mapped to HTTP status codes in main.py."""


class ValidationError(DomainError):
    """Invalid input — HTTP 422."""


class NotFoundError(DomainError):
    """Missing resource (e.g. event type) — HTTP 404."""


class ConflictError(DomainError):
    """Uniqueness/state conflict from an upstream — HTTP 409 (usually handled internally)."""


class SlotUnavailableError(DomainError):
    """Requested slot is no longer bookable — HTTP 409."""


class UpstreamError(DomainError):
    """An upstream service failed or returned an unexpected status — HTTP 502."""
