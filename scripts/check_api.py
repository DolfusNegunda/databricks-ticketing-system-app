"""Offline API checks -- no Lakebase connection required.

Verifies that the app boots without a database, that every route is wired, that
input validation runs before any query is issued, and that every failure comes
back in the documented error envelope.

    python scripts/check_api.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Make sure nothing points at a real instance, and skip the boot migration.
for key in ("PGHOST", "PGUSER", "LAKEBASE_URL", "PGPASSWORD", "LAKEBASE_INSTANCE_NAME"):
    os.environ.pop(key, None)
os.environ["LAKEBASE_AUTO_MIGRATE"] = "false"
os.environ["APP_DEFAULT_USER"] = "offline.check@example.com"
os.environ.setdefault("APP_BRAND_NAME", "Nexus Support")

from support_app import create_app  # noqa: E402

client = create_app().test_client()

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


print("\n== page, liveness, assets ==")
page = client.get("/")
check("GET / returns 200", page.status_code == 200, page.status_code)
html = page.get_data(as_text=True)
check("page carries the brand name", "Nexus Support" in html)
check("page references the logo mark", "logo-mark.svg" in html)
check("page injects the current user", "offline.check@example.com" in html)

live = client.get("/healthz")
check("GET /healthz ok without a database", live.status_code == 200)

for asset in (
    "/static/css/app.css",
    "/static/js/app.js",
    "/static/img/logo.svg",
    "/static/img/logo-mark.svg",
    "/static/img/favicon.svg",
):
    response = client.get(asset)
    check(f"{asset} is served", response.status_code == 200, response.status_code)

print("\n== /api/meta drives the UI vocabulary ==")
meta = client.get("/api/meta").get_json()
check("statuses", meta["statuses"] == ["open", "in_progress", "resolved", "closed"])
check("priorities", meta["priorities"] == ["low", "medium", "high", "urgent"])
check("categories", len(meta["categories"]) == 5, meta["categories"])
check("client roles exclude 'system'", meta["author_roles"] == ["customer", "agent"])
check("sort options", "activity" in meta["sort_options"], meta["sort_options"])
check("limits published", meta["limits"]["title_max"] == 200)

print("\n== identity is taken from the Databricks headers ==")
for header, expected in (
    ("X-Forwarded-Email", "dana@example.com"),
    ("X-Forwarded-Preferred-Username", "pref@example.com"),
    ("X-Forwarded-User", "user@example.com"),
):
    body = client.get("/api/meta", headers={header: expected}).get_json()
    check(f"{header} -> current_user", body["current_user"] == expected, body["current_user"])
check(
    "falls back to APP_DEFAULT_USER",
    client.get("/api/meta").get_json()["current_user"] == "offline.check@example.com",
)

print("\n== an unreachable Lakebase is reported, never crashed ==")
health = client.get("/api/health")
check("GET /api/health -> 503", health.status_code == 503, health.status_code)
body = health.get_json()
check("status is degraded", body["status"] == "degraded", body.get("status"))
check("auth mode is OAuth", body["lakebase"]["auth_mode"] == "databricks-oauth")
check(
    "no credential field is exposed",
    not any("pass" in key.lower() or "token" in key.lower() for key in body["lakebase"]),
    list(body["lakebase"]),
)
for path in ("/api/tickets", "/api/stats", "/api/tickets/1"):
    response = client.get(path)
    check(f"GET {path} -> 503 envelope", response.status_code == 503, response.status_code)
    check(f"GET {path} names the failure", "error" in response.get_json())

# The frontend reads `error.message` off every non-2xx response (see request()
# in static/js/app.js). /api/health is the one route that also returns a
# diagnostic body, so it has to satisfy both shapes -- otherwise the banner
# shows a generic "Request failed (503)" instead of the missing setting.
print("\n== every failure is readable by the frontend contract ==")
FAILING = [
    ("GET", "/api/health", None),
    ("GET", "/api/tickets", None),
    ("GET", "/api/stats", None),
    ("GET", "/api/tickets/1", None),
    ("GET", "/api/tickets?status=bogus", None),
    ("POST", "/api/tickets", {}),
    ("PATCH", "/api/tickets/1", {}),
]
for method, path, payload in FAILING:
    response = client.open(path, method=method, json=payload)
    body = response.get_json() or {}
    error = body.get("error") or {}
    check(f"{method} {path} carries error.message",
          bool(error.get("message")), body)
    check(f"{method} {path} carries error.code", bool(error.get("code")), body)

health_error = client.get("/api/health").get_json()["error"]["message"]
check("health 503 explains what is missing, not just the status code",
      "PGHOST" in health_error and "503" not in health_error, health_error)

print("\n== validation happens before any query ==")
cases: list[tuple[str, dict, int, str | None]] = [
    ("empty body", {}, 422, "title"),
    ("title too short", {"title": "ab"}, 422, "title"),
    ("title too long", {"title": "x" * 201}, 422, "title"),
    ("unknown status", {"title": "Valid title", "status": "banana"}, 422, "status"),
    ("unknown priority", {"title": "Valid title", "priority": "nope"}, 422, "priority"),
    ("unknown category", {"title": "Valid title", "category": "nope"}, 422, "category"),
    ("client claims system role",
     {"title": "Valid title", "author_role": "system"}, 422, "author_role"),
]
for label, payload, status, field in cases:
    response = client.post("/api/tickets", json=payload)
    check(f"POST /api/tickets {label} -> {status}", response.status_code == status,
          response.status_code)
    if field:
        fields = response.get_json()["error"].get("fields", {})
        check(f"  ... names the '{field}' field", field in fields, fields)

multi = client.post(
    "/api/tickets", json={"title": "Valid title", "priority": "x", "category": "y"}
).get_json()["error"]["fields"]
check("every bad field is reported at once", len(multi) == 2, multi)

check(
    "non-JSON body -> missing_body",
    client.post("/api/tickets", data="nope", content_type="application/json")
    .get_json()["error"]["code"] == "missing_body",
)
check(
    "JSON array body -> invalid_body",
    client.post("/api/tickets", json=["a"]).get_json()["error"]["code"] == "invalid_body",
)
check(
    "blank message -> 422 on message_text",
    "message_text"
    in client.post("/api/tickets/1/messages", json={"message_text": "   "})
    .get_json()["error"]["fields"],
)
check(
    "empty PATCH -> nothing_to_update",
    client.patch("/api/tickets/1", json={}).get_json()["error"]["code"]
    == "nothing_to_update",
)
check("PATCH bad status -> 422", client.patch("/api/tickets/1",
      json={"status": "banana"}).status_code == 422)

print("\n== query parameters are validated too ==")
bad_status = client.get("/api/tickets?status=bogus")
check("unknown status filter -> 400", bad_status.status_code == 400)
check("message lists the allowed statuses",
      "in_progress" in bad_status.get_json()["error"]["message"])
check("unknown sort -> 400", client.get("/api/tickets?sort=sideways").status_code == 400)
check("non-numeric limit -> 400", client.get("/api/tickets?limit=abc").status_code == 400)
check("comma-separated filters reach the data layer",
      client.get("/api/tickets?priority=high,urgent").status_code == 503)

print("\n== HTTP errors use the same envelope ==")
for label, response, status in (
    ("non-numeric ticket id", client.get("/api/tickets/abc"), 404),
    ("unknown api route", client.get("/api/nope"), 404),
    ("wrong method", client.put("/api/tickets/1"), 405),
):
    check(f"{label} -> {status} envelope",
          response.status_code == status and "error" in response.get_json(),
          response.status_code)

print("\n== supplied connection string is parsed correctly ==")
from support_app import config, db  # noqa: E402

FAKE_PW = "fake-not-a-real-password"
FAKE_HOST = "demo-instance.database.cloud.databricks.com"
GOOD = (
    f"postgresql://svc_user:{FAKE_PW}@{FAKE_HOST}:5432"
    "/databricks_postgres?sslmode=require"
)


def parses(dsn: str):
    try:
        return db._parse_dsn(dsn), None
    except db.LakebaseUnavailable as exc:
        return None, str(exc)


url, err = parses(GOOD)
check("standard DSN parses", err is None, err)
check("  host extracted", url and url.host == FAKE_HOST, url and url.host)
check("  username extracted", url and url.username == "svc_user")
check("  database extracted", url and url.database == "databricks_postgres")
check("  port extracted", url and url.port == 5432, url and url.port)
check("  sslmode preserved", url and url.query.get("sslmode") == "require")
check("  driver forced to psycopg2",
      url and url.drivername == "postgresql+psycopg2", url and url.drivername)

alias, err = parses(GOOD.replace("postgresql://", "postgres://", 1))
check("postgres:// alias is normalised", err is None and alias.host == FAKE_HOST, err)

quoted, err = parses(f'  "{GOOD}"  ')
check("surrounding quotes and whitespace are stripped",
      err is None and quoted.host == FAKE_HOST, err)

encoded, err = parses(
    f"postgresql://svc_user:pa%40ss%3Aword@{FAKE_HOST}:5432/databricks_postgres"
)
check("percent-encoded password decodes to the literal value",
      err is None and encoded.password == "pa@ss:word",
      err or (encoded and encoded.password))

_, err = parses("postgresql://svc_user:pw@/databricks_postgres")
check("missing host is rejected", err is not None and "host" in err.lower(), err)

_, err = parses(f"postgresql://{FAKE_HOST}:5432/databricks_postgres")
check("missing username is rejected", err is not None and "username" in err.lower(), err)

_, err = parses("this is not a connection string")
check("garbage is rejected", err is not None, err)
check("  ... and the error explains percent-encoding",
      err is not None and "percent-encode" in err, err)
check("  ... without echoing the value",
      err is not None and "this is not a connection string" not in err, err)

# target_summary() reads config at call time, so set the module attributes
# directly and restore them afterwards.
#
# IMPORTANT for anyone extending this block: do NOT call db.get_engine() in here.
# The engine is a module-level singleton, and it decides once at construction
# whether to register the OAuth token hook (based on _static_password()). Building
# it against these temporary values would freeze that decision against config
# that is about to be restored, and every later check would fail confusingly.
# Everything below touches pure functions only.
saved = (config.LAKEBASE_URL, config.PGPASSWORD, config.PGHOST, config.PGUSER)
try:
    config.LAKEBASE_URL = GOOD
    config.PGPASSWORD = ""
    config.PGHOST = "ignored.example.com"
    config.PGUSER = "ignored_user"

    summary = db.target_summary()
    check("connection string overrides PG* values",
          summary["host"] == FAKE_HOST and summary["user"] == "svc_user", summary)
    check("config_source reports the connection string",
          summary["config_source"] == "connection-string", summary["config_source"])
    check("auth_mode reports password auth",
          summary["auth_mode"] == "connection-string", summary["auth_mode"])
    check("instance name derived from the host",
          summary["instance"] == "demo-instance", summary["instance"])
    check("password IS used for the connection",
          db._static_password() == FAKE_PW)
    check("password NEVER appears in the health summary",
          FAKE_PW not in repr(summary), summary)

    config.LAKEBASE_URL = (
        f"postgresql://svc_user@{FAKE_HOST}:5432/databricks_postgres"
    )
    summary = db.target_summary()
    check("passwordless connection string falls back to OAuth",
          summary["auth_mode"] == "databricks-oauth", summary["auth_mode"])
    check("  ... while still using its host and user",
          summary["host"] == FAKE_HOST and summary["user"] == "svc_user", summary)

    config.LAKEBASE_URL = "postgresql://:@/"
    summary = db.target_summary()
    check("an unparseable connection string is reported, not raised",
          summary["config_source"] == "connection-string (unparseable)",
          summary["config_source"])
finally:
    config.LAKEBASE_URL, config.PGPASSWORD, config.PGHOST, config.PGUSER = saved

print(f"\n{total - len(failures)}/{total} checks passed")
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("API ALL GREEN")
