"""Keeping what arrived from a reply that was cut off at the output cap (PLAN.md step 25f). Plain code: no AI.

A cut-off reply is JSON that stops in the middle, so it cannot be parsed whole. But the questions inside it are written one
after another, so every question whose closing brace arrived is complete and good. This reads them one at a time and
stops at the first one that did not finish. Nothing is guessed or repaired: a half-written question is dropped.
"""

from __future__ import annotations

import json
import re

# The reply lists its questions under these keys (v4 groups them; v1 to v3 use one flat list).
LIST_KEYS = ("questions", "discovery", "control", "brand")


def _complete_objects(text: str, key: str) -> list[dict]:
    """Every whole {...} in the list that follows `"key": [`, up to the first one that is cut short."""
    m = re.search(rf'"{key}"\s*:\s*\[', text)          # the key of a list, not the word used as a value ("kind": "brand")
    if m is None:
        return []
    decoder, pos, found = json.JSONDecoder(), m.end(), []
    while pos < len(text):
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(text) or text[pos] != "{":
            break                                       # the end of the list, or the cut
        try:
            obj, pos = decoder.raw_decode(text, pos)
        except ValueError:
            break                                       # this question was cut short: it and everything after it is lost
        if isinstance(obj, dict):
            found.append(obj)
    return found


def _opening_fields(text: str) -> dict:
    """The small fields before the first list ("local", "local_reason"), if they arrived whole."""
    firsts = [m.start() for k in LIST_KEYS if (m := re.search(rf'"{k}"\s*:\s*\[', text))]
    if not firsts:
        return {}
    head = text[:min(firsts)].strip()
    head = head[len("```json"):].strip() if head.startswith("```json") else head
    head = head.rstrip().rstrip(",")
    try:
        data = json.loads(head + "}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def reply_json(text: str) -> str:
    """The reply's JSON object, when the model wrote a few lines of reasoning before it (step 25g; a model with thinking off
    sometimes thinks aloud in the answer). Starts at the first `{` that opens a whole object holding one of our keys and
    ends where that object ends, so words before or after it are ignored. A reply with no such object comes back as it was,
    and fails the normal checks. Nothing inside the object is changed."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, end = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and ({"local", *LIST_KEYS} & obj.keys()):
            return text[m.start():end]
    return text


def salvage(text: str) -> dict:
    """What can be kept from a cut-off reply, as a dict shaped like a whole reply (only the lists that had questions)."""
    text = text[max(text.find("{"), 0):]                 # words before the JSON are not part of it
    out = _opening_fields(text)
    for key in LIST_KEYS:
        items = _complete_objects(text, key)
        if items:
            out[key] = items
    return out
