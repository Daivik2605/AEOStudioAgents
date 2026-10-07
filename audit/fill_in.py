"""The audit fill-in file (PLAN.md section 3.2): answers go in through a markdown sheet, not the terminal.

`generate_fill_in` writes the sheet. `import_fill_in` reads it back and saves the answers.
Both sides use the constants below, so the layout lives in exactly one place.

How a sheet is read (so a pasted answer can contain anything):
  * A block starts at a heading line of the exact form  `## Q3 · ChatGPT · run 1 of 3 · [Q3-chatgpt-r1]`
    and ends at the next such heading (or the end of the file).
  * Inside a block the five label lines (QUESTION..., MODE..., SEARCHED..., ANSWER..., LINKS...) are matched
    as whole lines. The first four are found in order, first match each; the LINKS label is the LAST match.
    So an answer that happens to contain a blank line, a `---` rule, markdown, the word QUESTION, or even
    a copy of a label, still comes through intact.
  * The closing `---` is only removed from the very end of the links box.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import UUID

from audit import store
from audit.derive import DeriveReport, derive_run
from audit.manual import ACTOR, ENGINES, AuditError
from audit.question_set import QuestionSet, QuestionSetError, check_not_edited, load_question_set
from audit.sources import merge_sources, parse_link
from audit.summary import build_summary
from core.db import get_connection
from core.journal import write_journal
from core.report_files import numbered_name, write_run_files
from diagnose.report import REPORTS_ROOT, safe_checkpoint, slugify_domain

SHEET_MAGIC = "AEO FILL-IN SHEET v1"
SHEET_ANCHOR = "audit_fill_in.md"       # numbered as audit_fill_in_run<N>_<date>.md

ENGINE_LABELS = {"chatgpt": "ChatGPT", "perplexity": "Perplexity"}

# Header: pre-filled facts are `KEY: value` lines; the three boxes are a label with the value below it.
OPERATOR_LABEL = "OPERATOR (your name):"
LOGGED_LABEL = "LOGGED IN OR OUT (out / in):"
LOCATION_LABEL = "LOCATION YOU ASK FROM (city):"
HEADER_LABELS = [OPERATOR_LABEL, LOGGED_LABEL, LOCATION_LABEL]

# Block labels, exactly as PLAN.md shows them.
Q_LABEL = "QUESTION (copy this):"
MODE_LABEL = "MODE (e.g. free-Instant, Thinking):"
SEARCH_LABEL = "SEARCHED THE WEB (y / n / unclear):"
ANSWER_LABEL = "ANSWER (paste the full answer below this line):"
LINKS_LABEL = "LINKS IT SHOWED (one per line):"
RULE = "---"

HEADING_RE = re.compile(r"^## Q(\d+) · (.+?) · run (\d+) of (\d+) · \[(Q\d+-[a-z]+-r\d+)\]\s*$")
KEY_RE = re.compile(r"^([A-Z][A-Z ]*[A-Z]): (.*)$")


def block_id(number: int, engine: str, repeat: int) -> str:
    return f"Q{number}-{engine}-r{repeat}"


# ---- writing the sheet --------------------------------------------------------

def render_block(number: int, engine: str, repeat: int, repeats: int, question: str) -> str:
    """One block. The only place the block layout is written."""
    return (
        f"## Q{number} · {ENGINE_LABELS[engine]} · run {repeat} of {repeats} · [{block_id(number, engine, repeat)}]\n\n"
        f"{Q_LABEL}\n{question}\n\n"
        f"{MODE_LABEL}\n\n\n"
        f"{SEARCH_LABEL}\n\n\n"
        f"{ANSWER_LABEL}\n\n\n"
        f"{LINKS_LABEL}\n\n\n"
        f"{RULE}\n")


def render_sheet(*, business: dict, qs: QuestionSet, qs_path: str, checkpoint: str | None, day: date,
                 run_id, repeats: int) -> str:
    head = f"""# Audit fill-in sheet

{SHEET_MAGIC}

How to fill this in: use a logged-out or incognito window and a fresh chat for every block. Type your name,
out/in and your city once, below. Then, for each block: copy the question into the engine, paste the full
answer under the ANSWER line, the links it showed under the LINKS line, and say whether it searched the web.
Do not change the question text or the lines in capitals. You can stop and come back, or split the blocks
between two people: import the file again whenever you like, and answers already saved are never duplicated.

BUSINESS: {business['name'] or business['domain']}
BUSINESS ID: {business['id']}
QUESTION SET: {qs.version}
QUESTION SET FILE: {qs_path}
CHECKPOINT: {checkpoint or ''}
DATE: {day.isoformat()}
PROBE RUN: {run_id}
REPEATS: {repeats}

{OPERATOR_LABEL}


{LOGGED_LABEL}


{LOCATION_LABEL}


{RULE}

"""
    blocks = [render_block(n, e, r, repeats, q)
              for n, q in enumerate(qs.questions, start=1) for e in ENGINES for r in range(1, repeats + 1)]
    return head + "\n".join(blocks)


@dataclass
class GenerateResult:
    path: Path
    probe_run_id: str
    blocks: int
    warnings: list[str] = field(default_factory=list)


def generate_fill_in(business_id: str, question_set_path: str, *, checkpoint: str | None, repeats: int = 1,
                     reports_root: Path | None = None, today: date | None = None) -> GenerateResult:
    if repeats < 1:
        raise AuditError("--repeats must be 1 or more")
    try:
        qs = load_question_set(question_set_path)
    except QuestionSetError as exc:
        raise AuditError(str(exc)) from exc

    conn = get_connection()
    try:
        business = store.load_business_and_profile(conn, business_id)
        if business is None:
            raise AuditError(f"no business with id {business_id}")
        if business["profile_id"] is None:
            raise AuditError("this business has no profile row (run `aeo add` to create one)")
        try:
            check_not_edited(conn, qs)
        except QuestionSetError as exc:
            raise AuditError(str(exc)) from exc

        total = len(qs.questions) * len(ENGINES) * repeats
        run_id = store.start_run(conn, business=business, qs=qs, checkpoint=checkpoint, engines=ENGINES,
                                 answers_expected=total, how="fill_in_file", extra={"repeats": repeats})
        day = today or date.today()
        folder = (reports_root or REPORTS_ROOT) / slugify_domain(business["domain"]) / safe_checkpoint(checkpoint)
        text = render_sheet(business=business, qs=qs, qs_path=str(question_set_path), checkpoint=checkpoint,
                            day=day, run_id=run_id, repeats=repeats)
        try:
            number = write_run_files(folder, {SHEET_ANCHOR: text}, anchor=SHEET_ANCHOR, day=day, keep_latest=False)
        except OSError as exc:
            store.finish_run(conn, run_id, complete=False)    # do not leave a run open that has no sheet
            raise AuditError(f"could not write the fill-in file ({exc}); the run was marked failed") from exc
    finally:
        conn.close()

    return GenerateResult(path=folder / numbered_name(SHEET_ANCHOR, number, day), probe_run_id=str(run_id),
                          blocks=total, warnings=qs.target_warnings())


# ---- reading the sheet --------------------------------------------------------

@dataclass
class Block:
    id: str
    number: int
    engine_label: str
    repeat: int
    repeats: int
    question: str = ""
    mode: str = ""
    searched: str = ""
    answer: str = ""
    link_lines: list[str] = field(default_factory=list)
    damaged: str | None = None        # a label line is missing, so the block cannot be read


@dataclass
class Sheet:
    keys: dict[str, str]
    boxes: dict[str, str]
    blocks: list[Block]


def _trim_blank_lines(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _first(lines: list[str], label: str, after: int) -> int | None:
    return next((i for i in range(after, len(lines)) if lines[i].rstrip() == label), None)


def _last(lines: list[str], label: str) -> int | None:
    return next((i for i in range(len(lines) - 1, -1, -1) if lines[i].rstrip() == label), None)


def parse_sheet(text: str) -> Sheet:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    starts = [i for i, line in enumerate(lines) if HEADING_RE.match(line)]
    header = lines[: starts[0]] if starts else lines

    keys: dict[str, str] = {}
    for line in header:
        m = KEY_RE.match(line)
        if m and m.group(1) not in keys:
            keys[m.group(1)] = m.group(2).strip()

    boxes: dict[str, str] = {}
    for label in HEADER_LABELS:
        i = _first(header, label, 0)
        if i is None:
            continue
        value = []
        for line in header[i + 1:]:
            if line.rstrip() in HEADER_LABELS or line.rstrip() == RULE:
                break
            value.append(line)
        boxes[label] = " ".join(v.strip() for v in _trim_blank_lines(value))

    blocks = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        m = HEADING_RE.match(lines[start])
        block = Block(id=m.group(5), number=int(m.group(1)), engine_label=m.group(2),
                      repeat=int(m.group(3)), repeats=int(m.group(4)))
        body = lines[start + 1:end]
        qi = _first(body, Q_LABEL, 0)
        mi = _first(body, MODE_LABEL, qi + 1) if qi is not None else None
        si = _first(body, SEARCH_LABEL, mi + 1) if mi is not None else None
        ai = _first(body, ANSWER_LABEL, si + 1) if si is not None else None
        li = _last(body, LINKS_LABEL)
        if None in (qi, mi, si, ai) or li is None or li <= ai:
            block.damaged = "one of its label lines (QUESTION, MODE, SEARCHED, ANSWER, LINKS) is missing or out of order"
        else:
            block.question = "\n".join(_trim_blank_lines(body[qi + 1:mi]))
            block.mode = " ".join(l.strip() for l in _trim_blank_lines(body[mi + 1:si]))
            block.searched = " ".join(l.strip() for l in _trim_blank_lines(body[si + 1:ai]))
            block.answer = "\n".join(_trim_blank_lines(body[ai + 1:li]))
            tail = _trim_blank_lines(body[li + 1:])
            if tail and tail[-1].strip() == RULE:       # only the closing rule of the block
                tail = tail[:-1]
            block.link_lines = [l.strip() for l in tail if l.strip()]
        blocks.append(block)
    return Sheet(keys=keys, boxes=boxes, blocks=blocks)


def _searched_value(raw: str) -> tuple[bool, bool | None]:
    """(is it a valid reply, value). 'unclear' is stored as NULL, as in terminal mode."""
    r = raw.strip().lower()
    if r in ("y", "yes"):
        return True, True
    if r in ("n", "no"):
        return True, False
    if r in ("u", "unclear"):
        return True, None
    return False, None


def _logged_value(raw: str) -> str | None:
    r = raw.strip().lower().replace("_", " ")
    if r in ("out", "logged out"):
        return "logged_out"
    if r in ("in", "logged in"):
        return "logged_in"
    return None


# ---- importing ----------------------------------------------------------------

class ImportRefused(AuditError):
    """The import found problems and saved nothing. `.problems` lists every one."""

    def __init__(self, problems: list[str]):
        super().__init__(f"{len(problems)} problem(s) found; nothing was saved")
        self.problems = problems


@dataclass
class ImportResult:
    probe_run_id: str
    saved: int                       # new answers saved by this import
    already_saved: int               # blocks skipped because an earlier import saved them
    unfinished: list[str]            # partial mode: problems with blocks that were not saved
    saved_total: int
    expected_total: int
    complete: bool
    counts: dict
    summary: dict
    derive: DeriveReport


def import_fill_in(path: str | Path, *, partial: bool = False, question_set_path: str | None = None,
                   client=None) -> ImportResult:
    path = Path(path)
    if not path.is_file():
        raise AuditError(f"fill-in file not found: {path}")
    text = path.read_text(encoding="utf-8")
    sheet = parse_sheet(text)
    if SHEET_MAGIC not in text:
        raise AuditError(f"{path.name} is not an audit fill-in sheet (the line '{SHEET_MAGIC}' is missing)")

    run_id = sheet.keys.get("PROBE RUN", "")
    try:
        UUID(run_id)
    except ValueError:
        raise AuditError("the PROBE RUN line in the file is missing or damaged") from None

    conn = get_connection()
    try:
        run = store.get_run(conn, run_id)
        if run is None:
            raise AuditError(f"this file belongs to probe run {run_id}, which is not in this database")
        started = store.started_details(conn, run_id)
        if started is None:
            raise AuditError(f"probe run {run_id} has no start record in the journal")
        if run["status"] == "failed":
            raise AuditError("this probe run was marked failed, so it cannot take more answers")
        business = store.load_business_and_profile(conn, run["business_id"])

        qs_path = question_set_path or sheet.keys.get("QUESTION SET FILE")
        if not qs_path:
            raise AuditError("the file does not say which question set it came from; pass --question-set")
        try:
            qs = load_question_set(qs_path)
        except QuestionSetError as exc:
            raise AuditError(f"{exc} (use --question-set <path> if the file moved)") from exc
        # The hash lock: the questions must still be exactly what this run started with.
        if qs.version != started["question_set_version"] or qs.fingerprint != started["question_set_sha256"]:
            raise AuditError(
                f"{qs.version} no longer matches the questions this run started with. A used question set "
                "is never edited: restore it, or start a new audit with a new version.")
        try:
            check_not_edited(conn, qs)
        except QuestionSetError as exc:
            raise AuditError(str(exc)) from exc

        repeats = int(started.get("repeats", 1))
        engines = list(started["engines"])
        expected = {block_id(n, e, r): (qs.questions[n - 1], e, r)
                    for n in range(1, len(qs.questions) + 1) for e in engines for r in range(1, repeats + 1)}

        fatal = _fatal_problems(sheet, expected, qs)
        if fatal:
            raise ImportRefused(fatal)

        header_problems = _header_problems(sheet)
        saved_before = store.saved_triples(conn, run_id)
        to_save, problems, already = [], [], 0
        for b in sheet.blocks:
            triple = expected[b.id]
            if triple in saved_before:
                already += 1
                continue                         # saved by an earlier import: never touched again
            found = _block_problems(b)
            if found:
                problems += [f"{b.id}: {p}" for p in found]
            else:
                to_save.append(b)
        for missing_id in expected:
            if expected[missing_id] not in saved_before and missing_id not in {b.id for b in sheet.blocks}:
                problems.append(f"{missing_id}: the whole block is missing from the file")
        problems = header_problems + problems

        if problems and not partial:
            raise ImportRefused(problems)
        if header_problems:                      # without the header no answer can be stored, even in --partial
            raise ImportRefused(header_problems)

        logged_in_state = _logged_value(sheet.boxes[LOGGED_LABEL])
        location = sheet.boxes.get(LOCATION_LABEL) or None
        operator = sheet.boxes[OPERATOR_LABEL]

        new_ids = []
        with conn.transaction():                 # all of this file's new answers, or none of them
            for b in to_save:
                _, retrieval = _searched_value(b.searched)
                sources = merge_sources([parse_link(l) for l in b.link_lines], b.answer)
                row_id, _ = store.add_result(
                    conn, probe_run_id=run_id, question=expected[b.id][0], engine=expected[b.id][1],
                    raw_answer=b.answer, retrieval_activated=retrieval, engine_version=b.mode or None,
                    logged_in_state=logged_in_state, location_context=location, question_set_version=qs.version,
                    sources_cited=sources, repeat_number=expected[b.id][2])
                new_ids.append(row_id)

        saved_now = store.saved_triples(conn, run_id)
        total_saved = len(saved_now)
        complete = set(expected.values()) <= saved_now      # every question x engine x repeat has its answer
        if new_ids:
            write_journal(business_id=business["id"], entry_type="note", actor=ACTOR,
                          description=f"Imported {len(new_ids)} answer(s) from a fill-in file ({operator})",
                          related_table="probe_runs", related_id=run_id, conn=conn,
                          details={"probe_run_id": run_id, "operator": operator, "file": path.name,
                                   "partial": partial, "blocks_saved": [b.id for b in to_save]})
        if complete and run["status"] == "in_progress":
            store.complete_run(conn, business_id=business["id"], run_id=run_id, answers=total_saved)

        derived = derive_run(conn, business, run_id, client=client, only_ids=new_ids) if new_ids else DeriveReport()
        counts = store.summary_counts(conn, run_id)
        summary = build_summary(store.named_businesses_by_answer(conn, run_id))
    finally:
        conn.close()

    return ImportResult(probe_run_id=run_id, saved=len(new_ids), already_saved=already, unfinished=problems,
                        saved_total=total_saved, expected_total=len(expected), complete=complete,
                        counts=counts, summary=summary, derive=derived)


def _fatal_problems(sheet: Sheet, expected: dict, qs: QuestionSet) -> list[str]:
    """Problems that mean the file cannot be trusted at all, in either mode."""
    out, seen = [], set()
    for b in sheet.blocks:
        if b.id in seen:
            out.append(f"{b.id}: this block appears more than once in the file")
        seen.add(b.id)
        if b.id not in expected or b.id != block_id(b.number, b.engine_label.lower(), b.repeat):
            out.append(f"{b.id}: this block is not part of this audit, or its heading and id disagree "
                       "(wrong question number, engine or run number)")
            continue
        if b.damaged:
            continue                              # reported as an ordinary problem below
        want = expected[b.id][0]
        if b.question != want:
            out.append(f"{b.id}: the question text was changed. The frozen question is: \"{want}\". "
                       f"The file says: \"{b.question}\". Answers to an edited question would not be comparable.")
    return out


def _header_problems(sheet: Sheet) -> list[str]:
    out = []
    if not sheet.boxes.get(OPERATOR_LABEL):
        out.append(f"header: {OPERATOR_LABEL} is empty")
    logged = sheet.boxes.get(LOGGED_LABEL, "")
    if not logged:
        out.append(f"header: {LOGGED_LABEL} is empty")
    elif _logged_value(logged) is None:
        out.append(f"header: {LOGGED_LABEL} must be 'out' or 'in' (got '{logged}')")
    return out


def _block_problems(b: Block) -> list[str]:
    """What stops a block being saved. An empty MODE box is allowed (the engine does not always say),
    and an empty LINKS box means the answer showed no links."""
    if b.damaged:
        return [b.damaged]
    out = []
    if not b.searched:
        out.append("SEARCHED THE WEB is empty")
    elif not _searched_value(b.searched)[0]:
        out.append(f"SEARCHED THE WEB must be y, n or unclear (got '{b.searched}')")
    if not b.answer.strip():
        out.append("ANSWER is empty")
    for line in b.link_lines:
        try:
            parse_link(line)
        except ValueError as exc:
            out.append(f"LINKS: {exc}")
    return out
