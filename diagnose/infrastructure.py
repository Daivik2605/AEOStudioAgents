"""What CDN, host and framework a site seems to use, from the response headers of its main page (PLAN.md step 25).

The rules are data (diagnose/infrastructure_rules.yaml), not code, so a rule can be added without touching Python.
This is information, never a finding: a site behind Cloudflare is not "wrong". Any of the three may be unknown (None).

Overlap, noted on purpose: diagnose/platform_detect.py has its own HOST_RULES for Cloudflare, Vercel and WP Engine, which
feed the decline and conditional rules. This file does not replace them and they are not merged in this step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

RULES_FILE = Path(__file__).parent / "infrastructure_rules.yaml"
KINDS = ("cdn", "host", "framework")


class InfrastructureRulesError(Exception):
    """The rules file is malformed. Raised when it is loaded, so a typo is found at once."""


@dataclass(frozen=True)
class InfraRule:
    header: str
    pattern: str
    label: str
    kind: str


@dataclass
class Infrastructure:
    cdn: str | None = None
    host: str | None = None
    framework: str | None = None
    evidence: dict[str, str] = field(default_factory=dict)    # kind -> "cf-ray: 8a1b..." (the header that fired)

    def to_json(self) -> dict:
        return {"cdn": self.cdn, "host": self.host, "framework": self.framework, "evidence": self.evidence}

    def summary(self) -> str:
        return " · ".join(f"{k.upper() if k == 'cdn' else k.capitalize()}: {getattr(self, k) or 'unknown'}" for k in KINDS)


def load_rules(path: Path | None = None) -> list[InfraRule]:
    path = Path(path or RULES_FILE)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InfrastructureRulesError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise InfrastructureRulesError(f"{path.name} must have a 'rules:' list")
    rules = []
    for i, entry in enumerate(data["rules"], start=1):
        if not isinstance(entry, dict):
            raise InfrastructureRulesError(f"{path.name}: rule {i} is not a mapping")
        extra = set(entry) - {"header", "pattern", "label", "kind"}
        missing = {"header", "label", "kind"} - set(entry)
        if extra or missing:
            raise InfrastructureRulesError(f"{path.name}: rule {i} has "
                                           + (f"unknown fields {sorted(extra)}" if extra else f"no {sorted(missing)}"))
        if entry["kind"] not in KINDS:
            raise InfrastructureRulesError(f"{path.name}: rule {i} has kind {entry['kind']!r}; use one of {', '.join(KINDS)}")
        pattern = entry.get("pattern") or ""
        try:
            re.compile(pattern)
        except re.error as exc:
            raise InfrastructureRulesError(f"{path.name}: rule {i} has a bad pattern ({exc})") from exc
        rules.append(InfraRule(str(entry["header"]).lower(), pattern, str(entry["label"]), entry["kind"]))
    return rules


def detect_infrastructure(headers: dict[str, str], rules: list[InfraRule] | None = None) -> Infrastructure:
    """`headers` are lowercase-named, as FetchResult.headers gives them. The first rule to fire for a kind wins."""
    rules = load_rules() if rules is None else rules
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    found = Infrastructure()
    for rule in rules:
        if getattr(found, rule.kind) is not None or rule.header not in lowered:
            continue
        value = lowered[rule.header]
        if rule.pattern and not re.search(rule.pattern, value, re.IGNORECASE):
            continue
        setattr(found, rule.kind, rule.label)
        found.evidence[rule.kind] = f"{rule.header}: {value}"[:120]
    return found
