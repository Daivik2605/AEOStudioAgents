"""The plain-code half of `aeo questions draft` (PLAN.md step 13b): how many questions of each kind, what makes a draft
question acceptable, how long it is, and which eight are the spot check. No AI and no I/O here.

The model only proposes wording. Every number and every rule below is code, so the same proposal always gives the same
set, and a person can read why a question was kept or rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from dossier.questions import brand_terms, contains_brand, normalise

KINDS = ("discovery", "brand")
INTENTS = ("commercial", "informational")
LENGTHS = ("short", "medium", "long")

MIN_SIZE, MAX_SIZE, DEFAULT_SIZE = 12, 100, 32
DISCOVERY_SHARE = 0.75                  # of the whole set, controls included: 24 of 32
CONTROLS_PER_32 = 4
LENGTH_AIM = {"short": 0.40, "medium": 0.40, "long": 0.20}
LENGTH_TOLERANCE = 0.15                 # the summary warns when a length is this far from its aim

SHORT_UNDER = 12                        # words: short is fewer than 12
MEDIUM_UP_TO = 25                       # medium is 12 to 25; long is more than 25

SPOT_TOTAL, SPOT_DISCOVERY, SPOT_BRAND = 8, 5, 3

NEAR_DUPLICATE_RATIO = 0.88             # SequenceMatcher on the normalised text
NEAR_DUPLICATE_OVERLAP = 0.8            # shared words / all words


def length_of(text: str) -> str:
    """short / medium / long from the words actually in the question. The model's own idea of length is ignored."""
    words = len(text.split())
    if words < SHORT_UNDER:
        return "short"
    return "medium" if words <= MEDIUM_UP_TO else "long"


def _round(x: float) -> int:
    return int(x + 0.5)                 # always up at .5, unlike round(), so the numbers never depend on the size's parity


def _split(total: int, parts: int) -> list[int]:
    """total shared as evenly as possible; the first languages get the extra ones."""
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


@dataclass
class Targets:
    size: int
    discovery: int                      # real discovery questions (controls not included)
    control: int
    brand: int
    by_language: dict[str, dict[str, int]]
    length_aim: dict[str, int]

    def to_json(self) -> dict:
        return {"size": self.size, "discovery": self.discovery, "control": self.control, "brand": self.brand,
                "by_language": self.by_language, "length_aim": self.length_aim}


def make_targets(size: int, languages: list[str]) -> Targets:
    if not MIN_SIZE <= size <= MAX_SIZE:
        raise ValueError(f"--size must be between {MIN_SIZE} and {MAX_SIZE} (a spot check needs eight questions to pick from)")
    if not languages:
        raise ValueError("at least one language is needed")
    control = max(1, _round(size * CONTROLS_PER_32 / 32))
    discovery_total = _round(size * DISCOVERY_SHARE)
    brand = size - discovery_total
    discovery = discovery_total - control
    d, c, b = (_split(n, len(languages)) for n in (discovery, control, brand))
    by_language = {lang: {"discovery": d[i], "control": c[i], "brand": b[i]} for i, lang in enumerate(languages)}
    short, medium = _round(size * LENGTH_AIM["short"]), _round(size * LENGTH_AIM["medium"])
    return Targets(size, discovery, control, brand, by_language,
                   {"short": short, "medium": medium, "long": size - short - medium})


# ---- one proposed question ----------------------------------------------------------------------------------------------

@dataclass
class Candidate:
    text: str
    kind: str                           # discovery | brand
    language: str
    intent: str | None
    is_target: bool
    control: bool
    relies_on: list[int] = field(default_factory=list)

    @property
    def group(self) -> str:
        return "brand" if self.kind == "brand" else ("control" if self.control else "discovery")


@dataclass
class Rejection:
    text: str
    reasons: list[str]


# A yes/no question that states a claim about the business. English and French shapes; this is a heuristic, which is why
# the model must also say which confirmed fact a question relies on (relies_on_fact_ids).
_YES_NO_START = re.compile(r"^\s*(does|do|did|is|are|was|were|can|could|will|would|has|have|had|should)\b", re.I)
_YES_NO_FR = re.compile(r"^\s*(est-ce\s+qu|peut-on|peuvent-ils|a-t-|ont-ils)|-t-(il|elle|ils|elles|on)\b|\b(est|sont)-(il|elle|ils|elles)\b", re.I)


def asserts_a_claim(text: str) -> bool:
    return bool(_YES_NO_START.search(text) or _YES_NO_FR.search(text))


def _words(text: str) -> set[str]:
    return set(normalise(text).split())


def near_duplicate(a: str, b: str) -> bool:
    na, nb = normalise(a), normalise(b)
    if na == nb:
        return True
    wa, wb = _words(a), _words(b)
    if wa and wb and len(wa & wb) / len(wa | wb) >= NEAR_DUPLICATE_OVERLAP:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= NEAR_DUPLICATE_RATIO


def check_questions(raw: list[dict], *, name: str | None, domain: str | None, alternates: list[str] | None,
                    languages: list[str], confirmed_fact_ids: set[int],
                    already: list[Candidate]) -> tuple[list[Candidate], list[Rejection]]:
    """Splits what the model proposed into questions we keep and questions we reject, with the reasons.
    `already` are questions kept earlier (the first pass, when this is the retry), so a retry cannot repeat them."""
    terms = brand_terms(name, domain, alternates)
    kept: list[Candidate] = []
    rejected: list[Rejection] = []
    for item in raw:
        text = item.get("text") if isinstance(item, dict) else None
        problems = _tag_problems(item, languages)
        if problems:
            rejected.append(Rejection(text if isinstance(text, str) else str(item)[:120], problems))
            continue
        text = text.strip()
        kind, language = item["kind"], item["language"]
        control = bool(item.get("control") or False)
        relies_on = list(item.get("relies_on_fact_ids") or [])
        reasons: list[str] = []
        names_brand = contains_brand(text, terms)
        if kind == "discovery" and names_brand:
            reasons.append(f"a discovery question names the business (\"{names_brand}\"): the buyer has never heard of it")
        if kind == "brand" and not names_brand:
            reasons.append("a brand question must name the business or its web address")
        if kind == "brand":
            unconfirmed = [i for i in relies_on if i not in confirmed_fact_ids]
            if unconfirmed:
                reasons.append(f"it relies on fact(s) {unconfirmed}, which are not confirmed facts")
            elif asserts_a_claim(text) and not relies_on:
                reasons.append("it is a yes/no question that states a claim about the business, and no confirmed fact is given for it")
        for other in [*already, *kept]:
            if near_duplicate(text, other.text):
                reasons.append(f"it repeats an earlier question (\"{other.text}\")")
                break
        if reasons:
            rejected.append(Rejection(text, reasons))
            continue
        # A control or brand question is never a target, and a control is always informational: coerced, not rejected.
        intent = item.get("intent") if kind == "discovery" else None
        if control:
            intent = "informational"
        kept.append(Candidate(text=text, kind=kind, language=language, intent=intent,
                              is_target=bool(item.get("is_target") or False) and kind == "discovery" and not control,
                              control=control, relies_on=relies_on))
    return kept, rejected


def _tag_problems(item, languages: list[str]) -> list[str]:
    if not isinstance(item, dict):
        return ["it is not an object with the question's tags"]
    problems = []
    if not isinstance(item.get("text"), str) or not item["text"].strip():
        problems.append("it has no question text")
    if item.get("kind") not in KINDS:
        problems.append(f"kind must be one of {', '.join(KINDS)} (got {item.get('kind')!r})")
    if item.get("language") not in languages:
        problems.append(f"language must be one of {', '.join(languages)} (got {item.get('language')!r})")
    kind = item.get("kind")
    if kind == "discovery" and item.get("intent") not in INTENTS:
        problems.append(f"intent must be one of {', '.join(INTENTS)} for a discovery question (got {item.get('intent')!r})")
    if kind == "brand" and item.get("intent") not in (None, ""):
        problems.append("a brand question has no intent")
    for flag in ("is_target", "control"):
        if flag in item and item[flag] is not None and not isinstance(item[flag], bool):
            problems.append(f"{flag} must be true or false (got {item[flag]!r})")
    if kind == "brand" and item.get("control") is True:
        problems.append("a brand question cannot be a control")
    ids = item.get("relies_on_fact_ids")
    if ids is not None and not (isinstance(ids, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
        problems.append("relies_on_fact_ids must be a list of whole numbers")
    return problems


# ---- how many of each we have, and what is missing ---------------------------------------------------------------------------

def bucket_counts(items: list[Candidate]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for c in items:
        counts[(c.group, c.language)] = counts.get((c.group, c.language), 0) + 1
    return counts


def fit_to_targets(items: list[Candidate], targets: Targets) -> tuple[list[Candidate], list[Candidate], dict[tuple[str, str], int]]:
    """(kept, surplus, missing). Takes at most the target number of each (group, language), in the order given.
    Extra questions are reported as surplus, not silently used; shortfalls are reported as missing, never padded."""
    seen: dict[tuple[str, str], int] = {}
    kept, surplus = [], []
    for c in items:
        key = (c.group, c.language)
        want = targets.by_language.get(c.language, {}).get(c.group, 0)
        if seen.get(key, 0) < want:
            seen[key] = seen.get(key, 0) + 1
            kept.append(c)
        else:
            surplus.append(c)
    missing = {(group, lang): want - seen.get((group, lang), 0)
               for lang, row in targets.by_language.items() for group, want in row.items()
               if want - seen.get((group, lang), 0) > 0}
    return kept, surplus, missing


def order_for_file(items: list[Candidate], languages: list[str]) -> list[Candidate]:
    """Discovery (shortest first, by language), then controls, then brand questions, like the existing drafts."""
    rank = {name: i for i, name in enumerate(LENGTHS)}
    group_rank = {"discovery": 0, "control": 1, "brand": 2}
    lang_rank = {lang: i for i, lang in enumerate(languages)}
    return sorted(items, key=lambda c: (group_rank[c.group], lang_rank[c.language], rank[length_of(c.text)]))


# ---- the spot check, chosen by a fixed rule -------------------------------------------------------------------------------------

def choose_spot_check(items: list[Candidate], languages: list[str], domain: str | None) -> list[int]:
    """Indexes (into `items`) of the questions flagged spot_check. The rule, with no AI and no randomness:

      - never a control; 5 discovery and 3 brand where there are that many, else the other kind makes up the eight
      - within each kind, every requested language appears at least once (in the order the languages were given)
      - across the whole set, every length present appears at least once, as far as the eight places allow
      - at least one brand question that uses the web address, if there is one
      - what is left is filled in file order

    Within each step the first matching question, in the order given, wins."""
    host = (domain or "").lower().removeprefix("www.")
    disc = [i for i, c in enumerate(items) if c.kind == "discovery" and not c.control]
    brand = [i for i, c in enumerate(items) if c.kind == "brand" and not c.control]

    n_disc, n_brand = min(SPOT_DISCOVERY, len(disc)), min(SPOT_BRAND, len(brand))
    spare = SPOT_TOTAL - n_disc - n_brand            # one kind is short: the other kind takes its places
    more = min(spare, len(disc) - n_disc)
    n_disc, spare = n_disc + more, spare - more
    n_brand += min(spare, len(brand) - n_brand)

    def take(pool: list[int], budget: int, already: list[int], *, want_address: bool = False) -> list[int]:
        picked: list[int] = []

        def add(i: int | None) -> None:
            if i is not None and i not in picked and len(picked) < budget:
                picked.append(i)

        if want_address and host:
            add(next((i for i in pool if host in items[i].text.lower()), None))
        for lang in languages:
            if not any(items[i].language == lang for i in picked):
                add(next((i for i in pool if items[i].language == lang), None))
        for length in LENGTHS:
            if not any(length_of(items[i].text) == length for i in [*already, *picked]):
                add(next((i for i in pool if length_of(items[i].text) == length), None))
        for i in pool:
            add(i)
        return picked

    chosen = take(disc, n_disc, [])
    chosen += take(brand, n_brand, chosen, want_address=True)
    return sorted(chosen)
