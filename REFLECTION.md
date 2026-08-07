# Reflection

**Day 1 Homework — Nexus Support, a Lakebase-powered support application**

Dolfus Negunda ·
[Live app](https://ticketing-system-app-7474650241444565.aws.databricksapps.com/) ·
[Repository](https://github.com/DolfusNegunda/databricks-ticketing-system-app)

---

The hardest part was the gap between code that passes every local check and code
that survives managed infrastructure: the app deployed, created its tables, then
could not query them, because Lakebase sits behind a proxy that ignores the libpq
options parameter I had used to set search_path. The error blamed a missing table
when the real cause was a missing setting, so I rewrote the failure paths to
explain themselves — the app now starts in a degraded state and reports the
underlying error on /api/health instead of crash-looping. Lakebase differs from a
traditional analytics table because it is real transactional Postgres:
ticket_messages.ticket_id is an enforced foreign key that cascades on delete,
status and priority are CHECK constraints, and changing a ticket is a single-row
UPDATE in milliseconds rather than a MERGE that rewrites files. Delta would
happily accept an orphaned message or a misspelled status; Lakebase refuses,
which is why live operational state belongs there and Change Data Feed is the
bridge to analytics. Next I would add an AI triage agent that reads a ticket
thread and proposes a priority, category and draft reply — every action in the UI
is already a REST endpoint with one consistent error format, so the agent's tools
already exist.
