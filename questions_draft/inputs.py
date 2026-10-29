"""What `aeo questions draft` is allowed to know about the business (PLAN.md step 13b). Nothing else goes to the model.

  - With a truth document: its CONFIRMED facts, and nothing from the website.
  - Without one (a prospect): facts read from the site, every one marked "from website, unconfirmed", plus an excerpt
    of the site's own text.

The site comes from the latest diagnosis (its saved page, when the saved page is from that diagnosis), or, when there is
no diagnosis, from one fetch of the homepage. The caller is told which, in words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path

from core import client_files
from core.profiles import latest_profile
from diagnose.crawler import BROWSER_UA
from diagnose.fetch import Fetcher
from diagnose.html_utils import ParsedPage, parse_page
from truth import format as truth_format
from truth.draft import latest_diagnosis
from truth.prefill import from_diagnosis

CONFIRMED = "confirmed"
UNCONFIRMED = "from website, unconfirmed"
SITE_TEXT_WORDS = 2500                     # how much of the page's own text the model gets


class DraftInputError(Exception):
    """The draft cannot be made because there is nothing to base it on. The message says what to do."""


@dataclass
class DraftInputs:
    facts: list[dict] = field(default_factory=list)       # [{"id", "field", "value", "status"}]
    facts_source: str = ""                                # in words, for the model and for the file header
    truth_version: int | None = None
    site_text: str | None = None                          # only when no facts are confirmed
    page_source: str = ""                                 # where the site's text came from, in words
    diagnosis: dict | None = None
    page_language: str | None = None                      # from <html lang>, if the page says
    language_source: str = ""

    @property
    def confirmed_ids(self) -> set[int]:
        return {f["id"] for f in self.facts if f["status"] == CONFIRMED}

    @property
    def confirmed(self) -> bool:
        return bool(self.confirmed_ids)


def utc_day(when) -> str:
    """The diagnosis' date as `aeo diagnose` names its files: in UTC, whatever the database session's time zone is."""
    return when.astimezone(timezone.utc).date().isoformat()


def _page_language(parsed: ParsedPage | None) -> str | None:
    lang = (parsed.html_attrs.get("lang") if parsed else "") or ""
    primary = re.split(r"[-_]", lang.strip().lower())[0]
    return primary if re.fullmatch(r"[a-z]{2,3}", primary) else None


def _saved_page(domain: str, diagnosis: dict, clients_root: Path | None) -> Path | None:
    """The raw page the latest diagnosis saved, if the newest saved page is from the same day as that diagnosis."""
    folder = client_files.evidence_dir(domain, clients_root)
    day = utc_day(diagnosis["checked_at"])
    found = []
    for path in folder.glob(f"{day}_raw_page_run*.html"):
        match = re.search(r"_run(\d+)\.html$", path.name)
        if match:
            found.append((int(match.group(1)), path))
    return max(found)[1] if found else None


def gather_inputs(conn, business: dict, *, fetcher: Fetcher | None = None, clients_root: Path | None = None) -> DraftInputs:
    out = DraftInputs()
    profile = latest_profile(conn, business["id"])
    confirmed = [f for f in (profile["facts"] if profile else []) if f.get("status") == CONFIRMED and f.get("value")]
    out.diagnosis = latest_diagnosis(conn, business["id"])

    # ---- the site's page ----
    html, parsed = None, None
    if out.diagnosis:
        saved = _saved_page(business["domain"], out.diagnosis, clients_root)
        if saved:
            html = saved.read_text(encoding="utf-8", errors="replace")
            out.page_source = (f"the latest diagnosis ({out.diagnosis['checkpoint'] or 'no label'}, "
                               f"{utc_day(out.diagnosis['checked_at'])}) and the page it saved")
    if html is None:
        why = "there is no diagnosis for this business" if not out.diagnosis else "the latest diagnosis has no saved page"
        got = (fetcher or Fetcher()).get(business["website_url"], BROWSER_UA)
        if got.status == 200 and got.text.strip():
            html = got.text
            out.page_source = f"the homepage, fetched once just now ({why})"
        elif not confirmed:
            raise DraftInputError(
                f"There is nothing to base the draft on: {why}, and the homepage could not be fetched "
                f"({('HTTP ' + str(got.status)) if got.status else got.error}). Run `aeo diagnose {business['website_url']} "
                f"--business {business['domain']}` first, or confirm facts in a truth document.")
        else:
            out.page_source = "none (the homepage could not be fetched); only the confirmed facts are used"
    parsed = parse_page(html) if html else None
    out.page_language = _page_language(parsed)
    out.language_source = ("the page's lang attribute" if out.page_language else "no language is stated on the page")

    # ---- the facts ----
    if confirmed:
        out.truth_version = profile["version"]
        out.facts_source = f"confirmed by the client in the truth document (version {profile['version']})"
        out.facts = [{"id": i, "field": f["label"], "value": f["value"], "status": CONFIRMED}
                     for i, f in enumerate(confirmed, start=1)]
        return out

    out.facts_source = "from website, unconfirmed: read from the business's own website; the client has confirmed nothing"
    labels = {slot.key: slot.label for slot in truth_format.SLOTS}
    # An empty business here on purpose: what was typed at `aeo add` is not "from the website".
    # The structured data the diagnosis stored; with no diagnosis, what the homepage we just fetched carries.
    blocks = out.diagnosis["existing_jsonld"] if out.diagnosis else (parsed.jsonld_raw if parsed else [])
    values, _, _ = from_diagnosis({"existing_jsonld": blocks}, {"name": None, "website_url": None})
    facts = [(labels.get(key, key), value) for key, value in values.items()]
    if parsed:
        h1 = next((t for t in parsed.h1_texts if t), None)
        facts += [("Main heading of the page", h1), ("Meta description", parsed.meta.get("description"))]
        out.site_text = " ".join(parsed.main_text.split()[:SITE_TEXT_WORDS]) or None
    out.facts = [{"id": i, "field": field_, "value": value, "status": UNCONFIRMED}
                 for i, (field_, value) in enumerate(((f, v) for f, v in facts if v), start=1)]
    if not out.facts and not out.site_text:
        raise DraftInputError(
            "The site gave no readable text and no structured data to base questions on (a page built in the visitor's "
            "browser looks like this). Confirm facts in a truth document first, or add them by hand.")
    return out
