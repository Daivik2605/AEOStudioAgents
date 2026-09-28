from __future__ import annotations

import pytest

from core import journal
from core.db import get_connection


def test_write_journal_inserts_a_row(business_id):
    entry_id = journal.write_journal(
        business_id=business_id,
        entry_type="note",
        description="test note",
        actor="pytest",
        details={"foo": "bar"},
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT business_id, entry_type, description, actor, details "
                "FROM client_journal WHERE id = %s",
                (entry_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None
    assert str(row[0]) == business_id
    assert row[1] == "note"
    assert row[2] == "test note"
    assert row[3] == "pytest"
    assert row[4] == {"foo": "bar"}


def test_write_journal_rejects_unknown_entry_type(business_id):
    with pytest.raises(ValueError, match="entry_type"):
        journal.write_journal(
            business_id=business_id,
            entry_type="not_a_real_entry_type",
            description="should never be written",
            actor="pytest",
        )


def test_write_journal_uses_the_given_connection(business_id, monkeypatch):
    def _fail():
        raise AssertionError("write_journal should not have opened its own connection")

    monkeypatch.setattr(journal, "get_connection", _fail)

    conn = get_connection()
    try:
        entry_id = journal.write_journal(
            business_id=business_id,
            entry_type="note",
            description="used the caller's connection",
            actor="pytest",
            conn=conn,
        )
        assert entry_id is not None
    finally:
        conn.close()
