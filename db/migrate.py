#!/usr/bin/env python3
"""Applies the SQL files in db/migrations, in order, tracking progress in a
schema_migrations table.

Migrations are append-only (see CLAUDE.md): once a migration has been applied,
its file must never change. Before applying anything, this script recomputes
the checksum of every already-applied migration file and compares it to the
checksum stored when it was applied. If any differ, it refuses to run at all.

Usage:
    python db/migrate.py                  # applies to DATABASE_URL (the dev database)
    python db/migrate.py <database-url>    # applies to a different database, e.g. aeo_test:
                                            #   python db/migrate.py "$TEST_DATABASE_URL"
"""

import hashlib
import os
import re
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
ENV_FILE = Path(__file__).parent.parent / ".env"

DOLLAR_QUOTE_RE = re.compile(r"\$[A-Za-z0-9_]*\$")


def load_env_file(path: Path) -> None:
    """Minimal .env loader, so DATABASE_URL doesn't have to be exported by hand.
    Doesn't override a value already set in the real environment.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def checksum(sql_text: str) -> str:
    return hashlib.sha256(sql_text.encode("utf-8")).hexdigest()


def split_statements(sql_text: str) -> list[str]:
    """Splits a .sql file into individual statements on top-level semicolons.

    Migration 013 defines two PL/pgSQL functions with dollar-quoted bodies
    (containing their own semicolons), so a naive split on ';' would cut
    those bodies in half. This tracks quote/comment state so it only splits
    on a semicolon that is actually a statement terminator.
    """
    statements = []
    current = []
    i, n = 0, len(sql_text)
    in_single = in_double = in_line_comment = in_block_comment = False
    dollar_tag = None

    while i < n:
        ch = sql_text[i]

        if dollar_tag:
            if sql_text.startswith(dollar_tag, i):
                current.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
            else:
                current.append(ch)
                i += 1
            continue

        if in_line_comment:
            current.append(ch)
            i += 1
            if ch == "\n":
                in_line_comment = False
            continue

        if in_block_comment:
            if sql_text.startswith("*/", i):
                current.append("*/")
                i += 2
                in_block_comment = False
            else:
                current.append(ch)
                i += 1
            continue

        if in_single:
            current.append(ch)
            i += 1
            if ch == "'":
                in_single = False
            continue

        if in_double:
            current.append(ch)
            i += 1
            if ch == '"':
                in_double = False
            continue

        if sql_text.startswith("--", i):
            in_line_comment = True
            current.append("--")
            i += 2
            continue
        if sql_text.startswith("/*", i):
            in_block_comment = True
            current.append("/*")
            i += 2
            continue
        if ch == "'":
            in_single = True
            current.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            current.append(ch)
            i += 1
            continue
        if ch == "$":
            match = DOLLAR_QUOTE_RE.match(sql_text, i)
            if match:
                dollar_tag = match.group(0)
                current.append(dollar_tag)
                i += len(dollar_tag)
                continue
        if ch == ";":
            statements.append("".join(current).strip())
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)

    return [s for s in statements if s]


def ensure_migrations_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def main() -> None:
    load_env_file(ENV_FILE)
    override_url = sys.argv[1] if len(sys.argv) > 1 else None
    database_url = override_url or os.environ.get("DATABASE_URL")
    if not database_url:
        sys.exit("DATABASE_URL is not set (checked command-line argument, environment, and .env)")

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        sys.exit(f"No migration files found in {MIGRATIONS_DIR}")

    conn = psycopg.connect(database_url)
    # Each migration is applied via an explicit conn.transaction() block below,
    # so autocommit is needed here -- otherwise psycopg keeps everything inside
    # one ambient transaction that only commits if something later calls
    # conn.commit(), and a nested conn.transaction() just becomes a savepoint.
    conn.autocommit = True
    ensure_migrations_table(conn)

    with conn.cursor() as cur:
        cur.execute("SELECT version, checksum FROM schema_migrations")
        applied = dict(cur.fetchall())

    pending = []
    for path in files:
        version = path.stem
        sql_text = path.read_text()
        file_checksum = checksum(sql_text)

        if version in applied:
            if applied[version] != file_checksum:
                sys.exit(
                    f"Refusing to run: {path.name} has changed since it was applied "
                    f"(checksum mismatch). Migrations are append-only: add a new "
                    f"migration instead of editing this one."
                )
            continue

        pending.append((version, path, sql_text, file_checksum))

    if not pending:
        print("Database is up to date. No migrations to apply.")
        return

    for version, path, sql_text, file_checksum in pending:
        print(f"Applying {path.name} ...")
        with conn.transaction():
            for statement in split_statements(sql_text):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (version, file_checksum),
            )

    print(f"Applied {len(pending)} migration(s).")


if __name__ == "__main__":
    main()
