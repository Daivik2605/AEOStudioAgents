"""Turns the tool's internal values into the client's words (PLAN.md step 22b).

The report data keeps the internal values (finding codes, checkpoint labels, platform keys, evidence types), because
they are the record and the comparison needs them. Only the page the client reads goes through here, and every
sentence it uses comes from client_report/strings.py, so another language is a new dictionary and no code change.

Nothing here invents a value. A value that is not in the finding's own details is left out and a sentence that
does not need it is used instead (the `.fallback` strings).
"""

from __future__ import annotations

import json
import re

from client_report.strings import STRINGS, t

WEB_DEV = ("web_dev_aeo", "web_dev_only")
# The name inside a stored finding's evidence text, e.g.  "name": "11297775 Canada Inc"
_NAME_IN_EVIDENCE = re.compile(r'"name":\s*("(?:[^"\\]|\\.)*")')


def schema_name(finding: dict) -> str | None:
    """The business name the website's structured data gave, read from the finding's own evidence text."""
    m = _NAME_IN_EVIDENCE.search(finding.get("evidence") or "")
    if not m:
        return None
    try:
        name = json.loads(m.group(1))
    except ValueError:
        return None
    return name.strip() or None


class Words:
    def __init__(self, report: dict):
        self.lang = report["tool"]["language"]
        self.business = report["business"]["name"] or report["business"]["domain"]
        self.engagement = report.get("engagement")
        self.label_dates = report.get("label_dates") or {}
        self.labels = report["labels"]

    def t(self, key: str, **kw) -> str:
        return t(key, self.lang, **kw)

    def has(self, key: str) -> bool:
        return key in STRINGS[self.lang]

    # ---- checkpoint labels ---------------------------------------------------------------------------------------
    def label(self, label: str | None, date: str | None = None) -> str:
        """'before' -> 'your previous website', and so on. `date` is the day of that checkpoint (used by 'check')."""
        if not label:
            return ""
        if label == "before":
            return self.t("label.before.web" if self.engagement in WEB_DEV else "label.before.other")
        if label == "check":
            return self.t("label.check", date=date) if date else self.t("label.check.nodate")
        return self.t("label." + label)

    def side(self, which: str) -> str:
        """The client's words for side 'a' or 'b' of the report (its labels and dates are in the report data)."""
        return self.label(self.labels[which], self.label_dates.get(which))

    # ---- platforms and evidence ----------------------------------------------------------------------------------
    def platform(self, key: str | None) -> str:
        if not key:
            return self.t("platform.none")
        k = "platform." + key
        return self.t(k) if self.has(k) else key.replace("_", " ").title()

    def platform_phrase(self, key: str | None) -> str:
        """For use inside a sentence: the name, or 'your website builder' when it is not known."""
        return self.platform(key) if key else self.t("platform.unknown")

    def evidence_type(self, key: str | None) -> str:
        if not key:
            return self.t("evtype.other")
        k = "evtype." + key
        return self.t(k) if self.has(k) else key.replace("_", " ").capitalize()

    def verdict(self, v: str | None) -> str:
        return self.t("health.verdict_short." + v) if v and self.has("health.verdict_short." + v) else "-"

    def headline(self, d: dict) -> str:
        """'8 of 9 closed · 1 new'. New findings are always part of the headline."""
        closed, total, new = len(d["closed"]), d["a_total"], len(d["new"])
        if total == 0:
            return self.t("health.headline.none_new", new=new) if new else self.t("health.headline.none")
        if new:
            return self.t("health.headline.closed_new", closed=closed, total=total, new=new)
        return self.t("health.headline.closed", closed=closed, total=total)

    # ---- findings and fixes --------------------------------------------------------------------------------------
    def finding(self, finding: dict, platform: str | None = None) -> str:
        """One sentence for the client. `finding` has at least a `code`; `evidence` fills in values when it carries them."""
        code = finding["code"]
        key = "finding." + code
        if not self.has(key):
            return self.t("finding.UNKNOWN")
        values = {"business": self.business, "platform": self.platform_phrase(platform)}
        fallback = key + ".fallback"
        if self.has(fallback):
            name = schema_name(finding)
            if name is None:
                return self.t(fallback, **values)
            values["name"] = name
        return self.t(key, **values)

    def fix_title(self, fix: dict, platform: str | None = None) -> str:
        """The client's title for one recommended fix. The internal title stays in the recommendations file."""
        code, section = fix.get("finding_code"), fix.get("section")
        values = {"platform": self.platform_phrase(platform)}
        if section == "hygiene":
            if code == "LLMS_TXT_MISSING":
                return self.t("fix.LLMS_TXT_MISSING")
            return self.t("fix.hyg.schema_deploy" if fix.get("file") == "schema" else "fix.hyg.confirm_facts")
        if code == "AUDIT_WRONG_FACT" and fix.get("quote"):
            return self.t("fix.AUDIT_WRONG_FACT.quote", quote=fix["quote"])
        if code == "AUDIT_TARGET_QUESTION_GAP" and fix.get("question"):
            return self.t("fix.AUDIT_TARGET_QUESTION_GAP.question", question=fix["question"])
        return self.t("fix." + code, **values) if code and self.has("fix." + code) else self.t("fix.generic")
