"""`aeo files migrate`: copy the old reports/ folder into the new clients/ layout (PLAN.md section 10).

It only COPIES. Nothing in reports/ is changed or deleted, and nothing in clients/ is overwritten
(a name that is taken gets _2, _3 ... unless the file there is byte-for-byte the same, in which case
it is already migrated and is skipped, so running --apply twice does not make duplicates).

The old layout was  reports/<domain>/<checkpoint>/<file>.  The new one has no checkpoint folders, so
every client's files land in one place and are told apart by date. That means run numbers have to be
worked out again (old_site and new_site_no_aeo both had a "run 1"). They are numbered in date order.

Which files go where:
  diagnosis / audit_fill_in reports  ->  generated/   (the tool made them)
  everything else (raw_page, audit_manual_snapshot.md, anything unrecognised)  ->  evidence/
  A file we cannot recognise is treated as evidence on purpose: evidence is never overwritten and is
  backed up, so guessing "evidence" can never lose anything.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from core import client_files
from core.checkpoints import CHECKPOINTS, RENAMED

REPORTS_ROOT = Path(__file__).parent.parent / "reports"      # the old layout; left alone

GENERATED_KINDS = {"diagnosis", "audit_fill_in"}
# Files of one family were written together and share a run number (the raw page belongs to its diagnosis).
RUN_FAMILY = {"diagnosis": "diagnosis", "raw_page": "diagnosis", "audit_fill_in": "audit_fill_in"}

NUMBERED = re.compile(r"^(?P<stem>.+)_run(?P<n>\d+)_(?P<date>\d{4}-\d{2}-\d{2})\.(?P<ext>[A-Za-z0-9]+)$")
STAMPED = re.compile(r"^(?P<stem>.+)\.(?P<date>\d{8})T(?P<time>\d{6})\.(?P<ext>[A-Za-z0-9]+)$")
IGNORED = {".DS_Store"}


@dataclass
class Item:
    src: Path
    dst: Path | None            # None when skipped
    action: str                 # "copy" | "skip"
    note: str = ""


@dataclass
class Plan:
    items: list[Item] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def to_copy(self) -> list[Item]:
        return [i for i in self.items if i.action == "copy"]


@dataclass
class _Entry:
    src: Path
    folder: str                 # the old checkpoint folder name ("" if the file sat directly under the client)
    stem: str
    ext: str
    form: str                   # numbered | stamped | plain
    day: str                    # YYYY-MM-DD
    order: tuple                # sorts entries of the same day
    n: int | None = None        # the old run number, if it had one


def _mtime_day(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def _classify(src: Path, folder: str) -> _Entry:
    name = src.name
    if m := NUMBERED.match(name):
        return _Entry(src, folder, m["stem"], m["ext"], "numbered", m["date"], ("n", int(m["n"])), int(m["n"]))
    if m := STAMPED.match(name):
        d = m["date"]
        return _Entry(src, folder, m["stem"], m["ext"], "stamped", f"{d[:4]}-{d[4:6]}-{d[6:]}", ("s", m["time"]))
    stem, _, ext = name.rpartition(".")
    if not stem:
        stem, ext = name, ""
    return _Entry(src, folder, stem, ext, "plain", _mtime_day(src), ("p",))


def _resolve(dst: Path, data: bytes, taken: set[Path]) -> tuple[Path | None, str]:
    """The path to copy to, or (None, reason) if the same bytes are already there. Tries dst, dst_2, dst_3 ..."""
    candidate, n = dst, 2
    while True:
        if candidate in taken:
            pass                                        # another file in this same plan is going there
        elif candidate.exists():
            if candidate.read_bytes() == data:
                return None, f"already copied to {candidate.name}"
        else:
            return candidate, "" if candidate == dst else f"{dst.name} was taken, so _{n - 1} was added"
        candidate = dst.with_name(f"{dst.stem}_{n}{dst.suffix}")
        n += 1


def plan_migration(reports_root: Path, clients_root: Path | None = None) -> Plan:
    plan = Plan()
    reports_root = Path(reports_root)
    if not reports_root.is_dir():
        plan.notes.append(f"{reports_root} does not exist, so there is nothing to migrate.")
        return plan
    taken: set[Path] = set()
    unknown_folders: set[str] = set()

    for client_dir in sorted(p for p in reports_root.iterdir() if p.is_dir()):
        slug = client_dir.name
        entries: list[_Entry] = []
        for src in sorted(p for p in client_dir.rglob("*") if p.is_file() and p.name not in IGNORED):
            rel = src.relative_to(client_dir)
            folder = rel.parts[0] if len(rel.parts) > 1 else ""
            if folder and folder not in CHECKPOINTS and folder not in RENAMED and folder != "unspecified":
                unknown_folders.add(f"{slug}/{folder}")
            entries.append(_classify(src, folder))

        # A "latest" copy (diagnosis.md) that is identical to a dated copy next to it is a duplicate: skip it.
        # If it differs (a newer run that only exists as "latest"), it is real history and is kept.
        kept: list[_Entry] = []
        for e in entries:
            if e.form == "plain":
                twin = next((o for o in entries if o is not e and o.form != "plain" and o.folder == e.folder
                             and o.stem == e.stem and o.ext == e.ext and o.src.read_bytes() == e.src.read_bytes()), None)
                if twin:
                    plan.items.append(Item(e.src, None, "skip", f"same bytes as {twin.src.name}; there are no 'latest' copies any more"))
                    continue
            kept.append(e)

        # Run numbers, per family, in date order. A group is the set of files written together.
        groups: dict[tuple, list[_Entry]] = {}
        for e in kept:
            if e.stem in RUN_FAMILY:
                token = (e.form, e.day, e.order) if e.form != "plain" else ("plain",)
                groups.setdefault((RUN_FAMILY[e.stem], e.folder, token), []).append(e)
        runs: dict[tuple, int] = {}
        for family in sorted({k[0] for k in groups}):
            ordered = sorted((k for k in groups if k[0] == family),
                             key=lambda k: (min(x.day for x in groups[k]), groups[k][0].order, k[1]))
            runs.update({k: i for i, k in enumerate(ordered, start=1)})

        for e in kept:
            if e.stem in RUN_FAMILY:
                token = (e.form, e.day, e.order) if e.form != "plain" else ("plain",)
                key = (RUN_FAMILY[e.stem], e.folder, token)
                day = date.fromisoformat(min(x.day for x in groups[key]))
                name = client_files.dated_name(day, e.stem, e.ext, runs[key])
            elif e.form == "numbered":
                name = client_files.dated_name(date.fromisoformat(e.day), e.stem, e.ext, e.n)
            else:
                name = client_files.dated_name(date.fromisoformat(e.day), e.stem, e.ext)
            folder = (client_files.generated_dir if e.stem in GENERATED_KINDS else client_files.evidence_dir)(slug, clients_root)
            dst, why = _resolve(folder / name, e.src.read_bytes(), taken)
            if dst is None:
                plan.items.append(Item(e.src, None, "skip", why))
            else:
                taken.add(dst)
                plan.items.append(Item(e.src, dst, "copy", why))

    if unknown_folders:
        plan.notes.append("These old folders are not checkpoint labels: " + ", ".join(sorted(unknown_folders))
                          + ". Their files are copied anyway; file names no longer carry a label (the database does).")
    return plan


def apply_plan(plan: Plan) -> list[Item]:
    """Copies every planned file. Never deletes, never overwrites. Returns what was copied."""
    done = []
    for item in plan.to_copy:
        item.dst.parent.mkdir(parents=True, exist_ok=True)
        # "xb" refuses to open an existing file: if something appeared there since the plan was made, stop loudly.
        with open(item.src, "rb") as source, open(item.dst, "xb") as target:
            shutil.copyfileobj(source, target)
        shutil.copystat(item.src, item.dst)             # keeps the original modified time
        done.append(item)
    return done


def odd_checkpoints(conn) -> list[tuple[str, str, int]]:
    """Checkpoint values in the database that are not one of the four labels: (table, value, rows).
    Listed for a person to look at; never rewritten here."""
    out = []
    with conn.cursor() as cur:
        for table in ("diagnoses", "probe_runs"):
            cur.execute(f"SELECT checkpoint, count(*) FROM {table} WHERE checkpoint IS NOT NULL "
                        "AND checkpoint <> ALL(%s) GROUP BY checkpoint ORDER BY checkpoint", (list(CHECKPOINTS),))
            out += [(table, value, n) for value, n in cur.fetchall()]
    return out
