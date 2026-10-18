"""The `aeo` command: `diagnose`, `add`, `audit`, and the client-file commands (engagement, evidence, log, event, timeline, files)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Optional

import typer

from audit.cost_gate import CostGateAborted
from audit.fill_in import ImportRefused, generate_fill_in, import_fill_in
from audit.manual import AuditError, run_manual_audit
from audit.report import ReportError, make_report
from dossier.run import DossierError, make_dossier
from client_report.run import ClientReportError, make_client_report, rebuild_from_json
from compare.run import CompareError, make_comparison
from recommend.run import RecommendError, make_recommendations
from recommend.robots_file import TRAINING_MODES
from audit.summary import summary_lines
from core import client_files, evidence as ev, files_migrate, timeline as tl
from core.businesses import (ACCESS_LEVELS, ENGAGEMENT_TYPES, BusinessError, add_with_checks, get_engagement,
                             resolve_business, set_engagement)
from core.checkpoints import CHECKPOINTS, CheckpointError, check_checkpoint
from core.db import get_connection
from core.operator import operator_name
from truth import draft as truth_draft
from truth.importer import TruthImportAborted, TruthImportError, TruthImportRefused, import_truth
from diagnose import platform_data as pd
from diagnose.report import decline_pitch_outline
from diagnose.run import DiagnoseError, run_diagnosis

TERMINAL_WIDTH = 100   # findings are wrapped to fit a normal terminal

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="AEO Studio tools. Git, sending and deploying are always done by a person.")


def _fail(message: str) -> None:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _find_business(ref: str) -> dict:
    """--business takes an id, a domain or a name. Stops with a plain message if it is none or several."""
    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001 - say plainly that the database is the problem
        _fail(f"could not reach the database: {exc}")
    try:
        return resolve_business(conn, ref)
    except BusinessError as exc:
        _fail(str(exc))
    finally:
        conn.close()


CHECKPOINT_HELP = "One of: " + ", ".join(CHECKPOINTS) + "."


@app.callback()
def main() -> None:
    """AEO Studio tools."""
    # An empty callback makes Typer keep `diagnose` as a named command, so more
    # commands (recommend...) can be added later without changing how the others are called.


@app.command()
def diagnose(
    url: str = typer.Argument(..., help="The site to check, e.g. https://example.com"),
    business: Optional[str] = typer.Option(None, "--business", help="Business id, domain or name. Links the result to it and adds a model-written summary."),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help=CHECKPOINT_HELP),
) -> None:
    """Free, code-only check of whether a site can be helped: serve, conditional or decline."""
    business_id = _find_business(business)["id"] if business else None
    typer.echo(f"Checking {url} (about 8 requests, one per second)...")
    try:
        out = run_diagnosis(url, business_id=business_id, checkpoint=checkpoint)
    except DiagnoseError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    v = out.verdict
    colour = {"serve": typer.colors.GREEN, "conditional": typer.colors.YELLOW, "decline": typer.colors.RED}[v.verdict]
    det = out.facts.detection
    typer.echo("")
    typer.secho(f"Verdict: {v.verdict.upper()}", fg=colour, bold=True)
    typer.echo(v.reason)
    if det.platform:
        typer.echo(f"Platform: {pd.PLATFORM_LABELS[det.platform]}" + (f" {det.version}" if det.version else ""))
    else:
        typer.echo("Platform: not recognised")
    if out.ai_explanation:
        typer.echo("")
        typer.echo(out.ai_explanation)
    typer.echo(f"\n{len(out.findings)} finding(s):")
    for f in out.findings:
        # Plain sentence first, code in brackets afterwards for looking it up later.
        # Continuation lines line up under the first word, not under the severity.
        prefix = f"  [{f['severity']:<8}] "
        typer.echo(textwrap.fill(
            f"{f['what']}. ({f['code']})" if not f["what"].endswith((".", "!", "?")) else f"{f['what']} ({f['code']})",
            width=TERMINAL_WIDTH, initial_indent=prefix, subsequent_indent=" " * len(prefix),
            break_long_words=False, break_on_hyphens=False))
    for w in out.warnings:
        typer.secho(f"Warning: {w}", fg=typer.colors.YELLOW, err=True)

    if v.verdict == "decline":
        typer.echo("")
        typer.secho("This site cannot carry the work. A migration pitch would cover:", bold=True)
        for line in decline_pitch_outline(pd.PLATFORM_LABELS[det.platform], v.reason):
            typer.echo(f"  - {line}")
        typer.echo("No pitch has been written. A person must type `yes` first.")
        if out.decline_result == "declined":
            typer.echo("Business status set to 'declined', with the reason recorded in the journal.")
        elif out.decline_result == "already_declined":
            typer.echo("Business was already 'declined'; status left as it is.")
        elif out.decline_result == "not_a_prospect":
            typer.secho("Status NOT changed: this business is not a prospect, so declining it is your decision. "
                        "Use: aeo status --business <id> declined --reason \"...\"", fg=typer.colors.YELLOW)
        else:
            typer.echo("No business was given, so no status was changed.")

    typer.echo(f"\nReport (run {out.run_number}): {out.report_path}")


def _is_interactive() -> bool:
    return sys.stdin.isatty()


@app.command()
def add(
    url: str = typer.Argument(..., help="The business's website, e.g. https://example.com"),
    name: Optional[str] = typer.Option(None, "--name", help="The business's name."),
    same_business: Optional[str] = typer.Option(None, "--same-business", help="Id of an existing business with this name: confirms this is it, adds nothing."),
    force_new: bool = typer.Option(False, "--force-new", help="Confirms this is a different business from one with the same name."),
) -> None:
    """Add a business (status: prospect) with an empty profile, so it can be diagnosed and audited."""
    try:
        result = add_with_checks(url, name, same_business=same_business, force_new=force_new,
                                 interactive=_is_interactive(),
                                 confirm=lambda question: typer.confirm(question, default=False))
    except BusinessError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    b = result.business
    if not result.created:
        typer.echo(f"Nothing was added: {b['name']} is already here as business id {b['id']} ({b['domain']}).")
        typer.echo("Use that id from now on. Its web address was not changed; if the address has moved, "
                   "that is a decision for a person to make on purpose.")
        return
    typer.echo(f"Added {b['name'] or b['domain']} ({b['domain']}), status: prospect")
    typer.echo(f"Business id: {b['id']}")
    typer.echo("The profile is empty: no facts have been confirmed yet.")


audit_app = typer.Typer(invoke_without_command=True, add_completion=False,
                        help="Ask the engines, store every raw answer, work out who each answer names.")
app.add_typer(audit_app, name="audit")


def _print_run_numbers(counts: dict, summary: dict, derive, run_id: str) -> None:
    """The short plain-English numbers shared by the terminal and fill-in routes."""
    typer.echo(f"Answers collected: {counts['answers']}")
    typer.echo(f"Answers that named the business: {counts['named_business']}")
    typer.echo(f"Answers that recommended the business: {counts['recommended_business']}")
    for line in summary_lines(summary):
        typer.echo(line)
    if counts["not_analysed"]:
        typer.secho(f"{counts['not_analysed']} answer(s) could not be read by the model and were sent to the "
                    "human queue. The raw answers are saved.", fg=typer.colors.YELLOW)
    if derive.claim_check_failed:
        typer.secho(f"{derive.claim_check_failed} claim check(s) failed and were sent to the human queue.",
                    fg=typer.colors.YELLOW)
    typer.echo(f"Run id: {run_id}")


@audit_app.callback()
def audit(
    ctx: typer.Context,
    business: Optional[str] = typer.Option(None, "--business", help="Business id, domain or name."),
    question_set: Optional[str] = typer.Option(None, "--question-set", help="Path to the frozen question set, e.g. question_sets/oland-stations_v1.yaml"),
    mode: str = typer.Option("manual", "--mode", help="manual (default). batch is not built yet."),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help=CHECKPOINT_HELP),
    fill_in: bool = typer.Option(False, "--fill-in", help="Write a fill-in file to complete in an editor, then load it with `aeo audit import <file>`."),
    repeats: Optional[int] = typer.Option(None, "--repeats", help="With --fill-in: how many times each question is asked on each engine (default 1)."),
) -> None:
    """Ask the engines (by hand), store every raw answer, then work out who each answer names."""
    if ctx.invoked_subcommand is not None:
        return                                     # `aeo audit import ...`
    if mode == "batch":
        _fail("batch mode is not built yet. It is only built after manual mode has run cleanly ten times "
              "(PLAN.md section 3.2).")
    if mode != "manual":
        _fail(f"unknown mode '{mode}'. Use manual.")
    if not business or not question_set:
        _fail("--business and --question-set are both required.")
    if repeats is not None and not fill_in:
        _fail("--repeats only works together with --fill-in.")
    business = _find_business(business)["id"]

    if fill_in:
        try:
            made = generate_fill_in(business, question_set, checkpoint=checkpoint, repeats=repeats or 1)
        except AuditError as exc:
            _fail(str(exc))
        for w in made.warnings:
            typer.secho(f"WARNING: {w}", fg=typer.colors.YELLOW)
        typer.echo(f"Fill-in file written ({made.blocks} blocks): {made.path}")
        typer.echo(f"Fill it in with any editor, then load it with:  aeo audit import {made.path}")
        typer.echo(f"Run id: {made.probe_run_id}")
        return

    try:
        out = run_manual_audit(business, question_set, checkpoint=checkpoint, ask=input, say=typer.echo)
    except CostGateAborted:
        raise typer.Exit(1)                        # "Aborted." was already printed; nothing was saved
    except AuditError as exc:
        _fail(str(exc))

    typer.echo("")
    if out.complete:
        typer.secho("Audit complete.", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho(f"Audit NOT complete: {len(out.missing)} answer(s) missing. The run is marked failed.",
                    fg=typer.colors.YELLOW, bold=True)
    _print_run_numbers(out.counts, out.summary, out.derive, out.probe_run_id)


@audit_app.command("import")
def import_answers(
    file: str = typer.Argument(..., help="The filled-in sheet written by `aeo audit --fill-in`."),
    partial: bool = typer.Option(False, "--partial", help="Save the finished blocks and leave the rest for a later import."),
    question_set: Optional[str] = typer.Option(None, "--question-set", help="Only if the question-set file has moved."),
) -> None:
    """Load a filled-in sheet. By default nothing is saved unless every box is filled."""
    try:
        res = import_fill_in(file, partial=partial, question_set_path=question_set, ask=input, say=typer.echo)
    except CostGateAborted:
        raise typer.Exit(1)                        # "Aborted." was already printed; nothing was saved
    except ImportRefused as exc:
        typer.secho("Nothing was saved. Fix these and import again:", fg=typer.colors.RED, err=True)
        for problem in exc.problems:
            typer.echo(f"  - {problem}", err=True)
        raise typer.Exit(1)
    except AuditError as exc:
        _fail(str(exc))

    if res.evidence_copy:
        typer.echo(f"A copy of the filled sheet is kept in: {res.evidence_copy}")
    if res.evidence_copy_error:
        typer.secho(f"Warning: the answers are saved, but the copy into evidence/ failed: {res.evidence_copy_error}",
                    fg=typer.colors.YELLOW, err=True)
    typer.echo(f"Saved {res.saved} new answer(s). {res.already_saved} block(s) were already saved earlier and were skipped.")
    if res.unfinished:
        typer.secho(f"{len(res.unfinished)} thing(s) still to fill in (those blocks were not saved):", fg=typer.colors.YELLOW)
        for problem in res.unfinished:
            typer.echo(f"  - {problem}")
    typer.echo("")
    if res.complete:
        typer.secho("Audit complete: every answer is saved.", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho(f"Audit not complete yet: {res.saved_total} of {res.expected_total} answers saved. "
                    "Fill in the rest and import the same file again.", fg=typer.colors.YELLOW, bold=True)
    _print_run_numbers(res.counts, res.summary, res.derive, res.probe_run_id)


@audit_app.command("report")
def audit_report(
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
    run: Optional[str] = typer.Option(None, "--run", help="The probe run id (printed by `aeo audit`)."),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help="The latest finished run at this checkpoint. " + CHECKPOINT_HELP),
    partial: bool = typer.Option(False, "--partial", help="Allow an unfinished run. The report carries an INCOMPLETE banner and its scores are not saved."),
) -> None:
    """Write the audit report for one run: scores with ranges, share of voice, wrong facts, every answer. No AI, no fix plan."""
    b = _find_business(business)
    try:
        if checkpoint:
            check_checkpoint(checkpoint)
        made = make_report(b, run=run, checkpoint=checkpoint, partial=partial)
    except (CheckpointError, ReportError) as exc:
        _fail(str(exc))
    data = made.data
    hl = data["headline"]["measures"]["visibility"]
    typer.echo(f"{data['design']['label']} report for run {made.run_id}.")
    if made.other_runs:
        typer.echo(f"({made.other_runs} other run(s) match; pass --run <id> to choose a different one.)")
    if data["incomplete"]:
        typer.secho(f"INCOMPLETE: {data['incomplete']['answers_saved']} of {data['incomplete']['answers_expected']} answers. "
                    "The report says so at the top. Its scores were not saved.", fg=typer.colors.YELLOW, bold=True)
    if data["extraction"]["failed"]:
        typer.secho(f"{len(data['extraction']['failed'])} answer(s) could not be read by the model and were left out of the scores.",
                    fg=typer.colors.YELLOW)
    rng = f" (95% range {hl['low']:.1f}%{chr(8211)}{hl['high']:.1f}%)" if hl["low"] is not None else " (no range: too few questions)"
    typer.echo(f"Visibility score: {hl['value']:.1f}%{rng}, tier {data['headline']['tier']}.")
    typer.echo("Scores saved to visibility_scores." if made.scores_saved else "Scores were not saved (unfinished run).")
    typer.echo(f"Report: {made.md_path}")
    typer.echo(f"Data:   {made.json_path}")



# ---- recommend (PLAN.md section 3.3) -------------------------------------------------------------------------

@app.command("recommend")
def recommend(
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
    run: Optional[str] = typer.Option(None, "--run", help="The finished audit run to prioritise by (default: the latest finished one)."),
    diagnosis: Optional[str] = typer.Option(None, "--diagnosis", help="The diagnosis id to use (default: the latest)."),
    no_audit: bool = typer.Option(False, "--no-audit", help="Go ahead without a finished audit. The fix list says it was not prioritised by one."),
    training_bots: str = typer.Option("keep", "--training-bots", help="What robots.txt does about AI training bots (GPTBot, ClaudeBot): "
                                      + ", ".join(TRAINING_MODES) + ". 'keep' changes nothing about them."),
) -> None:
    """Write the on-page fixes for a client: robots.txt, llms.txt, schema.json and a prioritised fix list. No AI. Never touches the live site."""
    b = _find_business(business)
    try:
        made = make_recommendations(b, run=run, diagnosis=diagnosis, no_audit=no_audit, training_bots=training_bots)
    except RecommendError as exc:
        _fail(str(exc))
    d = made.data
    typer.echo(f"Recommendations for {b['name'] or b['domain']}: {len(d['fixes'])} fix(es).")
    if not d["prioritised_by_audit"]:
        typer.secho("NOT PRIORITISED BY AN AUDIT: the list says so at the top.", fg=typer.colors.YELLOW, bold=True)
    for w in made.warnings:
        typer.secho(w, fg=typer.colors.YELLOW)
    cannot = [f for f in d["fixes"] if f["cannot_do"]]
    if cannot:
        typer.secho(f"{len(cannot)} fix(es) cannot be done on this platform; the document says what to do instead.", fg=typer.colors.YELLOW)
    if d["jsonld"]["status"] != "valid":
        typer.secho("schema.json was NOT written: " + " ".join(d["jsonld"]["errors"]), fg=typer.colors.YELLOW)
    typer.echo("")
    typer.echo("robots.txt compared with the live file:")
    typer.echo(made.robots_diff.rstrip("\n") if made.robots_diff else "(no difference)")
    typer.echo("")
    for key in ("recommendations", "robots", "llms", "schema"):
        if key in made.paths:
            typer.echo(f"{key:16s}{made.paths[key]}")
    typer.echo("Nothing was sent or deployed. A person puts these files on the site.")



# ---- compare (PLAN.md step 20) ------------------------------------------------------------------------------------

@app.command("compare")
def compare(
    label_a: str = typer.Argument(..., help="The earlier checkpoint. " + CHECKPOINT_HELP),
    label_b: str = typer.Argument(..., help="The later checkpoint."),
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
    a_run: Optional[str] = typer.Option(None, "--a-run", help="The audit run to use for A (default: the latest finished one at that checkpoint)."),
    b_run: Optional[str] = typer.Option(None, "--b-run", help="The audit run to use for B."),
    a_diagnosis: Optional[str] = typer.Option(None, "--a-diagnosis", help="The diagnosis to use for A (default: the latest at that checkpoint)."),
    b_diagnosis: Optional[str] = typer.Option(None, "--b-diagnosis", help="The diagnosis to use for B."),
) -> None:
    """Compare two checkpoints: findings closed / still open / new, and audit scores with ranges. No AI."""
    b = _find_business(business)
    try:
        made = make_comparison(b, label_a, label_b, a_run=a_run, b_run=b_run, a_diagnosis=a_diagnosis, b_diagnosis=b_diagnosis)
    except (CompareError, CheckpointError) as exc:
        _fail(str(exc))
    d, a = made.data["diagnosis"], made.data["audit"]
    typer.echo(f"{label_a} vs {label_b} for {b['name'] or b['domain']}.")
    typer.echo(("Diagnosis: " + d["headline"] + ".") if d["available"] else f"Diagnosis: not compared ({d['reason']}).")
    if a["available"]:
        v = a["headline"]["measures"]["visibility"]["change"]
        typer.echo(f"Audit ({a['design']['label']}): visibility {v['value']:+.1f} points, {v['label']}. "
                   + made.data["detection_floor"]["text"])
    else:
        typer.echo(f"Audit: not compared ({a['reason']}).")
    events = made.data["between"]["events"]
    if events:
        typer.secho(f"{events} event(s) outside our control happened in between; the document flags them.", fg=typer.colors.YELLOW)
    typer.echo(f"Comparison: {made.md_path}")
    typer.echo(f"Data:       {made.json_path}")



# ---- client report (PLAN.md step 22) -------------------------------------------------------------------------------

@app.command("client-report")
def client_report(
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
    stage: Optional[str] = typer.Option(None, "--stage", help="starting_point, work_done, results or check."),
    a: Optional[str] = typer.Option(None, "--a", help="Earlier checkpoint to compare (results; or the health comparison in starting_point). " + CHECKPOINT_HELP),
    b: Optional[str] = typer.Option(None, "--b", help="Later checkpoint to compare."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Skip the AI summaries. Nothing is spent."),
    from_json: Optional[Path] = typer.Option(None, "--from-json", help="Rebuild the HTML from a saved .json. No data is gathered and no AI is used."),
) -> None:
    """Write the client report for one stage as a single HTML file. Never sends anything: a person reviews and sends it."""
    bz = _find_business(business)
    try:
        if from_json is not None:
            if stage or a or b:
                _fail("--from-json rebuilds a saved report as it was; do not combine it with --stage, --a or --b")
            made = rebuild_from_json(bz, from_json)
            note = {True: "identical to the original", False: "DIFFERENT from the original (the original was left alone)",
                    None: "the original HTML was missing, so it was recreated"}[made.identical_to_original]
            typer.echo(f"Rebuilt from {from_json.name}: {note}.")
            typer.echo(f"Report: {made.html_path}")
            return
        if stage is None:
            _fail("--stage is required: starting_point, work_done, results or check")
        made = make_client_report(bz, stage, a=a, b=b, no_ai=no_ai, ask=input, say=typer.echo)
    except CostGateAborted:
        typer.echo("Nothing was written. Run again and type yes, or add --no-ai.")
        raise typer.Exit(1)
    except ClientReportError as exc:
        _fail(str(exc))
    ai = made.data["ai"]
    typer.echo(f"Client report ({stage}) for {bz['name'] or bz['domain']}, version {made.data['version']}.")
    typer.echo("AI summaries: " + (f"{ai['calls'] - len(ai['rejected'])} of {ai['calls']} kept" if ai["used"] else "none (no AI used)") + ".")
    typer.echo(f"Report: {made.html_path}")
    typer.echo(f"Data:   {made.json_path}")
    typer.echo("Nothing was sent. A person reads it first, then sends it.")


# ---- baseline dossier (PLAN.md step 22e) ----------------------------------------------------------------------------

@app.command()
def dossier(
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
    questions: Optional[Path] = typer.Option(None, "--questions", help="A proposed question set (YAML). Default: the latest one an audit used."),
) -> None:
    """Write the internal baseline dossier (.md, .html, .json). No AI. Never shown to the client."""
    bz = _find_business(business)
    try:
        made = make_dossier(bz, questions=questions)
    except DossierError as exc:
        _fail(str(exc))
    d = made.data
    typer.echo(f"Dossier version {d['version']} for {bz['name'] or bz['domain']}. INTERNAL: do not send it to the client.")
    if made.notes_created:
        typer.echo(f"Created the notes file: {made.notes_path}  (write your notes under its headings; it is never overwritten)")
    for w in made.warnings:
        typer.echo(f"WARNING: {w}")
    for s in d["sections"]:
        if s["key"] in ("next", "problems"):
            typer.echo(f"{s['title']}: {s['answer']}")
    typer.echo(f"Document: {made.md_path}")
    typer.echo(f"Web page: {made.html_path}")
    typer.echo(f"Data:     {made.json_path}")


# ---- engagement, evidence, work log, events, timeline (PLAN.md sections 2 and 10) -------------------------

@app.command()
def engagement(
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
    engagement_type: Optional[str] = typer.Option(None, "--type", help="One of: " + ", ".join(ENGAGEMENT_TYPES) + "."),
    access: Optional[str] = typer.Option(None, "--access", help="One of: " + ", ".join(ACCESS_LEVELS) + "."),
) -> None:
    """Record what we were hired for and how much access we have. With neither option, shows the current values."""
    b = _find_business(business)
    conn = get_connection()
    try:
        if engagement_type is None and access is None:
            current = get_engagement(conn, b["id"])
            typer.echo(f"{b['name'] or b['domain']}: type {current['engagement_type'] or 'not set'}, "
                       f"access {current['access_level'] or 'not set'}")
            return
        try:
            result = set_engagement(conn, b["id"], engagement_type=engagement_type, access_level=access,
                                    actor=operator_name())
        except BusinessError as exc:
            _fail(str(exc))
    finally:
        conn.close()
    typer.echo(f"{b['name'] or b['domain']}: type {result['engagement_type'] or 'not set'}, "
               f"access {result['access_level'] or 'not set'}"
               + ("" if result["changed"] else "  (already like this; nothing changed)"))


evidence_app = typer.Typer(add_completion=False, no_args_is_help=True,
                           help="Files that came in from outside: reports, screenshots, emails.")
app.add_typer(evidence_app, name="evidence")


@evidence_app.command("add")
def evidence_add(
    file: Path = typer.Argument(..., help="The file to keep. It is copied; the original is left where it is."),
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help=CHECKPOINT_HELP + " Required."),
    note: str = typer.Option(..., "--note", help="What we understood from it."),
    evidence_type: Optional[str] = typer.Option(None, "--type", help="e.g. " + ", ".join(ev.EVIDENCE_TYPES) + ". Other names are allowed."),
    source: Optional[str] = typer.Option(None, "--from", help="us or client."),
    captured: Optional[str] = typer.Option(None, "--captured", help="Date it was captured, YYYY-MM-DD."),
    period_start: Optional[str] = typer.Option(None, "--period-start", help="First day the evidence covers, YYYY-MM-DD."),
    period_end: Optional[str] = typer.Option(None, "--period-end", help="Last day the evidence covers, YYYY-MM-DD."),
    finding: Optional[str] = typer.Option(None, "--finding", help="A finding code it relates to, e.g. RENDER_H1_MISSING."),
    internal: bool = typer.Option(False, "--internal", help="Never shown to the client."),
) -> None:
    """Copy a file into the client's evidence/ folder and record it."""
    b = _find_business(business)
    conn = get_connection()
    try:
        added = ev.add_evidence(conn, business=b, checkpoint=checkpoint, file=file, note=note,
                                evidence_type=evidence_type, source=source, captured=captured,
                                period_start=period_start, period_end=period_end, finding=finding,
                                internal=internal, operator=operator_name())
    except (CheckpointError, ev.EvidenceError) as exc:
        _fail(str(exc))
    finally:
        conn.close()
    typer.echo(f"Kept: {added['stored_at']}")
    typer.echo(f"Evidence id: {added['id']}" + ("  (internal: not shown to the client)" if internal else ""))


@evidence_app.command("list")
def evidence_list(
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help="Only this checkpoint. " + CHECKPOINT_HELP),
) -> None:
    """Show the evidence records for a business."""
    b = _find_business(business)
    conn = get_connection()
    try:
        rows = ev.list_evidence(conn, b["id"], checkpoint)
    except CheckpointError as exc:
        _fail(str(exc))
    finally:
        conn.close()
    if not rows:
        typer.echo("No evidence recorded.")
        return
    for r in rows:
        bits = [r["checkpoint"], r["evidence_type"] or "no type", f"from {r['source']}" if r["source"] else "source not stated",
                f"captured {r['captured_on']}" if r["captured_on"] else "capture date not stated",
                f"covers {r['period_start']} to {r['period_end']}" if r["period_start"] else None,
                f"finding {r['finding_code']}" if r["finding_code"] else None,
                "client can see" if r["client_visible"] else "INTERNAL",
                f"added by {r['operator']} on {r['created_at']:%Y-%m-%d}"]
        typer.echo(f"{r['file_path']}")
        typer.echo("  " + "; ".join(x for x in bits if x))
        if r["note"]:
            typer.echo(f"  {r['note']}")


@app.command("log")
def log_work(
    text: str = typer.Argument(..., help="What was done, in plain words."),
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
    finding: Optional[str] = typer.Option(None, "--finding", help="The finding code this work closes."),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help=CHECKPOINT_HELP),
    file: Optional[Path] = typer.Option(None, "--file", help="A file to keep as evidence (needs --checkpoint)."),
) -> None:
    """Record a piece of work done for the client."""
    b = _find_business(business)
    conn = get_connection()
    try:
        added = ev.add_work(conn, business=b, text=text, operator=operator_name(), finding=finding,
                            checkpoint=checkpoint, file=file)
    except (CheckpointError, ev.EvidenceError) as exc:
        _fail(str(exc))
    finally:
        conn.close()
    typer.echo(f"Work recorded for {b['name'] or b['domain']}.")
    if added["evidence"]:
        typer.echo(f"File kept as evidence: {added['evidence']['stored_at']}")


@app.command("event")
def log_event(
    text: str = typer.Argument(..., help="What happened outside our control, in plain words."),
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
) -> None:
    """Record something that can move the numbers but that we did not do (the client edited their site, an engine changed)."""
    b = _find_business(business)
    conn = get_connection()
    try:
        ev.add_event(conn, business=b, text=text, operator=operator_name())
    except ev.EvidenceError as exc:
        _fail(str(exc))
    finally:
        conn.close()
    typer.echo(f"Event recorded for {b['name'] or b['domain']}.")


@app.command("timeline")
def show_timeline(
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
) -> None:
    """Everything that happened for this client, oldest first. Also rebuilds timeline.md and work_log.md."""
    b = _find_business(business)
    conn = get_connection()
    try:
        items, timeline_path, work_path = tl.regenerate(conn, b)
    finally:
        conn.close()
    typer.echo(tl.render_timeline(b, items).rstrip())
    typer.echo(f"\nWritten: {timeline_path}\n         {work_path}")


files_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Move files between layouts.")
app.add_typer(files_app, name="files")


@files_app.command("migrate")
def files_migrate_cmd(
    apply: bool = typer.Option(False, "--apply", help="Actually copy. Without it, only the plan is printed."),
) -> None:
    """Copy reports/ into the new clients/ layout. Copies only: nothing is deleted or overwritten."""
    plan = files_migrate.plan_migration(files_migrate.REPORTS_ROOT, client_files.clients_root())
    project = files_migrate.REPORTS_ROOT.parent

    def show(path):
        try:
            return str(path.relative_to(project))
        except ValueError:
            return str(path)

    for item in plan.items:
        if item.action == "copy":
            typer.echo(f"{show(item.src)}  ->  {show(item.dst)}" + (f"   ({item.note})" if item.note else ""))
        else:
            typer.echo(f"{show(item.src)}  ->  (skipped: {item.note})")
    for note in plan.notes:
        typer.secho(note, fg=typer.colors.YELLOW)

    conn = get_connection()
    try:
        odd = files_migrate.odd_checkpoints(conn)
    finally:
        conn.close()
    if odd:
        typer.secho("\nThese checkpoint values in the database are not one of "
                    f"{', '.join(CHECKPOINTS)}. They were left as they are; a person decides what each one should become:",
                    fg=typer.colors.YELLOW)
        for table, value, count in odd:
            typer.echo(f"  {table}: '{value}' ({count} row{'s' if count != 1 else ''})")

    typer.echo("")
    if not plan.to_copy:
        typer.echo("Nothing to copy.")
        return
    if not apply:
        typer.echo(f"Dry run: {len(plan.to_copy)} file(s) would be copied. Nothing was changed. "
                   "Run again with --apply to copy them.")
        return
    done = files_migrate.apply_plan(plan)
    typer.secho(f"Copied {len(done)} file(s).", fg=typer.colors.GREEN)
    typer.echo("reports/ was not touched. When you are happy with clients/, delete reports/ by hand.")


# ---- the truth document (PLAN.md section 16) ----------------------------------------------------------------

truth_app = typer.Typer(add_completion=False, no_args_is_help=True,
                        help="What is true about the business: facts the client confirms, and their story.")
app.add_typer(truth_app, name="truth")


@truth_app.command("draft")
def truth_draft_cmd(
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
) -> None:
    """Write the questionnaire for the client, pre-filled from the latest diagnosis."""
    b = _find_business(business)
    conn = get_connection()
    try:
        made = truth_draft.generate_draft(conn, b)
    finally:
        conn.close()
    typer.echo(f"Truth document written: {made.path}")
    if made.from_site:
        typer.echo(f"{made.from_site} fact(s) were pre-filled from the site's structured data in the latest diagnosis.")
    if made.from_record:
        typer.echo(f"{made.from_record} fact(s) were pre-filled from what was entered when the business was added.")
    if made.from_site or made.from_record:
        typer.echo("All pre-filled facts are marked unconfirmed: they are guesses for the client to check.")
    if not made.diagnosis_used:
        typer.secho("There is no diagnosis for this business yet, so nothing could be pre-filled from the site. "
                    "Run `aeo diagnose` first if you want the guesses.", fg=typer.colors.YELLOW)
    if made.carried_forward:
        typer.echo(f"{made.carried_forward} fact(s) were carried over from profile version {made.based_on_version}, "
                   "with their confirmation kept.")
    typer.echo(f"Send it to the client. When it comes back filled in:  aeo truth import --business {b['domain']} <file>")


@truth_app.command("import")
def truth_import_cmd(
    file: Path = typer.Argument(..., help="The filled-in truth document."),
    business: str = typer.Option(..., "--business", help="Business id, domain or name."),
) -> None:
    """Save a filled-in truth document as a new profile version (after you read the changes and type yes)."""
    b = _find_business(business)
    try:
        res = import_truth(b, file, ask=input, say=typer.echo)
    except TruthImportAborted:
        raise typer.Exit(1)                        # the reason was already printed; nothing was saved
    except TruthImportRefused as exc:
        typer.secho("Nothing was saved. Fix these and import again:", fg=typer.colors.RED, err=True)
        for problem in exc.problems:
            typer.echo(f"  - {problem}", err=True)
        raise typer.Exit(1)
    except TruthImportError as exc:
        _fail(str(exc))
    if not res.saved:
        return
    typer.secho(f"Saved as profile version {res.version}.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"{res.confirmed} confirmed fact(s), {res.unconfirmed} unconfirmed.")
    typer.echo(f"A copy of the filled file is kept in: {res.evidence_copy}")
