"""Existing structured data (PLAN.md section 3.1 step 5).

The raw JSON-LD blocks are stored exactly as found (see html_utils). This
module only READS them to produce issues; it never rewrites, tidies or
re-serialises anything. If we improve a check later we re-run it on the
stored raw blocks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from diagnose.render import normalise

# schema.org types. Not exhaustive: it covers the types small businesses use most.
LOCAL_BUSINESS_TYPES = {
    "LocalBusiness", "Dentist", "Physician", "MedicalClinic", "Restaurant", "Store",
    "ProfessionalService", "LegalService", "Attorney", "AccountingService", "RealEstateAgent",
    "HomeAndConstructionBusiness", "AutoRepair", "HealthAndBeautyBusiness", "HairSalon",
    "BeautySalon", "DaySpa", "FoodEstablishment", "Bakery", "CafeOrCoffeeShop", "BarOrPub",
    "LodgingBusiness", "Hotel", "SportsActivityLocation", "Plumber", "Electrician",
    "GeneralContractor", "RoofingContractor", "MovingCompany", "FinancialService",
    "InsuranceAgency", "TravelAgency", "AutomotiveBusiness",
}
ORG_TYPES = LOCAL_BUSINESS_TYPES | {
    "Organization", "Corporation", "OnlineStore", "OnlineBusiness", "NGO",
    "EducationalOrganization", "GovernmentOrganization", "MedicalOrganization",
    "SportsOrganization", "Airline", "Consortium",
}
ENTITY_TYPES = ORG_TYPES | {"WebSite", "Person"}

_LEGAL_SUFFIX = re.compile(r"\b(inc|ltd|llc|corp|corporation|gmbh|sarl|ltee|ltée|limited)\b\.?\s*$", re.I)
_NUMBERED_COMPANY = re.compile(r"^\d{5,}\b")


@dataclass
class SchemaIssue:
    code: str
    what: str
    evidence: str


@dataclass
class SchemaAnalysis:
    blocks: list[dict] = field(default_factory=list)   # {valid, error, types} per raw block
    issues: list[SchemaIssue] = field(default_factory=list)


def _types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    raw = [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]
    return [t.rsplit("/", 1)[-1] for t in raw]


def _walk(obj: Any) -> Iterator[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _brief(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)[:160]


def analyse(raw_blocks: list[str], business_names: list[str] | None = None) -> SchemaAnalysis:
    out = SchemaAnalysis()
    if not raw_blocks:
        out.issues.append(SchemaIssue(
            "SCHEMA_NONE_FOUND", "The page has no JSON-LD structured data", "no <script type=\"application/ld+json\"> found"))
        return out

    parsed: list[Any] = []
    for i, raw in enumerate(raw_blocks):
        try:
            data = json.loads(raw)
        except ValueError as exc:
            out.blocks.append({"index": i, "valid": False, "error": str(exc), "types": []})
            out.issues.append(SchemaIssue(
                "SCHEMA_INVALID_JSON", f"JSON-LD block {i + 1} is not valid JSON, so crawlers will skip it",
                f"{exc}; starts: {raw.strip()[:80]!r}"))
            continue
        parsed.append(data)
        types = sorted({t for n in _walk(data) for t in _types(n)})
        out.blocks.append({"index": i, "valid": True, "error": None, "types": types})

    nodes = [n for data in parsed for n in _walk(data)]
    # A node that is only {"@type", "@id"} is a pointer to an entity defined elsewhere, not a
    # block to check.
    org_nodes = [n for n in nodes if set(_types(n)) & ORG_TYPES and not set(n) <= {"@id", "@type"}]
    known_names = [normalise(n) for n in (business_names or []) if n]

    for node in org_nodes:
        types = "/".join(_types(node))
        name = node.get("name")
        if not isinstance(name, str) or not name.strip():
            extra = f"; it does have legalName {_brief(node['legalName'])}" if node.get("legalName") else ""
            out.issues.append(SchemaIssue(
                "SCHEMA_ORG_MISSING_NAME", f"A block of type {types} has no 'name' at all{extra}",
                f"keys: {sorted(k for k in node if not k.startswith('@'))}"))
        else:
            numbered = bool(_NUMBERED_COMPANY.match(name.strip()))
            legal_vs_brand = (bool(_LEGAL_SUFFIX.search(name)) and known_names
                              and not any(k and k in normalise(name) for k in known_names))
            if numbered or legal_vs_brand:
                out.issues.append(SchemaIssue(
                    "SCHEMA_NAME_IS_LEGAL_ENTITY",
                    f"Structured data names the business {name!r}, which looks like the legal entity rather than the brand",
                    f'"name": {_brief(name)}'))
        if set(_types(node)) & LOCAL_BUSINESS_TYPES and not node.get("areaServed"):
            out.issues.append(SchemaIssue(
                "SCHEMA_LOCALBUSINESS_NO_AREASERVED",
                f"A block of type {types} has no areaServed, so nothing says where the business actually works",
                f"types {types}; address: {_brief(node.get('address'))}"))

    for node in nodes:
        hours = node.get("openingHours")
        if hours is not None:
            items = hours if isinstance(hours, list) else [hours]
            if items and all(isinstance(h, str) and not re.sub(r"[\s,]", "", h) for h in items):
                out.issues.append(SchemaIssue(
                    "SCHEMA_OPENING_HOURS_EMPTY", "openingHours is present but holds no actual hours",
                    f'"openingHours": {_brief(hours)}'))
        same_as = node.get("sameAs")
        if same_as is not None:
            urls = [same_as] if isinstance(same_as, str) else [u for u in same_as if isinstance(u, str)]
            tracked = [u for u in urls if "?" in u]
            if tracked:
                out.issues.append(SchemaIssue(
                    "SCHEMA_SAMEAS_HAS_TRACKING_PARAMS",
                    "sameAs links carry query strings (tracking parameters), so they are not clean profile URLs",
                    "; ".join(tracked)[:200]))

    # Entities that never point at each other leave a crawler guessing whether
    # they are one business or several.
    entities = [n for n in nodes if set(_types(n)) & ENTITY_TYPES]
    ids = {n["@id"] for n in entities if isinstance(n.get("@id"), str)}
    referenced = any(
        set(n) == {"@id"} and n["@id"] in ids for n in nodes
    )
    if len(entities) >= 2 and not referenced:
        names = ", ".join("/".join(_types(n)) for n in entities)
        out.issues.append(SchemaIssue(
            "SCHEMA_ENTITIES_UNLINKED",
            f"{len(entities)} entity blocks that do not reference each other by @id",
            names[:200]))
    return out
