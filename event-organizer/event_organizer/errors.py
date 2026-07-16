class DomainError(Exception):
    """Base for domain errors mapped to HTTP status codes in main.py."""


class Unauthorized(DomainError):  # noqa: N818 - name fixed by the plan's error map, not an Error-suffixed exc
    """Bad/absent credentials or token — HTTP 401."""


class Forbidden(DomainError):  # noqa: N818 - name fixed by the plan's error map, not an Error-suffixed exc
    """Authenticated but not allowed — HTTP 403."""


class NotFoundError(DomainError):
    """Missing resource — HTTP 404."""


class ConflictError(DomainError):
    """Uniqueness/state conflict — HTTP 409."""


class ValidationError(DomainError):
    """Invalid input — HTTP 422."""


class UpstreamError(DomainError):
    """Upstream service failed/returned an unexpected status — HTTP 502."""
