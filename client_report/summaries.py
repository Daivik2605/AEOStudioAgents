"""The AI part of the client report: one short plain-language summary per section (PLAN.md step 22).

Rules, all enforced in code and not left to the prompt:
  * It runs only behind the cost gate: the cost card is shown and a person types `yes` (PLAN.md section 3.2).
  * Every call goes through core/llm.py, so it is logged in `runs`.
  * Every number in a summary must appear in the report's own data. If one does not, the summary is REJECTED and the
    section is printed without it. There is no retry and no looser second try.
  * A summary that promises an outcome (a deny-list of phrases) is rejected the same way.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from audit.cost_gate import confirm_cost
from client_report.strings import STRINGS
from client_report.words import Words
from core import llm

MODEL = "claude-sonnet-5"          # price is in core/model_prices.json
PROMPT_VERSION = "v2"              # prompts/client_report/v2.md (v1 stays on disk: it produced earlier reports)
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "client_report" / f"{PROMPT_VERSION}.md"
AGENT = "client_report"

# Sections that get a summary, and only when they have something to summarise.
SUMMARY_SECTIONS = ("health", "ai_today", "recommend", "work", "r_health", "r_ai", "r_between")

_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_WORDS = {"zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
          "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
          "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000}
_WORD = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.I)
# Phrases that promise or predict an outcome. A summary containing one is thrown away.
_PROMISES = re.compile(
    r"\bguarantee|\bwill (?:increase|improve|boost|rank|appear|be named|be recommended|double|grow|lead|result|drive|ensure|help you|make you)"
    r"|\bis certain\b|\bdefinitely\b|\bsure to\b|\bbound to\b", re.I)


def norm(token: str) -> str:
    """'62.50' and '62.5' are the same number; '1,000' is 1000."""
    try:
        return format(Decimal(token.replace(",", "")).normalize(), "f")
    except InvalidOperation:
        return token


def numbers_in_text(text: str) -> set[str]:
    return {norm(m.group(0)) for m in _NUMBER.finditer(text)}


def numbers_in_data(value) -> set[str]:
    """Every number the report can show: numbers as stored, floats as shown (one decimal), and digits inside text."""
    out: set[str] = set()
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        out.add(norm(repr(value)))
        if isinstance(value, float):
            out.add(norm(f"{value:.1f}"))
        return out
    if isinstance(value, str):
        return numbers_in_text(value)
    if isinstance(value, dict):
        for k, v in value.items():
            out |= numbers_in_text(str(k)) | numbers_in_data(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out |= numbers_in_data(v)
    return out


def allowed_numbers(report: dict) -> set[str]:
    """Numbers in the report's data (never its AI summaries) and in its fixed wording."""
    data = {"sections": [{"key": s["key"], "data": s["data"]} for s in report["sections"]], "business": report["business"],
            "labels": report["labels"], "generated_on": report["generated_on"], "version": report["version"]}
    fixed = numbers_in_data(list(STRINGS[report["tool"]["language"]].values()))
    return numbers_in_data(data) | fixed


def check_summary(text: str, allowed: set[str]) -> str | None:
    """Why this summary must be thrown away, or None if it is acceptable."""
    if not text.strip():
        return "the summary was empty"
    unknown = sorted(n for n in numbers_in_text(text) if n not in allowed)
    if unknown:
        return f"it contains number(s) that are not in the report's data: {', '.join(unknown)}"
    spelled = sorted({w.lower() for w in _WORD.findall(text) if str(_WORDS[w.lower()]) not in allowed})
    if spelled:
        return f"it contains spelled-out number(s) that are not in the report's data: {', '.join(spelled)}"
    promise = _PROMISES.search(text)
    if promise:
        return f"it promises or predicts an outcome ('{promise.group(0)}')"
    return None


def summary_facts(key: str, data: dict, w: Words) -> dict | None:
    """The compact facts the model may use for one section. None means there is nothing to summarise.
    They are in the client's words (w), so the model never sees an internal label or finding code."""
    if key == "health":
        if not data.get("available"):
            return None
        latest = data["latest"]
        out = {"verdict": w.verdict(latest["verdict"]), "platform": w.platform(latest["platform"]), "date": latest["date"],
               "findings": [{"severity": f["severity"], "what": w.finding(f, latest["platform"])} for f in latest["findings"]][:12]}
        if data.get("comparison"):
            c = data["comparison"]
            out["compared"] = {"from": w.label(c["a"]["checkpoint"], c["a"]["date"]), "to": w.label(c["b"]["checkpoint"], c["b"]["date"]),
                               "headline": w.headline(c)}
        return out
    if key in ("ai_today", "r_ai"):
        if not data.get("available"):
            return None
        h = data["headline"]
        design = data["design"]["label"]
        if key == "ai_today":
            return {"design": design, "tier": h["tier"],
                    "measures": {m: {"value": v["value"], "low": v["low"], "high": v["high"]} for m, v in h["measures"].items()},
                    "wrong_facts_found": data["wrong_facts"]["incorrect"], "answers": data["extraction"]["scored"]}
        return {"design": design, "tier_from": h["tier_a"], "tier_to": h["tier_b"],
                "measures": {m: {"from": v["a"]["value"], "to": v["b"]["value"], "change": v["change"]["value"],
                                 "change_low": v["change"]["low"], "change_high": v["change"]["high"], "reading": v["change"]["label"]}
                             for m, v in h["measures"].items()}}
    if key == "recommend":
        if not data.get("available"):
            return None
        return {"fixes": [w.fix_title(f, data.get("platform")) for f in data["fixes"]][:15]}
    if key == "work":
        items = data.get("items") or []
        return {"entries": [{"date": x["date"], "work": x["text"], "closes": w.finding({"code": x["finding_code"], "evidence": x.get("finding_evidence")}, x.get("finding_platform"))
                                if x["finding_code"] and x.get("finding") else None} for x in items][:25]} if items else None
    if key == "r_health":
        if not data.get("available"):
            return None
        say = lambda group: [w.finding(c, c.get("platform")) for c in group][:10]  # noqa: E731
        return {"headline": w.headline(data), "fixed": say(data["fixed"] if "fixed" in data else data["closed"]),
                "removed_with_the_old_website_not_fixed": say(data.get("no_longer_applies") or []),
                "still_open": say(data["still_open"]), "new": say(data["new"])}
    if key == "r_between":
        items = data.get("entries") or []
        return {"from": data["from"], "to": data["to"], "entries": [
            {"date": e["date"], "kind": "event outside our control" if e["outside_our_control"] else "our work", "text": e["text"]}
            for e in items][:25]} if items else None
    return None


def planned_sections(report: dict) -> list[dict]:
    w = Words(report)
    return [s for s in report["sections"] if s["key"] in SUMMARY_SECTIONS and summary_facts(s["key"], s["data"], w) is not None]


def add_summaries(report: dict, *, ask: Callable[[str], str], say: Callable[[str], None], client=None) -> dict:
    """Fills in `summary` for each section that has one. Raises CostGateAborted if the person does not type yes.
    Returns the report with its `ai` block updated."""
    sections = planned_sections(report)
    report["ai"] = {"used": False, "model": None, "calls": 0, "rejected": []}
    if not sections:
        return report
    confirm_cost(len(sections), model=MODEL, ask=ask, say=say, label="Client report summaries")
    allowed = allowed_numbers(report)
    report["ai"].update(used=True, model=MODEL, calls=len(sections))
    system = PROMPT_PATH.read_text()
    w = Words(report)
    for s in sections:
        payload = {"stage": report["stage"], "section": s["key"], "facts": summary_facts(s["key"], s["data"], w)}
        try:
            result = llm.call(business_id=report["business"]["id"], agent=AGENT, model=MODEL, prompt_version=PROMPT_VERSION,
                              system=system, messages=[{"role": "user", "content": json.dumps(payload)}],
                              max_tokens=300, client=client)
        except Exception as exc:  # noqa: BLE001 - a failed summary must not lose the report; the failure is already in `runs`
            report["ai"]["rejected"].append({"section": s["key"], "reason": f"the model call failed ({exc})"})
            say(f"Summary for '{s['key']}' skipped: the model call failed ({exc}).")
            continue
        text = result.text.strip()
        problem = check_summary(text, allowed)
        if problem:
            report["ai"]["rejected"].append({"section": s["key"], "reason": problem})
            say(f"Summary for '{s['key']}' rejected: {problem}. The section is printed without it.")
            continue
        s["summary"] = text
    return report
