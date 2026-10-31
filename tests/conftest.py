"""Shared test fixtures.

These tests run against a real Postgres database (never mocked -- only the
Anthropic client is), so `docker compose up -d` must already be running and
that database must already be migrated. To keep test data out of the real
dev database, tests always run against aeo_test, never against aeo: the
_use_test_database fixture below points DATABASE_URL at TEST_DATABASE_URL
for the whole test session before anything else touches the database. Set
up aeo_test once with:

    docker compose exec postgres psql -U postgres -c "CREATE DATABASE aeo_test;"
    python db/migrate.py "$TEST_DATABASE_URL"

Per PLAN.md, nothing is ever deleted, so rows tests create do pile up in
aeo_test over time -- harmless, since it's a throwaway database. Each test
that needs a business gets its own row with a unique domain, so tests never
collide with each other.
"""

from __future__ import annotations

import io
import os
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from core.db import ENV_FILE, _load_env_file, get_connection
from tests.db_guard import check_connected, check_urls


@pytest.fixture(scope="session", autouse=True)
def _use_test_database() -> None:
    """Sends every test to the test database, and refuses to run if that is not a safe *_test database (tests/db_guard.py)."""
    _load_env_file(ENV_FILE)
    dev_url = os.environ.get("DATABASE_URL")                     # read before it is replaced below
    test_url = os.environ.get("TEST_DATABASE_URL")
    expected = check_urls(test_url, dev_url)
    os.environ["DATABASE_URL"] = test_url
    conn = get_connection()
    try:
        check_connected(conn, expected)
    finally:
        conn.close()


@pytest.fixture
def business_id() -> str:
    conn = get_connection()
    try:
        domain = f"core-test-{uuid.uuid4().hex}.example"
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO businesses (domain, website_url) VALUES (%s, %s) RETURNING id",
                (domain, f"https://{domain}"),
            )
            return str(cur.fetchone()[0])
    finally:
        conn.close()


# ---- Fake Anthropic client -------------------------------------------------
# Minimal stand-ins for the bits of the real anthropic SDK response shape
# that core/llm.py reads: response.content (blocks with .type/.text or
# .type/.name/.input/.id), response.stop_reason, response.usage.*_tokens.


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str = "toolu_fake"
    type: str = "tool_use"


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class FakeResponse:
    content: list[Any]
    stop_reason: str
    usage: FakeUsage


class _FakeMessages:
    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeAnthropicClient ran out of queued responses")
        return self._responses.pop(0)


class FakeAnthropicClient:
    """Queue up responses; each .messages.create() call pops the next one."""

    def __init__(self, responses: list[FakeResponse]):
        self.messages = _FakeMessages(responses)


def text_response(text: str, input_tokens: int = 10, output_tokens: int = 5) -> FakeResponse:
    return FakeResponse(
        content=[FakeTextBlock(text=text)],
        stop_reason="end_turn",
        usage=FakeUsage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def tool_use_response(
    name: str, tool_input: dict[str, Any], input_tokens: int = 10, output_tokens: int = 5
) -> FakeResponse:
    return FakeResponse(
        content=[FakeToolUseBlock(name=name, input=tool_input)],
        stop_reason="tool_use",
        usage=FakeUsage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


# ---- `aeo diagnose` tests must never reach the real internet --------------------

@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Every diagnose test passes in a Fetcher wired to a fake transport. If one forgets,
    this makes the default client fail loudly instead of making a real request."""
    def _refuse():
        raise AssertionError("a test tried to make a real HTTP request: pass in a Fetcher with a fake transport")
    monkeypatch.setattr("diagnose.fetch.make_client", _refuse)


# ---- the cost gate needs a person at a keyboard; tests pretend there is one ------

@pytest.fixture(autouse=True)
def _pretend_a_terminal(monkeypatch):
    """pytest has no terminal, and the cost gate now refuses to run without one. Tests act as
    the person: a terminal is present and anyone asked to approve the cost hears "yes". The tests
    that check the no-terminal refusal switch it back off (`no_tty`)."""
    monkeypatch.setattr("audit.cost_gate.stdin_is_tty", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\n" * 50))


# ---- client files must never land in the real clients/ folder ----------------------------------

@pytest.fixture(autouse=True)
def _clients_folder_in_tmp(tmp_path_factory, monkeypatch):
    """Anything that writes client files without being told where (an import, a CLI command) would
    otherwise create folders in the real clients/. Point it at a throwaway folder for every test."""
    from core import client_files
    root = tmp_path_factory.mktemp("clients")
    monkeypatch.setattr(client_files, "CLIENTS_ROOT", root)
    return root
