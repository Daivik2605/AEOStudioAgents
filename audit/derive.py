"""The two AI jobs of `aeo audit` (PLAN.md section 1). Both read an answer that is
already stored, and both are safe to re-run: nothing here touches the raw answer.

1. Extraction  - which businesses does this answer name, in what order, and with what
                 reason and descriptive words? Was ours among them, and was it proposed
                 as the answer rather than listed as an also-ran?
2. Claim check - what does the answer say about the client, and does it match
                 the facts the client confirmed? Only runs if there are such facts.

The model reads and labels. Counting and any score are plain Python elsewhere. That includes "is this business ours":
the model lists the businesses the answer names, and the code decides which one is the client, by comparing the name or
domain with the canonical name, every alias and the domain (core/names.py). The model's own opinion is kept next to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import psycopg
from pydantic import BaseModel, Field, model_validator

from audit import store
from core.names import Identity
from core.runner import AgentDefinition, AgentOutputInvalid, run_agent

MODEL = "claude-sonnet-5"     # price is in core/model_prices.json
PROMPTS = Path(__file__).parent.parent / "prompts"


class NamedBusiness(BaseModel):
    name: str
    domain: str | None = None
    is_client: bool
    recommended: bool
    position: int = Field(ge=1)          # 1 = the first business the answer names
    reason: str | None = None            # the answer's own reason, None if it gives none
    descriptors: list[str] = []          # the answer's own words for this business


class ExtractOutput(BaseModel):
    businesses: list[NamedBusiness]

    @model_validator(mode="after")
    def _positions_are_distinct(self):
        # Two businesses cannot both be "second". If the model says so, the output is invalid,
        # which sends it through the same retry-once-then-human_queue path as any other bad output.
        positions = [b.position for b in self.businesses]
        if len(set(positions)) != len(positions):
            raise ValueError(f"each business needs its own position, but got {positions}")
        return self


class Claim(BaseModel):
    claim: str
    field: str | None = None
    verdict: Literal["correct", "incorrect", "unverifiable"]
    note: str


class ClaimOutput(BaseModel):
    claims: list[Claim]


# Earlier prompt files stay exactly as they were: runs already logged their versions (extract v1 and v2, claim check v1).
EXTRACT = AgentDefinition(name="audit_extract", model=MODEL, prompt_version="v3",
                          prompt_path=PROMPTS / "audit_extract" / "v3.md",
                          output_schema=ExtractOutput, max_tokens=2500)
CLAIM_CHECK = AgentDefinition(name="audit_claim_check", model=MODEL, prompt_version="v2",
                              prompt_path=PROMPTS / "audit_claim_check" / "v2.md",
                              output_schema=ClaimOutput, max_tokens=2500)


@dataclass
class DeriveReport:
    extracted: int = 0
    extraction_failed: int = 0
    claims_checked: int = 0
    claims_skipped: int = 0         # no confirmed facts, or the client was not named in the answer
    claim_check_failed: int = 0


def client_identity(business: dict) -> Identity:
    """The names this business answers to, from a business loaded by audit.store (canonical name, aliases, domain)."""
    return Identity(canonical=business["name"], aliases=list(business.get("aliases") or []), domain=business["domain"],
                    former=list(business.get("former_names") or []))


def decide_client(named: list[dict], identity: Identity) -> list[dict]:
    """Sets `is_client` from the rule, not from the model. A business is the client when its name is the canonical name,
    an alias, an earlier name, or its domain is ours (same words, see core/names.py). A similar name is another business.
    What the model said is kept as `client_by_model`, and which of our names matched as `matched_as`."""
    for b in named:
        b["client_by_model"] = bool(b.get("is_client"))
        b["matched_as"] = identity.match(b.get("name"), b.get("domain"))
        b["is_client"] = b["matched_as"] is not None
    return named


def count_claims(claims: list[dict]) -> tuple[int, int]:
    """(correct, incorrect) from stored claims. Unverifiable ones are left out, which is
    what scoring.accuracy_score expects (PLAN.md section 11)."""
    correct = sum(1 for c in claims if c["verdict"] == "correct")
    incorrect = sum(1 for c in claims if c["verdict"] == "incorrect")
    return correct, incorrect


def derive_run(conn: psycopg.Connection, business: dict, probe_run_id, *, client=None,
               only_ids=None) -> DeriveReport:
    """Runs extraction (and claim checking where it applies) on every answer not yet extracted,
    or only on `only_ids` if given."""
    report = DeriveReport()
    for row in store.results_to_extract(conn, probe_run_id, only_ids):
        try:
            out = run_agent(EXTRACT, business_id=business["id"], client=client, input_data={
                "question": row["question"], "answer": row["answer"],
                "client": {"name": business["name"], "aliases": list(business.get("aliases") or []),
                           "domain": business["domain"]}})
        except AgentOutputInvalid:
            # run_agent has already put this in human_queue. The answer stays un-extracted
            # (businesses_named is NULL), so it can be picked up again.
            report.extraction_failed += 1
            continue

        named = decide_client([b.model_dump() for b in out.businesses], client_identity(business))
        recognized = any(b["is_client"] for b in named)
        store.save_extraction(
            conn, row["id"], businesses=named, named_any=bool(named), recognized=recognized,
            recommended=any(b["is_client"] and b["recommended"] for b in named))
        report.extracted += 1

        # Claim check: nothing to check against, or nothing said about the client -> no model call at all.
        if not business["facts"] or not recognized:
            report.claims_skipped += 1
            continue
        try:
            checked = run_agent(CLAIM_CHECK, business_id=business["id"], client=client, input_data={
                "client_name": business["name"], "client_aliases": list(business.get("aliases") or []),
                "client_domain": business["domain"],
                "answer": row["answer"], "confirmed_facts": business["facts"]})
        except AgentOutputInvalid:
            report.claim_check_failed += 1
            continue
        store.save_claims(conn, row["id"], [c.model_dump() for c in checked.claims])
        report.claims_checked += 1
    return report
