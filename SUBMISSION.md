# Submission guide

Working checklist for finalising the Day 1 homework. This file is for you — the
thing you actually submit is [REFLECTION.md](REFLECTION.md) plus the items below.

## What to submit

| Item | Value |
| --- | --- |
| Databricks App URL | https://ticketing-system-app-7474650241444565.aws.databricksapps.com/ |
| Source code, zipped | `nexus-support-source.zip` — see step 3 |
| Screenshot of the app | `docs/screenshot-app.png` |
| Screenshot of Lakebase | `docs/screenshot-lakebase.png` |
| Reflection | [REFLECTION.md](REFLECTION.md) |
| Repository (optional) | https://github.com/DolfusNegunda/databricks-ticketing-system-app |

---

## Step 1 — Take the two screenshots

**Screenshot A — the deployed application.** Open a ticket first, so one image
shows the list, the status filter chips with counts, the six statistic tiles, the
message thread and the status control together. Check the header chip reads
**Lakebase connected**. Save as `docs/screenshot-app.png`.

**Screenshot B — the Lakebase tables and records.** In a SQL editor connected to
the Lakebase instance, run these and capture the results.

```sql
-- 1. the tables (they live in the `support` schema, not `public`)
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema = 'support'
ORDER BY table_name;

-- 2. sample records
SELECT ticket_id, title, status, priority, category, created_by, created_at
FROM support.tickets
ORDER BY ticket_id;

SELECT message_id, ticket_id, author, author_role, created_at,
       left(message_text, 60) AS message_preview
FROM support.ticket_messages
ORDER BY ticket_id, message_id;

-- 3. proof of the foreign key -- worth having in the same image
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'support.ticket_messages'::regclass AND contype = 'f';
```

Save as `docs/screenshot-lakebase.png`.

## Step 2 — Commit the screenshots

This is what puts them inside the zip.

```bash
cd lakebase-ai-support-app
git add docs/screenshot-app.png docs/screenshot-lakebase.png
git commit -m "Add submission screenshots"
git push
```

## Step 3 — Build the zip

```bash
git archive --format=zip --output=../nexus-support-source.zip HEAD
```

Use `git archive` rather than zipping the folder by hand: it includes only
committed, non-ignored files, so `.venv/`, `__pycache__/` and any local `.env`
cannot end up in the submission.

### Contents

```
README.md                  documentation and deployment guide
REFLECTION.md              the reflection
SUBMISSION.md              this file
app.py                     entry point
app.yaml                   Databricks Apps config (no credentials)
requirements.txt
env.example                local-dev template, no values
.gitignore

support_app/               config, db, repository, errors, routes, app factory
sql/001_schema.sql         tables, FK, indexes, trigger, view, CDF readiness
sql/002_seed.sql           idempotent demo data
static/                    css, js, logo and favicon SVGs
templates/index.html
notebooks/setup_secrets.py notebook that stores the connection string as a secret
scripts/                   check_api, check_sql, check_connection, verify_lakebase
docs/                      the two screenshots
```

## Step 4 — Safety check

No credential may appear in the zip. This should return only synthetic values:

```bash
git grep -nE "postgresql://[A-Za-z0-9_]+:[^@]+@" -- .
```

Expected hits, all in `scripts/check_api.py`, all fake test fixtures:
`fake-not-a-real-password`, a percent-encoding test, and a deliberately
malformed `svc_user:pw@/`. Anything else — stop and investigate.

The real connection string exists only in the Databricks secret scope
(`nexus-support/lakebase-url`) and any local `.env`, which is git-ignored.
`app.yaml` holds only the secret's name.

## Step 5 — Optional test evidence

```bash
python scripts/verify_lakebase.py
```

Prints a pass/fail report against the live instance covering the four things the
brief asks you to confirm — tickets load, one can be created, a message added, a
status changed — plus proof each change survives a re-read on a new connection,
which is the "changes remain after refreshing" requirement stated precisely. It
deletes its own test ticket afterwards.

---

## Requirements coverage

| Requirement | Where |
| --- | --- |
| Two related tables with FK | `sql/001_schema.sql` — `ticket_messages.ticket_id` → `tickets.ticket_id`, `ON DELETE CASCADE` |
| 3+ tickets, 2+ messages each, 2+ statuses | 6 tickets, 15 messages, all four statuses |
| View all tickets | Left panel, `GET /api/tickets` |
| Select a ticket, view messages | Right panel, `GET /api/tickets/<id>` |
| Create a ticket | New ticket modal, `POST /api/tickets` |
| Add a message | Composer, `POST /api/tickets/<id>/messages` |
| Update status | Status dropdown, `PATCH /api/tickets/<id>` |
| Reads and writes go to Lakebase | `support_app/repository.py` — every read and write is SQL against Lakebase |
| Deployed and tested | Databricks Apps, URL above |

## Bonus challenges

| Bonus | How it is met |
| --- | --- |
| Priority **and** category | Both, enforced by `CHECK` constraints, editable in the detail pane |
| Filtering by status | Multi-select chips with live counts, plus priority, category, free-text search across message bodies, and four sort orders |
| Input validation and helpful errors | Three layers — browser, `errors.py`, `CHECK` constraints — naming the offending field and reporting all problems at once |
| Ticket statistics | Six live tiles, including mean resolution time computed in SQL |
| Delete with confirmation | Modal naming the ticket and its message count, requires typing `DELETE` |
| Improved visual design | Design system built from the product mark's own palette |

## Beyond the brief

An append-only `ticket_status_history` audit table; automatic system messages
when a status changes; a `/api/health` readiness probe; writes attributed to the
signed-in Databricks user via the forwarded identity header; Change Data Feed
readiness; no database credential anywhere in the repository; and 472 automated
checks — 100 API and 372 SQL statements validated against PostgreSQL's own
parser.
