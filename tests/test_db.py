from __future__ import annotations

import pytest

from core import db


def test_get_connection_can_query_real_database():
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone() == (1,)
    finally:
        conn.close()


def test_get_connection_is_autocommit():
    conn = db.get_connection()
    try:
        assert conn.autocommit is True
    finally:
        conn.close()


def test_get_connection_requires_database_url(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Point at an empty file so the real project .env can't supply the value.
    monkeypatch.setattr(db, "ENV_FILE", tmp_path / "no_such_file.env")

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        db.get_connection()
