# Reflection

**Day 1 Homework — Nexus Support, a Lakebase-powered support application**

Dolfus Negunda

---

The hardest part had nothing to do with tickets or the interface; it was the gap
between code that passes every local check and code that survives contact with
managed infrastructure. The app deployed, created its tables, and then could not
query them, because Lakebase sits behind a connection proxy that ignores the
libpq `options` parameter I had used to set `search_path` — so a lookup failed on
a table created one statement earlier, and the whole setup transaction rolled
back. The diagnosis was harder than the fix, since the error named a missing
table when the real problem was a missing setting, so I rebuilt the failure paths
to explain themselves: `search_path` now comes from a plain `SET`, the setup is
schema-qualified so it cannot depend on it, and the app starts in a degraded
state that reports the underlying error at `/api/health` instead of crash-looping.
Lakebase differs from a traditional analytics table because it is real
transactional Postgres — `ticket_messages.ticket_id` is an enforced foreign key
that cascades on delete, status and priority are `CHECK` constraints the database
refuses to break, and moving a ticket to `in_progress` is a single-row `UPDATE`
that commits in milliseconds, where the same edit against Delta would be a
`MERGE` that rewrites files, in a format that would happily accept an orphaned
message or a misspelled status. The two are complements rather than alternatives,
which is why I set `REPLICA IDENTITY FULL` from the start so Change Data Feed can
publish these rows into Unity Catalog without an ETL job, and the feature I would
add next is an AI triage agent that reads a ticket thread and proposes a
priority, category and draft reply — every action in the UI is also a REST
endpoint with a single consistent error format, so the agent's tools already
exist.
