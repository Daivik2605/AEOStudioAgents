"""The guard that keeps the tests off the dev database (PLAN.md step 25c, item 6)."""

from __future__ import annotations

import os

import pytest

from core.db import get_connection
from tests.db_guard import UnsafeTestDatabase, check_connected, check_urls

DEV = "postgresql://user:secret@localhost:5434/aeo"
TEST = "postgresql://user:secret@localhost:5434/aeo_test"


def test_a_proper_test_database_passes():
    assert check_urls(TEST, DEV) == "aeo_test"
    assert check_urls(TEST, None) == "aeo_test"


@pytest.mark.parametrize("test_url, dev_url, fragment", [
    (None, DEV, "TEST_DATABASE_URL is not set"),
    ("", DEV, "TEST_DATABASE_URL is not set"),
    (DEV, DEV, "does not end in"),                                                    # the dev database passed off as the test one
    ("postgresql://user:secret@localhost:5434/aeo_staging", DEV, "does not end in"),
    ("postgresql://user:secret@localhost:5434/postgres", DEV, "does not end in"),
    (TEST, TEST, "same database"),                                                    # a dev database that is itself called aeo_test
])
def test_anything_else_is_refused(test_url, dev_url, fragment):
    with pytest.raises(UnsafeTestDatabase, match=fragment):
        check_urls(test_url, dev_url)


def test_a_refusal_never_prints_the_password():
    with pytest.raises(UnsafeTestDatabase) as caught:
        check_urls("postgresql://user:hunter2@localhost:5434/aeo", DEV)
    assert "hunter2" not in str(caught.value) and "secret" not in str(caught.value)


def test_a_test_database_on_another_server_with_the_dev_name_is_not_the_dev_database():
    assert check_urls("postgresql://user:secret@db.example:5432/aeo_test", DEV) == "aeo_test"


def test_the_connection_these_tests_use_is_the_test_database():
    conn = get_connection()
    try:
        name = conn.execute("SELECT current_database()").fetchone()[0]
        assert name.endswith("_test")
        check_connected(conn, name)
        with pytest.raises(UnsafeTestDatabase):
            check_connected(conn, "aeo")                                          # it answered as the test database, not as dev
    finally:
        conn.close()
    assert os.environ["DATABASE_URL"] == os.environ["TEST_DATABASE_URL"]
