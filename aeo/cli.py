"""The `aeo` command: `diagnose`, `add` and `audit` so far."""

from __future__ import annotations

import sys
import textwrap
from typing import Optional

import typer

from audit.manual import AuditError, run_manual_audit
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


@app.command()
def audit(
    business: str = typer.Option(..., "--business", help="Business id."),
    question_set: str = typer.Option(..., "--question-set", help="Path to the frozen question set, e.g. question_sets/oland-stations_v1.yaml"),
    mode: str = typer.Option("manual", "--mode", help="manual (default). batch is not built yet."),
    checkpoint: Optional[str] = typer.Option(None, "--checkpoint", help="Free text, e.g. old_site or new_site_no_aeo."),
) -> None:
    """Ask the engines (by hand), store every raw answer, then work out who each answer names."""
    if mode == "batch":
        typer.secho("Error: batch mode is not built yet. It is only built after manual mode has run "
                    "cleanly ten times (PLAN.md section 3.2).", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if mode != "manual":
        typer.secho(f"Error: unknown mode '{mode}'. Use manual.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    try:
        out = run_manual_audit(business, question_set, checkpoint=checkpoint, ask=input, say=typer.echo)
    except AuditError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    c = out.counts
    typer.echo("")
    if out.complete:
        typer.secho("Audit complete.", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho(f"Audit NOT complete: {len(out.missing)} answer(s) missing. The run is marked failed.",
                    fg=typer.colors.YELLOW, bold=True)
    typer.echo(f"Answers collected: {c['answers']}")
    typer.echo(f"Answers that named the business: {c['named_business']}")
    typer.echo(f"Answers that recommended the business: {c['recommended_business']}")
    if c["not_analysed"]:
        typer.secho(f"{c['not_analysed']} answer(s) could not be read by the model and were sent to the "
                    "human queue. The raw answers are saved.", fg=typer.colors.YELLOW)
    if out.derive.claim_check_failed:
        typer.secho(f"{out.derive.claim_check_failed} claim check(s) failed and were sent to the human queue.",
                    fg=typer.colors.YELLOW)
    typer.echo(f"Run id: {out.probe_run_id}")
