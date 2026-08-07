# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A support-ticket app deployed as a **Databricks App**, storing its operational
data in **Lakebase** (Databricks-managed Postgres). Flask API + server-rendered
page + vanilla JS. No build step, no bundler, no npm.

Live: https://ticketing-system-app-7474650241444565.aws.databricksapps.com/

## Commands

```bash
# setup
python -m venv .venv && .venv/Scripts/activate      # Windows; bin/activate elsewhere
pip install -r requirements.txt
pip install pglast                                   # only needed for check_sql.py

# run locally (needs LAKEBASE_URL in .env; see env.example)
python app.py                                        # http://localhost:8000

# verification -- see "Testing" below
python scripts/check_api.py          # offline: routing, validation, error envelope, DSN parsing
python scripts/check_sql.py          # offline: every SQL statement, real PostgreSQL parser
python scripts/check_connection.py   # live, read-only: can we reach Lakebase, and as whom
python scripts/verify_lakebase.py    # live, read-write: full CRUD round trip, cleans up after itself
```

## Testing

There is no pytest suite. Verification is four standalone scripts, each of which
prints `PASS`/`FAIL` per check, exits non-zero on failure, and ends with a
count. They are linear — there is no way to run a single check, but each section
prints a `== header ==` so `| grep -A20 "section name"` works for narrowing
output.

The two offline scripts must pass before any commit: **100 API checks** and
**372 SQL checks**. `check_sql.py` uses `pglast` (libpg_query — PostgreSQL's own
parser), so a pass means real Postgres accepts the grammar, including the
plpgsql in the trigger and the seed `DO` block. It parses both SQL files, all
256 filter/sort permutations of the list query, all 95 `UPDATE` shapes, and
every static query in `repository.py`. It also runs negative controls, so a pass
proves the checker still detects broken SQL.

The live scripts need a real Lakebase connection and are the only way to verify
runtime behaviour — the offline suites cannot see the database, the browser, or
the proxy.

## Architecture

Request flow: `routes.py` (HTTP + validation) → `repository.py` (all SQL) →
`db.py` (engine, credentials, bootstrap) → Lakebase.

- **`support_app/config.py`** — environment → validated settings, plus the
  domain vocabulary (`STATUSES`, `PRIORITIES`, `CATEGORIES`, `SORT_OPTIONS`).
- **`support_app/db.py`** — connection string parsing, credential strategy,
  engine construction, idempotent bootstrap.
- **`support_app/repository.py`** — every SQL statement in the app. Nothing
  else talks to the database.
- **`support_app/errors.py`** — `ApiError` hierarchy and the field validators.
- **`support_app/__init__.py`** — app factory; the *only* place exceptions
  become HTTP responses.
- **`sql/001_schema.sql`, `sql/002_seed.sql`** — applied on every boot, both
  idempotent. `__SCHEMA__` is substituted at runtime.

### Authentication: two paths, `LAKEBASE_URL` wins

1. A connection string from a Databricks secret (`app.yaml` holds only the
   secret's *name* via `valueFrom`).
2. No credential at all — `db._TokenProvider` mints a short-lived Postgres token
   from the app's own OAuth service principal and injects it in SQLAlchemy's
   `do_connect` event.

A connection string with a user but no password is valid: it supplies host and
user, the token supplies the secret. `pool_recycle` is deliberately below the
credential lifetime so a pooled socket never outlives the credential that opened
it.

### Bootstrap on boot

`create_app()` calls `db.ensure_ready()`, which applies the schema and seeds when
`tickets` is empty. Failures are **captured, never raised** — the app must start
even against an unreachable database so it can report the cause on
`/api/health`. State lands in `db.bootstrap_state()`.

### The error envelope

Every failure leaves the API as `{"error": {"code", "message", "fields?"}}`.
Handlers live only in `__init__.py`; routes and repository raise and let them
translate. `/api/health` returns a diagnostic body *and* this envelope, because
the frontend reads `error.message` off every non-2xx response.

## Hard-won constraints — do not undo these

Each of these was a production failure. The comments in the code explain why;
this is the index.

1. **`search_path` is set with a `SET` statement in the `connect` event, never
   the libpq `options` parameter.** Lakebase sits behind a proxy that reserves
   `options` for endpoint routing and silently drops it, so every unqualified
   query fails with "relation does not exist". The bootstrap's emptiness check
   is additionally schema-qualified so it can never depend on `search_path`.
2. **Multi-statement SQL runs on the raw DBAPI cursor** (`_run_sql_script`), not
   `exec_driver_sql`. For a statement with no parameters SQLAlchemy passes an
   empty `immutabledict`, which psycopg2's C extension rejects as a
   non-sequence.
3. **`ensure_ready()` catches bare `Exception`.** It runs inside `create_app()`,
   so anything escaping kills the deployment at startup. A narrower tuple once
   let a `TypeError` through and crash-looped it.
4. **The `REPLICA IDENTITY FULL` block swallows every error** (`WHEN OTHERS`).
   It is a CDF optimisation and must never be able to roll back the schema
   transaction. Consequence: a failure there is invisible, so
   `verify_lakebase.py` reads `pg_class.relreplident` and reports the real
   setting.
5. **`[hidden] { display: none !important; }` in `app.css` is load-bearing.**
   `[hidden]` lives in the UA stylesheet, so any component rule setting
   `display` beats it. `check_api.py` asserts this rule exists.
6. **`replaceChildren` stringifies `null` into the text `"null"`** — filter
   conditional children before passing them.

## Conventions

- **Nothing user-supplied is ever formatted into SQL.** Only three things are
  interpolated into SQL text: the schema name (regex-validated in `config.py`),
  column names from `repository._UPDATABLE`, and `ORDER BY` fragments from
  `config.SORT_OPTIONS`. Everything else is a bound parameter.
- **The frontend builds DOM with `createElement`/`textContent` only.** No
  `innerHTML`, so ticket text can never be interpreted as markup.
- **Domain vocabulary is defined twice on purpose** — as tuples in `config.py`
  and as `CHECK` constraints in `001_schema.sql`. Changing a status, priority or
  category means changing both, plus the constraint-name mapping in
  `__init__.py` that turns violations into per-field messages.
- **`author_role: "system"` is reserved for the app** and rejected from clients;
  it is what status-change messages are written as.
- Writes are attributed to the signed-in Databricks user, read from
  `X-Forwarded-Email` (see `routes.current_user`).
- Static assets are cache-busted by mtime via the `asset_url()` template helper.
  Without it a redeploy leaves browsers running the previous `app.js`.

## Deploying

The app deploys from a **workspace Git folder**, which **does not auto-pull**.
Clicking Deploy redeploys whatever the folder already has — pull first, or your
change will appear not to have landed. Then hard-refresh the browser once.

`notebooks/setup_secrets.py` is a Databricks notebook that stores the connection
string as a secret; it splits widget creation and reading across two cells
deliberately, because doing both in one cell reads the box before the value is
typed and silently stores an empty secret.
