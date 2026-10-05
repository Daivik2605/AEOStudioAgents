"""The frozen question set: a versioned YAML file holding an ordered list of questions.

    question_sets/oland-stations_v1.yaml

    note: optional free text
    questions:
      - "first question, exactly as it will be typed into the engine"
      - "second question"

The version is the file's name without ".yaml" (here: "oland-stations_v1"), so
the file and its version can never disagree. The name must end in _v1, _v2 ...

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


@dataclass
class QuestionSet:
    version: str
    questions: list[str]
    fingerprint: str      # sha256 of the questions only, so comments and whitespace may change


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
        # intent / is_target come later (PLAN.md step 9.5); ignoring them silently would hide a mistake.
        raise QuestionSetError(f"{path.name} has fields not supported yet: {', '.join(sorted(extra))}")

    questions = data["questions"]
    if not isinstance(questions, list) or not questions:
        raise QuestionSetError(f"{path.name}: 'questions' must be a non-empty list")
    if not all(isinstance(q, str) and q.strip() for q in questions):
        raise QuestionSetError(f"{path.name}: every question must be non-empty text")
    questions = [q.strip() for q in questions]
    if len(set(questions)) != len(questions):
        raise QuestionSetError(f"{path.name}: the same question appears twice")

    fingerprint = hashlib.sha256(json.dumps(questions, ensure_ascii=False).encode("utf-8")).hexdigest()
    return QuestionSet(version=version, questions=questions, fingerprint=fingerprint)


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
