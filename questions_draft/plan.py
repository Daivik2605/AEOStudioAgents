"""The plain-code half of `aeo questions draft` (PLAN.md step 13b): how many questions of each kind, what makes a draft
question acceptable, how long it is, and which eight are the spot check. No AI and no I/O here.

The model only proposes wording. Every number and every rule below is code, so the same proposal always gives the same
set, and a person can read why a question was kept or rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from core.question_length import LENGTHS, length_of  # noqa: F401  (the one rule, PLAN.md section 8; re-exported here for callers)
from audit.question_set import FACT_TOPICS  # noqa: F401  (the one list; re-exported here)
from dossier.questions import brand_terms, contains_brand, normalise
from questions_draft.location import PlaceRule, anchor, language_issue

KINDS = ("discovery", "brand")
INTENTS = ("commercial", "informational")

MIN_SIZE, MAX_SIZE, DEFAULT_SIZE = 12, 100, 32
BRAND_PER_32 = 10                       # PLAN.md section 8: at 32, 18 discovery + 4 control + 10 brand
CONTROLS_PER_32 = 4
MIN_TOPICS = 6                          # brand questions spread over at least this many fact topics (fewer if fewer questions)

# What a customer is trying to settle when choosing a business (PLAN.md step 25e). The draft's discovery questions are
# spread across these; the spot check takes different ones. Kept in the draft result only, not in the file.
NEEDS = ("service", "urgent", "price", "payment", "audience", "trust", "convenience", "language", "compare")
LENGTH_AIM = {"short": 0.40, "medium": 0.40, "long": 0.20}
LENGTH_TOLERANCE = 0.15                 # the summary warns when a length is this far from its aim

SPOT_TOTAL, SPOT_DISCOVERY, SPOT_BRAND = 8, 5, 3

NEAR_DUPLICATE_RATIO = 0.88             # SequenceMatcher on the normalised text
NEAR_DUPLICATE_OVERLAP = 0.8            # shared words / all words


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
    brand = _round(size * BRAND_PER_32 / 32)
    discovery = size - brand - control
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
    needs_review: bool = False          # kept, but a person must look (a judgement call, not a certain fault)
    review_reason: str | None = None
    repairs: list[str] = field(default_factory=list)   # tags fixed where the intent was clear
    fact_topic: str | None = None       # brand questions: what fact the answer is about (FACT_TOPICS)
    need: str | None = None             # discovery questions: what the customer is settling (NEEDS); not saved in the file
    place: str | None = None            # the place word the model says it used; checked, not trusted
    place_how: str | None = None        # how the question carries a place, when one was required
    needs_place: bool = False           # a place was required of this question

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
# "Who owns example.com": nobody types that when choosing a business, and the answer is not a fact we confirm.
_OWNER_ASK = re.compile(r"\bwho\s+(?:owns|owned|is\s+behind|is\s+the\s+owner\s+of)\b|\bowner\s+of\b|\bqui\s+(?:possede|possède|est\s+le\s+proprietaire|est\s+le\s+propriétaire)\b", re.I)
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


def _flag(c: Candidate, reason: str) -> None:
    c.needs_review = True
    c.review_reason = reason if not c.review_reason else f"{c.review_reason}; {reason}"


def _as_bool(value):
    """True/False for a real bool, or for the words true/false/yes/no; None for unset; the value itself if unclear."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "yes", "false", "no"):
        return value.strip().lower() in ("true", "yes")
    return value


def _repair_tags(item: dict, languages: list[str], terms) -> tuple[dict | None, list[str], list[str], list[str]]:
    """(tags, repairs, flags, problems). `problems` is non-empty only when there is no question text to keep.
    A repair is made only where the intent is clear (case, spaces, "true" for true, a brand question that cannot
    have an intent). Where it is not clear the question is kept with the tag empty and a flag says so."""
    repairs: list[str] = []
    flags: list[str] = []
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        return None, [], [], ["it has no question text"]
    text = text.strip()
    tags: dict = {"text": text}

    kind = item.get("kind")
    if isinstance(kind, str) and kind.strip().lower() in KINDS:
        tags["kind"] = kind.strip().lower()
        if tags["kind"] != kind:
            repairs.append(f"kind \"{kind}\" written as {tags['kind']}")
    else:
        tags["kind"] = "brand" if contains_brand(text, terms) else "discovery"
        flags.append(f"kind was {kind!r}; worked out from the text as {tags['kind']}")

    lang = item.get("language")
    wanted = {l.lower(): l for l in languages}
    if lang in languages:
        tags["language"] = lang
    elif isinstance(lang, str) and lang.strip().lower() in wanted:
        tags["language"] = wanted[lang.strip().lower()]
        repairs.append(f"language \"{lang}\" written as {tags['language']}")
    elif len(languages) == 1:
        tags["language"] = languages[0]
        flags.append(f"language was {lang!r}; only {languages[0]} was asked for, so it was set to that")
    else:
        tags["language"] = None
        flags.append(f"language was {lang!r} and several were asked for: set it by hand")

    for flag in ("is_target", "control"):
        value = _as_bool(item.get(flag))
        if isinstance(value, bool) or value is None:
            tags[flag] = bool(value)
            if item.get(flag) is not None and not isinstance(item.get(flag), bool):
                repairs.append(f"{flag} \"{item[flag]}\" written as {tags[flag]}")
        else:
            tags[flag] = False
            flags.append(f"{flag} was {item[flag]!r}; set to false")

    intent = item.get("intent")
    if tags["kind"] == "brand":
        tags["intent"] = None
        if intent not in (None, ""):
            repairs.append("a brand question has no intent: removed")
        if tags["control"]:
            tags["control"] = False
            repairs.append("a brand question cannot be a control: control set to false")
    else:
        if isinstance(intent, str) and intent.strip().lower() in INTENTS:
            tags["intent"] = intent.strip().lower()
            if tags["intent"] != intent:
                repairs.append(f"intent \"{intent}\" written as {tags['intent']}")
        else:
            tags["intent"] = None
            flags.append(f"intent was {intent!r}: set it by hand")

    topic = item.get("fact_topic")
    tags["fact_topic"] = None
    if tags["kind"] == "brand":
        if isinstance(topic, str) and topic.strip().lower() in FACT_TOPICS:
            tags["fact_topic"] = topic.strip().lower()
            if tags["fact_topic"] != topic:
                repairs.append(f"fact_topic \"{topic}\" written as {tags['fact_topic']}")
        elif topic not in (None, ""):
            flags.append(f"fact_topic was {topic!r}, which is not one of the topics: set it by hand")
    elif topic not in (None, ""):
        repairs.append("a discovery question has no fact_topic: removed")
    need = item.get("need")
    tags["need"] = need.strip().lower() if isinstance(need, str) and need.strip().lower() in NEEDS else None
    place = item.get("place")
    tags["place"] = place.strip() if isinstance(place, str) and place.strip() else None

    ids = item.get("relies_on_fact_ids")
    if ids is None:
        tags["relies_on"] = []
    elif isinstance(ids, list):
        fixed = []
        for i in ids:
            if isinstance(i, bool):
                continue
            if isinstance(i, int):
                fixed.append(i)
            elif isinstance(i, str) and i.strip().isdigit():
                fixed.append(int(i.strip()))
                repairs.append(f"fact id \"{i}\" written as {int(i.strip())}")
        tags["relies_on"] = fixed
        if len(fixed) != len(ids):
            flags.append("some fact ids were not whole numbers and were left out")
    else:
        tags["relies_on"] = []
        flags.append("relies_on_fact_ids was not a list and was ignored")
    return tags, repairs, flags, []


def check_questions(raw: list[dict], *, name: str | None, domain: str | None, alternates: list[str] | None,
                    languages: list[str], confirmed_fact_ids: set[int],
                    already: list[Candidate], place_rule: PlaceRule | None = None) -> tuple[list[Candidate], list[Rejection]]:
    """Splits what the model proposed into questions we keep and questions we drop, with the reasons.

    Dropped: no question text; an exact repeat; a near repeat; a brand question that does not name the business;
    a brand question relying on a fact that is not confirmed. Everything else that is only a judgement call is KEPT
    and flagged needs_review with a short reason: the yes/no "claim" heuristic, a discovery question that names the
    business (re-tagged brand), tags that could not be repaired. `already` are questions kept earlier (the retry
    cannot repeat them).

    Step 25e adds: a non-control discovery question for a local business must carry a place (dropped, and replaced by the
    retry, if it does not); a brand question must have a fact_topic (flagged if not); "who owns" questions are dropped;
    a place written differently from the site's own form in that language, or a question that looks like two asks, is flagged."""
    terms = brand_terms(name, domain, alternates)
    kept: list[Candidate] = []
    rejected: list[Rejection] = []
    for item in raw:
        if not isinstance(item, dict):
            rejected.append(Rejection(str(item)[:120], ["it is not an object with the question's tags"]))
            continue
        tags, repairs, flags, problems = _repair_tags(item, languages, terms)
        if problems:
            rejected.append(Rejection(str(item.get("text"))[:120], problems))
            continue
        text, kind = tags["text"], tags["kind"]
        control, relies_on = tags["control"], tags["relies_on"]
        names_brand = contains_brand(text, terms)
        c = Candidate(text=text, kind=kind, language=tags["language"], intent=tags["intent"],
                      is_target=tags["is_target"], control=control, relies_on=relies_on, repairs=repairs,
                      fact_topic=tags["fact_topic"], need=tags["need"], place=tags["place"])
        for f in flags:
            _flag(c, f)

        reasons: list[str] = []
        if kind == "discovery" and names_brand:
            # Re-tagged, not dropped: the question is fine as a brand question. Target, intent, control and spot check reset.
            c.kind, c.intent, c.is_target, c.control, c.need = "brand", None, False, False, None
            _flag(c, f"drafted as a discovery question but it names the business (\"{names_brand}\"): re-tagged brand")
            kind = "brand"
        if kind == "brand" and not names_brand:
            reasons.append("a brand question must name the business or its web address")
        if kind == "discovery" and not c.control and place_rule is not None and place_rule.required:
            c.needs_place = True
            c.place_how = anchor(place_rule, text, c.place)
            if c.place_how is None:
                reasons.append("it names no place: a customer choosing a business types the city, a neighbourhood the site names, "
                               "or \"near me\". Add the place to the question")
            else:
                issue = language_issue(place_rule, text, c.language)
                if issue:
                    _flag(c, issue)
        if kind == "discovery" and not c.control and text.count("?") > 1:
            _flag(c, "it may ask two things in one question (more than one question mark)")
        if kind == "brand":
            if _OWNER_ASK.search(text):
                reasons.append("a \"who owns\" question: not something a customer asks when choosing, and not a confirmed fact")
            if c.fact_topic is None and not any("fact_topic" in f for f in flags):
                _flag(c, "a brand question with no fact_topic: set which fact its answer is about")
        if kind == "brand":
            unconfirmed = [i for i in relies_on if i not in confirmed_fact_ids]
            if unconfirmed:
                reasons.append(f"it relies on fact(s) {unconfirmed}, which are not confirmed facts")
            elif asserts_a_claim(text) and not relies_on:
                _flag(c, "a yes/no question that states a claim about the business, with no confirmed fact given for it")
        for other in [*already, *kept]:
            if normalise(text) == normalise(other.text):
                reasons.append(f"it is an exact repeat of an earlier question (\"{other.text}\")")
                break
            if near_duplicate(text, other.text):
                reasons.append(f"it repeats an earlier question (\"{other.text}\")")
                break
        if reasons:
            rejected.append(Rejection(text, reasons))
            continue
        if c.control:
            c.intent = "informational"                       # a control is always informational: coerced, not rejected
        c.is_target = c.is_target and c.kind == "discovery" and not c.control
        kept.append(c)
    return kept, rejected


# ---- how many of each we have, and what is missing ---------------------------------------------------------------------------

def bucket_counts(items: list[Candidate]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for c in items:
        counts[(c.group, c.language)] = counts.get((c.group, c.language), 0) + 1
    return counts


def fit_to_targets(items: list[Candidate], targets: Targets) -> tuple[list[Candidate], list[Candidate], dict[tuple[str, str], int]]:
    """(kept, surplus, missing). Takes at most the target number of each (group, language). Clean questions are
    considered before flagged ones, so a flagged question only takes a place a clean one has not filled; otherwise the
    order given decides. A question whose language could not be settled (flagged) is kept outside the places.
    Extra clean questions are reported as surplus, not silently used; extra flagged ones are kept (nothing flagged is dropped); shortfalls are reported as missing, never padded."""
    seen: dict[tuple[str, str], int] = {}
    kept, surplus = [], []
    for c in sorted(items, key=lambda c: c.needs_review):          # stable: clean first, file order inside each
        if c.language not in targets.by_language:
            kept.append(c)
            continue
        key = (c.group, c.language)
        want = targets.by_language[c.language].get(c.group, 0)
        if seen.get(key, 0) < want:
            seen[key] = seen.get(key, 0) + 1
            kept.append(c)
        elif c.needs_review:
            kept.append(c)                  # a flagged question is never left out; the file may then hold more than the plan
        else:
            surplus.append(c)
    # keep the order the questions arrived in
    position = {id(c): i for i, c in enumerate(items)}
    kept.sort(key=lambda c: position[id(c)])
    missing = {(group, lang): want - seen.get((group, lang), 0)
               for lang, row in targets.by_language.items() for group, want in row.items()
               if want - seen.get((group, lang), 0) > 0}
    return kept, surplus, missing


# ---- the length mix: what is short, and one top-up for a bucket that is far under ------------------------------------------------

TOPUP_GAP = 3                           # a length bucket more than this many questions under its target gets a top-up


def length_counts(items: list[Candidate]) -> dict[str, int]:
    counts = {name: 0 for name in LENGTHS}
    for c in items:
        counts[length_of(c.text)] += 1
    return counts


def topup_request(items: list[Candidate], targets: Targets) -> dict | None:
    """The one bucket to top up, or None. The bucket furthest under its target, if it is more than TOPUP_GAP under.
    Says how many, and in which (group, language) places the new questions can go: the places that hold the most
    questions of a length that is over its target, since those are the ones a new question replaces."""
    counts = length_counts(items)
    gaps = {name: targets.length_aim[name] - counts[name] for name in LENGTHS}
    bucket = max(LENGTHS, key=lambda name: gaps[name])            # first in LENGTHS order wins a tie
    if gaps[bucket] <= TOPUP_GAP:
        return None
    over = {name for name in LENGTHS if counts[name] > targets.length_aim[name]}
    room: dict[tuple[str, str], int] = {}
    for c in items:
        if c.language in targets.by_language and length_of(c.text) in over and not c.control:
            room[(c.group, c.language)] = room.get((c.group, c.language), 0) + 1
    need = gaps[bucket]
    places = []
    for key, n in sorted(room.items(), key=lambda kv: (-kv[1], kv[0])):
        if need <= 0:
            break
        take = min(n, need)
        places.append({"group": key[0], "language": key[1], "count": take})
        need -= take
    count = sum(p["count"] for p in places)
    return {"length": bucket, "count": count, "places": places} if count else None


def merge_topup(items: list[Candidate], extras: list[Candidate], targets: Targets, length: str) -> tuple[list[Candidate], list[Candidate]]:
    """(new list, left out). A new question of the asked length goes into an empty place if its (group, language) has
    one; otherwise it takes the place of one question in the same (group, language) whose length is over its target,
    flagged ones first, last in the file first. A new question that fits no place is left out. Nothing is added beyond
    the plan."""
    items = list(items)
    left_out: list[Candidate] = []
    for new in extras:
        if length_of(new.text) != length or length_counts(items)[length] >= targets.length_aim[length]:
            left_out.append(new)
            continue
        counts = length_counts(items)
        taken = sum(1 for c in items if (c.group, c.language) == (new.group, new.language))
        if taken < targets.by_language.get(new.language, {}).get(new.group, 0):
            items.append(new)                              # an empty place (a question was dropped): fill it, replace nothing
            continue
        victims = [c for c in items if (c.group, c.language) == (new.group, new.language) and not c.control
                   and counts[length_of(c.text)] > targets.length_aim[length_of(c.text)]]
        if not victims:
            left_out.append(new)
            continue
        victim = sorted(victims, key=lambda c: (not c.needs_review, -items.index(c)))[0]
        items[items.index(victim)] = new
        left_out.append(victim)
    return items, left_out


def order_for_file(items: list[Candidate], languages: list[str]) -> list[Candidate]:
    """Discovery (shortest first, by language), then controls, then brand questions, like the existing drafts."""
    rank = {name: i for i, name in enumerate(LENGTHS)}
    group_rank = {"discovery": 0, "control": 1, "brand": 2}
    lang_rank = {lang: i for i, lang in enumerate(languages)}
    return sorted(items, key=lambda c: (group_rank[c.group], lang_rank.get(c.language, len(languages)), rank[length_of(c.text)]))


# ---- what the brand questions cover -------------------------------------------------------------------------------------------

def topics_used(items: list[Candidate]) -> list[str]:
    return sorted({c.fact_topic for c in items if c.kind == "brand" and c.fact_topic})


def needs_used(items: list[Candidate]) -> list[str]:
    return [n for n in NEEDS if any(c.need == n for c in items if c.kind == "discovery" and not c.control)]


def coverage_warnings(items: list[Candidate], *, name: str | None, domain: str | None, location: str | None,
                      needs_place: bool) -> list[str]:
    """Things the brand and discovery questions should cover. Warnings only: never padded, never a reason to drop."""
    out: list[str] = []
    brand = [c for c in items if c.kind == "brand"]
    if brand:
        want = min(MIN_TOPICS, len(brand))
        have = topics_used(brand)
        if len(have) < want:
            out.append(f"Brand questions cover {len(have)} fact topic(s) ({', '.join(have) or 'none'}); the aim is at least {want}.")
        host = (domain or "").lower().removeprefix("www.")
        if host and not any(host in c.text.lower() for c in brand):
            out.append("No brand question uses the web address (the \"with the URL\" variant).")
        if name and needs_place and location and not any(
                fold_has(c.text, name) and fold_has(c.text, location) for c in brand):
            out.append(f"No brand question puts the city after the name (\"{name} {location}\"), which tests whether AI mixes it up with another business.")
        if name and not any(_RECOGNITION.search(c.text) and fold_has(c.text, name) for c in brand):
            out.append("No plain recognition question (\"What is <name>?\").")
    disc = [c for c in items if c.kind == "discovery" and not c.control]
    if disc and len(disc) >= len(NEEDS):
        have = needs_used(disc)
        if len(have) < len(NEEDS):
            missed = [n for n in NEEDS if n not in have]
            out.append(f"Discovery questions cover {len(have)} of {len(NEEDS)} customer needs; none for: {', '.join(missed)}.")
    return out


_RECOGNITION = re.compile(r"^\s*(what|who)(?:\s+is|'s|’s)\b|^\s*(qu'est-ce que|qui est|c'est quoi)\b", re.I)


def fold_has(text: str, needle: str) -> bool:
    from questions_draft.location import fold
    n = fold(needle)
    return bool(n) and n in fold(text)


# ---- the spot check, chosen by a fixed rule -------------------------------------------------------------------------------------

def choose_spot_check(items: list[Candidate], languages: list[str], domain: str | None) -> list[int]:
    """Indexes (into `items`) of the questions flagged spot_check. The rule, with no AI and no randomness:

      - never a control and never a question flagged needs_review; 5 discovery and 3 brand where there are that many, else the other kind makes up the eight
      - discovery: a different customer need each (service, urgent, price ...), each carrying a place when one is required
      - brand: a different fact_topic each (a plain recognition question counts as "other" and comes last)
      - within each kind, every requested language appears at least once, and every length present, as far as the places allow
      - at least one brand question that uses the web address, if there is one
      - what is left is filled in file order

    Within each step the first matching question, in the order given, wins."""
    host = (domain or "").lower().removeprefix("www.")
    # a discovery question that had to carry a place and does not is never picked (the checks already drop it; this is the second lock)
    disc = [i for i, c in enumerate(items) if c.kind == "discovery" and not c.control and not c.needs_review
            and not (c.needs_place and c.place_how is None)]
    brand = [i for i, c in enumerate(items) if c.kind == "brand" and not c.control and not c.needs_review]

    n_disc, n_brand = min(SPOT_DISCOVERY, len(disc)), min(SPOT_BRAND, len(brand))
    spare = SPOT_TOTAL - n_disc - n_brand            # one kind is short: the other kind takes its places
    more = min(spare, len(disc) - n_disc)
    n_disc, spare = n_disc + more, spare - more
    n_brand += min(spare, len(brand) - n_brand)

    def distinct(pool: list[int], picked: list[int], budget: int, already: list[int], key) -> None:
        """Adds questions with a value of `key` not yet picked. Among those, a new language beats a new length beats file order."""
        seen = {key(items[i]) for i in picked}
        while len(picked) < budget:
            unmet = [l for l in languages if not any(items[j].language == l for j in picked) and any(items[j].language == l for j in pool)]
            if budget - len(picked) <= len(unmet):
                return                      # the places that are left go to the languages not yet covered
            options = [i for i in pool if i not in picked and key(items[i]) is not None and key(items[i]) not in seen]
            if not options:
                return
            best = max(options, key=lambda i: (key(items[i]) != "other",
                                               not any(items[j].language == items[i].language for j in picked),
                                               not any(length_of(items[j].text) == length_of(items[i].text) for j in [*already, *picked])))
            picked.append(best)
            seen.add(key(items[best]))

    def take(pool: list[int], budget: int, already: list[int], *, want_address: bool = False, key=None) -> list[int]:
        picked: list[int] = []

        def add(i: int | None) -> None:
            if i is not None and i not in picked and len(picked) < budget:
                picked.append(i)

        if want_address and host:
            add(next((i for i in pool if host in items[i].text.lower()), None))
        if key is not None:
            distinct(pool, picked, budget, already, key)
        for lang in languages:
            if not any(items[i].language == lang for i in picked):
                add(next((i for i in pool if items[i].language == lang), None))
        for length in LENGTHS:
            if not any(length_of(items[i].text) == length for i in [*already, *picked]):
                add(next((i for i in pool if length_of(items[i].text) == length), None))
        for i in pool:
            add(i)
        return picked

    chosen = take(disc, n_disc, [], key=lambda c: c.need)
    chosen += take(brand, n_brand, chosen, want_address=True, key=lambda c: c.fact_topic)
    return sorted(chosen)
