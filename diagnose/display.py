"""Showing repeated findings once (PLAN.md step 25c).

A diagnosis can hold several findings with the same code: four structured-data blocks with no areaServed, three crawlers
we could not connect as. Every place that LISTS findings for a person (the terminal, the diagnosis report, the client
report, the dossier) shows one line per code, with a count and up to three examples.

This is display only. The stored finding records are never changed, and compare still matches on the code alone, so
nothing here can change a result. `group_findings` returns new dicts and leaves the findings it is given untouched.
"""

from __future__ import annotations

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
MAX_EXAMPLES = 3
EXAMPLE_CHARS = 140

# How "{n} of these" reads for the codes that can repeat. Any other code reads "<what> (n times)".
PLURAL: dict[str, str] = {
    "SCHEMA_LOCALBUSINESS_NO_AREASERVED": "{n} structured-data blocks have no areaServed, so nothing says where the business works",
    "SCHEMA_ORG_MISSING_NAME": "{n} structured-data blocks for the business have no name at all",
    "SCHEMA_INVALID_JSON": "{n} structured-data blocks are not valid JSON, so crawlers will skip them",
    "SCHEMA_NAME_IS_LEGAL_ENTITY": "{n} structured-data blocks name the legal entity rather than the brand",
    "SCHEMA_OPENING_HOURS_EMPTY": "{n} structured-data blocks have opening hours with no actual hours",
    "SCHEMA_SAMEAS_HAS_TRACKING_PARAMS": "{n} sameAs lists carry tracking parameters",
    "CRAWLER_CONNECT_UNCONFIRMED": "Could not connect as {n} AI crawlers; may be temporary; re-run to confirm",
}


def _short(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= EXAMPLE_CHARS else text[: EXAMPLE_CHARS - 1] + "…"


def group_findings(findings: list[dict]) -> list[dict]:
    """One entry per code, in the order the codes first appear:
    {code, severity, what, count, examples, unconfirmed, findings}. `what` is the sentence to show; `examples` are up to
    three distinct pieces of evidence (only when there is more than one finding); `findings` are the originals."""
    groups: dict[str, list[dict]] = {}
    for f in findings:
        groups.setdefault(f["code"], []).append(f)
    out = []
    for code, items in groups.items():
        n = len(items)
        first = items[0]
        if n == 1:
            what, examples = first["what"], []
        else:
            what = PLURAL[code].format(n=n) if code in PLURAL else f"{first['what']} ({n} times)"
            seen, examples = set(), []
            for f in items:
                text = _short(f.get("evidence") or f["what"])
                if text not in seen:
                    seen.add(text)
                    examples.append(text)
            examples = examples[:MAX_EXAMPLES]
        out.append({"code": code, "count": n, "what": what, "examples": examples,
                    "severity": min((f["severity"] for f in items), key=SEVERITY_ORDER.index),      # the most serious one
                    "unconfirmed": all(f.get("unconfirmed") for f in items), "findings": items})
    return out
