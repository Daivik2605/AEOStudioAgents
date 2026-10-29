"""`aeo questions draft` (PLAN.md step 13b): the AI proposes a first set of questions; code checks every one; a person edits.

The fourth permitted use of AI in this project (PLAN.md section 16). The model only proposes wording. Counts, lengths,
the brand-name rules, duplicate detection and the choice of the eight spot-check questions are all plain code in
plan.py, so the same proposal always gives the same file.

The draft is written next to the existing drafts (clients/<domain>/question_set_draft_v<N>.yaml), in the format the
audit's own loader reads, and is checked with that loader and `resolve_tags` before it is saved. It is never "frozen":
freezing is a person moving a reviewed file into question_sets/. This command never writes there, and never overwrites
an earlier draft.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from audit.cost_gate import confirm_cost
from audit.question_set import QuestionSetError, load_question_set, resolve_tags
from core import client_files
from core.journal import write_journal
from core.runner import AgentDefinition, AgentOutputInvalid, run_agent
from diagnose.fetch import Fetcher
from diagnose.store import load_business
from questions_draft import plan
from questions_draft.inputs import DraftInputError, DraftInputs, gather_inputs, utc_day

MODEL = "claude-sonnet-5"                 # price is in core/model_prices.json
PROMPT_VERSION = "v1"
PROMPTS = Path(__file__).parent.parent / "prompts"
AGENT = "questions_draft"
ACTOR = "aeo questions draft"
OUTPUT_TOKENS_PER_QUESTION = 70           # rough: a question and its tags as JSON
CHARS_PER_TOKEN = 4                       # rough: used only for the cost card's estimate
DRAFT_NAME = re.compile(r"^question_set_draft_v(\d+)\.(?:yaml|md)$")


class DraftError(Exception):
    """The draft cannot be made or saved. The message says what to do."""


class DraftedQuestion(BaseModel):
    """Lenient on purpose: every tag is checked by code, one question at a time, so a single bad tag rejects that
    question and not the whole reply."""
    model_config = ConfigDict(extra="ignore")
    text: Any = None
    kind: Any = None
    language: Any = None
    intent: Any = None
    is_target: Any = None
    control: Any = None
    relies_on_fact_ids: Any = None


class DraftOutput(BaseModel):
    questions: list[DraftedQuestion]


DRAFT_AGENT = AgentDefinition(name=AGENT, model=MODEL, prompt_version=PROMPT_VERSION,
                              prompt_path=PROMPTS / AGENT / f"{PROMPT_VERSION}.md", output_schema=DraftOutput,
                              max_tokens=8000)


@dataclass
class DraftResult:
    path: Path
    version: int
    languages: list[str]
    targets: plan.Targets
    items: list[dict]                                   # the saved questions, in file order, with every tag
    spot_check: list[str]
    rejected: list[plan.Rejection] = field(default_factory=list)
    surplus: list[str] = field(default_factory=list)
    missing: dict[tuple[str, str], int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    source_lines: list[str] = field(default_factory=list)
    calls: int = 0
    cost: dict = field(default_factory=dict)            # calls, input_tokens, output_tokens, cost_usd (from `runs`)


# ---- the request ---------------------------------------------------------------------------------------------------------------

def build_payload(business: dict, inputs: DraftInputs, targets: plan.Targets, languages: list[str], note: str | None) -> dict:
    payload: dict[str, Any] = {
        "business": {"name": business["name"], "domain": business["domain"], "url": business["website_url"]},
        "languages": languages, "operator_note": note or None,
        "facts_source": inputs.facts_source, "facts": inputs.facts,
        "plan": {"total": targets.size, "per_language": targets.by_language},
        "length_aim": targets.length_aim,
    }
    if inputs.site_text:
        payload["site_text"] = inputs.site_text
    return payload


def _estimate_tokens(payload: dict, targets: plan.Targets) -> tuple[int, int]:
    system = DRAFT_AGENT.prompt_path.read_text()
    return ((len(system) + len(json.dumps(payload))) // CHARS_PER_TOKEN, targets.size * OUTPUT_TOKENS_PER_QUESTION + 200)


def _still_needed(missing: dict[tuple[str, str], int]) -> list[dict]:
    return [{"group": g, "language": lang, "count": n} for (g, lang), n in sorted(missing.items())]


# ---- the run -----------------------------------------------------------------------------------------------------------------

def draft_questions(conn, business: dict, *, languages: list[str] | None, size: int, note: str | None,
                    ask: Callable[[str], str], say: Callable[[str], None], client=None, fetcher: Fetcher | None = None,
                    clients_root: Path | None = None, today: date | None = None) -> DraftResult:
    """Gathers what the site and the truth document say, asks the cost gate, drafts, checks, retries once, writes."""
    business = load_business(conn, business["id"])          # name, domain, website address and alternate names
    if business is None:
        raise DraftError("that business does not exist")
    try:
        inputs = gather_inputs(conn, business, fetcher=fetcher, clients_root=clients_root)
    except DraftInputError as exc:
        raise DraftError(str(exc)) from exc

    source_lines = [f"Site text: {inputs.page_source}.", f"Facts: {inputs.facts_source}."]
    if languages is None:
        languages = [inputs.page_language or "en"]
        source_lines.append(f"Language: {languages[0]} ({inputs.language_source}" + ("" if inputs.page_language else "; defaulting to en") + ").")
    else:
        source_lines.append(f"Languages: {', '.join(languages)} (asked for).")
    try:
        targets = plan.make_targets(size, languages)
    except ValueError as exc:
        raise DraftError(str(exc)) from exc
    for line in source_lines:
        say(line)

    payload = build_payload(business, inputs, targets, languages, note)
    # The cost gate: a card and a typed yes, and a refusal with no person at the keyboard. Worst case is shown (two calls).
    confirm_cost(2, model=MODEL, ask=ask, say=say, tokens=_estimate_tokens(payload, targets),
                 label="AI question drafting (a second call only if some questions are rejected)")

    started = conn.execute("SELECT now()").fetchone()[0]
    calls = 0
    name, domain, alternates = business["name"], business["domain"], business["alternate_names"]

    def propose(extra: dict | None) -> list[dict]:
        nonlocal calls
        calls += 1
        out = run_agent(DRAFT_AGENT, business_id=business["id"], client=client,
                        input_data={**payload, **({"retry": extra} if extra else {})})
        return [q.model_dump() for q in out.questions]

    try:
        first = propose(None)
    except AgentOutputInvalid as exc:
        raise DraftError(f"The model did not return a usable draft, even after a retry. It has been put in the review "
                         f"queue (`aeo queue`). Nothing was saved. ({exc})") from exc

    check = dict(name=name, domain=domain, alternates=alternates, languages=languages,
                 confirmed_fact_ids=inputs.confirmed_ids)
    kept, rejected = plan.check_questions(first, already=[], **check)
    fitted, surplus, missing = plan.fit_to_targets(kept, targets)

    warnings: list[str] = []
    if rejected or missing:
        say(f"{len(rejected)} question(s) were rejected and {sum(missing.values())} are missing: asking once more.")
        try:
            second = propose({"accepted": [c.text for c in fitted],
                              "rejected": [{"text": r.text, "reasons": r.reasons} for r in rejected],
                              "needed": _still_needed(missing)})
        except (AgentOutputInvalid, DraftError):
            second = []
            warnings.append("The second request did not return a usable reply, so the first draft's good questions are all there is.")
        more, rejected_again = plan.check_questions(second, already=fitted, **check)
        rejected += rejected_again
        fitted, extra_surplus, missing = plan.fit_to_targets([*fitted, *more], targets)
        surplus += extra_surplus

    ordered = plan.order_for_file(fitted, languages)
    spot = plan.choose_spot_check(ordered, languages, domain)
    items = [_item(c, i in spot) for i, c in enumerate(ordered)]
    if len(spot) < plan.SPOT_TOTAL:
        warnings.append(f"Only {len(spot)} question(s) could be flagged for the spot check (it needs {plan.SPOT_TOTAL}).")
    warnings += _length_warnings(items)

    version, path, header_warnings = _write(items, business=business, inputs=inputs, languages=languages, targets=targets,
                                            note=note, rejected=rejected, missing=missing,
                                            clients_root=clients_root, today=today or date.today())
    warnings += header_warnings
    write_journal(business_id=business["id"], entry_type="note", actor=ACTOR, conn=conn,
                  description=f"Question set draft v{version} written ({len(items)} questions; not frozen)",
                  details={"draft": path.name, "questions": len(items), "facts_source": inputs.facts_source,
                           "truth_version": inputs.truth_version, "model": MODEL, "prompt_version": PROMPT_VERSION,
                           "rejected": len(rejected), "missing": sum(missing.values())})
    return DraftResult(path=path, version=version, languages=languages, targets=targets, items=items,
                       spot_check=[it["text"] for it in items if it["spot_check"]], rejected=rejected,
                       surplus=[c.text for c in surplus], missing=missing, warnings=warnings,
                       source_lines=source_lines, calls=calls, cost=_cost_since(conn, business["id"], started))


def _item(c: plan.Candidate, spot: bool) -> dict:
    item = {"text": c.text, "type": c.kind}
    if c.kind == "discovery":
        item["intent"] = c.intent
    item.update({"length": plan.length_of(c.text), "language": c.language, "is_target": c.is_target,
                 "control": c.control, "spot_check": spot})
    return item


def _length_warnings(items: list[dict]) -> list[str]:
    out = []
    for length in plan.LENGTHS:
        share = sum(1 for i in items if i["length"] == length) / len(items) if items else 0
        if abs(share - plan.LENGTH_AIM[length]) > plan.LENGTH_TOLERANCE:
            out.append(f"Length mix: {share:.0%} {length} (the aim is {plan.LENGTH_AIM[length]:.0%}). Edit the draft if you want it closer.")
    return out


def _cost_since(conn, business_id: str, started) -> dict:
    row = conn.execute("SELECT count(*), COALESCE(sum(input_tokens), 0), COALESCE(sum(output_tokens), 0), COALESCE(sum(cost_usd), 0) "
                       "FROM runs WHERE business_id = %s AND agent = %s AND created_at >= %s", (business_id, AGENT, started)).fetchone()
    return {"calls": row[0], "input_tokens": row[1], "output_tokens": row[2], "cost_usd": float(row[3])}


# ---- the file --------------------------------------------------------------------------------------------------------------------

def _one_line(text: str | None) -> str:
    return " ".join(str(text or "").split())


def next_version(folder: Path) -> int:
    """One more than the highest draft number already in the folder (the hand-made v0 counts), so nothing is overwritten."""
    numbers = [int(m.group(1)) for p in folder.glob("question_set_draft_v*.*") if (m := DRAFT_NAME.match(p.name))]
    return max(numbers, default=0) + 1


def render_file(items: list[dict], *, version: int, business: dict, inputs: DraftInputs, languages: list[str],
                targets: plan.Targets, note: str | None, rejected: list[plan.Rejection],
                missing: dict, today: date) -> str:
    who = business["name"] or business["domain"]
    used = (f"truth document version {inputs.truth_version} (confirmed facts only)" if inputs.truth_version
            else "the business's website only, every fact unconfirmed")
    diag = inputs.diagnosis
    based_on = (f"diagnosis {diag['checkpoint'] or 'with no label'} of {utc_day(diag['checked_at'])} (id {diag['id']})"
                if diag else "no diagnosis")
    lines = [
        f"# {who}: proposed question set, DRAFT v{version} (NOT FROZEN)",
        f"# Drafted by AI on {today.isoformat()}: model {MODEL}, prompt {AGENT}/{PROMPT_VERSION}. A person must review and edit every question.",
        f"# Facts used: {used}. Based on: {based_on}. Site text: {inputs.page_source}.",
        f"# Languages: {', '.join(languages)}. Size asked for: {targets.size}." + (f" Operator note: {_one_line(note)}" if note else ""),
        "# Freezing means a person moving a reviewed copy into question_sets/. This command never writes there.",
        "# Tags: type (discovery/brand), intent, length (counted from the words: short under 12, medium 12-25, long over 25),",
        "#       language, is_target, control (unrelated topic, to notice drift), spot_check (the eight for the quick Phase A check).",
    ]
    if rejected:
        lines.append(f"# {len(rejected)} proposed question(s) were rejected by the checks and are not in this file (see the command's summary).")
    if missing:
        lines.append("# STILL MISSING: " + ", ".join(f"{n} {g} in {lang}" for (g, lang), n in sorted(missing.items())) + ". Add them by hand.")
    lines += [f"note: {json.dumps('DRAFT, not frozen. Generated by AI from ' + used + '. Review every question before freezing.')}",
              "questions:"]
    lines += [f"  - {json.dumps(item, ensure_ascii=False)}" for item in items]
    return "\n".join(lines) + "\n"


def _write(items: list[dict], *, business: dict, inputs: DraftInputs, languages: list[str], targets: plan.Targets,
           note: str | None, rejected: list[plan.Rejection], missing: dict,
           clients_root: Path | None, today: date) -> tuple[int, Path, list[str]]:
    """Checks the file with the audit's own loader, then saves it as the next draft number. Never overwrites."""
    folder = client_files.client_dir(business["domain"], clients_root)
    folder.mkdir(parents=True, exist_ok=True)
    for _ in range(20):                                    # a second process could take a number between our look and our write
        version = next_version(folder)
        text = render_file(items, version=version, business=business, inputs=inputs, languages=languages, targets=targets,
                           note=note, rejected=rejected, missing=missing, today=today)
        warnings = _check_with_loader(text, version, business)
        path = folder / f"question_set_draft_v{version}.yaml"
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(text)
        except FileExistsError:
            continue
        return version, path, warnings
    raise DraftError("could not find a free draft number")


def _check_with_loader(text: str, version: int, business: dict) -> list[str]:
    """The same loader and `resolve_tags` the audit uses. A file they refuse is not saved. Returns their warnings."""
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / f"question_set_draft_v{version}.yaml"
        probe.write_text(text, encoding="utf-8")
        try:
            qs = load_question_set(probe)
            _, warnings = resolve_tags(qs, business["name"], business["domain"])
        except QuestionSetError as exc:
            raise DraftError(f"the draft did not pass the question-set checks, so it was not saved: {exc}") from exc
    return warnings


# ---- what is printed ----------------------------------------------------------------------------------------------------------------

def summary_lines(r: DraftResult) -> list[str]:
    def count(key) -> dict:
        out: dict = {}
        for it in r.items:
            out[it[key]] = out.get(it[key], 0) + 1
        return out
    kinds = count("type")
    controls = sum(1 for it in r.items if it["control"])
    lines = ["", f"Draft v{r.version} saved (NOT frozen): {r.path}",
             f"{len(r.items)} of {r.targets.size} questions: {kinds.get('discovery', 0)} discovery"
             f" (of which {controls} control) and {kinds.get('brand', 0)} brand.",
             "By length: " + ", ".join(f"{n} {k}" for k, n in count("length").items()) +
             " (aim: " + ", ".join(f"{n} {k}" for k, n in r.targets.length_aim.items()) + ").",
             "By language: " + ", ".join(f"{n} {k}" for k, n in count("language").items()) + "."]
    lines += ["", f"The {len(r.spot_check)} spot-check questions:"] + [f"  {i}. {t}" for i, t in enumerate(r.spot_check, start=1)]
    if r.rejected:
        lines += ["", f"{len(r.rejected)} question(s) were rejected and are NOT in the file:"]
        lines += [f"  - \"{x.text}\" — " + "; ".join(x.reasons) for x in r.rejected]
    if r.missing:
        lines += ["", "STILL MISSING (not padded; add by hand or run the command again):"]
        lines += [f"  - {n} {g} question(s) in {lang}" for (g, lang), n in sorted(r.missing.items())]
    if r.surplus:
        lines += ["", f"{len(r.surplus)} extra question(s) beyond the plan were left out."]
    lines += [f"Warning: {w}" for w in r.warnings]
    c = r.cost
    lines += ["", f"AI cost: {c.get('calls', r.calls)} call(s), {c.get('input_tokens', 0):,} tokens in, "
                  f"{c.get('output_tokens', 0):,} out, ${c.get('cost_usd', 0):.4f} (logged in the runs table)."]
    return lines
