"""Runs one agent: loads its prompt and Pydantic output schema, calls the
model through core/llm.py, and checks that what comes back actually matches
the schema before handing it to the rest of the system.

The agreed contract for an agent (see PLAN.md section 17.1) is: a prompt
file (the system prompt), a Pydantic model describing the required output,
and a model name. The agent's prompt is responsible for telling the model to
answer with nothing but JSON matching that schema -- this file just checks
that promise was kept.

If the model's answer doesn't validate, this tries once more, with the error
explained back to the model. If it still doesn't validate, this writes to
human_queue and stops -- it never returns a guessed or partial result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from core import llm
from core.db import get_connection
from core.journal import write_journal


class AgentOutputInvalid(Exception):
    """Raised when an agent's output still doesn't match its schema after one retry."""


@dataclass
class AgentDefinition:
    name: str  # stored in runs.agent and client_journal.actor
    model: str
    prompt_version: str  # must match the prompt file, e.g. "v1" for prompts/<name>/v1.md
    prompt_path: Path
    output_schema: type[BaseModel]
    tools: list[dict[str, Any]] | None = None
    tool_handlers: dict[str, Callable[[dict[str, Any]], Any]] | None = None
    max_tokens: int = 4096


def run_agent(
    agent_def: AgentDefinition,
    *,
    business_id: str | UUID,
    input_data: dict[str, Any],
    client: Any = None,
) -> BaseModel:
    system = agent_def.prompt_path.read_text()
    messages: list[dict[str, Any]] = [{"role": "user", "content": json.dumps(input_data)}]

    last_error: str | None = None
    last_raw_text: str = ""

    for attempt in range(2):
        if attempt == 1:
            messages.append({"role": "assistant", "content": last_raw_text})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That response was invalid: {last_error}\n"
                        "Reply again with ONLY valid JSON matching the required "
                        "schema, and nothing else."
                    ),
                }
            )

        try:
            result = llm.call(
                business_id=business_id,
                agent=agent_def.name,
                model=agent_def.model,
                prompt_version=agent_def.prompt_version,
                system=system,
                messages=messages,
                tools=agent_def.tools,
                tool_handlers=agent_def.tool_handlers,
                max_tokens=agent_def.max_tokens,
                client=client,
            )
        except Exception as exc:  # llm.call() already logged this failure to `runs`
            last_error = str(exc)
            last_raw_text = ""
            continue

        try:
            data = json.loads(result.text)
            return agent_def.output_schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            last_raw_text = result.text
            continue

    _send_to_human_queue(agent_def, business_id=business_id, error=last_error, raw_output=last_raw_text)
    raise AgentOutputInvalid(f"{agent_def.name} did not produce valid output after a retry: {last_error}")


def _send_to_human_queue(
    agent_def: AgentDefinition,
    *,
    business_id: str | UUID,
    error: str | None,
    raw_output: str,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO human_queue (business_id, item_type, reason, payload)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    business_id,
                    "agent_output_invalid",
                    f"{agent_def.name} output failed schema validation twice",
                    Jsonb({"agent": agent_def.name, "error": error, "raw_output": raw_output}),
                ),
            )
        write_journal(
            business_id=business_id,
            entry_type="error",
            description=f"{agent_def.name} output failed validation twice and was sent to human_queue",
            actor=agent_def.name,
            related_table="human_queue",
            details={"error": error},
            conn=conn,
        )
    finally:
        conn.close()
