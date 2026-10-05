"""The `aeo` command. Right now it has exactly one command: `diagnose`."""

from __future__ import annotations

import textwrap
from typing import Optional

import typer

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
    # commands (audit, recommend...) can be added later without changing how this one is called.


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

    typer.echo(f"\nReport: {out.report_dir}/diagnosis.md")
