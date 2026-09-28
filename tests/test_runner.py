from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from core import runner
from core.db import get_connection
from tests.conftest import FakeAnthropicClient, text_response


class DummyOutput(BaseModel):
    headline: str
    score: int


@pytest.fixture
def prompt_path(tmp_path):
    path = tmp_path / "v1.md"
    path.write_text("You are a dummy test agent. Reply with only JSON matching the schema.")
    return path


def make_agent_def(prompt_path, **overrides) -> runner.AgentDefinition:
    defaults = dict(
        name="dummy-agent",
        model="claude-sonnet-5",
        prompt_version="v1",
        prompt_path=prompt_path,
        output_schema=DummyOutput,
    )
    defaults.update(overrides)
    return runner.AgentDefinition(**defaults)


def test_run_agent_succeeds_on_first_try(business_id, prompt_path):
    valid_json = json.dumps({"headline": "great business", "score": 90})
    client = FakeAnthropicClient([text_response(valid_json)])

    result = runner.run_agent(
        make_agent_def(prompt_path),
        business_id=business_id,
        input_data={"industry": "testing"},
        client=client,
    )

    assert isinstance(result, DummyOutput)
    assert result.headline == "great business"
    assert result.score == 90
    assert len(client.messages.calls) == 1


def test_run_agent_retries_once_then_succeeds(business_id, prompt_path):
    invalid_json = json.dumps({"headline": "missing score field"})
    valid_json = json.dumps({"headline": "fixed it", "score": 42})
    client = FakeAnthropicClient([text_response(invalid_json), text_response(valid_json)])

    result = runner.run_agent(
        make_agent_def(prompt_path),
        business_id=business_id,
        input_data={"industry": "testing"},
        client=client,
    )

    assert result.headline == "fixed it"
    assert result.score == 42
    assert len(client.messages.calls) == 2


def test_run_agent_gives_up_after_one_retry_and_queues_for_a_human(business_id, prompt_path):
    always_invalid = json.dumps({"headline": "still missing score"})
    client = FakeAnthropicClient([text_response(always_invalid), text_response(always_invalid)])

    with pytest.raises(runner.AgentOutputInvalid):
        runner.run_agent(
            make_agent_def(prompt_path),
            business_id=business_id,
            input_data={"industry": "testing"},
            client=client,
        )

    assert len(client.messages.calls) == 2

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT item_type, reason FROM human_queue WHERE business_id = %s",
                (business_id,),
            )
            hq_row = cur.fetchone()

            cur.execute(
                "SELECT entry_type, actor FROM client_journal WHERE business_id = %s AND entry_type = 'error'",
                (business_id,),
            )
            journal_row = cur.fetchone()
    finally:
        conn.close()

    assert hq_row is not None
    assert hq_row[0] == "agent_output_invalid"
    assert "dummy-agent" in hq_row[1]

    assert journal_row is not None
    assert journal_row[1] == "dummy-agent"


def test_run_agent_treats_api_failure_like_a_validation_failure(business_id, prompt_path):
    class ExplodingMessages:
        def create(self, **kwargs):
            raise RuntimeError("network blip")

    class ExplodingClient:
        messages = ExplodingMessages()

    with pytest.raises(runner.AgentOutputInvalid):
        runner.run_agent(
            make_agent_def(prompt_path),
            business_id=business_id,
            input_data={"industry": "testing"},
            client=ExplodingClient(),
        )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reason FROM human_queue WHERE business_id = %s",
                (business_id,),
            )
            hq_row = cur.fetchone()
    finally:
        conn.close()

    assert hq_row is not None
