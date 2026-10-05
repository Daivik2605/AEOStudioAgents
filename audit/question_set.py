"""The frozen question set: a versioned YAML file holding an ordered list of questions.

    question_sets/oland-stations_v1.yaml

    note: optional free text
    questions:
      - "a plain question, exactly as it will be typed into the engine"
      - text: "a question with its tags"
        intent: commercial        # optional: commercial / informational / navigational
        is_target: true           # optional: one of the few we are optimising for

The version is the file's name without ".yaml" (here: "oland-stations_v1"), so
the file and its version can never disagree. The name must end in _v1, _v2 ...

A plain string and a mapping with only "text" mean the same thing.

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


class QuestionSetError(Exception):
    """The file is missing, malformed, or was changed after it was used."""


INTENTS = ("commercial", "informational", "navigational")


@dataclass
class Question:
    text: str
    intent: str | None = None
    is_target: bool = False


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
    canonical = [q.text if (q.intent is None and not q.is_target)
                 else {"text": q.text, "intent": q.intent, "is_target": q.is_target} for q in items]
    fingerprint = hashlib.sha256(json.dumps(canonical, ensure_ascii=False).encode("utf-8")).hexdigest()
    return QuestionSet(version=version, items=items, fingerprint=fingerprint)


def _parse_question(filename: str, entry) -> Question:
    if isinstance(entry, str):
        entry = {"text": entry}
    if not isinstance(entry, dict):
        raise QuestionSetError(f"{filename}: every question must be text, or a mapping with 'text:'")
    extra = set(entry) - {"text", "intent", "is_target"}
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
    return Question(text=text.strip(), intent=intent, is_target=target)


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
