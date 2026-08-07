"""HTTP surface: a JSON API plus the single page that consumes it.

The API is deliberately plain REST with a stable response envelope, because the
later boot camp projects point an AI agent at these same endpoints.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from flask import Blueprint, jsonify, render_template, request

from . import config, db, repository
from .errors import (
    ApiError,
    FieldCollector,
    ServiceUnavailableError,
    parse_limit,
    parse_offset,
)

log = logging.getLogger(__name__)

api = Blueprint("api", __name__, url_prefix="/api")
ui = Blueprint("ui", __name__)

#: Sentinel distinguishing "key absent" from "key present and null" in PATCH.
_MISSING = object()

#: Roles a client may set. ``system`` is written only by the app itself.
_CLIENT_ROLES = ("customer", "agent")

_IDENTITY_HEADERS = (
    "X-Forwarded-Email",
    "X-Forwarded-Preferred-Username",
    "X-Forwarded-User",
)


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------
def current_user() -> str:
    """The signed-in Databricks user, forwarded by Databricks Apps."""
    for header in _IDENTITY_HEADERS:
        value = (request.headers.get(header) or "").strip()
        if value:
            return value
    return config.DEFAULT_USER


def _body() -> FieldCollector:
    return FieldCollector(request.get_json(silent=True))


def _multi(name: str, allowed: Iterable[str]) -> list[str]:
    """Read a repeatable and/or comma-separated query parameter."""
    allowed = tuple(allowed)
    values: list[str] = []
    for raw in request.args.getlist(name):
        values.extend(part.strip().lower() for part in raw.split(",") if part.strip())

    unknown = [value for value in values if value not in allowed]
    if unknown:
        raise ApiError(
            f"Unknown {name} value(s): {', '.join(sorted(set(unknown)))}. "
            f"Allowed values: {', '.join(allowed)}.",
            code="invalid_query",
        )
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(values))


def _nullable_text(
    collector: FieldCollector, field: str, max_len: int, label: str | None = None
) -> Any:
    """Read an optional, explicitly-nullable text field for PATCH semantics."""
    if not collector.has(field):
        return _MISSING
    raw = collector.payload.get(field)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    return collector.text(
        field, required=True, min_len=1, max_len=max_len, label=label
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
@ui.get("/")
def index() -> str:
    return render_template(
        "index.html",
        brand_name=config.BRAND_NAME,
        brand_tagline=config.BRAND_TAGLINE,
        current_user=current_user(),
    )


@ui.get("/healthz")
def healthz():
    """Liveness probe -- intentionally does not touch the database."""
    return jsonify({"status": "ok", "app": config.BRAND_NAME})


# ---------------------------------------------------------------------------
# Metadata & diagnostics
# ---------------------------------------------------------------------------
@api.get("/meta")
def meta():
    """Domain vocabulary, so the UI never hard-codes what the DB constrains."""
    return jsonify(
        {
            "brand": {"name": config.BRAND_NAME, "tagline": config.BRAND_TAGLINE},
            "statuses": list(config.STATUSES),
            "priorities": list(config.PRIORITIES),
            "categories": list(config.CATEGORIES),
            "author_roles": list(_CLIENT_ROLES),
            "sort_options": list(config.SORT_OPTIONS),
            "default_sort": config.DEFAULT_SORT,
            "limits": {
                "title_min": config.TITLE_MIN,
                "title_max": config.TITLE_MAX,
                "message_max": config.MESSAGE_MAX,
                "description_max": config.DESCRIPTION_MAX,
                "page_size_max": config.MAX_PAGE_SIZE,
            },
            "current_user": current_user(),
        }
    )


@api.get("/health")
def health():
    """Readiness probe: reports actual Lakebase connectivity."""
    payload: dict[str, Any] = {
        "app": config.BRAND_NAME,
        "lakebase": db.target_summary(),
        "bootstrap": db.bootstrap_state(),
    }
    try:
        payload["database"] = db.probe()
        payload["status"] = "ok"
        return jsonify(payload)
    except Exception as exc:  # surfaced to the operator, not swallowed
        log.warning("Health probe failed: %s", exc)
        payload["status"] = "degraded"
        payload["detail"] = str(exc)
        # Carry the standard envelope too: this is the one route that returns a
        # useful body on failure, and the UI banner reads `error.message` to
        # tell the operator exactly which setting is missing.
        payload["error"] = {"code": "lakebase_unavailable", "message": str(exc)}
        return jsonify(payload), 503


@api.get("/stats")
def get_stats():
    return jsonify(repository.stats())


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
@api.get("/tickets")
def list_tickets():
    sort = (request.args.get("sort") or config.DEFAULT_SORT).strip().lower()
    if sort not in config.SORT_OPTIONS:
        raise ApiError(
            f"Unknown sort {sort!r}. Allowed values: "
            f"{', '.join(config.SORT_OPTIONS)}.",
            code="invalid_query",
        )

    search = (request.args.get("q") or "").strip()
    return jsonify(
        repository.list_tickets(
            statuses=_multi("status", config.STATUSES),
            priorities=_multi("priority", config.PRIORITIES),
            categories=_multi("category", config.CATEGORIES),
            search=search or None,
            assigned_to=(request.args.get("assigned_to") or "").strip() or None,
            created_by=(request.args.get("created_by") or "").strip() or None,
            sort=sort,
            limit=parse_limit(request.args.get("limit")),
            offset=parse_offset(request.args.get("offset")),
        )
    )


@api.post("/tickets")
def create_ticket():
    body = _body()
    actor = current_user()

    title = body.text(
        "title",
        required=True,
        min_len=config.TITLE_MIN,
        max_len=config.TITLE_MAX,
        label="title",
    )
    description = body.text(
        "description", required=False, max_len=config.DESCRIPTION_MAX
    )
    status = body.choice("status", config.STATUSES, default="open")
    priority = body.choice("priority", config.PRIORITIES, default="medium")
    category = body.choice("category", config.CATEGORIES, default="general")
    created_by = body.text("created_by", required=False, max_len=255) or actor
    assigned_to = body.text("assigned_to", required=False, max_len=255)
    first_message = body.text(
        "first_message", required=False, max_len=config.MESSAGE_MAX
    )
    author_role = body.choice("author_role", _CLIENT_ROLES, default="customer")
    body.raise_if_invalid()

    ticket = repository.create_ticket(
        title=title,
        description=description,
        status=status,
        priority=priority,
        category=category,
        created_by=created_by,
        assigned_to=assigned_to,
        first_message=first_message,
        author_role=author_role,
    )
    return jsonify(ticket), 201


@api.get("/tickets/<int:ticket_id>")
def get_ticket(ticket_id: int):
    return jsonify(repository.get_ticket(ticket_id))


@api.patch("/tickets/<int:ticket_id>")
def update_ticket(ticket_id: int):
    body = _body()
    changes: dict[str, Any] = {}

    if body.has("title"):
        changes["title"] = body.text(
            "title",
            required=True,
            min_len=config.TITLE_MIN,
            max_len=config.TITLE_MAX,
            label="title",
        )
    if body.has("status"):
        changes["status"] = body.choice("status", config.STATUSES, required=True)
    if body.has("priority"):
        changes["priority"] = body.choice("priority", config.PRIORITIES, required=True)
    if body.has("category"):
        changes["category"] = body.choice("category", config.CATEGORIES, required=True)

    description = _nullable_text(body, "description", config.DESCRIPTION_MAX)
    if description is not _MISSING:
        changes["description"] = description

    assigned_to = _nullable_text(body, "assigned_to", 255, label="assignee")
    if assigned_to is not _MISSING:
        changes["assigned_to"] = assigned_to

    body.raise_if_invalid()

    return jsonify(repository.update_ticket(ticket_id, changes, actor=current_user()))


@api.delete("/tickets/<int:ticket_id>")
def delete_ticket(ticket_id: int):
    result = repository.delete_ticket(ticket_id)
    log.info("Ticket %s deleted by %s", ticket_id, current_user())
    return jsonify({"deleted": result})


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
@api.get("/tickets/<int:ticket_id>/messages")
def list_messages(ticket_id: int):
    messages = repository.list_messages(ticket_id)
    return jsonify({"items": messages, "total": len(messages)})


@api.post("/tickets/<int:ticket_id>/messages")
def add_message(ticket_id: int):
    body = _body()
    actor = current_user()

    message_text = body.text(
        "message_text",
        required=True,
        min_len=config.MESSAGE_MIN,
        max_len=config.MESSAGE_MAX,
        label="message",
    )
    author = body.text("author", required=False, max_len=255) or actor
    author_role = body.choice("author_role", _CLIENT_ROLES, default="customer")
    body.raise_if_invalid()

    message = repository.add_message(
        ticket_id=ticket_id,
        message_text=message_text,
        author=author,
        author_role=author_role,
    )
    return jsonify(message), 201


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------
@api.post("/admin/bootstrap")
def run_bootstrap():
    """Re-apply the schema on demand (idempotent).

    Useful when the app was deployed before the Lakebase resource was attached.
    ``?seed=true`` also loads the demo rows when the table is empty.
    """
    force_seed = (request.args.get("seed") or "").strip().lower() in {"1", "true", "yes"}
    try:
        return jsonify(db.bootstrap(force_seed=force_seed))
    except db.LakebaseUnavailable as exc:
        raise ServiceUnavailableError(str(exc)) from exc
    except Exception as exc:
        # Deliberately broad and deliberately NOT re-raised: the generic error
        # handlers hide the underlying message, and this endpoint exists
        # precisely to show it. Whatever went wrong -- a missing GRANT, a
        # restricted statement, a driver type error -- the operator needs to
        # read it, not a sanitised summary.
        log.exception("Manual bootstrap failed")
        raise ApiError(
            f"The schema could not be applied: {getattr(exc, 'orig', exc)}",
            status=503,
            code="bootstrap_failed",
        ) from exc
