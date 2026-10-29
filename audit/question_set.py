"""The frozen question set: a versioned YAML file holding an ordered list of questions.

    question_sets/oland-stations_v1.yaml

    note: optional free text
    questions:
      - "a plain question, exactly as it will be typed into the engine"
      - text: "a question with its tags"
        intent: commercial        # optional: commercial / informational / navigational
        is_target: true           # optional: one of the few we are optimising for
        type: discovery           # optional: discovery (buyer does not know the client) or brand (names the client)
        length: short             # optional: short / medium / long (worked out from the word count if left out)
        language: fr              # optional: the language the buyer asks in
        control: true             # optional: an informational question we deliberately do not work on
        spot_check: true          # optional: part of the small first-look set

The version is the file's name without ".yaml" (here: "oland-stations_v1"), so
the file and its version can never disagree. The name must end in _v1, _v2 ...

A plain string and a mapping with only "text" mean the same thing.

Two kinds of question, never mixed (PLAN.md section 8). Discovery questions never name the client and are the only
ones that measure visibility. Brand questions name the client and measure recognition and accuracy. The file may say
which is which; if it does not, the audit works it out from the text when it starts (see resolve_tags).

A version is never edited once it has been used (PLAN.md section 8). Changing
a question means writing _v2. We enforce that: every audit run records a
fingerprint of its questions in the journal, and a run whose questions differ
from an earlier run (one that collected at least one answer) under the same
version is refused.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml

# The word-matching rules for "does this question name the client" live with the dossier, which needed them first.
from dossier.questions import LENGTHS, TYPES, brand_terms, contains_brand, length_of


class QuestionSetError(Exception):
    """The file is missing, malformed, or was changed after it was used."""


INTENTS = ("commercial", "informational", "navigational")


@dataclass
class Question:
    text: str
    intent: str | None = None
    is_target: bool = False
    type: str | None = None         # discovery / brand; None = not said in the file
    length: str | None = None       # short / medium / long; None = not said in the file
    language: str | None = None
    control: bool = False
    spot_check: bool = False


@dataclass
class QuestionSet:
    version: str
    items: list[Question]
    fingerprint: str      # sha256 of the questions, intents and targets; comments and whitespace may change

    @property
    def questions(self) -> list[str]:
        return [q.text for q in self.items]

    def target_warnings(self) -> list[str]:
        """A target on an informational question is allowed, but almost always a mistake."""
        return [f"Target question is tagged informational: \"{q.text}\". Informational questions almost "
                "never trigger a real engine search (ChatGPT searched on 0.9% of them vs 86.5% of commercial "
                "ones; Cloro, PLAN.md section 6), so optimising for it is unlikely to show up in the audit."
                for q in self.items if q.is_target and q.intent == "informational"]


VERSION_RE = re.compile(r"^.+_v\d+$")


def load_question_set(path: str | Path) -> QuestionSet:
    path = Path(path)
    if not path.is_file():
        raise QuestionSetError(f"question set file not found: {path}")
    version = path.stem
    if not VERSION_RE.match(version):
        raise QuestionSetError(
            f"'{path.name}': the file name must end in _v1, _v2 ... (e.g. oland-stations_v1.yaml), "
            "because the name is the version")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise QuestionSetError(f"{path.name} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict) or "questions" not in data:
        raise QuestionSetError(f"{path.name} must have a 'questions:' list")
    extra = set(data) - {"questions", "note"}
    if extra:
        # intent and is_target belong on each question, not at the top; ignoring a stray field would hide a mistake.
        raise QuestionSetError(f"{path.name} has fields not supported yet: {', '.join(sorted(extra))}")

    raw = data["questions"]
    if not isinstance(raw, list) or not raw:
        raise QuestionSetError(f"{path.name}: 'questions' must be a non-empty list")
    items = [_parse_question(path.name, entry) for entry in raw]
    if len({q.text for q in items}) != len(items):
        raise QuestionSetError(f"{path.name}: the same question appears twice")

    # What the fingerprint covers. A question with no tags counts as its bare text, exactly as before
    # tags existed, so sets already used keep their fingerprint. is_target false is the same as unset.
    # The newer tags are added only when the file states them, so a set frozen before they existed
    # keeps the fingerprint it was frozen with. (What the audit works out itself is not in the file, so not here.)
    def canonical_entry(q: Question):
        if (q.intent is None and not q.is_target and q.type is None and q.length is None
                and q.language is None and not q.control and not q.spot_check):
            return q.text
        entry = {"text": q.text, "intent": q.intent, "is_target": q.is_target}
        for key in ("type", "length", "language"):
            if getattr(q, key) is not None:
                entry[key] = getattr(q, key)
        for key in ("control", "spot_check"):
            if getattr(q, key):
                entry[key] = True
        return entry
    canonical = [canonical_entry(q) for q in items]
    fingerprint = hashlib.sha256(json.dumps(canonical, ensure_ascii=False).encode("utf-8")).hexdigest()
    return QuestionSet(version=version, items=items, fingerprint=fingerprint)


def _parse_question(filename: str, entry) -> Question:
    if isinstance(entry, str):
        entry = {"text": entry}
    if not isinstance(entry, dict):
        raise QuestionSetError(f"{filename}: every question must be text, or a mapping with 'text:'")
    extra = set(entry) - {"text", "intent", "is_target", "type", "length", "language", "control", "spot_check"}
    if extra:
        raise QuestionSetError(f"{filename}: a question has fields not supported: {', '.join(sorted(extra))}")
    text = entry.get("text")
    if not isinstance(text, str) or not text.strip():
        raise QuestionSetError(f"{filename}: every question must have non-empty text")
    intent, target = entry.get("intent"), entry.get("is_target", False)
    if intent is not None and intent not in INTENTS:
        raise QuestionSetError(f"{filename}: intent must be one of {', '.join(INTENTS)} (got {intent!r})")
    if not isinstance(target, bool):
        raise QuestionSetError(f"{filename}: is_target must be true or false (got {target!r})")
    kind, length, language = entry.get("type"), entry.get("length"), entry.get("language")
    if kind is not None and kind not in TYPES:
        raise QuestionSetError(f"{filename}: type must be one of {', '.join(TYPES)} (got {kind!r})")
    if length is not None and length not in LENGTHS:
        raise QuestionSetError(f"{filename}: length must be one of {', '.join(LENGTHS)} (got {length!r})")
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise QuestionSetError(f"{filename}: language must be text such as en or fr (got {language!r})")
    flags = {}
    for key in ("control", "spot_check"):
        flags[key] = entry.get(key, False)
        if not isinstance(flags[key], bool):
            raise QuestionSetError(f"{filename}: {key} must be true or false (got {flags[key]!r})")
    return Question(text=text.strip(), intent=intent, is_target=target, type=kind, length=length,
                    language=language.strip() if language else None, **flags)


def resolve_tags(qs: QuestionSet, business_name: str | None, domain: str | None) -> tuple[list[dict], list[str]]:
    """(every question with its tags settled, warnings). Needs the business, because 'discovery or brand' means
    'does the question name this business'. Raises QuestionSetError for a question that cannot be right.

    - Tagged discovery but names the business: refused. A discovery question that names the client would make
      visibility look better than it is (PLAN.md section 8).
    - No type tag: brand if it names the business, otherwise discovery. The result says the type was derived,
      so a report can show it.
    - Tagged brand but does not name the business: kept, with a warning. It cannot measure recognition.
    - No length tag: worked out from the word count.
    """
    terms = brand_terms(business_name, domain)
    resolved, warnings = [], []
    for q in qs.items:
        found = contains_brand(q.text, terms)
        if q.type == "discovery" and found:
            raise QuestionSetError(
                f"{qs.version}: \"{q.text}\" is tagged discovery but names the business (\"{found}\"). A discovery "
                "question must not: the buyer has not heard of the client. Tag it brand, or reword it, in a new version.")
        if q.type == "brand" and not found:
            warnings.append(f"Brand question does not name the business: \"{q.text}\". It cannot show whether AI "
                            "recognises them. Is it meant to be a discovery question?")
        kind = q.type or ("brand" if found else "discovery")
        resolved.append({"text": q.text, "intent": q.intent, "is_target": q.is_target,
                         "type": kind, "type_source": "tag" if q.type else "derived from the text",
                         "length": q.length or length_of(q.text), "length_source": "tag" if q.length else "word count",
                         "language": q.language, "control": q.control, "spot_check": q.spot_check})
    return resolved, warnings


def check_not_edited(conn: psycopg.Connection, qs: QuestionSet) -> None:
    """Refuses if this version was already used with different questions."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT j.details->>'question_set_sha256' FROM client_journal j "
            "JOIN probe_results r ON r.probe_run_id::text = j.details->>'probe_run_id' "
            "WHERE j.entry_type = 'probe_started' AND j.details->>'question_set_version' = %s",
            (qs.version,),
        )
        used = {row[0] for row in cur.fetchall()}
    if used and used != {qs.fingerprint}:
        raise QuestionSetError(
            f"{qs.version} was already used in an audit with different questions. "
            f"A used version is never edited: copy it to {_next_version(qs.version)} and change that one.")


def _next_version(version: str) -> str:
    stem, _, number = version.rpartition("_v")
    return f"{stem}_v{int(number) + 1}"
