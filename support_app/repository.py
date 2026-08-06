"""All SQL against Lakebase lives here.

Two rules hold throughout: user-supplied values are always bound parameters,
never string-formatted; and anything that must appear in the SQL text itself
(column names, ORDER BY fragments) comes from a whitelist in ``config``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text

from . import config
from .db import get_engine
from .errors import ApiError, NotFoundError

log = logging.getLogger(__name__)

_PRIORITY_RANK = (
    "CASE t.priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 "
    "WHEN 'medium' THEN 2 ELSE 1 END"
)

_TICKET_FIELDS = (
    "ticket_id",
    "title",
    "description",
    "status",
    "priority",
    "category",
    "created_by",
    "assigned_to",
    "created_at",
    "updated_at",
    "resolved_at",
)
_TICKET_COLUMNS = ", ".join(f"t.{name}" for name in _TICKET_FIELDS)
_TICKET_COLUMNS_BARE = ", ".join(_TICKET_FIELDS)

#: Columns a client may change through PATCH /api/tickets/<id>.
_UPDATABLE = ("title", "description", "status", "priority", "category", "assigned_to")


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def _clean(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row(mapping: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if mapping is None:
        return None
    return {key: _clean(val) for key, val in mapping.items()}


def _rows(mappings: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _clean(val) for key, val in m.items()} for m in mappings]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _ticket_filters(
    *,
    statuses: Sequence[str] | None,
    priorities: Sequence[str] | None,
    categories: Sequence[str] | None,
    search: str | None,
    assigned_to: str | None,
    created_by: str | None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if statuses:
        clauses.append("t.status = ANY(:statuses)")
        params["statuses"] = list(statuses)
    if priorities:
        clauses.append("t.priority = ANY(:priorities)")
        params["priorities"] = list(priorities)
    if categories:
        clauses.append("t.category = ANY(:categories)")
        params["categories"] = list(categories)
    if assigned_to:
        clauses.append("t.assigned_to = :assigned_to")
        params["assigned_to"] = assigned_to
    if created_by:
        clauses.append("t.created_by = :created_by")
        params["created_by"] = created_by
    if search:
        clauses.append(
            """(
                t.title ILIKE :search
                OR COALESCE(t.description, '') ILIKE :search
                OR t.created_by ILIKE :search
                OR EXISTS (
                    SELECT 1 FROM ticket_messages tm
                    WHERE tm.ticket_id = t.ticket_id
                      AND tm.message_text ILIKE :search
                )
            )"""
        )
        params["search"] = f"%{search}%"

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def build_list_sql(where: str, order_by: str) -> tuple[str, str]:
    """Compose the ticket list and matching count queries.

    Kept as a pure function so the generated SQL can be parsed and checked
    without a live database (see scripts/check_sql.py).
    """
    list_sql = f"""
        SELECT {_TICKET_COLUMNS},
               COALESCE(m.message_count, 0) AS message_count,
               GREATEST(t.updated_at, COALESCE(m.last_message_at, t.updated_at))
                   AS last_activity_at,
               {_PRIORITY_RANK} AS priority_rank
        FROM tickets t
        LEFT JOIN (
            SELECT ticket_id,
                   COUNT(*)        AS message_count,
                   MAX(created_at) AS last_message_at
            FROM ticket_messages
            GROUP BY ticket_id
        ) m ON m.ticket_id = t.ticket_id
        {where}
        ORDER BY {order_by}
        LIMIT :limit OFFSET :offset
    """
    count_sql = f"SELECT COUNT(*) FROM tickets t {where}"
    return list_sql, count_sql


def build_update_sql(columns: Sequence[str], status_changed: bool) -> str:
    """Compose the ticket UPDATE. ``columns`` must come from ``_UPDATABLE``."""
    unknown = [column for column in columns if column not in _UPDATABLE]
    if unknown:
        raise ApiError(
            f"Unknown field(s): {', '.join(unknown)}.", code="nothing_to_update"
        )

    assignments = [f"{column} = :{column}" for column in columns]
    if status_changed:
        assignments.append(
            "resolved_at = CASE WHEN :status IN ('resolved', 'closed') "
            "THEN COALESCE(resolved_at, now()) ELSE NULL END"
        )
    return f"""
        UPDATE tickets
        SET {', '.join(assignments)}
        WHERE ticket_id = :ticket_id
    """


def list_tickets(
    *,
    statuses: Sequence[str] | None = None,
    priorities: Sequence[str] | None = None,
    categories: Sequence[str] | None = None,
    search: str | None = None,
    assigned_to: str | None = None,
    created_by: str | None = None,
    sort: str = config.DEFAULT_SORT,
    limit: int = config.DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    order_by = config.SORT_OPTIONS.get(sort) or config.SORT_OPTIONS[config.DEFAULT_SORT]
    where, params = _ticket_filters(
        statuses=statuses,
        priorities=priorities,
        categories=categories,
        search=search,
        assigned_to=assigned_to,
        created_by=created_by,
    )
    list_sql, count_sql = build_list_sql(where, order_by)

    with get_engine().connect() as connection:
        items = _rows(
            connection.execute(
                text(list_sql), {**params, "limit": limit, "offset": offset}
            )
            .mappings()
            .all()
        )
        total = connection.execute(text(count_sql), params).scalar_one()

    for item in items:
        item.pop("priority_rank", None)

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(items),
    }


def get_ticket(ticket_id: int, *, include_thread: bool = True) -> dict[str, Any]:
    with get_engine().connect() as connection:
        ticket = _row(
            connection.execute(
                text(
                    f"""
                    SELECT {_TICKET_COLUMNS},
                           (SELECT COUNT(*) FROM ticket_messages tm
                             WHERE tm.ticket_id = t.ticket_id) AS message_count
                    FROM tickets t
                    WHERE t.ticket_id = :ticket_id
                    """
                ),
                {"ticket_id": ticket_id},
            )
            .mappings()
            .one_or_none()
        )
        if ticket is None:
            raise NotFoundError(f"Ticket {ticket_id} does not exist.")

        if include_thread:
            ticket["messages"] = _rows(
                connection.execute(
                    text(
                        """
                        SELECT message_id, ticket_id, message_text, author,
                               author_role, created_at
                        FROM ticket_messages
                        WHERE ticket_id = :ticket_id
                        ORDER BY created_at ASC, message_id ASC
                        """
                    ),
                    {"ticket_id": ticket_id},
                )
                .mappings()
                .all()
            )
            ticket["status_history"] = _rows(
                connection.execute(
                    text(
                        """
                        SELECT history_id, from_status, to_status, changed_by, changed_at
                        FROM ticket_status_history
                        WHERE ticket_id = :ticket_id
                        ORDER BY changed_at DESC, history_id DESC
                        """
                    ),
                    {"ticket_id": ticket_id},
                )
                .mappings()
                .all()
            )

    return ticket


def list_messages(ticket_id: int) -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM tickets WHERE ticket_id = :ticket_id"),
            {"ticket_id": ticket_id},
        ).scalar_one_or_none()
        if exists is None:
            raise NotFoundError(f"Ticket {ticket_id} does not exist.")

        return _rows(
            connection.execute(
                text(
                    """
                    SELECT message_id, ticket_id, message_text, author,
                           author_role, created_at
                    FROM ticket_messages
                    WHERE ticket_id = :ticket_id
                    ORDER BY created_at ASC, message_id ASC
                    """
                ),
                {"ticket_id": ticket_id},
            )
            .mappings()
            .all()
        )


def stats() -> dict[str, Any]:
    sql = """
        SELECT
            COUNT(*)                                                   AS total,
            COUNT(*) FILTER (WHERE status = 'open')                      AS status_open,
            COUNT(*) FILTER (WHERE status = 'in_progress')               AS status_in_progress,
            COUNT(*) FILTER (WHERE status = 'resolved')                  AS status_resolved,
            COUNT(*) FILTER (WHERE status = 'closed')                    AS status_closed,
            COUNT(*) FILTER (WHERE priority = 'low')                    AS priority_low,
            COUNT(*) FILTER (WHERE priority = 'medium')                 AS priority_medium,
            COUNT(*) FILTER (WHERE priority = 'high')                   AS priority_high,
            COUNT(*) FILTER (WHERE priority = 'urgent')                 AS priority_urgent,
            COUNT(*) FILTER (
                WHERE priority IN ('high', 'urgent')
                  AND status IN ('open', 'in_progress')
            )                                                           AS needs_attention,
            COUNT(*) FILTER (WHERE created_at >= now() - interval '7 days')
                                                                        AS created_last_7d,
            COUNT(*) FILTER (
                WHERE resolved_at IS NOT NULL
                  AND resolved_at >= now() - interval '7 days'
            )                                                           AS resolved_last_7d,
            COUNT(*) FILTER (WHERE assigned_to IS NULL
                               AND status IN ('open', 'in_progress'))   AS unassigned,
            AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 3600.0)
                FILTER (WHERE resolved_at IS NOT NULL)                  AS avg_resolution_hours,
            (SELECT COUNT(*) FROM ticket_messages)                      AS message_count
        FROM tickets
    """
    with get_engine().connect() as connection:
        row = _row(connection.execute(text(sql)).mappings().one()) or {}

    total = row.get("total") or 0
    avg_hours = row.get("avg_resolution_hours")
    return {
        "total": total,
        "message_count": row.get("message_count") or 0,
        "needs_attention": row.get("needs_attention") or 0,
        "unassigned": row.get("unassigned") or 0,
        "created_last_7d": row.get("created_last_7d") or 0,
        "resolved_last_7d": row.get("resolved_last_7d") or 0,
        "avg_resolution_hours": round(avg_hours, 1) if avg_hours is not None else None,
        "by_status": {
            "open": row.get("status_open") or 0,
            "in_progress": row.get("status_in_progress") or 0,
            "resolved": row.get("status_resolved") or 0,
            "closed": row.get("status_closed") or 0,
        },
        "by_priority": {
            "low": row.get("priority_low") or 0,
            "medium": row.get("priority_medium") or 0,
            "high": row.get("priority_high") or 0,
            "urgent": row.get("priority_urgent") or 0,
        },
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def create_ticket(
    *,
    title: str,
    description: str | None,
    status: str,
    priority: str,
    category: str,
    created_by: str,
    assigned_to: str | None = None,
    first_message: str | None = None,
    author_role: str = "customer",
) -> dict[str, Any]:
    """Insert a ticket and, optionally, its opening message atomically."""
    with get_engine().begin() as connection:
        ticket = _row(
            connection.execute(
                text(
                    f"""
                    INSERT INTO tickets
                        (title, description, status, priority, category,
                         created_by, assigned_to, resolved_at)
                    VALUES
                        (:title, :description, :status, :priority, :category,
                         :created_by, :assigned_to,
                         CASE WHEN :status IN ('resolved', 'closed') THEN now() END)
                    RETURNING {_TICKET_COLUMNS_BARE}
                    """
                ),
                {
                    "title": title,
                    "description": description,
                    "status": status,
                    "priority": priority,
                    "category": category,
                    "created_by": created_by,
                    "assigned_to": assigned_to,
                },
            )
            .mappings()
            .one()
        )

        ticket_id = ticket["ticket_id"]
        connection.execute(
            text(
                """
                INSERT INTO ticket_status_history
                    (ticket_id, from_status, to_status, changed_by)
                VALUES (:ticket_id, NULL, :status, :actor)
                """
            ),
            {"ticket_id": ticket_id, "status": status, "actor": created_by},
        )

        if first_message:
            _insert_message(
                connection,
                ticket_id=ticket_id,
                message_text=first_message,
                author=created_by,
                author_role=author_role,
            )

    log.info("Created ticket %s by %s", ticket["ticket_id"], created_by)
    return get_ticket(ticket["ticket_id"])


def _insert_message(
    connection,  # noqa: ANN001 - SQLAlchemy Connection
    *,
    ticket_id: int,
    message_text: str,
    author: str,
    author_role: str,
) -> dict[str, Any]:
    return _row(
        connection.execute(
            text(
                """
                INSERT INTO ticket_messages
                    (ticket_id, message_text, author, author_role)
                VALUES (:ticket_id, :message_text, :author, :author_role)
                RETURNING message_id, ticket_id, message_text, author,
                          author_role, created_at
                """
            ),
            {
                "ticket_id": ticket_id,
                "message_text": message_text,
                "author": author,
                "author_role": author_role,
            },
        )
        .mappings()
        .one()
    )


def add_message(
    *,
    ticket_id: int,
    message_text: str,
    author: str,
    author_role: str = "customer",
) -> dict[str, Any]:
    with get_engine().begin() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM tickets WHERE ticket_id = :ticket_id FOR UPDATE"),
            {"ticket_id": ticket_id},
        ).scalar_one_or_none()
        if exists is None:
            raise NotFoundError(f"Ticket {ticket_id} does not exist.")

        message = _insert_message(
            connection,
            ticket_id=ticket_id,
            message_text=message_text,
            author=author,
            author_role=author_role,
        )

    log.info("Added message %s to ticket %s", message["message_id"], ticket_id)
    return message


def update_ticket(
    ticket_id: int, changes: Mapping[str, Any], *, actor: str
) -> dict[str, Any]:
    """Apply a partial update, auditing any status transition."""
    updates = {
        key: value for key, value in changes.items() if key in _UPDATABLE
    }
    if not updates:
        raise ApiError(
            "No updatable fields were supplied. Allowed fields: "
            + ", ".join(_UPDATABLE),
            code="nothing_to_update",
        )

    with get_engine().begin() as connection:
        current = connection.execute(
            text(
                "SELECT status FROM tickets WHERE ticket_id = :ticket_id FOR UPDATE"
            ),
            {"ticket_id": ticket_id},
        ).scalar_one_or_none()
        if current is None:
            raise NotFoundError(f"Ticket {ticket_id} does not exist.")

        new_status = updates.get("status")
        status_changed = new_status is not None and new_status != current

        # Column names come from the _UPDATABLE whitelist, never from input.
        connection.execute(
            text(build_update_sql(list(updates), status_changed)),
            {**updates, "ticket_id": ticket_id},
        )

        if status_changed:
            connection.execute(
                text(
                    """
                    INSERT INTO ticket_status_history
                        (ticket_id, from_status, to_status, changed_by)
                    VALUES (:ticket_id, :from_status, :to_status, :actor)
                    """
                ),
                {
                    "ticket_id": ticket_id,
                    "from_status": current,
                    "to_status": new_status,
                    "actor": actor,
                },
            )
            if config.LOG_STATUS_CHANGES_AS_MESSAGES:
                _insert_message(
                    connection,
                    ticket_id=ticket_id,
                    message_text=(
                        f"Status changed from {_label(current)} "
                        f"to {_label(new_status)}."
                    ),
                    author=actor,
                    author_role="system",
                )

    log.info(
        "Updated ticket %s by %s (fields=%s)", ticket_id, actor, ",".join(updates)
    )
    return get_ticket(ticket_id)


def delete_ticket(ticket_id: int) -> dict[str, Any]:
    """Delete a ticket; the FK cascade removes its messages and history."""
    with get_engine().begin() as connection:
        summary = connection.execute(
            text(
                """
                SELECT t.title,
                       (SELECT COUNT(*) FROM ticket_messages tm
                         WHERE tm.ticket_id = t.ticket_id) AS message_count
                FROM tickets t
                WHERE t.ticket_id = :ticket_id
                FOR UPDATE
                """
            ),
            {"ticket_id": ticket_id},
        ).mappings().one_or_none()
        if summary is None:
            raise NotFoundError(f"Ticket {ticket_id} does not exist.")

        connection.execute(
            text("DELETE FROM tickets WHERE ticket_id = :ticket_id"),
            {"ticket_id": ticket_id},
        )

    log.info("Deleted ticket %s (%s messages)", ticket_id, summary["message_count"])
    return {
        "ticket_id": ticket_id,
        "title": summary["title"],
        "deleted_messages": summary["message_count"],
    }


def _label(value: str | None) -> str:
    return (value or "unknown").replace("_", " ")
