class DomainError(Exception):
    """Base for domain errors mapped to HTTP status codes in main.py."""


class ValidationError(DomainError):
    """Invalid input — mapped to HTTP 422."""


class NotFoundError(DomainError):
    """Missing aggregate — mapped to HTTP 404."""


class ConflictError(DomainError):
    """Uniqueness / state conflict — mapped to HTTP 409."""


class UpstreamError(DomainError):
    """External fetch failed / returned an unexpected status."""
