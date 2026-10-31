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

from pydantic import BaseModel, ConfigDict, model_validator

from audit.cost_gate import confirm_cost
from core.llm import estimate_cost
from audit.question_set import QuestionSetError, load_question_set, resolve_tags
from core import client_files
from core.journal import write_journal
from core.runner import AgentDefinition, AgentOutputCutOff, AgentOutputInvalid, run_agent
from core.question_length import MEDIUM_MAX_WORDS, SHORT_MAX_WORDS
from diagnose.fetch import Fetcher
from diagnose.store import load_business
from questions_draft import location as loc
from questions_draft import plan
from questions_draft.salvage import LIST_KEYS, reply_json, salvage
from questions_draft.inputs import DraftInputError, DraftInputs, gather_inputs, utc_day

MODEL = "claude-sonnet-5"                 # price is in core/model_prices.json
PROMPT_VERSION = "v4"           # v1 to v3 stay on disk, untouched, for the drafts and runs that used them
PROMPTS = Path(__file__).parent.parent / "prompts"
AGENT = "questions_draft"
ACTOR = "aeo questions draft"
# The output cap of one call, with thinking off (step 25f). A question costs about 60 to 100 tokens in the v4 reply shape (the
# older, fuller shape was about 105), so 32 questions are about 2,000 to 3,400 tokens. 6,000 leaves room for the longest
# real-situation questions, a second language and the model being wordier than usual, without paying for a cap far above need.
# Before step 25f the cap was 8,000 and the model's hidden thinking used most of it, which is why replies were cut off.
OUTPUT_CAP = 6000
NO_THINKING = {"type": "disabled"}        # the draft is a rule-following writing job; the prompt holds every rule
MAX_CALLS = 3                             # the whole command: first draft, one retry, one length top-up. Never more.
WORST_CHARS_PER_TOKEN = 3                 # pessimistic on purpose: the card's "up to" must not be beaten by a real run
FOLLOW_UP_TOKENS_PER_QUESTION = 120       # what a later call adds: the accepted and rejected lists it is sent
FOLLOW_UP_EXTRA_TOKENS = 400
TYPICAL_RUNS = 5                          # how many recent runs the "typical" figure averages
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
    fact_topic: Any = None          # brand questions: what fact the answer is about
    need: Any = None                # discovery questions: what the customer is settling
    place: Any = None               # the place word the question carries (checked by code)


class DraftOutput(BaseModel):
    """v1 to v3 replies carry one flat `questions` list. v4 groups them (`discovery`, `control`, `brand`), so the kind and the
    fields that are fixed for a group (a control is informational and not a target; a brand question has no intent) are
    never written by the model: `all_questions` fills them in from the group."""
    model_config = ConfigDict(extra="ignore")
    local: Any = None               # does this business serve customers in one physical area? (the model's choice, shown to the operator)
    local_reason: Any = None
    questions: list[DraftedQuestion] = []
    discovery: list[DraftedQuestion] = []
    control: list[DraftedQuestion] = []
    brand: list[DraftedQuestion] = []

    @model_validator(mode="after")
    def _has_a_list(self):
        # "local" alone is a real reply: a local business with no place stops after step one, with no questions at all
        if not (set(LIST_KEYS) | {"local"}) & self.model_fields_set:
            raise ValueError("the reply has no list of questions")
        return self

    def all_questions(self, languages: list[str]) -> list[dict]:
        """Every question as one dict, in the order: flat list, discovery, control, brand. Where only one language was asked
        for and a question names none, it is that language (the model is not asked to write it)."""
        out = [q.model_dump() for q in self.questions]
        for q in self.discovery:
            out.append({**q.model_dump(), "kind": "discovery", "control": False})
        for q in self.control:
            out.append({**q.model_dump(), "kind": "discovery", "control": True, "intent": "informational", "is_target": False})
        for q in self.brand:
            out.append({**q.model_dump(), "kind": "brand", "control": False, "is_target": False, "intent": None})
        if len(languages) == 1:
            for q in out:
                if q.get("language") in (None, ""):
                    q["language"] = languages[0]
        return out


def keep_what_arrived(text: str) -> DraftOutput:
    """For a reply cut off at the output cap: every question that arrived whole, nothing else (questions_draft/salvage.py)."""
    kept = salvage(text)
    try:
        return DraftOutput.model_validate(kept)
    except ValueError:                                   # nothing arrived whole
        return DraftOutput(questions=[])


DRAFT_AGENT = AgentDefinition(name=AGENT, model=MODEL, prompt_version=PROMPT_VERSION,
                              prompt_path=PROMPTS / AGENT / f"{PROMPT_VERSION}.md", output_schema=DraftOutput,
                              max_tokens=OUTPUT_CAP, thinking=NO_THINKING, on_cut_off=keep_what_arrived,
                              clean_reply=reply_json)


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
    topped_up: str | None = None                        # the length asked for in the top-up call, if one was made
    repairs: list[str] = field(default_factory=list)    # tags fixed quietly, one line each
    warnings: list[str] = field(default_factory=list)
    source_lines: list[str] = field(default_factory=list)
    calls: int = 0
    cost: dict = field(default_factory=dict)            # calls, input_tokens, output_tokens, cost_usd (from `runs`)
    place: loc.Place = field(default_factory=loc.Place)  # the location used, and where it came from
    local: bool | None = None                           # the model's choice: does a place belong in the questions
    local_reason: str | None = None
    needs: list[str] = field(default_factory=list)      # customer needs the kept discovery questions cover
    spot_notes: dict[str, str] = field(default_factory=dict)   # spot-check text -> "need: service" or "fact topic: hours"


# ---- the request ---------------------------------------------------------------------------------------------------------------

def build_payload(business: dict, inputs: DraftInputs, targets: plan.Targets, languages: list[str], note: str | None,
                  place: loc.Place | None = None) -> dict:
    place = place or loc.Place()
    payload: dict[str, Any] = {
        "business": {"name": business["name"], "domain": business["domain"], "url": business["website_url"]},
        "languages": languages, "operator_note": note or None,
        "location": place.location, "location_source": place.source or None, "other_places_on_the_site": place.known,
        "facts_source": inputs.facts_source, "facts": inputs.facts,
        "plan": {"total": targets.size, "per_language": targets.by_language},
        "length_aim": targets.length_aim,
    }
    by_list = plan.length_by_list(targets)
    if by_list:
        payload["length_by_list"] = by_list
    if inputs.site_text:
        payload["site_text"] = inputs.site_text
    return payload


def worst_case(payload: dict, targets: plan.Targets) -> dict:
    """The most one run of this command can cost: MAX_CALLS calls, each with the whole output cap. Later calls are sent
    more (the lists of accepted and rejected questions, or the model's own earlier reply), so they are given more input."""
    system = DRAFT_AGENT.prompt_path.read_text()
    first_in = (len(system) + len(json.dumps(payload))) // WORST_CHARS_PER_TOKEN
    later_in = first_in + DRAFT_AGENT.max_tokens + targets.size * FOLLOW_UP_TOKENS_PER_QUESTION + FOLLOW_UP_EXTRA_TOKENS
    cap = DRAFT_AGENT.max_tokens
    usd = estimate_cost(MODEL, first_in, cap) + (MAX_CALLS - 1) * estimate_cost(MODEL, later_in, cap)
    return {"calls": MAX_CALLS, "input_tokens": first_in, "later_input_tokens": later_in, "output_cap": cap, "usd": usd}


def typical_cost(conn) -> dict | None:
    """The average real cost of this command's last few runs, from what each run wrote to the journal. None if none logged."""
    rows = conn.execute("SELECT (details->>'cost_usd')::numeric FROM client_journal WHERE actor = %s AND entry_type = 'note' "
                        "AND details ? 'cost_usd' ORDER BY created_at DESC LIMIT %s", (ACTOR, TYPICAL_RUNS)).fetchall()
    costs = [float(r[0]) for r in rows]
    if not costs:
        return None
    return {"runs": len(costs), "usd": sum(costs) / len(costs), "low": min(costs), "high": max(costs)}


def cost_card(payload: dict, targets: plan.Targets, typical: dict | None) -> list[str]:
    w = worst_case(payload, targets)
    lines = [f"  AI question drafting · {MODEL}",
             f"    Up to {w['calls']} calls: the first draft, one retry if some questions are dropped or missing, one top-up if a length is far under its target.",
             f"    Input about {w['input_tokens']:,} tokens per call (a retry or top-up is sent more); output capped at {w['output_cap']:,} tokens per call (thinking off).",
             f"    Up to ${w['usd']:.3f} (every call as big as it can be)."]
    if typical:
        lines.append(f"    Typical ${typical['usd']:.3f}: the average of the last {typical['runs']} run(s) of this command "
                     f"(${typical['low']:.3f} to ${typical['high']:.3f}).")
    else:
        lines.append("    Typical: not known yet; no earlier run of this command has logged its cost.")
    return lines


def _still_needed(missing: dict[tuple[str, str], int]) -> list[dict]:
    return [{"group": g, "language": lang, "count": n} for (g, lang), n in sorted(missing.items())]


# ---- the run -----------------------------------------------------------------------------------------------------------------

def draft_questions(conn, business: dict, **options) -> DraftResult:
    """Gathers what the site and the truth document say, asks the cost gate, drafts, checks, retries once, writes.
    If the command fails after it has called the model, the money is still spent: one journal note records the cost, so the
    card's "Typical" figure counts failed drafts too (they write no draft file and so no note of their own)."""
    progress: dict = {}
    try:
        return _draft_questions(conn, business, progress=progress, **options)
    except Exception as exc:
        if "started" in progress:
            spent = _cost_since(conn, business["id"], progress["started"])
            if spent["calls"]:
                write_journal(business_id=business["id"], entry_type="note", actor=ACTOR, conn=conn,
                              description=f"Question set draft failed, nothing saved: {_one_line(str(exc))[:160]}",
                              details={"outcome": "failed", "model": MODEL, "prompt_version": PROMPT_VERSION, "calls": spent["calls"],
                                       "input_tokens": spent["input_tokens"], "output_tokens": spent["output_tokens"],
                                       "cost_usd": spent["cost_usd"]})
        raise


def _draft_questions(conn, business: dict, *, progress: dict, languages: list[str] | None, size: int, note: str | None,
                     ask: Callable[[str], str], say: Callable[[str], None], client=None, fetcher: Fetcher | None = None,
                     clients_root: Path | None = None, today: date | None = None, location: str | None = None) -> DraftResult:
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
    place = loc.find_place(inputs.jsonld_blocks, inputs.page_text, location)
    if place.location:
        source_lines.append(f"Location: {place.location} ({place.source}).")
    else:
        source_lines.append("Location: none given with --location and none found in the page's structured data or text; "
                            "the model will say whether this business is local.")
    if place.known:
        source_lines.append("Other places the site names: " + ", ".join(place.known) + ".")
    for line in source_lines:
        say(line)

    payload = build_payload(business, inputs, targets, languages, note, place)
    # The cost gate: the worst case (every call, as big as it can be) and, separately, what recent runs really cost.
    confirm_cost(MAX_CALLS, model=MODEL, ask=ask, say=say, lines=cost_card(payload, targets, typical_cost(conn)))

    started = conn.execute("SELECT now()").fetchone()[0]
    progress["started"] = started
    calls = 0
    name, domain, alternates = business["name"], business["domain"], business["alternate_names"]

    def calls_left() -> int:
        """What is left of the three calls. Counted from the `runs` table, so a model call made inside run_agent
        (its own retry for a reply that is not valid JSON) spends the budget too."""
        used = conn.execute("SELECT count(*) FROM runs WHERE business_id = %s AND agent = %s AND created_at >= %s",
                            (business["id"], AGENT, started)).fetchone()[0]
        return MAX_CALLS - used

    last_reply: list = []
    cut_offs: list[tuple[int, int]] = []      # (which model call, how many whole questions arrived) for each reply cut off at the cap

    def propose(extra: dict | None, key: str = "retry") -> list[dict]:
        nonlocal calls
        left = calls_left()
        if left <= 0:
            raise DraftError("the three model calls for this command are used up")
        calls += 1
        try:
            out = run_agent(DRAFT_AGENT, business_id=business["id"], client=client, max_attempts=min(2, left),
                            input_data={**payload, **({key: extra} if extra else {})})
        except AgentOutputCutOff as cut:
            # The reply ran out of room. Every question that arrived whole is kept; the call is not asked again, and it is
            # not an invalid reply. What is still missing is asked for by the next call, as usual.
            out = cut.partial
            cut_offs.append((MAX_CALLS - calls_left(), len(out.all_questions(languages))))
        last_reply[:] = [out]
        return out.all_questions(languages)

    try:
        first = propose(None)
    except AgentOutputInvalid as exc:
        raise DraftError(f"The model did not return a usable draft, even after a retry. It has been put in the review "
                         f"queue (`aeo queue`). Nothing was saved. ({exc})") from exc

    # Is a place needed in the questions? The model says whether the business is local; the operator sees its answer.
    # With no place known and a local business, there is nothing honest to put in the questions, so the command stops.
    local = plan._as_bool(last_reply[0].local)
    local = local if isinstance(local, bool) else None
    why = " ".join(str(last_reply[0].local_reason or "").split()) or None
    if not place.location and local is not False:
        spent = _cost_since(conn, business["id"], started)
        raise DraftError(
            ("The model says this business is local" if local else "The model could not say whether this business is local")
            + (f" ({why})" if why else "") + ", and no place was given or found on its site. A question a customer types names "
            "a place, and a place is never guessed. Run it again with --location \"<city or area>\". "
            f"Nothing was saved. One model call was made (${spent['cost_usd']:.4f}).")
    needs_place = local is not False
    if local is False:
        say("The model says this business is not local" + (f" ({why})" if why else "")
            + ". No place is required in its discovery questions.")
    elif local:
        say("The model says this business is local" + (f" ({why})" if why else "") + ". Every discovery question must carry a place.")
    else:
        say("The model did not say whether this business is local. A place is required, because one was given or found.")
    payload["business_is_local"] = needs_place
    place_rule = loc.PlaceRule(required=needs_place, location=place.location, known=place.known,
                               by_language=place.by_language, corpus=inputs.page_text)

    warnings: list[str] = []
    if not first and not cut_offs and place.location:
        warnings.append("The model's first reply held no questions, although a place was known. The next call asked for all of them.")

    check = dict(name=name, domain=domain, alternates=alternates, languages=languages,
                 confirmed_fact_ids=inputs.confirmed_ids, place_rule=place_rule)
    kept, rejected = plan.check_questions(first, already=[], **check)
    fitted, surplus, missing = plan.fit_to_targets(kept, targets)

    if rejected or missing:
        say(f"{len(rejected)} question(s) were dropped and {sum(missing.values())} are missing: asking once more.")
        try:
            second = propose({"accepted": [c.text for c in fitted], "fact_topics_used": plan.topics_used(fitted),
                              "rejected": [{"text": r.text, "reasons": r.reasons} for r in rejected],
                              "needed": _still_needed(missing),
                              **({"cut_off": True} if cut_offs else {})})
        except (AgentOutputInvalid, DraftError):
            second = []
            warnings.append("The second request did not return a usable reply, so the first draft's good questions are all there is.")
        more, rejected_again = plan.check_questions(second, already=fitted, **check)
        rejected += rejected_again
        fitted, extra_surplus, missing = plan.fit_to_targets([*fitted, *more], targets)
        surplus += extra_surplus

    # One top-up for a length that is far under its target; it asks for that length only.
    topped_up = None
    request = plan.topup_request(fitted, targets)
    if request and calls_left() <= 0:
        warnings.append(f"{request['count']} more {request['length']} question(s) were wanted for the length mix, but all "
                        f"{MAX_CALLS} model calls were already used. Not padded.")
    if request and calls_left() > 0:
        say(f"The mix is {request['count']} or more short on {request['length']} questions: asking once for those.")
        try:
            third = propose({"length": request["length"], "count": request["count"], "places": request["places"],
                             "accepted": [c.text for c in fitted], "fact_topics_used": plan.topics_used(fitted)}, key="topup")
        except (AgentOutputInvalid, DraftError):
            third = []
            warnings.append("The top-up request did not return a usable reply, so the length mix is as the earlier calls left it.")
        topped_up = request["length"]
        extras, rejected_topup = plan.check_questions(third, already=fitted, **check)
        rejected += rejected_topup
        fitted, left_out = plan.merge_topup(fitted, extras, targets, request["length"])
        surplus += left_out

    for call_no, kept_n in cut_offs:
        asked_again = MAX_CALLS - calls_left() > call_no
        warnings.append(f"Reply {call_no} was cut off at the output cap ({OUTPUT_CAP:,} tokens) after {kept_n} complete question(s). "
                        f"Those were kept" + (", and the next call asked only for what was still missing." if asked_again
                                              else "; no further call was made, so what is missing is listed as missing."))

    ordered = plan.order_for_file(fitted, languages)
    spot = plan.choose_spot_check(ordered, languages, domain)
    items = [_item(c, i in spot) for i, c in enumerate(ordered)]
    if len(spot) < plan.SPOT_TOTAL:
        warnings.append(f"Only {len(spot)} question(s) could be flagged for the spot check (it needs {plan.SPOT_TOTAL}).")
    warnings += _length_warnings(items, targets)
    warnings += plan.coverage_warnings(ordered, name=name, domain=domain, location=place.location, needs_place=needs_place)
    if len(items) > targets.size:
        warnings.append(f"The file holds {len(items)} questions, {len(items) - targets.size} more than the {targets.size} asked for, "
                        "because flagged questions are kept. Remove or reword them when you review.")

    version, path, header_warnings = _write(items, business=business, inputs=inputs, languages=languages, targets=targets,
                                            note=note, rejected=rejected, missing=missing, place=place, local=local, why=why,
                                            needs_place=needs_place,
                                            clients_root=clients_root, today=today or date.today())
    warnings += header_warnings
    cost = _cost_since(conn, business["id"], started)
    write_journal(business_id=business["id"], entry_type="note", actor=ACTOR, conn=conn,
                  description=f"Question set draft v{version} written ({len(items)} questions; not frozen)",
                  details={"draft": path.name, "questions": len(items), "facts_source": inputs.facts_source,
                           "truth_version": inputs.truth_version, "model": MODEL, "prompt_version": PROMPT_VERSION,
                           "rejected": len(rejected), "missing": sum(missing.values()), "cut_off_replies": len(cut_offs),
                           "needs_review": sum(1 for it in items if it.get("needs_review")),
                           "calls": cost["calls"], "input_tokens": cost["input_tokens"],
                           "output_tokens": cost["output_tokens"], "cost_usd": cost["cost_usd"]})
    return DraftResult(path=path, version=version, languages=languages, targets=targets, items=items,
                       spot_check=[it["text"] for it in items if it["spot_check"]], rejected=rejected,
                       surplus=[c.text for c in surplus], missing=missing, warnings=warnings, topped_up=topped_up,
                       repairs=[f"\"{c.text}\": {r}" for c in ordered for r in c.repairs],
                       source_lines=source_lines, calls=calls, cost=cost, place=place, local=local, local_reason=why,
                       needs=plan.needs_used(ordered),
                       spot_notes={c.text: (f"need: {c.need}" if c.kind == "discovery" and c.need else
                                            f"fact topic: {c.fact_topic}" if c.fact_topic else "")
                                   for c, it in zip(ordered, items) if it["spot_check"]})


def _item(c: plan.Candidate, spot: bool) -> dict:
    item = {"text": c.text, "type": c.kind}
    if c.kind == "discovery":
        item["intent"] = c.intent
    item.update({"length": plan.length_of(c.text), "language": c.language, "is_target": c.is_target,
                 "control": c.control, "spot_check": spot and not c.needs_review})
    if c.kind == "brand" and c.fact_topic:
        item["fact_topic"] = c.fact_topic
    if c.needs_review:                                   # only flagged questions carry the two fields
        item["needs_review"] = True
        item["review_reason"] = c.review_reason
    return item


def _length_warnings(items: list[dict], targets: plan.Targets) -> list[str]:
    out = []
    for length in plan.LENGTHS:
        have = sum(1 for i in items if i["length"] == length)
        share = have / len(items) if items else 0
        if abs(share - plan.LENGTH_AIM[length]) > plan.LENGTH_TOLERANCE or targets.length_aim[length] - have > plan.TOPUP_GAP:
            out.append(f"Length mix: {have} {length} ({share:.0%}); the aim is {targets.length_aim[length]} ({plan.LENGTH_AIM[length]:.0%}). "
                       "Not padded. Edit the draft if you want it closer.")
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
                missing: dict, today: date, place: loc.Place | None = None, local: bool | None = None,
                why: str | None = None, needs_place: bool = True) -> str:
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
        f"# Tags: type (discovery/brand), intent, length (counted: short {SHORT_MAX_WORDS} words or fewer, medium one sentence of "
        f"{SHORT_MAX_WORDS + 1}-{MEDIUM_MAX_WORDS}, long two or more sentences or over {MEDIUM_MAX_WORDS}; PLAN.md section 8),",
        "#       language, is_target, control (unrelated topic, to notice drift), spot_check (the eight for the quick Phase A check).",
    ]
    place = place or loc.Place()
    where = (f"{place.location} ({place.source})" if place.location else "none")
    choice = {True: "yes", False: "no", None: "not stated"}[local]
    lines.append(f"# Location used: {where}. Local business (the model's choice, check it): {choice}"
                 + (f" - {_one_line(why)}" if why else "") + (". Discovery questions carry a place." if needs_place else ". No place is required."))
    rating = any(it.get("fact_topic") == "rating" for it in items)
    if rating:
        lines.append("# fact_topic: rating - the correct answer for a rating question comes from a live source (the Google Business Profile "
                     "evidence). Check it against the evidence file for the same date; it is not in the truth document.")
    flagged = sum(1 for it in items if it.get("needs_review"))
    if flagged:
        lines.append(f"# {flagged} question(s) are flagged needs_review: true, each with a review_reason. A person decides whether to keep, reword or remove them.")
    if rejected:
        lines.append(f"# {len(rejected)} proposed question(s) were dropped by the checks and are not in this file (see the command's summary).")
    if missing:
        lines.append("# STILL MISSING: " + ", ".join(f"{n} {g} in {lang}" for (g, lang), n in sorted(missing.items())) + ". Add them by hand.")
    note_text = "DRAFT, not frozen. Generated by AI from " + used + ". Review every question before freezing."
    if rating:
        note_text += " A rating question is scored against the live Google Business Profile evidence for the same date, not the truth document."
    lines += [f"note: {json.dumps(note_text)}",
              "questions:"]
    lines += [f"  - {json.dumps(item, ensure_ascii=False)}" for item in items]
    return "\n".join(lines) + "\n"


def _write(items: list[dict], *, business: dict, inputs: DraftInputs, languages: list[str], targets: plan.Targets,
           note: str | None, rejected: list[plan.Rejection], missing: dict,
           clients_root: Path | None, today: date, place: loc.Place | None = None, local: bool | None = None,
           why: str | None = None, needs_place: bool = True) -> tuple[int, Path, list[str]]:
    """Checks the file with the audit's own loader, then saves it as the next draft number. Never overwrites."""
    folder = client_files.client_dir(business["domain"], clients_root)
    folder.mkdir(parents=True, exist_ok=True)
    for _ in range(20):                                    # a second process could take a number between our look and our write
        version = next_version(folder)
        text = render_file(items, version=version, business=business, inputs=inputs, languages=languages, targets=targets,
                           note=note, rejected=rejected, missing=missing, today=today, place=place, local=local,
                           why=why, needs_place=needs_place)
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
    flagged = [it for it in r.items if it.get("needs_review")]
    clean = [it for it in r.items if not it.get("needs_review")]
    lines = ["", f"Draft v{r.version} saved (NOT frozen): {r.path}",
             f"{len(r.items)} of {r.targets.size} questions: {kinds.get('discovery', 0)} discovery"
             f" (of which {controls} control) and {kinds.get('brand', 0)} brand.",
             "By length: " + ", ".join(f"{n} {k}" for k, n in count("length").items()) +
             " (aim: " + ", ".join(f"{n} {k}" for k, n in r.targets.length_aim.items()) + ")." +
             (f" A top-up for {r.topped_up} questions was asked." if r.topped_up else ""),
             "By language: " + ", ".join(f"{n} {k}" for k, n in count("language").items()) + "."]
    where = f"{r.place.location} ({r.place.source})" if r.place.location else "none"
    choice = {True: "local", False: "not local: no place required", None: "not stated"}[r.local]
    lines += [f"Location used: {where}. The model says the business is {choice}" + (f" ({r.local_reason})" if r.local_reason else "") + "."]
    if r.needs:
        lines += ["Customer needs covered by the discovery questions: " + ", ".join(r.needs) + "."]
    lines += ["", f"The {len(r.spot_check)} spot-check questions (never a flagged one):"]
    lines += [f"  {i}. {t}" + (f"   [{r.spot_notes[t]}]" if r.spot_notes.get(t) else "") for i, t in enumerate(r.spot_check, start=1)]

    lines += ["", f"1. Kept, clean: {len(clean)} question(s)."]
    if r.repairs:
        lines += [f"   {len(r.repairs)} tag(s) were repaired where the intent was clear:"] + [f"   - {x}" for x in r.repairs]
    lines += ["", f"2. Kept and FLAGGED for review (needs_review: true in the file): {len(flagged)} question(s)."]
    lines += [f"   - \"{it['text']}\" — {it['review_reason']}" for it in flagged]
    lines += ["", f"3. Dropped, not in the file: {len(r.rejected)} question(s)."]
    lines += [f"   - \"{x.text}\" — " + "; ".join(x.reasons) for x in r.rejected]
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
