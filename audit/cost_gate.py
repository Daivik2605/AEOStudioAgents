"""The cost gate (PLAN.md section 3.2): before the audit's AI calls, show what they will cost and
require a typed `yes`. Capturing answers costs nothing; only the model reading them does.

The estimate is a rough baseline, not a measurement: every call is assumed to use 800 input and
400 output tokens, priced from core/model_prices.json. Real cost is logged per call in `runs`.
"""

from __future__ import annotations

import sys
from typing import Callable

from core.llm import estimate_cost

INPUT_TOKENS_PER_CALL = 800
OUTPUT_TOKENS_PER_CALL = 400


NO_TERMINAL_MESSAGE = ("A person must be at the keyboard to approve this cost. "
                       "Run this again from a terminal.")


class CostGateAborted(Exception):
    """The person did not type yes. Nothing was saved. The message has already been printed."""


def stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def estimated_cost(calls: int, model: str, tokens: tuple[int, int] | None = None) -> float:
    """`tokens` is (input, output) per call, for a caller whose calls are not the audit's size. Default: the audit's."""
    tokens_in, tokens_out = tokens or (INPUT_TOKENS_PER_CALL, OUTPUT_TOKENS_PER_CALL)
    return calls * estimate_cost(model, tokens_in, tokens_out)


def card_lines(calls: int, model: str, *, claim_checks: int = 0, label: str = "AI extraction",
               tokens: tuple[int, int] | None = None) -> list[str]:
    lines = [f"  {label}: {calls} calls · {model} · ~${estimated_cost(calls, model, tokens):.3f}"]
    if claim_checks:
        lines.append(f"  Plus up to {claim_checks} claim checks (this business has confirmed facts) "
                     f"· {model} · ~${estimated_cost(claim_checks, model):.3f}")
    return lines


def confirm_cost(calls: int, *, model: str, claim_checks: int = 0,
                 ask: Callable[[str], str], say: Callable[[str], None], label: str = "AI extraction",
                 tokens: tuple[int, int] | None = None) -> None:
    """Prints the cost card, then asks for `yes`. Anything else raises CostGateAborted.

    With no terminal on stdin (piped or scheduled) the card is still printed, for the log, then
    the run is refused: spending money always needs a person at the keyboard. There is no
    flag or setting that skips the question (PLAN.md section 3.2).
    """
    if calls <= 0:
        return                                   # nothing to read, nothing to pay for
    for line in card_lines(calls, model, claim_checks=claim_checks, label=label, tokens=tokens):
        say(line)
    if not stdin_is_tty():
        say(NO_TERMINAL_MESSAGE)
        raise CostGateAborted()
    try:
        reply = ask("  Type yes to continue: ")
    except EOFError:
        reply = ""
    if reply.strip().lower() != "yes":
        say("Aborted.")
        raise CostGateAborted()
