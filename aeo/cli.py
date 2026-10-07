"""The `aeo` command: `diagnose`, `add` and `audit` so far."""

from __future__ import annotations

import sys
import textwrap
from typing import Optional

import typer

from audit.fill_in import ImportRefused, generate_fill_in, import_fill_in
from audit.manual import AuditError, run_manual_audit
from audit.summary import summary_lines
from core.businesses import BusinessError, add_with_checks
from diagnose import platform_data as pd
from diagnose.report import decline_pitch_outline
from diagnose.run import DiagnoseError, run_diagnosis

TERMINAL_WIDTH = 100   # findings are wrapped to fit a normal terminal

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="AEO Studio tools. Git, sending and deploying are always done by a person.")


@app.callback()
def main() -> None:
    """AEO Studio tools."""
    # An empty callback makes Typer keep `diagnose` as a named command, so more
    # commands (recommend...) can be added later without changing how the others are called.


@app.command()
def diagnose(
    url: str = typer.Argument(..., help="The site to check, e.g. https://example.com"),
    business: Optional[str] = typer.Option(None, "--business", help="Business id. Links the result to it and adds a model-written summary."),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help="Free text, e.g. old_site or new_site_no_aeo."),
) -> None:
    """Free, code-only check of whether a site can be helped: serve, conditional or decline."""
    typer.echo(f"Checking {url} (about 8 requests, one per second)...")
    try:
        out = run_diagnosis(url, business_id=business, checkpoint=checkpoint)
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

    typer.echo(f"\nReport (run {out.run_number}): {out.report_dir}/diagnosis.md")


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


def _fail(message: str) -> None:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


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
    business: Optional[str] = typer.Option(None, "--business", help="Business id."),
    question_set: Optional[str] = typer.Option(None, "--question-set", help="Path to the frozen question set, e.g. question_sets/oland-stations_v1.yaml"),
    mode: str = typer.Option("manual", "--mode", help="manual (default). batch is not built yet."),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help="Free text, e.g. old_site or new_site_no_aeo."),
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
        res = import_fill_in(file, partial=partial, question_set_path=question_set)
    except ImportRefused as exc:
        typer.secho("Nothing was saved. Fix these and import again:", fg=typer.colors.RED, err=True)
        for problem in exc.problems:
            typer.echo(f"  - {problem}", err=True)
        raise typer.Exit(1)
    except AuditError as exc:
        _fail(str(exc))

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
