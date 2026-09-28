"""The only place in this codebase that calls a Claude model.

Every call -- whether it succeeds or fails -- gets exactly one row in the
`runs` table before this function returns, so nothing that spends tokens (or
money) ever happens off the books.

Prices live in core/model_prices.json, not in this file, so updating a price
never means touching code.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

import anthropic
from psycopg.types.json import Jsonb

from core.db import get_connection

PRICES_PATH = Path(__file__).parent / "model_prices.json"


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


def _load_prices() -> dict[str, dict[str, float]]:
    data = json.loads(PRICES_PATH.read_text())
    return {key: value for key, value in data.items() if not key.startswith("_")}


def _price_for(model: str) -> dict[str, float]:
    prices = _load_prices()
    if model not in prices:
        raise ValueError(
            f"no price configured for model '{model}' in {PRICES_PATH.name} -- "
            "add it there before calling this model, so cost tracking stays honest"
        )
    return prices[model]


def _cost(price: dict[str, float], input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * price["input_per_million"]
        + output_tokens / 1_000_000 * price["output_per_million"]
    )


def _log_run(
    *,
    business_id: str | UUID,
    agent: str,
    model: str,
    prompt_version: str,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any] | None,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_ms: int,
    status: str,
    error: str | None,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO runs
                    (business_id, agent, model, prompt_version, input, output,
                     input_tokens, output_tokens, cost_usd, latency_ms, status, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    business_id,
                    agent,
                    model,
                    prompt_version,
                    Jsonb(input_payload),
                    Jsonb(output_payload) if output_payload is not None else None,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    latency_ms,
                    status,
                    error,
                ),
            )
    finally:
        conn.close()


def call(
    *,
    business_id: str | UUID,
    agent: str,
    model: str,
    prompt_version: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_handlers: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
    max_tokens: int = 4096,
    max_tool_turns: int = 8,
    client: Any = None,
) -> LLMResult:
    """Sends `messages` to a Claude model and returns its final text answer.

    If `tools` are given, this runs the tool-use loop itself: whenever the
    model asks to use a tool, the matching function in `tool_handlers` runs
    and its result is fed back to the model, until the model answers with
    plain text instead of a tool call (or `max_tool_turns` is reached).

    `client` lets tests inject a fake Anthropic client; production code
    leaves it out and a real one is created.
    """
    price = _price_for(model)  # fail before spending anything if we can't cost it
    client = client or anthropic.Anthropic()
    conversation = list(messages)
    input_record = {"system": system, "messages": messages}
    total_input_tokens = 0
    total_output_tokens = 0
    start = time.monotonic()

    try:
        response = None
        for _ in range(max_tool_turns):
            create_kwargs: dict[str, Any] = dict(
                model=model,
                system=system,
                messages=conversation,
                max_tokens=max_tokens,
            )
            if tools:
                create_kwargs["tools"] = tools

            response = client.messages.create(**create_kwargs)
            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            if response.stop_reason != "tool_use":
                break

            conversation.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                handler = (tool_handlers or {}).get(block.name)
                if handler is None:
                    raise ValueError(f"no handler registered for tool '{block.name}'")
                result = handler(block.input)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": str(result)}
                )
            conversation.append({"role": "user", "content": tool_results})
        else:
            raise RuntimeError(f"model kept calling tools past {max_tool_turns} turns")

    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        cost_usd = _cost(price, total_input_tokens, total_output_tokens)
        _log_run(
            business_id=business_id,
            agent=agent,
            model=model,
            prompt_version=prompt_version,
            input_payload=input_record,
            output_payload=None,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            status="failed",
            error=str(exc),
        )
        raise

    latency_ms = int((time.monotonic() - start) * 1000)
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    cost_usd = _cost(price, total_input_tokens, total_output_tokens)

    _log_run(
        business_id=business_id,
        agent=agent,
        model=model,
        prompt_version=prompt_version,
        input_payload=input_record,
        output_payload={"text": text},
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        status="success",
        error=None,
    )

    return LLMResult(
        text=text,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )
