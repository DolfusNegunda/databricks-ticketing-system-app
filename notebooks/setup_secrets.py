# Databricks notebook source
# MAGIC %md
# MAGIC # Nexus Support — store the Lakebase connection string
# MAGIC
# MAGIC Run this **once**, before deploying the app. It puts your Lakebase
# MAGIC connection string into a Databricks secret scope so the app can read it at
# MAGIC runtime without the value ever being committed to Git.
# MAGIC
# MAGIC ### Before you start
# MAGIC
# MAGIC Have your connection string ready. It looks like:
# MAGIC
# MAGIC ```
# MAGIC postgresql://<role>:<password>@<instance>.database.cloud.databricks.com:5432/databricks_postgres?sslmode=require
# MAGIC ```
# MAGIC
# MAGIC Two things worth checking first:
# MAGIC
# MAGIC 1. **Use a native Postgres role password, not a generated OAuth token.** A
# MAGIC    token expires after about an hour, and the app will work and then start
# MAGIC    failing. A native role password lasts until you rotate it.
# MAGIC 2. **Percent-encode special characters in the password.** `@` → `%40`,
# MAGIC    `:` → `%3A`, `/` → `%2F`, `?` → `%3F`, `#` → `%23`, `%` → `%25`. An
# MAGIC    unencoded `@` is the most common cause of "authentication failed".
# MAGIC
# MAGIC ### How to use this notebook
# MAGIC
# MAGIC 1. Run **Cell 1** — creates the scope and adds an input box at the top.
# MAGIC 2. **Paste your connection string into that box.** Do not type it into a
# MAGIC    code cell; code cells are saved in the notebook's revision history.
# MAGIC 3. Run **Cell 2** — stores the secret and removes the box.
# MAGIC 4. Attach the secret to your app (instructions in the last cell).
# MAGIC 5. **Delete this notebook** when you are done.
# MAGIC
# MAGIC Nothing here ever prints your password.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cell 1 — create the scope and the input box

# COMMAND ----------

from databricks.sdk import WorkspaceClient

SCOPE = "nexus-support"
KEY = "lakebase-url"

w = WorkspaceClient()

try:
    w.secrets.create_scope(scope=SCOPE)
    print(f"Created secret scope '{SCOPE}'.")
except Exception as exc:
    print(f"Scope '{SCOPE}' already exists (that is fine): {exc}")

dbutils.widgets.text("dsn", "", "Lakebase connection string")

print("\nNow paste your connection string into the 'Lakebase connection string'")
print("box at the TOP of this notebook, then run Cell 2.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cell 2 — store it
# MAGIC
# MAGIC Only run this **after** pasting the value into the box above. If the box is
# MAGIC still empty this cell stops with an error rather than storing a blank
# MAGIC secret, which would fail later in a confusing way.

# COMMAND ----------

from urllib.parse import urlparse

dsn = dbutils.widgets.get("dsn").strip().strip('"').strip("'")

if not dsn:
    raise ValueError(
        "The 'dsn' box is empty. Paste the connection string into the box at "
        "the top of the notebook, then re-run this cell."
    )
if not dsn.startswith(("postgresql://", "postgres://")):
    raise ValueError(
        "That does not look like a Postgres connection string — it should start "
        "with postgresql:// . (Value not shown.)"
    )

parsed = urlparse(dsn)
if not parsed.hostname:
    raise ValueError("The connection string has no host. (Value not shown.)")
if not parsed.username:
    raise ValueError("The connection string has no username. (Value not shown.)")
if not parsed.password:
    raise ValueError(
        "The connection string has no password. If the password contains @ : / "
        "? # or %, it must be percent-encoded. (Value not shown.)"
    )

w.secrets.put_secret(scope=SCOPE, key=KEY, string_value=dsn)
dbutils.widgets.remove("dsn")

# Confirm without revealing: compare lengths of what went in and what came back.
stored_ok = len(dbutils.secrets.get(scope=SCOPE, key=KEY)) == len(dsn)

print(f"Stored secret     : {SCOPE}/{KEY}")
print(f"Verified round-trip: {stored_ok}")
print(f"Keys in scope     : {[s.key for s in w.secrets.list_secrets(scope=SCOPE)]}")
print("\nConnection string points at (password not shown):")
print(f"  host    : {parsed.hostname}")
print(f"  port    : {parsed.port or 5432}")
print(f"  user    : {parsed.username}")
print(f"  database: {parsed.path.lstrip('/') or 'databricks_postgres'}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cell 3 — optional: grant the app read access
# MAGIC
# MAGIC Adding the Secret resource in the Apps UI normally handles this. Only run
# MAGIC this if the deployed app reports that it cannot read the secret.
# MAGIC
# MAGIC Find the service principal's **client id** on the app's **Authorization**
# MAGIC tab, then set it below and run the cell.

# COMMAND ----------

APP_SERVICE_PRINCIPAL = ""  # e.g. "1234abcd-..."; leave blank to skip

if APP_SERVICE_PRINCIPAL:
    from databricks.sdk.service.workspace import AclPermission

    w.secrets.put_acl(
        scope=SCOPE, principal=APP_SERVICE_PRINCIPAL, permission=AclPermission.READ
    )
    print(f"Granted READ on '{SCOPE}' to {APP_SERVICE_PRINCIPAL}.")
    for acl in w.secrets.list_acls(scope=SCOPE):
        print(f"  {acl.principal}: {acl.permission}")
else:
    print("Skipped — set APP_SERVICE_PRINCIPAL above if the app cannot read the secret.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next steps
# MAGIC
# MAGIC The secret exists. Now point the app at it.
# MAGIC
# MAGIC On your app: **Edit → Resources → Add resource → Secret**
# MAGIC
# MAGIC | Field | Value |
# MAGIC | --- | --- |
# MAGIC | Secret scope | `nexus-support` |
# MAGIC | Secret key | `lakebase-url` |
# MAGIC | **Resource key** | **`lakebase-url`** — must match `valueFrom` in `app.yaml` |
# MAGIC | Permission | Can read |
# MAGIC
# MAGIC Then **Deploy**. The app creates its schema, tables and demo data on first
# MAGIC boot, so there is nothing else to run.
# MAGIC
# MAGIC Confirm at `https://<your-app-url>/api/health` — you want `"status": "ok"`
# MAGIC and a row count under `database`.
# MAGIC
# MAGIC ### Finally
# MAGIC
# MAGIC **Delete this notebook.** Widget values persist in notebook state and
# MAGIC revision history can retain what was typed into them. The secret is safely
# MAGIC in the scope now; this notebook is no longer needed.
# MAGIC
# MAGIC To rotate the connection string later, re-clone it from Git and run it
# MAGIC again — `put_secret` overwrites the existing key in place, and the app
# MAGIC picks up the new value on its next restart.
