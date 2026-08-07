"""Read-only connection diagnostic. Run this first, before anything else.

Confirms the app can reach Lakebase with the connection you configured, and
prints what it found. Creates nothing, writes nothing, deletes nothing.

    python scripts/check_connection.py

It never prints your password. If parsing your connection string fails it tells
you which part is wrong without echoing the value.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _project_root() -> Path:
    """Locate the repository root.

    __file__ is undefined when this script is pasted into a notebook cell, so
    fall back to walking up from the working directory looking for the package.
    """
    try:
        return Path(__file__).resolve().parent.parent
    except NameError:
        start = Path.cwd().resolve()
        for directory in (start, *start.parents):
            if (directory / "support_app").is_dir():
                return directory
        raise SystemExit(
            f"Could not find the project root from {start}. Run this from "
            "inside the repository, or set ROOT to the folder holding "
            "support_app/."
        )


ROOT = _project_root()
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from support_app import config, db  # noqa: E402

print("=" * 68)
print("Connection target")
print("=" * 68)

summary = db.target_summary()
for key, value in summary.items():
    print(f"  {key:>14}: {value}")

if summary["config_source"] == "environment" and not summary["host"]:
    sys.exit(
        "\nNo connection is configured.\n"
        "  Set LAKEBASE_URL to your Postgres connection string (in .env locally,\n"
        "  or as a Databricks secret referenced from app.yaml when deployed),\n"
        "  or attach the Lakebase instance to the app as a Database resource."
    )

if summary["config_source"] == "connection-string (unparseable)":
    try:
        db._dsn()
    except db.LakebaseUnavailable as exc:
        sys.exit(f"\n{exc}")

if summary["auth_mode"] == "connection-string":
    print(
        "\n  Note: authenticating with the password from your connection string.\n"
        "  If that password is a short-lived Lakebase OAuth token rather than a\n"
        "  native Postgres role password, it will stop working when it expires."
    )

print("\n" + "=" * 68)
print("Connecting")
print("=" * 68)

try:
    with db.get_engine().connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT current_user            AS connected_as,
                       current_database()      AS database,
                       current_schema()        AS current_schema,
                       version()               AS server
                """
            )
        ).mappings().one()
        search_path = connection.execute(text("SHOW search_path")).scalar_one()
        can_create = connection.execute(
            text("SELECT has_database_privilege(current_user, current_database(), 'CREATE')")
        ).scalar_one()
except db.LakebaseUnavailable as exc:
    sys.exit(f"\nFAILED: {exc}")
except Exception as exc:
    hint = ""
    detail = str(exc).lower()
    if "password authentication failed" in detail:
        hint = (
            "\n  The credential was rejected. If the password contains @ : / ? # or %,"
            "\n  it must be percent-encoded inside the connection string."
        )
    elif "timeout" in detail or "could not connect" in detail:
        hint = (
            "\n  Could not reach the host. Check the hostname, that port 5432 is"
            "\n  reachable from here, and that the instance is running."
        )
    elif "does not exist" in detail and "database" in detail:
        hint = "\n  The database name in the connection string does not exist."
    sys.exit(f"\nFAILED: {type(exc).__name__}: {exc}{hint}")

print(f"  connected as: {row['connected_as']}")
print(f"      database: {row['database']}")
print(f"   search_path: {search_path}")
print(f"   can CREATE?: {can_create}")
print(f"        server: {row['server'].split(' on ')[0]}")

if not can_create:
    print(
        f"\n  This role cannot create objects in {row['database']}. Either grant it\n"
        f"  CREATE, or set LAKEBASE_SCHEMA to a schema that already exists and\n"
        f"  that this role owns, or apply sql/001_schema.sql yourself as an\n"
        f"  administrator (replacing __SCHEMA__ with your schema name)."
    )

print("\n" + "=" * 68)
print(f"Schema '{config.LAKEBASE_SCHEMA}'")
print("=" * 68)

# What sql/001_schema.sql creates. Checked explicitly because CREATE TABLE
# IF NOT EXISTS silently accepts a table that already exists with a *different*
# shape -- which then fails much later as a confusing missing-column error.
EXPECTED_COLUMNS = {
    "tickets": {
        "ticket_id", "title", "description", "status", "priority", "category",
        "created_by", "assigned_to", "created_at", "updated_at", "resolved_at",
    },
    "ticket_messages": {
        "message_id", "ticket_id", "message_text", "author", "author_role",
        "created_at",
    },
    "ticket_status_history": {
        "history_id", "ticket_id", "from_status", "to_status", "changed_by",
        "changed_at",
    },
}

shape_problems: list[str] = []

with db.get_engine().connect() as connection:
    tables = connection.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :schema ORDER BY table_name"
        ),
        {"schema": config.LAKEBASE_SCHEMA},
    ).scalars().all()

    if not tables:
        print("  Not created yet -- this is normal on a first run.")
        print("  It is applied automatically on app start, or run:")
        print("    python scripts/verify_lakebase.py")
    else:
        print(f"  existing tables: {', '.join(tables)}")
        for table, expected in EXPECTED_COLUMNS.items():
            if table not in tables:
                print(f"    {table:>22}: not created yet")
                continue

            count = connection.execute(
                text(f"SELECT COUNT(*) FROM {config.LAKEBASE_SCHEMA}.{table}")
            ).scalar_one()
            actual = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :schema AND table_name = :table"
                    ),
                    {"schema": config.LAKEBASE_SCHEMA, "table": table},
                ).scalars().all()
            )
            missing = expected - actual
            if missing:
                shape_problems.append(
                    f"{table} is missing: {', '.join(sorted(missing))}"
                )
                print(f"    {table:>22}: {count} row(s)  ** WRONG SHAPE **")
            else:
                extra = actual - expected
                note = f"  (+{len(extra)} extra column(s))" if extra else ""
                print(f"    {table:>22}: {count} row(s){note}")

if shape_problems:
    print("\n" + "!" * 68)
    print("A table already exists in this schema with a different shape:")
    for problem in shape_problems:
        print(f"  - {problem}")
    print(
        "\nCREATE TABLE IF NOT EXISTS will NOT fix this -- it skips the table\n"
        "silently, and queries then fail on the missing columns. Pick one:\n"
        f"  * point the app at a fresh schema:  LAKEBASE_SCHEMA=support_v2\n"
        f"  * or drop the old one:  DROP SCHEMA {config.LAKEBASE_SCHEMA} CASCADE;\n"
        "    (this deletes its data -- check what is in there first)"
    )
    print("!" * 68)
    sys.exit(1)

print("\nOK -- the app can reach Lakebase with this configuration.")
print("Next: python scripts/verify_lakebase.py  (full end-to-end round trip)")
