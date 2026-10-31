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
import re
from pathlib import Path

from core import llm

MODEL = "claude-sonnet-5"      # price is in core/model_prices.json
PROMPT_VERSION = "v1"          # prompts/diagnose/v1.md
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "diagnose" / f"{PROMPT_VERSION}.md"


class ExplanationRefused(Exception):
    """The model's paragraph said something the check cannot support. It is not used (the report has the findings without it)."""


# Words that claim something about how the page is built. They may only appear if the page content was actually read.
_RENDERING_CLAIM = re.compile(r"client[- ]?(?:side[- ])?render|server[- ]?side render|needs? (?:ssr|server)|rendered in the (?:visitor|browser)", re.I)


def explain_verdict(*, business_id: str, url: str, verdict: str, reason: str,
                    findings: list[dict], not_checked: list[dict] | None = None, client=None) -> str:
    """Returns the explanation paragraph. Raises if the model call fails.

    `not_checked` are checks we could not do (we were blocked, or the page did not load). They are handed to the model as
    facts, and if page content was among them, a paragraph that claims anything about rendering is refused."""
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
    if not_checked:
        payload["not_checked"] = [{"check": n["label"], "reason": n["reason"]} for n in not_checked]
    content_unread = any(n["check"] in ("render", "structured_data") for n in (not_checked or []))
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
    text = result.text.strip()
    if content_unread and _RENDERING_CLAIM.search(text):
        raise ExplanationRefused("the paragraph says how the page is built, but the page could not be read")
    return text
