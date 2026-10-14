"""The layout of the truth document, in one place. `render_document` writes it and `parse_document` reads it,
and both use the constants below, so they cannot drift apart.

    AEO TRUTH DOCUMENT v1
    BUSINESS: ...            (header: KEY: value lines before the first "##")
    BUSINESS ID: ...

    ## PART 1 · CONFIRMED FACTS
    ### F1                   one block per fact
    LABEL: Name as customers know it
    FACT: Acme Water Co     (may continue on the lines below, until the next label line)
    STATUS: unconfirmed      (confirmed | unconfirmed)
    CONFIRMED BY:
    CONFIRMED ON:            (YYYY-MM-DD)
    NOTE: ...                (guidance from us; ignored on import)

    ## PART 2 · THE STORY
    DESCRIPTION: ...         (each of the four runs until the next label line)

Rules a reader can rely on: a label is only a label at the very start of a line, and a text box may
hold anything else, including blank lines and markdown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from core.profiles import CONFIRMED, STORY_FIELDS, UNCONFIRMED

MAGIC = "AEO TRUTH DOCUMENT v1"
PART1 = "## PART 1 · CONFIRMED FACTS"
PART2 = "## PART 2 · THE STORY"

FACT_LABELS = ("LABEL", "FACT", "STATUS", "CONFIRMED BY", "CONFIRMED ON", "NOTE")
REQUIRED_FACT_LABELS = FACT_LABELS[:-1]          # NOTE is ours and may be deleted
STORY_LABELS = {"DESCRIPTION": "description", "GOALS": "goals", "CUSTOMERS": "customers",
                "CUSTOMER QUESTIONS": "customer_questions"}

HEADING_RE = re.compile(r"^### F(\d+)\s*$")
FACT_LINE_RE = re.compile(r"^(" + "|".join(FACT_LABELS) + r"):[ \t]*(.*)$")
STORY_LINE_RE = re.compile(r"^(" + "|".join(STORY_LABELS) + r"):[ \t]*(.*)$")
HEADER_RE = re.compile(r"^([A-Z][A-Z ]*[A-Z]): (.*)$")


# ---- what the draft asks about --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Slot:
    key: str            # used to match pre-fills; never stored
    label: str          # the fact's name; also how versions are compared with each other
    help: str


# Nothing here belongs to an industry. A business with three locations uses the extra lines.
SLOTS: tuple[Slot, ...] = (
    Slot("name", "Name as customers know it", "The name customers use when they talk about you or search for you."),
    Slot("legal_name", "Legal name", "The registered company name, if different."),
    Slot("founded", "Year founded", "A year, e.g. 2019."),
    Slot("website", "Website", "The main web address."),
    Slot("email", "Email", "The address customers should write to."),
    Slot("loc1_address", "Location 1 address", "Full street address of your first location. Leave empty if customers never visit you."),
    Slot("loc1_phone", "Location 1 phone", "The number customers should call."),
    Slot("loc1_hours", "Location 1 hours", "Opening hours, including seasonal changes (for example closed in winter)."),
    Slot("loc2_address", "Location 2 address", "A second location, if you have one. Otherwise leave empty."),
    Slot("loc2_phone", "Location 2 phone", ""),
    Slot("loc2_hours", "Location 2 hours", ""),
    Slot("services", "Services", "What you offer. Separate with semicolons or put one per line."),
    Slot("service_area", "Areas we serve", "Cities, regions or countries where you actually work."),
    Slot("languages", "Languages", "Languages you serve customers in."),
    Slot("price_ranges", "Price ranges", "Typical prices or ranges, if you are happy to state them."),
    Slot("social", "Social profiles", "Full web addresses, one per line."),
)
EXTRA_SLOTS = 5          # empty lines for facts we did not think to ask about

STORY_HELP = {
    "description": "What you do, in your own words.",
    "goals": "What you want from the next year: more of which customers, which services, which places.",
    "customers": "Who your customers are. Different kinds of customer are best listed one per line.",
    "customer_questions": "What do customers ask you before they buy? Write them the way customers say them.",
}


# ---- writing --------------------------------------------------------------------------------------------------------------

def _block(n: int, label: str, value: str, status: str, by: str, on: str, note: str) -> list[str]:
    lines = [f"### F{n}", f"LABEL: {label}".rstrip(), f"FACT: {value}".rstrip(), f"STATUS: {status}",
             f"CONFIRMED BY: {by}".rstrip(), f"CONFIRMED ON: {on}".rstrip()]
    if note:
        lines.append(f"NOTE: {note}")
    return lines + [""]


def render_document(*, business: dict, day: date, drafted_from: str, based_on_version: int | None,
                    facts: list[dict], story: dict) -> str:
    """`facts` are ready-made block contents: {label, value, status, confirmed_by, confirmed_on, note}."""
    out = [MAGIC, f"BUSINESS: {business['name'] or business['domain']}", f"BUSINESS ID: {business['id']}",
           f"DATE: {day.isoformat()}", f"DRAFTED FROM: {drafted_from}",
           f"BASED ON PROFILE VERSION: {based_on_version if based_on_version is not None else 'none'}", "",
           f"# Truth document: {business['name'] or business['domain']}", "",
           "This file is how we learn what is true about the business. Please fill it in and send it back.", "",
           "**Part 1 is for facts.** Only the business owner (or someone who speaks for them) confirms a fact.",
           "- Read each fact. If it is right, change `STATUS: unconfirmed` to `STATUS: confirmed`, then write who "
           "checked it (`CONFIRMED BY:`) and the date (`CONFIRMED ON:`, like 2026-10-14). All three are needed.",
           "- If a fact is wrong, type the right value over it.",
           "- Anything you are not sure about, leave as `unconfirmed`. We keep it, but we never use it to judge an AI's answer.",
           "- Values we found on your website are pre-filled to save you typing. They are our guess, not your word.",
           "- Leave the value empty if a line does not apply to you. Use the **Extra fact** lines for anything else customers should know.",
           "- Do not change the `LABEL:` of the printed lines. For extra facts, write your own label.", "",
           "**Part 2 is your story,** in your own words. We use it to understand your customers. "
           "It is never used to decide whether an AI is right or wrong.", "",
           PART1, ""]
    for n, f in enumerate(facts, start=1):
        out += _block(n, f["label"], f["value"], f["status"], f["confirmed_by"], f["confirmed_on"], f.get("note", ""))
    out += [PART2, ""]
    for label, key in STORY_LABELS.items():
        out += [f"<!-- {STORY_HELP[key]} -->", f"{label}:" + (f" {story[key]}" if story.get(key) else ""), ""]
    return "\n".join(out)


# ---- reading ----------------------------------------------------------------------------------------------------------------

@dataclass
class Parsed:
    header: dict[str, str] = field(default_factory=dict)
    facts: list[dict] = field(default_factory=list)       # only facts that have a value, in file order
    story: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _clean(lines: list[str]) -> str:
    """A text box: its lines joined, with blank lines trimmed off both ends and HTML comments removed."""
    text = "\n".join(lines)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return text.strip()


def parse_document(text: str, *, today: date | None = None) -> Parsed:
    today = today or date.today()
    out = Parsed()
    lines = text.splitlines()
    if MAGIC not in text:
        out.problems.append(f"this is not a truth document (the line '{MAGIC}' is missing)")
        return out
    p1 = next((i for i, l in enumerate(lines) if l.rstrip() == PART1), None)
    p2 = next((i for i, l in enumerate(lines) if l.rstrip() == PART2), None)
    if p1 is None:
        out.problems.append(f"the heading '{PART1}' is missing")
    if p2 is None:
        out.problems.append(f"the heading '{PART2}' is missing")
    if p1 is None or p2 is None:
        return out
    if p2 < p1:
        out.problems.append("Part 2 comes before Part 1; the file has been rearranged")
        return out

    for l in lines[:p1]:
        if (m := HEADER_RE.match(l)) and m.group(1) not in out.header:
            out.header[m.group(1)] = m.group(2).strip()

    # ---- Part 1: blocks ----
    blocks: list[tuple[int, dict[str, list[str]]]] = []
    current = None
    field_name = None
    for l in lines[p1 + 1:p2]:
        if m := HEADING_RE.match(l):
            current, field_name = {}, None
            blocks.append((int(m.group(1)), current))
        elif current is not None and (m := FACT_LINE_RE.match(l)):
            field_name = m.group(1)
            if field_name in current:                       # a label typed twice: keep the first, report below
                current.setdefault("_dup", []).append(field_name)
            else:
                current[field_name] = [m.group(2)]
        elif current is not None and field_name is not None and field_name in current:
            current[field_name].append(l)

    seen_ids: set[int] = set()
    seen_labels: dict[str, str] = {}
    for n, raw in blocks:
        where = f"F{n}"
        if n in seen_ids:
            out.problems.append(f"{where}: two blocks have this number")
            continue
        seen_ids.add(n)
        for dup in raw.get("_dup", []):
            out.problems.append(f"{where}: the line '{dup}:' appears twice")
        missing = [name for name in REQUIRED_FACT_LABELS if name not in raw]
        if missing:
            out.problems += [f"{where}: the line '{name}:' is missing" for name in missing]
            continue
        label = _clean(raw["LABEL"])
        value = _clean(raw["FACT"])
        status = _clean(raw["STATUS"]).lower()
        by, on = _clean(raw["CONFIRMED BY"]), _clean(raw["CONFIRMED ON"])
        name = f"{where} ({label})" if label else where

        if status not in (CONFIRMED, UNCONFIRMED):
            out.problems.append(f"{name}: STATUS must be 'confirmed' or 'unconfirmed', not '{status or 'empty'}'")
            continue
        on_date = None
        if on:
            try:
                on_date = date.fromisoformat(on)
            except ValueError:
                out.problems.append(f"{name}: CONFIRMED ON must be a date like 2026-10-14, not '{on}'")
            else:
                if on_date > today:
                    out.problems.append(f"{name}: CONFIRMED ON {on} is in the future")
        if not value:
            if status == CONFIRMED or by or on:
                out.problems.append(f"{name}: has confirmation details but no fact written in FACT:")
            continue                                           # an empty line that was left empty: nothing to store
        if not label:
            out.problems.append(f"{where}: this fact has no LABEL (say what it is, e.g. 'Founded in')")
            continue
        if label.casefold() in seen_labels:
            out.problems.append(f"{name}: the label is already used by {seen_labels[label.casefold()]}")
            continue
        seen_labels[label.casefold()] = where
        if status == CONFIRMED:
            if not by:
                out.problems.append(f"{name}: marked confirmed but CONFIRMED BY is empty")
            if not on:
                out.problems.append(f"{name}: marked confirmed but CONFIRMED ON is empty")
        elif by or on:
            out.problems.append(f"{name}: has CONFIRMED BY/ON filled but STATUS is 'unconfirmed'. "
                                "Set STATUS: confirmed, or clear both boxes")
        out.facts.append({"label": label, "value": value, "status": status,
                          "confirmed_by": by if status == CONFIRMED else None,
                          "confirmed_on": on_date.isoformat() if status == CONFIRMED and on_date else None})

    # ---- Part 2: the story ----
    boxes: dict[str, list[str]] = {}
    current_label = None
    for l in lines[p2 + 1:]:
        if m := STORY_LINE_RE.match(l):
            current_label = m.group(1)
            if current_label in boxes:
                out.problems.append(f"the story line '{current_label}:' appears twice")
            else:
                boxes[current_label] = [m.group(2)]
        elif current_label is not None and current_label in boxes:
            boxes[current_label].append(l)
    for label, key in STORY_LABELS.items():
        if label not in boxes:
            out.problems.append(f"the story line '{label}:' is missing")
        else:
            out.story[key] = _clean(boxes[label])
    return out
