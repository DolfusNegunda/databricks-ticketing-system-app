"""End-to-end verification against a real Lakebase instance.

Run this once the app is deployed (or locally with a Lakebase connection) to
prove the assignment's acceptance criteria against live data. It exercises the
same repository code the web app uses, then removes everything it created.

    python scripts/verify_lakebase.py

What it proves:
  * the schema exists, with a real FK from ticket_messages to tickets
  * the seeded data satisfies the brief (3+ tickets, 2+ messages each, 2+ statuses)
  * a ticket can be created, re-read, replied to, re-prioritised and re-statused
  * every change survives being read back on a brand new connection
  * deleting a ticket cascades to its messages and status history
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

from support_app import config, db, repository  # noqa: E402
from support_app.errors import NotFoundError  # noqa: E402

MARKER = "[verify] automated end-to-end check"
ACTOR = "verify.script@example.com"

failures: list[str] = []
total = 0


def check(name: str, condition: bool, detail: object = "") -> None:
    global total
    total += 1
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} :: {detail}")
        failures.append(name)


def scalar(sql: str, **params):
    with db.get_engine().connect() as connection:
        return connection.execute(text(sql), params).scalar()


# ---------------------------------------------------------------------------
print("Lakebase target:")
for key, value in db.target_summary().items():
    print(f"  {key:>10}: {value}")

print("\n== connect and bootstrap ==")
try:
    result = db.bootstrap()
    check("schema applied", result["schema_applied"], result)
    print(f"        seeded this run: {result['seeded']}")
except Exception as exc:
    sys.exit(f"\nCould not reach Lakebase: {exc}")

probe = db.probe()
print(f"        server: PostgreSQL {probe['server_version']}")

print("\n== structure ==")
for table in ("tickets", "ticket_messages", "ticket_status_history"):
    exists = scalar(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = :schema AND table_name = :table",
        schema=config.LAKEBASE_SCHEMA,
        table=table,
    )
    check(f"table {config.LAKEBASE_SCHEMA}.{table} exists", exists == 1)

fk = scalar(
    """
    SELECT COUNT(*)
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON kcu.constraint_name = tc.constraint_name
     AND kcu.constraint_schema = tc.constraint_schema
    JOIN information_schema.constraint_column_usage ccu
      ON ccu.constraint_name = tc.constraint_name
     AND ccu.constraint_schema = tc.constraint_schema
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_schema = :schema
      AND tc.table_name = 'ticket_messages'
      AND kcu.column_name = 'ticket_id'
      AND ccu.table_name = 'tickets'
    """,
    schema=config.LAKEBASE_SCHEMA,
)
check("ticket_messages.ticket_id references tickets.ticket_id", fk >= 1, fk)

trigger = scalar(
    "SELECT COUNT(*) FROM information_schema.triggers "
    "WHERE trigger_schema = :schema AND trigger_name = 'trg_tickets_updated_at'",
    schema=config.LAKEBASE_SCHEMA,
)
check("updated_at trigger installed", trigger >= 1, trigger)

print("\n== seeded data meets the brief ==")
listing = repository.list_tickets(limit=config.MAX_PAGE_SIZE)
seeded = [t for t in listing["items"] if MARKER not in t["title"]]
check("at least 3 tickets", len(seeded) >= 3, len(seeded))

statuses = {t["status"] for t in seeded}
check("at least 2 distinct statuses", len(statuses) >= 2, sorted(statuses))
print(f"        statuses present: {', '.join(sorted(statuses))}")

thin = [
    f"#{t['ticket_id']} has {t['message_count']}"
    for t in seeded
    if t["message_count"] < 2
]
check("every ticket has 2+ messages", not thin, thin)

# ---------------------------------------------------------------------------
created_id = None
try:
    print("\n== create ==")
    created = repository.create_ticket(
        title=MARKER,
        description="Created by scripts/verify_lakebase.py. Safe to delete.",
        status="open",
        priority="medium",
        category="technical",
        created_by=ACTOR,
        assigned_to=None,
        first_message="Opening message written by the verification script.",
        author_role="customer",
    )
    created_id = created["ticket_id"]
    check("ticket created with an id", isinstance(created_id, int) and created_id > 0,
          created_id)
    check("opening message stored", len(created["messages"]) == 1,
          len(created["messages"]))
    check("creation recorded in status history", len(created["status_history"]) == 1,
          created["status_history"])

    print("\n== read back on a fresh connection ==")
    reread = repository.get_ticket(created_id)
    check("title persisted", reread["title"] == MARKER)
    check("status persisted", reread["status"] == "open", reread["status"])
    check("author persisted", reread["created_by"] == ACTOR)
    check("appears in an unfiltered list",
          any(t["ticket_id"] == created_id for t in
              repository.list_tickets(limit=config.MAX_PAGE_SIZE)["items"]))

    print("\n== add a message ==")
    message = repository.add_message(
        ticket_id=created_id,
        message_text="Second message, added by the verification script.",
        author=ACTOR,
        author_role="agent",
    )
    check("message id returned", message["message_id"] > 0, message["message_id"])
    after = repository.get_ticket(created_id)
    check("thread now has 2 messages", len(after["messages"]) == 2,
          len(after["messages"]))
    check("message order is chronological",
          [m["message_id"] for m in after["messages"]]
          == sorted(m["message_id"] for m in after["messages"]))

    print("\n== update status ==")
    before_updated_at = after["updated_at"]
    updated = repository.update_ticket(created_id, {"status": "in_progress"}, actor=ACTOR)
    check("status changed", updated["status"] == "in_progress", updated["status"])
    check("updated_at moved (trigger fired)",
          updated["updated_at"] > before_updated_at,
          (before_updated_at, updated["updated_at"]))
    check("transition audited",
          any(h["from_status"] == "open" and h["to_status"] == "in_progress"
              for h in updated["status_history"]),
          updated["status_history"])
    if config.LOG_STATUS_CHANGES_AS_MESSAGES:
        check("system message added to the thread",
              any(m["author_role"] == "system" for m in updated["messages"]),
              [m["author_role"] for m in updated["messages"]])

    print("\n== resolve, then reopen ==")
    resolved = repository.update_ticket(created_id, {"status": "resolved"}, actor=ACTOR)
    check("resolved_at set on resolve", resolved["resolved_at"] is not None)
    reopened = repository.update_ticket(created_id, {"status": "open"}, actor=ACTOR)
    check("resolved_at cleared on reopen", reopened["resolved_at"] is None,
          reopened["resolved_at"])

    print("\n== update priority and assignee ==")
    bumped = repository.update_ticket(
        created_id, {"priority": "urgent", "assigned_to": ACTOR}, actor=ACTOR
    )
    check("priority updated", bumped["priority"] == "urgent", bumped["priority"])
    check("assignee updated", bumped["assigned_to"] == ACTOR, bumped["assigned_to"])
    check("unassign works",
          repository.update_ticket(
              created_id, {"assigned_to": None}, actor=ACTOR
          )["assigned_to"] is None)

    print("\n== filters and search find it ==")
    check("status filter matches",
          any(t["ticket_id"] == created_id
              for t in repository.list_tickets(statuses=["open"],
                                               limit=config.MAX_PAGE_SIZE)["items"]))
    check("status filter excludes",
          all(t["ticket_id"] != created_id
              for t in repository.list_tickets(statuses=["closed"],
                                               limit=config.MAX_PAGE_SIZE)["items"]))
    check("priority filter matches",
          any(t["ticket_id"] == created_id
              for t in repository.list_tickets(priorities=["urgent"],
                                               limit=config.MAX_PAGE_SIZE)["items"]))
    check("title search matches",
          any(t["ticket_id"] == created_id
              for t in repository.list_tickets(search="automated end-to-end",
                                               limit=config.MAX_PAGE_SIZE)["items"]))
    check("message-body search matches",
          any(t["ticket_id"] == created_id
              for t in repository.list_tickets(search="verification script",
                                               limit=config.MAX_PAGE_SIZE)["items"]))
    for sort in config.SORT_OPTIONS:
        page = repository.list_tickets(sort=sort, limit=5)
        check(f"sort={sort} returns rows", len(page["items"]) > 0, page["total"])

    print("\n== statistics ==")
    stats = repository.stats()
    check("total is positive", stats["total"] > 0, stats["total"])
    check("by_status sums to total",
          sum(stats["by_status"].values()) == stats["total"], stats["by_status"])
    check("by_priority sums to total",
          sum(stats["by_priority"].values()) == stats["total"], stats["by_priority"])
    check("message_count is positive", stats["message_count"] > 0)
    check("urgent open ticket counted in needs_attention",
          stats["needs_attention"] >= 1, stats["needs_attention"])

    print("\n== delete cascades ==")
    messages_before = scalar(
        "SELECT COUNT(*) FROM ticket_messages WHERE ticket_id = :id", id=created_id
    )
    history_before = scalar(
        "SELECT COUNT(*) FROM ticket_status_history WHERE ticket_id = :id", id=created_id
    )
    check("messages exist before delete", messages_before >= 2, messages_before)
    check("history exists before delete", history_before >= 3, history_before)

    deleted = repository.delete_ticket(created_id)
    check("delete reports the message count",
          deleted["deleted_messages"] == messages_before, deleted)
    created_id = None  # already gone; skip the cleanup branch

    check("ticket row removed",
          scalar("SELECT COUNT(*) FROM tickets WHERE ticket_id = :id",
                 id=deleted["ticket_id"]) == 0)
    check("messages cascaded away",
          scalar("SELECT COUNT(*) FROM ticket_messages WHERE ticket_id = :id",
                 id=deleted["ticket_id"]) == 0)
    check("history cascaded away",
          scalar("SELECT COUNT(*) FROM ticket_status_history WHERE ticket_id = :id",
                 id=deleted["ticket_id"]) == 0)

    try:
        repository.get_ticket(deleted["ticket_id"])
        check("reading a deleted ticket raises NotFound", False, "no exception")
    except NotFoundError:
        check("reading a deleted ticket raises NotFound", True)

    try:
        repository.add_message(
            ticket_id=deleted["ticket_id"],
            message_text="should not be possible",
            author=ACTOR,
        )
        check("messaging a deleted ticket raises NotFound", False, "no exception")
    except NotFoundError:
        check("messaging a deleted ticket raises NotFound", True)

finally:
    if created_id is not None:
        print(f"\nCleaning up leftover verification ticket #{created_id}")
        try:
            repository.delete_ticket(created_id)
        except Exception as exc:  # pragma: no cover
            print(f"  WARNING: could not delete #{created_id}: {exc}")

print(f"\n{total - len(failures)}/{total} checks passed")
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("LAKEBASE ALL GREEN -- the deployed app satisfies the brief.")
