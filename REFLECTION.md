# Reflection — Day 1 Homework

Nexus Support, a Lakebase-powered support application · Dolfus Negunda

**Databricks App URL:** https://ticketing-system-app-7474650241444565.aws.databricksapps.com/

**Source code:** https://github.com/DolfusNegunda/databricks-ticketing-system-app

---

**What was the most difficult part?**

The hardest part was the gap between code that passes every local check and code
that survives managed infrastructure: my app deployed, created its tables, and
then could not query them, because Lakebase sits behind a proxy that silently
ignores the libpq options parameter I had used to set search_path. The error
blamed a missing table when the real cause was a missing setting, so I rewrote
the failure paths to explain themselves — the app now starts in a degraded state
and reports the underlying error on /api/health instead of crash-looping.

**How is Lakebase different from storing this data in a traditional analytics
table?**

Lakebase is real transactional Postgres, so it enforces what an analytics table
cannot: ticket_messages.ticket_id is a foreign key that cascades on delete,
status and priority are CHECK constraints, and moving a ticket to in_progress is
a single-row UPDATE that commits in milliseconds rather than a MERGE that
rewrites files. A Delta table would happily store an orphaned message or a
misspelled status, which is why live operational state belongs in Lakebase, with
Change Data Feed as the bridge that publishes it to analytics.

**What feature would you add next?**

An AI triage agent that reads a ticket thread and proposes a priority, category
and draft reply — every action in the UI is already a REST endpoint with one
consistent error format, so the agent's tool surface already exists.

---

*Five sentences total: two on the difficulty, two on the difference, one on the
next feature.*
