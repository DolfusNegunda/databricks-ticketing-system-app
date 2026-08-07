"""Lakebase connectivity: credentials, engine, and one-time bootstrap.

Credential strategy
-------------------
Nothing in this repository stores or reads a database password. When running as
a Databricks App the process already holds an OAuth identity (its service
principal), and Lakebase accepts a short-lived token minted from that identity
as the Postgres password. The token is generated on demand, cached in memory,
and refreshed before it expires -- so a long-running app never wedges on an
expired credential.

``LAKEBASE_URL`` / ``PGPASSWORD`` remain supported purely as a local-development
escape hatch.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL, make_url

from . import config

log = logging.getLogger(__name__)

#: Refresh a token this long before its stated expiry.
_REFRESH_MARGIN = timedelta(minutes=8)
#: Assumed lifetime when the API does not report one.
_ASSUMED_LIFETIME = timedelta(minutes=50)


class LakebaseUnavailable(RuntimeError):
    """Raised when the app cannot reach or authenticate to Lakebase."""


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
class _TokenProvider:
    """Thread-safe cache around ``generate_database_credential``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._workspace = None

    def _client(self):
        if self._workspace is None:
            try:
                from databricks.sdk import WorkspaceClient
            except ImportError as exc:  # pragma: no cover
                raise LakebaseUnavailable(
                    "databricks-sdk is not installed, and no local LAKEBASE_URL "
                    "or PGPASSWORD was provided."
                ) from exc
            self._workspace = WorkspaceClient()
        return self._workspace

    @staticmethod
    def _parse_expiry(raw: object) -> datetime:
        if isinstance(raw, datetime):
            return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        if isinstance(raw, str) and raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                log.debug("Unparseable credential expiry %r; assuming default", raw)
        return datetime.now(timezone.utc) + _ASSUMED_LIFETIME

    def token(self) -> str:
        now = datetime.now(timezone.utc)
        with self._lock:
            if self._token and self._expires_at and now < self._expires_at - _REFRESH_MARGIN:
                return self._token

            instance = _instance_name()
            if not instance:
                raise LakebaseUnavailable(
                    "Cannot mint a Lakebase credential: set LAKEBASE_INSTANCE_NAME, "
                    "or supply a connection string / PGHOST from which the instance "
                    "name can be derived. (If you meant to authenticate with a "
                    "password, put it in LAKEBASE_URL or PGPASSWORD instead.)"
                )

            try:
                credential = self._client().database.generate_database_credential(
                    request_id=str(uuid.uuid4()),
                    instance_names=[instance],
                )
            except Exception as exc:  # SDK raises a wide variety of errors
                raise LakebaseUnavailable(
                    f"Failed to mint a Lakebase credential for instance {instance!r}: {exc}"
                ) from exc

            token = getattr(credential, "token", None)
            if not token:
                raise LakebaseUnavailable(
                    "Lakebase returned an empty credential token."
                )

            self._token = token
            self._expires_at = self._parse_expiry(
                getattr(credential, "expiration_time", None)
            )
            log.info(
                "Minted Lakebase credential for %s (expires %s)",
                instance,
                self._expires_at.isoformat(),
            )
            return self._token


_tokens = _TokenProvider()


def _parse_dsn(raw: str) -> URL:
    """Parse a supplied Postgres connection string into a SQLAlchemy URL.

    Deliberately forgiving about the things that differ between where a DSN gets
    copied from, and deliberately loud about the one thing that silently breaks
    it: an unencoded special character in the password.
    """
    candidate = raw.strip().strip('"').strip("'")
    # Databricks and psql both show postgresql://; accept the postgres:// alias
    # that SQLAlchemy itself rejects.
    if candidate.startswith("postgres://"):
        candidate = "postgresql://" + candidate[len("postgres://") :]

    try:
        url = make_url(candidate)
    except Exception as exc:
        raise LakebaseUnavailable(
            "LAKEBASE_URL is not a valid Postgres connection string. Expected "
            "postgresql://<user>:<password>@<host>:5432/<database>?sslmode=require -- "
            "and if the password contains any of @ : / ? # % it must be "
            "percent-encoded."
        ) from exc

    if not url.host:
        raise LakebaseUnavailable("LAKEBASE_URL is missing a host.")
    if not url.username:
        raise LakebaseUnavailable("LAKEBASE_URL is missing a username.")

    return url.set(drivername="postgresql+psycopg2")


def _dsn() -> URL | None:
    """The supplied connection string, parsed, or None if none was given."""
    if not config.LAKEBASE_URL:
        return None
    return _parse_dsn(config.LAKEBASE_URL)


def _static_password() -> str | None:
    """A password supplied by configuration, if any.

    When this returns None the engine falls back to minting OAuth tokens. That
    makes a connection string *without* a password a valid configuration: it
    supplies the host, user and database, and the token supplies the secret.
    """
    if config.PGPASSWORD:
        return config.PGPASSWORD
    if config.LAKEBASE_URL:
        try:
            return _dsn().password
        except LakebaseUnavailable:
            return None
    return None


def _instance_name() -> str:
    """Lakebase instance name, for the OAuth token path.

    Precedence has to match ``_build_url``: an explicit setting first, then the
    connection string, then PG*. If a connection string and PGHOST disagree, the
    engine connects to the connection string's host -- so a token minted for the
    PGHOST instance would be rejected by the instance we actually reach.
    """
    if config.LAKEBASE_INSTANCE_NAME:
        return config.LAKEBASE_INSTANCE_NAME

    host = ""
    if config.LAKEBASE_URL:
        try:
            host = _dsn().host or ""
        except LakebaseUnavailable:
            host = ""
    host = host or config.PGHOST
    return host.split(".", 1)[0] if "." in host else ""


def target_summary() -> dict[str, object]:
    """Non-sensitive description of the connection target.

    Safe to expose on /api/health: it reports where we are connecting and how we
    authenticate, and never the credential itself.
    """
    host: str | None = config.PGHOST or None
    port: int = config.PGPORT
    database: str = config.PGDATABASE
    user: str | None = config.PGUSER or None
    sslmode: str = config.PGSSLMODE
    source = "environment"

    if config.LAKEBASE_URL:
        source = "connection-string"
        try:
            url = _dsn()
            host = url.host
            port = url.port or port
            database = url.database or database
            user = url.username
            sslmode = str(url.query.get("sslmode") or sslmode)
        except LakebaseUnavailable:
            source = "connection-string (unparseable)"

    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "schema": config.LAKEBASE_SCHEMA,
        "instance": _instance_name() or None,
        "sslmode": sslmode,
        "config_source": source,
        "auth_mode": (
            "connection-string" if _static_password() else "databricks-oauth"
        ),
    }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
_engine: Engine | None = None
_engine_lock = threading.Lock()


def _build_url() -> URL:
    # A supplied connection string wins over everything else.
    dsn = _dsn()
    if dsn is not None:
        return dsn

    missing = [
        name
        for name, value in (("PGHOST", config.PGHOST), ("PGUSER", config.PGUSER))
        if not value
    ]
    if missing:
        raise LakebaseUnavailable(
            "No Lakebase connection is configured: missing "
            + ", ".join(missing)
            + ". Either set LAKEBASE_URL to your Postgres connection string, or "
            "attach the Lakebase instance to the app as a Database resource so "
            "Databricks injects PGHOST/PGUSER for you."
        )

    return URL.create(
        "postgresql+psycopg2",
        username=config.PGUSER,
        host=config.PGHOST,
        port=config.PGPORT,
        database=config.PGDATABASE,
    )


def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is not None:
        return _engine

    with _engine_lock:
        if _engine is not None:
            return _engine

        url = _build_url()
        connect_args: dict[str, object] = {
            "connect_timeout": 10,
            "application_name": config.PGAPPNAME,
        }
        if "sslmode" not in url.query:
            connect_args["sslmode"] = config.PGSSLMODE

        engine = create_engine(
            url,
            pool_pre_ping=True,
            # Recycle below the credential lifetime so pooled sockets are never
            # older than the token that opened them.
            pool_recycle=1500,
            pool_size=5,
            max_overflow=5,
            pool_timeout=30,
            future=True,
        )

        if _static_password() is None:
            @event.listens_for(engine, "do_connect")
            def _inject_oauth_token(_dialect, _conn_rec, _cargs, cparams):  # noqa: ANN001
                """Attach a fresh OAuth token as the password on every connect."""
                cparams["password"] = _tokens.token()
                return None

        @event.listens_for(engine, "connect", insert=True)
        def _set_search_path(dbapi_connection, _connection_record):  # noqa: ANN001
            """Point unqualified table names at our schema.

            This deliberately uses a SET statement rather than the libpq
            ``options=-c search_path=...`` connect parameter. Lakebase sits
            behind a connection proxy that reserves ``options`` for its own
            endpoint routing, so a search_path passed that way is silently
            dropped -- and every unqualified query then fails with
            "relation does not exist" even though the tables are right there.
            A plain SET is ordinary SQL and survives any proxy.
            """
            previous_autocommit = dbapi_connection.autocommit
            dbapi_connection.autocommit = True
            with dbapi_connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{config.LAKEBASE_SCHEMA}", public')
            dbapi_connection.autocommit = previous_autocommit

        _engine = engine
        summary = target_summary()
        log.info(
            "Lakebase engine ready (host=%s db=%s user=%s schema=%s auth=%s source=%s)",
            summary["host"],
            summary["database"],
            summary["user"],
            summary["schema"],
            summary["auth_mode"],
            summary["config_source"],
        )
        if summary["auth_mode"] == "connection-string":
            log.info(
                "Using the password from the supplied connection string. If that "
                "password is a short-lived Lakebase OAuth token rather than a "
                "native role password, connections will start failing when it "
                "expires -- use a native Postgres role for a long-lived app."
            )
        return _engine


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
_bootstrap_lock = threading.Lock()
_bootstrap_state: dict[str, object] = {"attempted": False, "ok": False, "error": None}


def _render_sql(filename: str) -> str:
    path = config.SQL_DIR / filename
    sql = path.read_text(encoding="utf-8")
    # LAKEBASE_SCHEMA is validated as a bare identifier in config, so this
    # substitution cannot introduce SQL beyond a schema name.
    return sql.replace("__SCHEMA__", config.LAKEBASE_SCHEMA)


def _tickets_is_empty(connection) -> bool:  # noqa: ANN001
    """Schema-qualified on purpose.

    This runs inside the bootstrap transaction, moments after CREATE SCHEMA. If
    it relied on search_path and that were not in effect, the lookup would fail
    and roll back every table just created -- leaving an empty database and an
    error pointing at the wrong thing. The schema name is validated in config.
    """
    total = connection.execute(
        text(f"SELECT COUNT(*) FROM {config.LAKEBASE_SCHEMA}.tickets")
    ).scalar_one()
    return total == 0


def _run_sql_script(connection, filename: str) -> None:  # noqa: ANN001
    """Execute a multi-statement .sql file verbatim, on the raw driver cursor.

    Not ``exec_driver_sql``: for a statement with no parameters SQLAlchemy still
    hands the driver an empty immutabledict, and psycopg2's C extension rejects
    that as a non-sequence ("immutabledict is not a sequence"). Going straight
    to the DBAPI cursor passes no parameters at all, which also means psycopg2
    performs no %-interpolation on the script body.

    The cursor comes from this Connection's own DBAPI connection, so the work
    stays inside the surrounding transaction.
    """
    sql = _render_sql(filename)
    with connection.connection.cursor() as cursor:
        cursor.execute(sql)


def bootstrap(force_seed: bool = False) -> dict[str, object]:
    """Apply the schema and, when the table is empty, the demo data.

    Safe to call repeatedly: every statement in ``sql/`` is idempotent.
    """
    engine = get_engine()
    result: dict[str, object] = {"schema_applied": False, "seeded": False}

    with engine.begin() as connection:
        _run_sql_script(connection, "001_schema.sql")
        result["schema_applied"] = True

        should_seed = force_seed or (
            config.SEED_ON_EMPTY and _tickets_is_empty(connection)
        )
        if should_seed:
            _run_sql_script(connection, "002_seed.sql")
            result["seeded"] = not _tickets_is_empty(connection)

    log.info("Lakebase bootstrap complete: %s", result)
    return result


def ensure_ready() -> dict[str, object]:
    """Run the bootstrap once per process, recording the outcome.

    Failures are captured rather than raised so the app can still start and
    surface a useful diagnostic in the UI and on ``/api/health``.
    """
    if _bootstrap_state["attempted"]:
        return dict(_bootstrap_state)

    with _bootstrap_lock:
        if _bootstrap_state["attempted"]:
            return dict(_bootstrap_state)

        _bootstrap_state["attempted"] = True
        if not config.AUTO_MIGRATE:
            _bootstrap_state.update(ok=True, error=None, skipped=True)
            return dict(_bootstrap_state)

        try:
            _bootstrap_state.update(ok=True, error=None, **bootstrap())
        except Exception as exc:
            # Intentionally bare. This runs during create_app(), so ANY escaping
            # exception takes the whole process down at startup -- which is the
            # exact opposite of this function's purpose. A narrower tuple once
            # let a TypeError through and crashed the deployment instead of
            # letting it come up degraded and report the cause on /api/health.
            log.exception("Lakebase bootstrap failed")
            _bootstrap_state.update(
                ok=False, error=f"{type(exc).__name__}: {exc}"
            )

        return dict(_bootstrap_state)


def bootstrap_state() -> dict[str, object]:
    return dict(_bootstrap_state)


def probe() -> dict[str, object]:
    """Cheap connectivity check used by ``/api/health``."""
    engine = get_engine()
    with engine.connect() as connection:
        version = connection.execute(text("SHOW server_version")).scalar_one()
        counts = connection.execute(
            text(
                """
                SELECT (SELECT COUNT(*) FROM tickets)         AS tickets,
                       (SELECT COUNT(*) FROM ticket_messages) AS messages
                """
            )
        ).mappings().one()
    return {
        "server_version": version,
        "tickets": counts["tickets"],
        "messages": counts["messages"],
    }
