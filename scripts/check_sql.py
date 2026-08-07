"""Validate every SQL statement this app can emit, without a database.

Uses ``pglast``, which wraps PostgreSQL's own parser (libpg_query), so a pass
here means real Postgres accepts the grammar -- including the plpgsql inside the
trigger function and the seed block.

    pip install pglast
    python scripts/check_sql.py

Covers:
  * sql/001_schema.sql and sql/002_seed.sql
  * every filter/sort permutation of the ticket list query
  * every UPDATE shape the PATCH endpoint can build
  * the static queries embedded in support_app/repository.py
"""

from __future__ import annotations

import ast
import itertools
import re
import sys
from pathlib import Path


def _project_root() -> Path:
    """Locate the repository root.

    __file__ is undefined when this script is pasted into a notebook cell, so
    fall back to walking up from the working directory looking for the package.
    """
    try:
        return Path(__file__).resolve().parent.parent
    except NameError:
        start = Path.cwd().resolve()
        for directory in (start, *start.parents):
            if (directory / "support_app").is_dir():
                return directory
        raise SystemExit(
            f"Could not find the project root from {start}. Run this from "
            "inside the repository, or set ROOT to the folder holding "
            "support_app/."
        )


ROOT = _project_root()
sys.path.insert(0, str(ROOT))

try:
    from pglast import get_postgresql_version, parse_sql
    # parse_plpgsql_json is the actual parser call. The higher-level
    # parse_plpgsql() wrapper json-decodes its output, and that decode step is
    # broken in pglast 8.x for functions RETURNS TRIGGER -- it fails even on a
    # trivially valid body. Parsing here, before the decode, is what validates
    # the plpgsql; the negative controls below prove it still rejects bad input.
    from pglast.parser import parse_plpgsql_json
except ImportError as exc:  # pragma: no cover
    sys.exit(f"pglast is required: pip install pglast ({exc})")

from support_app import config, repository  # noqa: E402

# SQLAlchemy named parameters are not Postgres syntax; NULL is a valid stand-in
# in every position we use them (including LIMIT/OFFSET).
_NAMED_PARAM = re.compile(r"(?<![:\w]):([a-z_][a-z0-9_]*)")

failures: list[str] = []
passed = 0


def check(label: str, sql: str, *, plpgsql: bool = False) -> None:
    global passed
    prepared = _NAMED_PARAM.sub("NULL", sql)
    try:
        if plpgsql:
            parse_plpgsql_json(prepared)
        else:
            statements = parse_sql(prepared)
            if not statements:
                raise ValueError("parsed to zero statements")
        passed += 1
        print(f"  PASS  {label}")
    except Exception as exc:  # ParseError and friends
        failures.append(label)
        print(f"  FAIL  {label}\n        {exc}")


def render(filename: str) -> str:
    text = (ROOT / "sql" / filename).read_text(encoding="utf-8")
    return text.replace("__SCHEMA__", config.LAKEBASE_SCHEMA)


# ---------------------------------------------------------------------------
print(f"PostgreSQL grammar: {'.'.join(str(p) for p in get_postgresql_version())}")
print(f"Target schema:      {config.LAKEBASE_SCHEMA}\n")

print("== sql/ files ==")
schema_sql = render("001_schema.sql")
seed_sql = render("002_seed.sql")
check("001_schema.sql", schema_sql)
check("002_seed.sql", seed_sql)

print("\n== plpgsql bodies ==")
trigger = re.search(
    r"CREATE OR REPLACE FUNCTION.*?\$fn\$;", schema_sql, re.DOTALL
)
if trigger:
    check("set_updated_at() trigger body", trigger.group(0), plpgsql=True)
else:
    failures.append("could not locate trigger function in 001_schema.sql")
    print("  FAIL  could not locate trigger function in 001_schema.sql")

do_block = re.search(r"DO \$seed\$(.*?)\$seed\$;", seed_sql, re.DOTALL)
if do_block:
    wrapped = (
        "CREATE FUNCTION __seed_check() RETURNS void LANGUAGE plpgsql AS $$"
        f"{do_block.group(1)}$$;"
    )
    check("seed DO block body", wrapped, plpgsql=True)
else:
    failures.append("could not locate DO block in 002_seed.sql")
    print("  FAIL  could not locate DO block in 002_seed.sql")

print("\n== negative controls (the checker must reject these) ==")
BAD = {
    "plpgsql missing END": (
        "CREATE FUNCTION bad1() RETURNS TRIGGER LANGUAGE plpgsql AS $b$ "
        "BEGIN RETURN NEW; $b$;",
        True,
    ),
    "plpgsql typo'd keyword": (
        "CREATE FUNCTION bad2() RETURNS TRIGGER LANGUAGE plpgsql AS $b$ "
        "BEGIN RETRUN NEW; END; $b$;",
        True,
    ),
    "sql unbalanced parenthesis": ("SELECT count(* FROM tickets;", False),
    "sql typo'd keyword": ("SELCT 1 FROM tickets;", False),
}
for label, (sql, is_plpgsql) in BAD.items():
    try:
        if is_plpgsql:
            parse_plpgsql_json(sql)
        else:
            parse_sql(sql)
    except Exception:
        passed += 1
        print(f"  PASS  rejected: {label}")
    else:
        failures.append(f"negative control not rejected: {label}")
        print(f"  FAIL  accepted invalid SQL: {label}")

# ---------------------------------------------------------------------------
print("\n== ticket list query permutations ==")
FILTER_KEYS = ("statuses", "priorities", "categories", "search", "assigned_to", "created_by")
SAMPLES = {
    "statuses": ["open", "in_progress"],
    "priorities": ["high"],
    "categories": ["billing"],
    "search": "timeout",
    "assigned_to": "agent@example.com",
    "created_by": "user@example.com",
}

permutations = 0
for active_count in range(len(FILTER_KEYS) + 1):
    for active in itertools.combinations(FILTER_KEYS, active_count):
        kwargs = {key: (SAMPLES[key] if key in active else None) for key in FILTER_KEYS}
        where, _ = repository._ticket_filters(**kwargs)
        for sort_key, order_by in config.SORT_OPTIONS.items():
            list_sql, count_sql = repository.build_list_sql(where, order_by)
            prepared = _NAMED_PARAM.sub("NULL", list_sql)
            try:
                parse_sql(prepared)
                parse_sql(_NAMED_PARAM.sub("NULL", count_sql))
                permutations += 1
            except Exception as exc:
                label = f"list sort={sort_key} filters={active or ('none',)}"
                failures.append(label)
                print(f"  FAIL  {label}\n        {exc}")
passed += permutations
print(f"  PASS  {permutations} list/count query permutations parsed")

# ---------------------------------------------------------------------------
print("\n== UPDATE permutations ==")
UPDATABLE = repository._UPDATABLE
update_ok = 0
for size in range(1, len(UPDATABLE) + 1):
    for columns in itertools.combinations(UPDATABLE, size):
        for status_changed in (False, True):
            if status_changed and "status" not in columns:
                continue
            sql = repository.build_update_sql(list(columns), status_changed)
            try:
                parse_sql(_NAMED_PARAM.sub("NULL", sql))
                update_ok += 1
            except Exception as exc:
                label = f"update columns={columns} status_changed={status_changed}"
                failures.append(label)
                print(f"  FAIL  {label}\n        {exc}")
passed += update_ok
print(f"  PASS  {update_ok} UPDATE permutations parsed")

# ---------------------------------------------------------------------------
print("\n== static queries in repository.py ==")
module_source = (ROOT / "support_app" / "repository.py").read_text(encoding="utf-8")
tree = ast.parse(module_source)
module_globals = vars(repository)

found = 0
skipped = 0
for node in ast.walk(tree):
    if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "text"):
        continue
    if not node.args:
        continue
    arg = node.args[0]

    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        sql = arg.value
    elif isinstance(arg, ast.JoinedStr):
        try:
            sql = eval(  # noqa: S307 - evaluating our own source's f-strings
                compile(ast.Expression(arg), "<repo-sql>", "eval"), module_globals, {}
            )
        except NameError:
            # Interpolates a local (the dynamic builders), covered above.
            skipped += 1
            continue
    else:
        skipped += 1
        continue

    found += 1
    check(f"repository.py line {arg.lineno}", sql)

print(f"  ({skipped} dynamic f-string(s) skipped -- covered by the permutation checks)")

# ---------------------------------------------------------------------------
print(f"\n{passed} checks passed, {len(failures)} failed")
if failures:
    for item in failures:
        print(f"  - {item}")
    sys.exit(1)
print("SQL ALL GREEN")
