# Reflection & submission guide

---

# PART 1 — The reflection (paste this)

> Five sentences, written from what actually happened building and deploying
> this. Edit the wording to sound like you before submitting.

The most difficult part had nothing to do with tickets or the UI: it was the
seam between my application and Lakebase's managed infrastructure, where my
schema and API passed every offline check and then failed on first deploy
because the app created its tables and immediately could not query them.
Lakebase runs behind a connection proxy that silently drops the libpq `options`
parameter I was using to set `search_path`, so an unqualified `SELECT` could not
resolve a table created one statement earlier, and the whole bootstrap
transaction rolled back — leaving an error that blamed a missing table when the
real cause was a missing setting. That taught me to make failures
self-describing rather than merely handled: I moved `search_path` to a plain
`SET` statement, schema-qualified the bootstrap so it can never depend on it,
and made the app start in a degraded state that reports the underlying error on
`/api/health` instead of crash-looping. Lakebase differs from a traditional
analytics table because it is genuinely transactional Postgres —
`ticket_messages.ticket_id` is an enforced foreign key with `ON DELETE CASCADE`,
statuses and priorities are `CHECK` constraints the database refuses to violate,
and moving a ticket to `in_progress` is a single-row `UPDATE` that commits in
milliseconds, whereas the same change against a Delta table would be a `MERGE`
that rewrites files, and Delta would happily accept an orphaned message or a
misspelled status because it enforces neither key relationships nor value
domains. The two are complements rather than substitutes, which is why I set
`REPLICA IDENTITY FULL` up front so Change Data Feed can publish these rows into
Unity Catalog as `lb_*_history` Delta tables with no ETL job — and the feature I
would add next is an AI triage agent that reads a new ticket's thread and
proposes a priority, category and draft reply, which is why every capability in
the UI is also a REST endpoint with one consistent error envelope, so the
agent's tool surface already exists.

### If you need the three answers separately

**What was the most difficult part?** Authentication and connectivity, not
application logic. Everything passed offline; every real failure was in the seam
between the app and managed infrastructure — a proxy dropping the `search_path`
parameter, a driver rejecting SQLAlchemy's empty parameter object, a too-narrow
`except` clause turning a recoverable bootstrap error into a crash loop. The fix
in each case was to make the failure explain itself.

**How is Lakebase different from a traditional analytics table?** It is OLTP
Postgres, so it enforces things Delta does not: a real foreign key with
`ON DELETE CASCADE`, `CHECK` constraints on status/priority/category, and
single-row `UPDATE`s that commit in milliseconds instead of a `MERGE` that
rewrites files. Lakebase holds the app's live state; Delta is for analysing it,
and CDF is the bridge between them.

**What feature would you add next?** An AI triage agent that reads a thread and
proposes priority, category and a draft reply. The REST API and its consistent
error envelope were built to be that agent's tool surface.

---

# PART 2 — Finalising your submission

## Step 1 — Take the two screenshots

**Screenshot A — the deployed application.** Open a ticket first, so one image
shows the list, status filter chips with counts, the six statistic tiles, the
message thread and the status control together. Make sure the header chip reads
**Lakebase connected**. Save as `docs/screenshot-app.png`.

**Screenshot B — the Lakebase tables and records.** In a SQL editor connected to
your Lakebase instance, run these three and capture the results:

```sql
-- 1. the tables (they are in the `support` schema, not `public`)
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

This is what puts them inside the zip automatically:

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

Use `git archive` rather than zipping the folder by hand. It includes **only
committed, non-ignored files**, so `.venv/`, `__pycache__/` and any local `.env`
cannot end up in the submission.

### What the zip will contain

```
README.md                  full documentation and deployment guide
REFLECTION.md              this file
app.py                     entry point
app.yaml                   Databricks Apps config (no credentials)
requirements.txt
env.example                local-dev template, no values
.gitignore

support_app/               config, db, repository, errors, routes, factory
sql/001_schema.sql         tables, FK, indexes, trigger, view, CDF readiness
sql/002_seed.sql           idempotent demo data
static/                    css, js, logo/favicon SVGs
templates/index.html
scripts/                   check_api, check_sql, check_connection, verify_lakebase
docs/                      your two screenshots
```

## Step 4 — Final safety check

Confirm no credential is in the zip. This should print nothing but synthetic
test values:

```bash
git grep -nE "postgresql://[A-Za-z0-9_]+:[^@]+@" -- .
```

Expected hits, both in `scripts/check_api.py`, both fake:
`fake-not-a-real-password` and `svc_user:pw@/` (a deliberately-malformed test
case). Anything else, stop and investigate.

Your real connection string lives only in your local `.env` (git-ignored) and in
the Databricks secret scope. `app.yaml` contains only the secret's *name*.

## Step 5 — Submit

| Item | What to provide |
| --- | --- |
| Databricks App URL | From **Compute → Apps → your app** |
| Source code, zipped | `nexus-support-source.zip` from Step 3 |
| Screenshot of the app | `docs/screenshot-app.png` |
| Screenshot of Lakebase | `docs/screenshot-lakebase.png` |
| Reflection | Part 1 above, in your own words |

---

## Optional extras, if you want them

**Test evidence.** `python scripts/verify_lakebase.py` prints a pass/fail report
against your live instance covering the four things the brief asks you to
confirm — tickets load, one can be created, a message added, a status changed —
plus proof each change survives a re-read on a new connection, which is the
"changes remain after refreshing" requirement stated precisely. It cleans up
after itself.

**Bonus challenges already met** (worth naming in your submission):

| Bonus | Where |
| --- | --- |
| Priority **and** category | Both, enforced by `CHECK` constraints, editable in the detail pane |
| Filtering by status | Multi-select chips with live counts, plus priority, category, search and four sort orders |
| Validation and helpful errors | Three layers — browser, `errors.py`, `CHECK` constraints — naming the field and reporting all problems at once |
| Ticket statistics | Six live tiles including mean resolution time computed in SQL |
| Delete with confirmation | Modal naming the ticket and message count, requires typing `DELETE` |
| Improved visual design | Design system built from the product mark's own palette |

**Beyond the brief**, if asked what else you did: an append-only
`ticket_status_history` audit table, automatic system messages on status change,
a `/api/health` readiness probe, identity taken from the signed-in Databricks
user, CDF readiness, and 469 automated checks (97 API + 372 SQL validated
against PostgreSQL's own parser).
