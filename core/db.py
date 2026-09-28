"""Shared database access.

Everything else in core/ -- and later, every agent -- reads DATABASE_URL and
opens a connection through get_connection() instead of doing it themselves,
so there is exactly one place that knows how to reach Postgres.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

ENV_FILE = Path(__file__).parent.parent / ".env"


def _load_env_file(path: Path) -> None:
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


def get_connection() -> psycopg.Connection:
    """Opens a new connection to the app database.

    Autocommit is on. Every write made through core/ (a logged run, a journal
    entry, a human_queue item) is meant to be durable the moment it happens,
    not held open in a shared transaction that something else might roll
    back. Code that genuinely needs several statements to succeed or fail
    together can still wrap them in `with conn.transaction():`.
    """
    _load_env_file(ENV_FILE)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set (checked environment and .env)")
    conn = psycopg.connect(database_url)
    conn.autocommit = True
    return conn
