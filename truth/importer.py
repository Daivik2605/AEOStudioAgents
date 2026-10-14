"""`aeo truth import`: read a filled truth document and save it as a NEW business profile version.

In this order, and each step can stop the whole thing with nothing saved:
  1. read and check the file; list every problem (same style as `aeo audit import`)
  2. work out what changed against the latest version, and print it
  3. a person types `yes` (no terminal = refused, no way around it)
  4. copy the filled file into evidence/, then save the version and write the journal entry together

Older versions are never edited or deleted. An unconfirmed fact is saved as unconfirmed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from audit import cost_gate
from core import client_files
from core.db import get_connection
from core.journal import write_journal
from core.operator import operator_name
from core.profiles import CONFIRMED, STORY_FIELDS, latest_profile, save_new_version
from truth import format as fmt

NO_TERMINAL_MESSAGE = "A person must be at the keyboard to approve this. Run this again from a terminal."

STORY_NAMES = {"description": "description", "goals": "goals", "customers": "customers",
               "customer_questions": "what customers ask"}


class TruthImportError(Exception):
    """The file cannot be imported (not found, for another business)."""


class TruthImportRefused(TruthImportError):
    """The file has problems. `.problems` lists every one; nothing was saved."""

    def __init__(self, problems: list[str]):
        super().__init__(f"{len(problems)} problem(s) found; nothing was saved")
        self.problems = problems


class TruthImportAborted(Exception):
    """The person did not type yes, or nobody was at the keyboard. The message was already printed."""


@dataclass
class Diff:
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    changed: list[tuple[dict, dict]] = field(default_factory=list)       # (old, new)
    story: dict[str, tuple[str, str]] = field(default_factory=dict)      # field -> (old, new), changed fields only

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.story)


@dataclass
class TruthImportResult:
    saved: bool
    version: int | None
    previous_version: int | None
    diff: Diff
    confirmed: int = 0
    unconfirmed: int = 0
    evidence_copy: Path | None = None


def _key(fact: dict) -> str:
    return fact["label"].casefold().strip()


def _same(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("value", "status", "confirmed_by", "confirmed_on"))


def diff_versions(old_facts: list[dict], new_facts: list[dict], old_story: dict, new_story: dict) -> Diff:
    """Facts are matched by label. A fact is 'changed' if its value, status or confirmation differs."""
    d = Diff()
    old = {_key(f): f for f in old_facts}
    new = {_key(f): f for f in new_facts}
    d.added = [f for k, f in new.items() if k not in old]
    d.removed = [f for k, f in old.items() if k not in new]
    d.changed = [(old[k], f) for k, f in new.items() if k in old and not _same(old[k], f)]
    for name in STORY_FIELDS:
        before, after = (old_story.get(name) or "").strip(), (new_story.get(name) or "").strip()
        if before != after:
            d.story[name] = (before, after)
    return d


def _short(text: str, n: int = 70) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _describe(f: dict) -> str:
    who = f" by {f['confirmed_by']} on {f['confirmed_on']}" if f["status"] == CONFIRMED else ""
    return f"\"{_short(f['value'])}\" [{f['status']}{who}]"


def diff_lines(diff: Diff, previous_version: int | None, new_version: int) -> list[str]:
    base = f"version {previous_version}" if previous_version else "nothing yet"
    out = [f"Changes compared with {base} (this would become version {new_version}):"]
    if diff.added:
        out.append(f"  Facts added ({len(diff.added)}):")
        out += [f"    + {f['label']}: {_describe(f)}" for f in diff.added]
    if diff.changed:
        out.append(f"  Facts changed ({len(diff.changed)}):")
        for old, new in diff.changed:
            out.append(f"    ~ {new['label']}: {_describe(old)}  ->  {_describe(new)}")
    if diff.removed:
        out.append(f"  Facts removed ({len(diff.removed)}):")
        out += [f"    - {f['label']}: {_describe(f)}" for f in diff.removed]
    if diff.story:
        out.append("  Story changed:")
        for name, (before, after) in diff.story.items():
            verb = "added" if not before else "removed" if not after else "changed"
            out.append(f"    {STORY_NAMES[name]}: {verb}")
    # A confirmed fact that changed or disappeared deserves a second look before it is saved.
    touched = [f for f in diff.removed if f["status"] == CONFIRMED]
    touched += [old for old, new in diff.changed
                if old["status"] == CONFIRMED and (new["status"] != CONFIRMED or new["value"] != old["value"])]
    if touched:
        out.append(f"  Look twice: {len(touched)} fact(s) that were confirmed before are changed or removed.")
    return out


def _confirm(lines: list[str], version: int, *, ask: Callable[[str], str], say: Callable[[str], None]) -> None:
    for line in lines:
        say(line)
    if not cost_gate.stdin_is_tty():              # the same check as the cost gate; no flag skips it
        say(NO_TERMINAL_MESSAGE)
        raise TruthImportAborted()
    try:
        reply = ask(f"  Type yes to save this as version {version}: ")
    except EOFError:
        reply = ""
    if reply.strip().lower() != "yes":
        say("Aborted. Nothing was saved.")
        raise TruthImportAborted()


def import_truth(business: dict, path: str | Path, *, ask: Callable[[str], str] = input,
                 say: Callable[[str], None] = print, clients_root: Path | None = None,
                 today: date | None = None, operator: str | None = None) -> TruthImportResult:
    path = Path(path)
    if not path.is_file():
        raise TruthImportError(f"file not found: {path}")
    parsed = fmt.parse_document(path.read_text(encoding="utf-8"), today=today)

    problems = list(parsed.problems)
    file_business = parsed.header.get("BUSINESS ID")
    if not problems and file_business != business["id"]:
        problems.append(f"this file was written for business id {file_business or '(none)'}, "
                        f"but you asked for {business['name'] or business['domain']} (id {business['id']})")
    if problems:
        raise TruthImportRefused(problems)

    conn = get_connection()
    try:
        previous = latest_profile(conn, business["id"])
        old_facts, old_story = (previous["facts"], previous["story"]) if previous else ([], {})
        diff = diff_versions(old_facts, parsed.facts, old_story, parsed.story)
        previous_version = previous["version"] if previous else None
        confirmed = sum(1 for f in parsed.facts if f["status"] == CONFIRMED)
        counts = dict(confirmed=confirmed, unconfirmed=len(parsed.facts) - confirmed)
        if diff.empty:
            say(f"Nothing changed since version {previous_version}. Nothing was saved.")
            return TruthImportResult(False, None, previous_version, diff, **counts)

        new_version = (previous_version or 0) + 1
        lines = diff_lines(diff, previous_version, new_version)
        lines.append(f"  After this: {confirmed} confirmed fact(s), {counts['unconfirmed']} unconfirmed. "
                     "Only confirmed facts are used to judge what AI says.")
        _confirm(lines, new_version, ask=ask, say=say)

        # The filled file is evidence (it came in), so it is kept before anything else is written.
        stem = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", path.stem)
        copy = client_files.copy_new(path, client_files.evidence_dir(business["domain"], clients_root),
                                     f"{(today or date.today()).isoformat()}_{client_files.safe_name(stem)}_filled{path.suffix}")

        with conn.transaction():
            profile_id, version = save_new_version(conn, business["id"], facts=parsed.facts, story=parsed.story)
            write_journal(
                business_id=business["id"], entry_type="questionnaire_received", actor=operator or operator_name(),
                description=f"Truth document imported: profile version {version} "
                            f"({confirmed} confirmed, {counts['unconfirmed']} unconfirmed facts)",
                related_table="business_profiles", related_id=profile_id, conn=conn,
                details={"version": version, "previous_version": previous_version, **counts,
                         "facts_added": len(diff.added), "facts_changed": len(diff.changed),
                         "facts_removed": len(diff.removed), "story_changed": sorted(diff.story),
                         "file": f"evidence/{copy.name}", "source_file": path.name})
    finally:
        conn.close()
    return TruthImportResult(True, version, previous_version, diff, evidence_copy=copy, **counts)
