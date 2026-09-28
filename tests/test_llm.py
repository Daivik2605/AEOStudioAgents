from __future__ import annotations

import pytest

from core import llm
from core.db import get_connection
from tests.conftest import FakeAnthropicClient, text_response, tool_use_response


def _last_run_row(business_id: str) -> tuple:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT agent, model, prompt_version, input, output, input_tokens,
                       output_tokens, cost_usd, latency_ms, status, error
                FROM runs
                WHERE business_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (business_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def test_successful_call_returns_text_and_logs_a_run(business_id):
    client = FakeAnthropicClient([text_response("hello there", input_tokens=100, output_tokens=50)])

    result = llm.call(
        business_id=business_id,
        agent="test-agent",
        model="claude-sonnet-5",
        prompt_version="v1",
        system="you are a test",
        messages=[{"role": "user", "content": "hi"}],
        client=client,
    )

    assert result.text == "hello there"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cost_usd == pytest.approx(100 / 1_000_000 * 2.0 + 50 / 1_000_000 * 10.0)

    row = _last_run_row(business_id)
    assert row is not None
    agent, model, prompt_version, input_payload, output_payload, in_tok, out_tok, cost, latency, status, error = row
    assert agent == "test-agent"
    assert model == "claude-sonnet-5"
    assert prompt_version == "v1"
    assert input_payload == {"system": "you are a test", "messages": [{"role": "user", "content": "hi"}]}
    assert output_payload == {"text": "hello there"}
    assert in_tok == 100
    assert out_tok == 50
    assert status == "success"
    assert error is None
    assert latency >= 0


def test_failed_call_is_still_logged(business_id):
    class ExplodingMessages:
        def create(self, **kwargs):
            raise RuntimeError("the API is down")

    class ExplodingClient:
        messages = ExplodingMessages()

    with pytest.raises(RuntimeError, match="the API is down"):
        llm.call(
            business_id=business_id,
            agent="test-agent",
            model="claude-sonnet-5",
            prompt_version="v1",
            system="you are a test",
            messages=[{"role": "user", "content": "hi"}],
            client=ExplodingClient(),
        )

    row = _last_run_row(business_id)
    assert row is not None
    status, error = row[9], row[10]
    assert status == "failed"
    assert "the API is down" in error


def test_unknown_model_raises_before_calling_the_api(business_id):
    calls = []

    class TrackingMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("should never be called")

    class TrackingClient:
        messages = TrackingMessages()

    with pytest.raises(ValueError, match="no price configured"):
        llm.call(
            business_id=business_id,
            agent="test-agent",
            model="some-model-nobody-priced",
            prompt_version="v1",
            system="you are a test",
            messages=[{"role": "user", "content": "hi"}],
            client=TrackingClient(),
        )

    assert calls == []


def test_tool_use_loop_calls_handler_and_returns_final_text(business_id):
    client = FakeAnthropicClient(
        [
            tool_use_response("lookup", {"query": "O'land"}, input_tokens=20, output_tokens=10),
            text_response("done, found it", input_tokens=30, output_tokens=15),
        ]
    )
    handler_calls = []

    def lookup_handler(tool_input):
        handler_calls.append(tool_input)
        return "found: O'land Stations"

    result = llm.call(
        business_id=business_id,
        agent="test-agent",
        model="claude-sonnet-5",
        prompt_version="v1",
        system="you are a test",
        messages=[{"role": "user", "content": "look it up"}],
        tools=[{"name": "lookup", "description": "look something up", "input_schema": {"type": "object"}}],
        tool_handlers={"lookup": lookup_handler},
        client=client,
    )

    assert handler_calls == [{"query": "O'land"}]
    assert result.text == "done, found it"
    assert result.input_tokens == 50
    assert result.output_tokens == 25
    assert len(client.messages.calls) == 2


def test_tool_use_without_a_handler_fails_and_logs(business_id):
    client = FakeAnthropicClient([tool_use_response("mystery_tool", {})])

    with pytest.raises(ValueError, match="no handler registered"):
        llm.call(
            business_id=business_id,
            agent="test-agent",
            model="claude-sonnet-5",
            prompt_version="v1",
            system="you are a test",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "mystery_tool", "description": "?", "input_schema": {"type": "object"}}],
            client=client,
        )

    row = _last_run_row(business_id)
    assert row[9] == "failed"
    assert "no handler registered" in row[10]
