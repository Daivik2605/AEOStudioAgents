"""The one AI step in `aeo diagnose`: turn a verdict that the rules already
decided into a short paragraph a person can send.

The model never decides. It is handed the verdict, the reason and the
findings, and told to explain them. The call goes through core/llm.py, so it
is logged in `runs` (tokens, cost, latency) like every other AI call.

It only runs when a business is given, because `runs.business_id` is NOT NULL
and there is no honest way to log the spend without one (PLAN.md section 11).
"""

from __future__ import annotations

import json
from pathlib import Path

from core import llm

MODEL = "claude-sonnet-5"      # price is in core/model_prices.json
PROMPT_VERSION = "v1"          # prompts/diagnose/v1.md
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "diagnose" / f"{PROMPT_VERSION}.md"


def explain_verdict(*, business_id: str, url: str, verdict: str, reason: str,
                    findings: list[dict], client=None) -> str:
    """Returns the explanation paragraph. Raises if the model call fails."""
    payload = {
        "url": url,
        "verdict": verdict,
        "reason": reason,
        "findings": [
            {"code": f["code"], "severity": f["severity"], "what": f["what"],
             "where_to_fix": f["where_to_fix"], "source": f["source"]}
            for f in findings if f["severity"] != "info"
        ][:12],
    }
    result = llm.call(
        business_id=business_id,
        agent="diagnose",
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        system=PROMPT_PATH.read_text(),
        messages=[{"role": "user", "content": json.dumps(payload)}],
        max_tokens=400,
        client=client,
    )
    return result.text.strip()
