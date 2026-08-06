"""Application factory for the Nexus Support app.

Also the single place where failures are turned into the API error envelope, so
neither the routes nor the repository need try/except noise.
"""

from __future__ import annotations

import logging

from flask import Flask, jsonify, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.exceptions import HTTPException

from . import config
from .db import LakebaseUnavailable, ensure_ready
from .errors import ApiError

log = logging.getLogger(__name__)

__all__ = ["create_app"]

#: Constraint names from sql/001_schema.sql mapped to human wording. The schema
#: is the last line of defence; if it rejects a write, say why in plain English.
_CONSTRAINT_MESSAGES = {
    "tickets_title_len": ("title", "Title must be between 3 and 200 characters."),
    "tickets_status_enum": ("status", "That status is not a recognised value."),
    "tickets_prio_enum": ("priority", "That priority is not a recognised value."),
    "tickets_cat_enum": ("category", "That category is not a recognised value."),
    "tickets_created_by": ("created_by", "A ticket author is required."),
    "ticket_messages_text_len": (
        "message_text",
        "A message must be between 1 and 5000 characters.",
    ),
    "ticket_messages_author": ("author", "A message author is required."),
    "ticket_messages_role": ("author_role", "That author role is not recognised."),
    "ticket_messages_ticket_fk": (
        "ticket_id",
        "That ticket no longer exists -- it may have been deleted.",
    ),
}


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG if config.DEBUG else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
    )
    # SQLAlchemy pool chatter is noise at INFO.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def _wants_json() -> bool:
    return (
        request.path.startswith("/api/")
        or request.accept_mimetypes.best == "application/json"
    )


def _envelope(code: str, message: str, status: int, **extra):
    payload = {"error": {"code": code, "message": message, **extra}}
    return jsonify(payload), status


def create_app() -> Flask:
    _configure_logging()

    app = Flask(
        __name__,
        static_folder="../static",
        static_url_path="/static",
        template_folder="../templates",
    )
    app.json.sort_keys = False
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0 if config.DEBUG else 3600

    from .routes import api, ui

    app.register_blueprint(ui)
    app.register_blueprint(api)

    # ---------------------------------------------------------------- errors
    @app.errorhandler(ApiError)
    def _handle_api_error(exc: ApiError):
        if exc.status >= 500:
            log.error("API error: %s", exc.message)
        return jsonify(exc.to_dict()), exc.status

    @app.errorhandler(IntegrityError)
    def _handle_integrity_error(exc: IntegrityError):
        detail = str(getattr(exc, "orig", exc))
        for constraint, (field, message) in _CONSTRAINT_MESSAGES.items():
            if constraint in detail:
                log.info("Database constraint %s rejected a write", constraint)
                return _envelope(
                    "validation_failed", message, 422, fields={field: message}
                )
        log.error("Unmapped integrity error: %s", detail)
        return _envelope(
            "conflict",
            "The database rejected this change because it would break a data rule.",
            409,
        )

    @app.errorhandler(LakebaseUnavailable)
    def _handle_unavailable(exc: LakebaseUnavailable):
        log.error("Lakebase unavailable: %s", exc)
        return _envelope("lakebase_unavailable", str(exc), 503)

    @app.errorhandler(SQLAlchemyError)
    def _handle_db_error(exc: SQLAlchemyError):
        log.exception("Database error")
        return _envelope(
            "database_error",
            "Lakebase could not complete that request. "
            "Check /api/health for connection details.",
            503,
            detail=type(exc).__name__,
        )

    @app.errorhandler(HTTPException)
    def _handle_http_error(exc: HTTPException):
        if not _wants_json():
            return exc
        return _envelope(
            (exc.name or "error").lower().replace(" ", "_"),
            exc.description or "Request could not be completed.",
            exc.code or 500,
        )

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        log.exception("Unhandled error")
        return _envelope(
            "internal_error",
            "Something went wrong handling that request.",
            500,
            detail=type(exc).__name__,
        )

    # ------------------------------------------------------------- bootstrap
    # Best effort: a Lakebase that is not reachable yet must not stop the app
    # from starting, otherwise the deployment fails with no diagnostics.
    state = ensure_ready()
    if state.get("ok"):
        log.info("Lakebase ready: %s", state)
    else:
        log.error(
            "Lakebase bootstrap did not complete: %s. "
            "The app will start; see /api/health.",
            state.get("error"),
        )

    return app
