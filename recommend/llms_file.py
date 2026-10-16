"""llms.txt in the llmstxt.org format (PLAN.md step 19).

    # Name                              one H1 (required)
    > One-paragraph summary             a blockquote
    Plain paragraphs with detail.
    ## Section                          H2 sections, each a list of  - [title](url): note
    - [About](https://...)

Built from the CONFIRMED facts, the story's description, and the main pages in the sitemap. An unconfirmed
fact never goes in. Page titles come from the web address (the tool does not open the pages), so a person
should read them over before the file goes live.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from truth.format import SLOTS

LABEL = {s.key: s.label for s in SLOTS}


def fact(facts: list[dict], label: str) -> str | None:
    """The value of a confirmed fact by label (case-insensitive), or None. `facts` is core.profiles.confirmed_facts."""
    for f in facts:
        if f["field"].casefold() == label.casefold():
            return f["value"].strip()
    return None


def location_numbers(facts: list[dict]) -> list[int]:
    """Which 'Location N ...' numbers have at least one confirmed fact."""
    nums = {int(m.group(1)) for f in facts if (m := re.match(r"^location (\d+) ", f["field"].casefold()))}
    return sorted(nums)


def page_title(url: str) -> str:
    path = unquote(urlsplit(url).path).strip("/")
    if not path:
        return "Home"
    slug = path.split("/")[-1]
    slug = re.sub(r"\.(html?|php|aspx?)$", "", slug, flags=re.I)
    words = re.sub(r"[-_]+", " ", slug).strip()
    return (words[:1].upper() + words[1:]) if words else "Page"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def build_llms(*, business_name: str, facts: list[dict], description: str | None, pages: list[str]) -> dict:
    """Returns {text, name_source, used_facts, left_out}. `facts` are confirmed facts only."""
    name = fact(facts, LABEL["name"])
    name_source = "confirmed fact" if name else "business record (the name is not confirmed yet)"
    name = name or business_name

    lines = [f"# {_one_line(name)}", ""]
    if description and description.strip():
        lines += [f"> {_one_line(description)}", ""]

    used: list[str] = []
    body: list[str] = []

    def add(label: str, text: str | None, as_label: str | None = None) -> None:
        if text:
            body.append(f"{as_label or label}: {_one_line(text)}")
            used.append(label)

    add(LABEL["services"], fact(facts, LABEL["services"]), "Services")
    add(LABEL["service_area"], fact(facts, LABEL["service_area"]), "Areas served")
    add(LABEL["languages"], fact(facts, LABEL["languages"]), "Languages")
    add(LABEL["founded"], fact(facts, LABEL["founded"]), "Founded")
    add(LABEL["price_ranges"], fact(facts, LABEL["price_ranges"]), "Prices")
    add(LABEL["email"], fact(facts, LABEL["email"]), "Email")
    nums = location_numbers(facts)
    for n in nums:
        for part, word in (("address", "Address"), ("phone", "Phone"), ("hours", "Hours")):
            label = f"Location {n} {part}"
            value = fact(facts, label)
            if value:
                body.append(f"{word}" + (f" ({n})" if len(nums) > 1 else "") + f": {_one_line(value)}")
                used.append(label)
    if body:
        lines += ["\n".join(f"{b}  " if i < len(body) - 1 else b for i, b in enumerate(body)), ""]

    if pages:
        lines.append("## Main pages")
        for url in pages:
            lines.append(f"- [{page_title(url)}]({url})")
        lines.append("")
    text = "\n".join(lines).rstrip("\n") + "\n"
    return {"text": text, "name_source": name_source, "used_facts": used}
