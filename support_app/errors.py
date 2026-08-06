"""A single error shape for the whole API, plus the input validators.

Every failure -- validation, missing row, database outage -- leaves the API as::

    {"error": {"code": "...", "message": "...", "fields": {"title": "..."}}}

so the frontend has exactly one branch to render and can attach messages to the
field that caused them.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from . import config


class ApiError(Exception):
    """An error with an HTTP status and a machine-readable code."""

    status = 400
    code = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        fields: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        self.fields: dict[str, str] = dict(fields or {})

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.fields:
            payload["fields"] = self.fields
        return {"error": payload}


class ValidationError(ApiError):
    status = 422
    code = "validation_failed"

    def __init__(self, fields: Mapping[str, str], message: str | None = None) -> None:
        super().__init__(
            message or "Please correct the highlighted fields.", fields=fields
        )


class NotFoundError(ApiError):
    status = 404
    code = "not_found"


class ServiceUnavailableError(ApiError):
    status = 503
    code = "lakebase_unavailable"


# ---------------------------------------------------------------------------
# Field validators -- each collects into a shared ``errors`` dict so a single
# response can report every problem at once instead of one per round trip.
# ---------------------------------------------------------------------------
class FieldCollector:
    """Accumulates per-field messages and raises them together."""

    def __init__(self, payload: Mapping[str, Any] | None) -> None:
        if payload is None:
            raise ApiError(
                "A JSON request body is required.", code="missing_body"
            )
        if not isinstance(payload, Mapping):
            raise ApiError(
                "The request body must be a JSON object.", code="invalid_body"
            )
        self.payload = payload
        self.errors: dict[str, str] = {}

    # -- helpers ------------------------------------------------------------
    def _raw(self, field: str) -> Any:
        return self.payload.get(field)

    def has(self, field: str) -> bool:
        return field in self.payload

    def fail(self, field: str, message: str) -> None:
        self.errors.setdefault(field, message)

    def raise_if_invalid(self) -> None:
        if self.errors:
            raise ValidationError(self.errors)

    # -- typed reads --------------------------------------------------------
    def text(
        self,
        field: str,
        *,
        required: bool = True,
        min_len: int = 1,
        max_len: int = 1000,
        label: str | None = None,
        default: str | None = None,
    ) -> str | None:
        label = label or field.replace("_", " ")
        raw = self._raw(field)

        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if required:
                self.fail(field, f"{label.capitalize()} is required.")
                return None
            return default

        if not isinstance(raw, str):
            self.fail(field, f"{label.capitalize()} must be text.")
            return None

        value = raw.strip()
        if len(value) < min_len:
            self.fail(
                field,
                f"{label.capitalize()} must be at least {min_len} characters "
                f"(currently {len(value)}).",
            )
            return None
        if len(value) > max_len:
            self.fail(
                field,
                f"{label.capitalize()} must be {max_len} characters or fewer "
                f"(currently {len(value)}).",
            )
            return None
        return value

    def choice(
        self,
        field: str,
        allowed: Iterable[str],
        *,
        required: bool = False,
        default: str | None = None,
        label: str | None = None,
    ) -> str | None:
        allowed = tuple(allowed)
        label = label or field.replace("_", " ")
        raw = self._raw(field)

        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if required:
                self.fail(field, f"{label.capitalize()} is required.")
                return None
            return default

        value = str(raw).strip().lower()
        if value not in allowed:
            self.fail(
                field,
                f"{label.capitalize()} must be one of: {', '.join(allowed)}.",
            )
            return None
        return value


def parse_limit(raw: Any) -> int:
    if raw in (None, ""):
        return config.DEFAULT_PAGE_SIZE
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ApiError("limit must be a whole number.", code="invalid_query") from None
    return max(1, min(value, config.MAX_PAGE_SIZE))


def parse_offset(raw: Any) -> int:
    if raw in (None, ""):
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ApiError("offset must be a whole number.", code="invalid_query") from None
    return max(0, value)
