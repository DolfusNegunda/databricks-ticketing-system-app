# Reflection & submission

> Draft in your voice — edit freely before submitting. The five numbered
> sentences below are the deliverable; everything after them is supporting
> detail you can draw on if asked.

## Reflection (5 sentences)

The hardest part was authentication rather than anything to do with tickets:
Lakebase will hand you two things that both look like a connection string — a
generated OAuth token and a native Postgres role password — and only the second
one is still working an hour later, which is the kind of bug that surfaces after
the demo rather than during it. Getting that right also meant keeping the string
out of Git entirely by loading it from a Databricks secret through `app.yaml`'s
`valueFrom`, percent-encoding the special characters in the password, and
recycling the connection pool below the credential's lifetime so a pooled socket
is never older than the credential that opened it. Lakebase differs from a
traditional analytics table because it is genuinely transactional Postgres —
`ticket_messages.ticket_id` is a real enforced foreign key with `ON DELETE
CASCADE`, statuses and priorities are `CHECK` constraints the database refuses to
violate, and flipping one ticket to `in_progress` is a single-row `UPDATE` that
commits in milliseconds. The same operation against a Delta analytics table would
be a `MERGE` that rewrites files, and Delta would happily accept an orphaned
message or a misspelled status because it enforces neither key relationships nor
value domains. That difference is exactly the point: Lakebase is where the app's
live state belongs, and Change Data Feed is the bridge that publishes those rows
into Unity Catalog for analytics without an ETL job — which is why I set
`REPLICA IDENTITY FULL` on all three tables up front. The feature I would add
next is an AI triage agent that reads a new ticket's thread and proposes a
priority, category and draft reply, which is why every capability in the UI is
also a plain REST endpoint with one consistent error shape — the agent's tool
surface already exists.

## Submission checklist

- [ ] **Databricks App URL** — from the app's page in **Compute → Apps**
- [ ] **Source code, zipped** — see the command below
- [ ] **Screenshot of the deployed application** — save as `docs/screenshot-app.png`
- [ ] **Screenshot of the Lakebase tables and sample records** — save as
      `docs/screenshot-lakebase.png`
- [ ] **Reflection** — the five sentences above
- [ ] Confirm no passwords, connection strings, API keys or secret values appear
      anywhere in the zip. The connection string lives only in your local `.env`
      (git-ignored) and in the Databricks secret scope — `app.yaml` holds just
      the secret's *name*, and `env.example` holds no values. Quick proof:

      ```bash
      git grep -nE "postgresql://[^<]" -- . ; echo "exit $? (1 = clean)"
      ```

### Zipping the source

Exclude the virtualenv, caches and any local `.env`:

```powershell
# from the repository root
$exclude = @('.venv', '__pycache__', '.git', '.env')
Get-ChildItem -Recurse -Force |
  Where-Object { $n = $_.FullName; -not ($exclude | Where-Object { $n -like "*\$_*" }) } |
  Compress-Archive -DestinationPath ..\nexus-support-source.zip -Force
```

Simplest reliable alternative, if the repo is committed to Git:

```bash
git archive --format=zip --output=../nexus-support-source.zip HEAD
```

`git archive` only includes committed, non-ignored files, so `.venv` and `.env`
cannot leak into the zip.

### Screenshots worth taking

**Application** — the two-pane view with a ticket selected, so one image shows
the list, the status filter chips with counts, the statistics tiles, the message
thread and the status control together. The header chip reading *Lakebase
connected* is worth having in frame.

**Lakebase** — in a SQL editor or notebook against the instance:

```sql
SELECT ticket_id, title, status, priority, category, created_by, created_at
FROM support.tickets
ORDER BY ticket_id;

SELECT message_id, ticket_id, author, author_role, created_at,
       left(message_text, 60) AS message_preview
FROM support.ticket_messages
ORDER BY ticket_id, message_id;
```

To evidence the foreign key in the same screenshot:

```sql
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'support.ticket_messages'::regclass
  AND contype = 'f';
```

## Test evidence

If the submission form allows an appendix, `python scripts/verify_lakebase.py`
prints a pass/fail report against the live instance covering the four things the
brief asks you to confirm — existing tickets load, a ticket can be created, a
message can be added, a status can be updated — plus proof that each change
survives a re-read on a new connection, which is the "changes remain after
refreshing" requirement stated precisely.

## Supporting detail

**Why the credential choice matters.** A connection string whose password is a
generated OAuth token works on the first request and then fails roughly an hour
later. A native Postgres role password does not expire, which is what an
always-on app needs. The app supports both shapes and logs which one is active at
startup, and it also supports having no password at all — in that mode it mints a
token from its own service-principal identity at SQLAlchemy's `do_connect` event,
so every new physical connection gets a fresh credential and nothing above the
engine layer knows credentials exist.

**Keeping the credential out of the repo.** `app.yaml` names the secret rather
than holding it (`valueFrom: lakebase-url`), which means the deployable artifact
is safe to commit and share. The failure paths were written with the same care as
the happy path: a malformed connection string produces a message naming the
missing piece without echoing the value, and `/api/health` reports host,
database, role, schema and auth mode but never the password.

**Where the schema does the work.** Three layers validate every write: the
browser, the Python validators, and `CHECK` constraints in Postgres. The third
layer is the one that actually matters — it holds even if someone writes to the
table from a notebook, and the app maps constraint violations back to friendly
per-field messages rather than surfacing a 500.

**What I would build after triage.** Full-text search using `pg_trgm` instead of
`ILIKE`, SLA timers driven off `ticket_status_history`, and a Unity Catalog
dashboard over the CDF-published tables — the audit table exists partly because
it is what an SLA report would need.
