"""`aeo audit --mode manual`: a person asks each engine by hand and pastes the answer back.

Order: collect and save every raw answer first (each one is written the moment
it is entered), then run the two AI jobs on what was collected. If the person
stops early, what they entered is kept and the run is marked 'failed' with the
missing questions written to the journal. It is never reported as complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from audit import store
from audit.sources import merge_sources, parse_link
from audit.summary import build_summary
from audit.derive import DeriveReport, derive_run
from audit.question_set import QuestionSetError, check_not_edited, load_question_set
from core.db import get_connection
from core.journal import write_journal

# Hardcoded for now (PLAN.md section 8: ChatGPT for reach, Perplexity because it always retrieves).
ENGINES = ["chatgpt", "perplexity"]
END_MARKER = "END"      # a line with only this ends a pasted answer
QUIT_MARKER = "/quit"   # as the first line of an answer: stop the whole run
ACTOR = store.ACTOR


class AuditError(Exception):
    """A problem the user can fix (unknown business, bad question set)."""


@dataclass
class AuditOutcome:
    probe_run_id: str
    complete: bool
    missing: list[dict]
    counts: dict
    summary: dict
    derive: DeriveReport
    version: str
    warnings: list[str] = field(default_factory=list)


class _Stop(Exception):
    """The operator quit (typed /quit, pressed Ctrl-D with nothing entered, or Ctrl-C)."""


def run_manual_audit(business_id: str, question_set_path: str, *, checkpoint: str | None,
                     ask: Callable[[str], str], say: Callable[[str], None], client=None) -> AuditOutcome:
    """`ask(prompt)` returns one typed line (raises EOFError at end of input); `say(text)` prints."""
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

        pairs = [(q, e) for q in qs.questions for e in ENGINES]
        say(f"\nManual audit for {business['name'] or business['domain']}")
        say(f"Question set {qs.version}: {len(qs.questions)} questions x {len(ENGINES)} engines = {len(pairs)} answers.")
        warnings = qs.target_warnings()
        for w in warnings:
            say(f"WARNING: {w}")
        if warnings:
            say("")
        say("For every answer: use a logged-out or incognito window, start a fresh chat for each question,\n"
            "and copy the full answer exactly as shown.\n")

        try:
            logged_in_state = _ask_logged_out(ask, say)
            location = ask("Where are you asking from, e.g. a city (Enter to skip): ").strip() or None
        except EOFError:
            raise AuditError("input ended before the run started; nothing was saved") from None

        run_id = store.start_run(conn, business=business, qs=qs, checkpoint=checkpoint, engines=ENGINES,
                                 answers_expected=len(pairs), how="terminal")

        done: set[tuple[str, str]] = set()
        try:
            for number, (question, engine) in enumerate(pairs, start=1):
                _collect_one(conn, ask, say, run_id=run_id, question=question, engine=engine, number=number,
                             total=len(pairs), logged_in_state=logged_in_state, location=location,
                             version=qs.version)
                done.add((question, engine))
        except (_Stop, KeyboardInterrupt, EOFError):
            say("\nStopping early. Answers already entered are kept.")

        missing = [{"question": q, "engine": e} for q, e in pairs if (q, e) not in done]
        complete = not missing
        if complete:
            store.complete_run(conn, business_id=business["id"], run_id=run_id, answers=len(done))
        else:
            store.finish_run(conn, run_id, complete=False)
            write_journal(business_id=business["id"], entry_type="note", actor=ACTOR,
                          description=f"Manual audit ended early: {len(done)} of {len(pairs)} answers collected",
                          related_table="probe_runs", related_id=run_id, conn=conn,
                          details={"probe_run_id": str(run_id), "answers": len(done),
                                   "answers_missing": len(missing), "missing": missing})

        if done:
            say(f"\nReading {len(done)} answer(s) with the model to find which businesses they name...")
        derived = derive_run(conn, business, run_id, client=client)
        counts = store.summary_counts(conn, run_id)
        summary = build_summary(store.named_businesses_by_answer(conn, run_id))
    finally:
        conn.close()

    return AuditOutcome(probe_run_id=str(run_id), complete=complete, missing=missing, counts=counts, summary=summary,
                        derive=derived, version=qs.version, warnings=warnings)


def _ask_logged_out(ask, say) -> str:
    while True:
        reply = ask("Are you logged OUT of both engines (incognito)? [y/n]: ").strip().lower()
        if reply in ("y", "yes"):
            return "logged_out"
        if reply in ("n", "no"):
            say("Noted as logged in. The plan expects logged-out answers, so these will not be comparable "
                "with logged-out runs.")
            return "logged_in"
        say("Please type y or n.")


def _collect_one(conn, ask, say, *, run_id, question, engine, number, total, logged_in_state, location, version):
    say("-" * 70)
    say(f"Answer {number} of {total}  |  ask on: {engine.upper()}")
    say(f"Question (type it exactly as written):\n\n  {question}\n")
    answer = _read_answer(ask, say, engine)
    pasted = _read_sources(ask, say, engine)

    while True:
        reply = ask(f"Did {engine} visibly search the web for this? [y/n/unclear]: ").strip().lower()
        if reply in ("y", "yes"):
            retrieval = True
        elif reply in ("n", "no"):
            retrieval = False
        elif reply in ("u", "unclear"):
            retrieval = None            # stored as NULL: we do not know
        else:
            say("Please type y, n or unclear.")
            continue
        break
    engine_version = ask("Model and reasoning mode shown, e.g. gpt-5-thinking (Enter to skip): ").strip() or None

    _, repeat = store.add_result(
        conn, probe_run_id=run_id, question=question, engine=engine, raw_answer=answer,
        retrieval_activated=retrieval, engine_version=engine_version, logged_in_state=logged_in_state,
        location_context=location, question_set_version=version,
        sources_cited=merge_sources(pasted, answer))
    say(f"Saved (repeat {repeat}).")


def _read_answer(ask, say, engine) -> str:
    say(f"Paste the full {engine} answer, then type {END_MARKER} on a line by itself.  "
        f"(Type {QUIT_MARKER} instead to stop the run.)")
    while True:
        lines: list[str] = []
        while True:
            try:
                line = ask("")
            except EOFError:
                if not lines:
                    raise _Stop() from None
                break                   # Ctrl-D after pasting also ends the answer
            if not lines and line.strip() == QUIT_MARKER:
                raise _Stop()
            if line.strip() == END_MARKER:
                break
            lines.append(line)
        answer = "\n".join(lines).strip("\n")
        if answer.strip():
            return answer
        say(f"That answer was empty. Paste it again, then {END_MARKER} (or {QUIT_MARKER} to stop).")


def _read_sources(ask, say, engine) -> list[dict]:
    """The links the engine showed, one per line. Copying the answer usually drops them, so they
    get their own box. Type END straight away if the answer cited nothing."""
    say(f"Now paste the links {engine} showed (its sources), one per line, then {END_MARKER}. "
        f"If it showed none, just type {END_MARKER}.")
    links: list[dict] = []
    while True:
        line = ask("").strip()
        if line == END_MARKER:
            return links
        if not line:
            continue
        try:
            links.append(parse_link(line))
        except ValueError as exc:
            say(f"Not saved: {exc}. Type it again as a full link, or skip it.")
