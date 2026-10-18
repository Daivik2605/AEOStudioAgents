"""Reading a proposed question set for the dossier, and the brand-name check.

This reader is deliberately forgiving. The strict loader (audit/question_set.py) refuses tags it does not know, and the
discovery/brand, length and language tags are not part of it yet (step 22d). A draft set is exactly where those tags first
appear, so the dossier reads them as they are written and reports what is missing. It validates nothing and changes nothing.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

TYPES = ("discovery", "brand")
LENGTHS = ("short", "medium", "long")
QUESTION_DIR = Path(__file__).parent.parent / "question_sets"


class DossierQuestionsError(Exception):
    """The question file cannot be read."""


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def compact(text: str) -> str:
    return normalise(text).replace(" ", "")


def length_of(text: str) -> str:
    """short: 3-6 words (search-box style). long: two or more sentences, or a long run. Else medium (PLAN.md section 8)."""
    words = len(text.split())
    sentences = len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])
    if words <= 6:
        return "short"
    if sentences >= 2 or words > 30:
        return "long"
    return "medium"


def brand_terms(name: str | None, domain: str | None, alternates: list[str] | None = None) -> list[str]:
    """What counts as 'the brand' in a question: the name, other names, the domain, and the domain without its ending."""
    terms = [t for t in [name, *(alternates or [])] if t]
    if domain:
        host = domain.lower().removeprefix("www.")
        terms += [host, host.split(".")[0]]
    return [t for t in dict.fromkeys(terms) if len(compact(t)) >= 4]


def contains_brand(question: str, terms: list[str]) -> str | None:
    """The term found in the question, or None. Matches 'O'land Stations' as 'oland stations' and the bare domain."""
    low, norm, comp = question.lower(), normalise(question), compact(question)
    for term in terms:
        if term.lower() in low or f" {normalise(term)} " in f" {norm} " or compact(term) in comp:
            return term
    return None


def read_questions(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise DossierQuestionsError(f"question file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DossierQuestionsError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise DossierQuestionsError(f"{path.name} must have a 'questions:' list")
    items = []
    for entry in data["questions"]:
        entry = {"text": entry} if isinstance(entry, str) else entry
        if not isinstance(entry, dict) or not str(entry.get("text") or "").strip():
            raise DossierQuestionsError(f"{path.name}: every question needs text")
        text = str(entry["text"]).strip()
        tag_length = entry.get("length")
        items.append({"text": text, "type": entry.get("type"), "intent": entry.get("intent"),
                      "length": tag_length if tag_length in LENGTHS else length_of(text),
                      "length_from": "tag" if tag_length in LENGTHS else "word count",
                      "language": entry.get("language"), "is_target": bool(entry.get("is_target", False))})
    return {"version": path.stem, "file": path.name, "items": items}


def find_question_file(version: str, directory: Path | None = None) -> Path | None:
    path = (directory or QUESTION_DIR) / f"{version}.yaml"
    return path if path.is_file() else None


def classify(text: str, tag: str | None, terms: list[str]) -> tuple[str, str]:
    """('discovery' | 'brand', how we know). A tag wins; with no tag, a question that names the brand is a brand question."""
    if tag in TYPES:
        return tag, "tag"
    return ("brand" if contains_brand(text, terms) else "discovery"), "derived from the text"
