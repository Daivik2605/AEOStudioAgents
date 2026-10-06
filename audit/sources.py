"""Links an engine showed for an answer (PLAN.md section 3.2, "Sources need their own box").

Copying an answer out of ChatGPT or Perplexity usually drops the links, so the operator pastes
them separately. We also pick up any URL written inside the answer text. Plain Python, no AI.
Each stored entry is {"url", "domain", "origin"}; origin is "pasted" or "in_answer".
"""

from __future__ import annotations

import re

from core.businesses import BusinessError, parse_site

_HTTP = re.compile(r"^https?://", re.IGNORECASE)
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)
_TRAILING = ".,;:!?"      # sentence punctuation that is not part of the link


def parse_link(line: str) -> dict:
    """One pasted line -> {"url", "domain"}. Raises ValueError (with a message for the operator) if it is not an http/https link."""
    url = line.strip()
    if not _HTTP.match(url):
        raise ValueError(f"'{url}' is not a link that starts with http:// or https://")
    try:
        domain, _ = parse_site(url)       # the same domain clean-up `aeo add` uses (lowercase, no www.)
    except BusinessError:
        raise ValueError(f"'{url}' is not a usable web link") from None
    return {"url": url, "domain": domain}


def links_in_text(text: str) -> list[dict]:
    """Every http/https URL written inside an answer, in reading order."""
    found = []
    for match in _URL_IN_TEXT.findall(text):
        try:
            found.append(parse_link(match.rstrip(_TRAILING)))
        except ValueError:
            continue                       # something that only looked like a link
    return found


def merge_sources(pasted: list[dict], answer_text: str) -> list[dict]:
    """Pasted links first, then links found in the answer that were not pasted. One entry per URL.
    A link that is both pasted and in the answer counts as pasted."""
    merged: list[dict] = []
    seen: set[str] = set()
    for origin, links in (("pasted", pasted), ("in_answer", links_in_text(answer_text))):
        for link in links:
            if link["url"] in seen:
                continue
            seen.add(link["url"])
            merged.append({"url": link["url"], "domain": link["domain"], "origin": origin})
    return merged
