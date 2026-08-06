"""Runtime configuration, resolved once at import time.

Everything here comes from the environment. There are two supported ways to
point the app at Lakebase, and neither puts a credential in this repository:

1. ``LAKEBASE_URL`` -- a full Postgres connection string, supplied through a
   Databricks secret and surfaced as an environment variable by ``app.yaml``.
   Takes precedence when present.
2. The ``PG*`` variables, which Databricks injects automatically when the
   Lakebase instance is attached to the app as a Database resource. The password
   is then minted at runtime from the app's own OAuth identity.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

try:  # optional: only used for local development
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is not required in production
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = PROJECT_ROOT / "sql"

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_TRUTHY = {"1", "true", "yes", "on"}


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUTHY


def _text(name: str, default: str = "") -> str:
    return (os.getenv(name) or "").strip() or default


def _schema_name() -> str:
    """Validate the schema name -- it is interpolated into DDL, never bound."""
    value = _text("LAKEBASE_SCHEMA", "support").lower()
    if not _IDENTIFIER.match(value):
        raise ValueError(
            f"LAKEBASE_SCHEMA must be a simple lowercase identifier, got {value!r}"
        )
    return value


def _instance_name() -> str:
    """Lakebase instance name, derived from PGHOST when not set explicitly.

    PGHOST looks like ``<instance-name>.database.cloud.databricks.com``.
    """
    explicit = _text("LAKEBASE_INSTANCE_NAME")
    if explicit:
        return explicit
    host = _text("PGHOST")
    return host.split(".", 1)[0] if "." in host else ""


# --- Branding ---------------------------------------------------------------
BRAND_NAME = _text("APP_BRAND_NAME", "Nexus Support")
BRAND_TAGLINE = _text("APP_BRAND_TAGLINE", "Support desk on Databricks Lakebase")

# --- Lakebase connection ----------------------------------------------------
PGHOST = _text("PGHOST")
PGPORT = int(_text("PGPORT", "5432"))
PGDATABASE = _text("PGDATABASE", "databricks_postgres")
PGUSER = _text("PGUSER") or _text("DATABRICKS_CLIENT_ID")
PGSSLMODE = _text("PGSSLMODE", "require")
PGAPPNAME = _text("PGAPPNAME", "nexus-support-app")
LAKEBASE_INSTANCE_NAME = _instance_name()
LAKEBASE_SCHEMA = _schema_name()

# A full Postgres connection string. When set it wins over every PG* value
# above, and its password (if it has one) is used instead of an OAuth token.
# Supply it through a Databricks secret -- never commit it. See db._parse_dsn.
LAKEBASE_URL = _text("LAKEBASE_URL")
PGPASSWORD = _text("PGPASSWORD")

# --- Behaviour --------------------------------------------------------------
AUTO_MIGRATE = _flag("LAKEBASE_AUTO_MIGRATE", True)
SEED_ON_EMPTY = _flag("LAKEBASE_SEED_ON_EMPTY", True)
LOG_STATUS_CHANGES_AS_MESSAGES = _flag("APP_LOG_STATUS_CHANGES", True)
DEFAULT_USER = _text("APP_DEFAULT_USER", "unknown.user@local")
DEBUG = _flag("FLASK_DEBUG", False)
PORT = int(_text("DATABRICKS_APP_PORT", "") or _text("PORT", "8000"))

# --- Domain vocabulary (mirrors the CHECK constraints in sql/001_schema.sql) -
STATUSES: tuple[str, ...] = ("open", "in_progress", "resolved", "closed")
PRIORITIES: tuple[str, ...] = ("low", "medium", "high", "urgent")
CATEGORIES: tuple[str, ...] = (
    "general",
    "billing",
    "technical",
    "account",
    "feature_request",
)
AUTHOR_ROLES: tuple[str, ...] = ("customer", "agent", "system")

#: Statuses that mean "no longer being worked".
TERMINAL_STATUSES: frozenset[str] = frozenset({"resolved", "closed"})

#: Whitelisted sort keys -> ORDER BY fragments. Never built from user input.
SORT_OPTIONS: dict[str, str] = {
    "newest": "t.created_at DESC",
    "oldest": "t.created_at ASC",
    "activity": "last_activity_at DESC",
    "priority": "priority_rank DESC, t.created_at DESC",
}
DEFAULT_SORT = "activity"

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 100

TITLE_MIN, TITLE_MAX = 3, 200
MESSAGE_MIN, MESSAGE_MAX = 1, 5000
DESCRIPTION_MAX = 5000

# The non-sensitive description of the connection target lives in
# db.target_summary(), which can also parse LAKEBASE_URL.
