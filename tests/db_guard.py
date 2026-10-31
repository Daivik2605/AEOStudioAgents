"""The test suite must never run against the dev database (CLAUDE.md: "Tests only ever touch aeo_test").

It once did: twelve `core-test-<hex>.example` businesses made by the runner tests on 28 Sep 2026 are still in the dev
database. tests/conftest.py already sent every test to TEST_DATABASE_URL; this adds the checks that make a wrong setup
fail loudly instead of quietly writing into the wrong place:

  1. TEST_DATABASE_URL must be set;
  2. its database name must end in "_test" (aeo_test);
  3. it must not be the same server and database as DATABASE_URL (the dev database);
  4. and, once connected, the database that really answered must be that same *_test database.

Messages never print a URL: it holds a password.
"""

from __future__ import annotations

import psycopg
from psycopg.conninfo import conninfo_to_dict

REQUIRED_SUFFIX = "_test"


class UnsafeTestDatabase(RuntimeError):
    """The tests were about to run against a database that is not the test database."""


def _where(url: str) -> tuple[str, str, str]:
    parts = conninfo_to_dict(url)
    return (parts.get("host") or "localhost", str(parts.get("port") or "5432"), parts.get("dbname") or "")


def check_urls(test_url: str | None, dev_url: str | None) -> str:
    """Raises UnsafeTestDatabase unless `test_url` is a *_test database that is not the dev database. Returns its name."""
    if not test_url:
        raise UnsafeTestDatabase("TEST_DATABASE_URL is not set (checked the environment and .env): tests refuse to run "
                                 "against DATABASE_URL, so they never write test data into the real dev database")
    host, port, name = _where(test_url)
    if not name.endswith(REQUIRED_SUFFIX):
        raise UnsafeTestDatabase(f"TEST_DATABASE_URL points at the database '{name}', which does not end in "
                                 f"'{REQUIRED_SUFFIX}'. Tests only run against the test database (aeo_test).")
    if dev_url and _where(dev_url) == (host, port, name):
        raise UnsafeTestDatabase(f"TEST_DATABASE_URL and DATABASE_URL are the same database ('{name}'). "
                                 "Tests refuse to run against the dev database.")
    return name


def check_connected(conn: psycopg.Connection, expected_name: str) -> None:
    """After connecting: the database that answered must be the one we meant."""
    actual = conn.execute("SELECT current_database()").fetchone()[0]
    if actual != expected_name or not actual.endswith(REQUIRED_SUFFIX):
        raise UnsafeTestDatabase(f"the tests connected to '{actual}', expected '{expected_name}'. "
                                 "Tests only run against the test database.")
