"""Runs one diagnosis from start to finish (PLAN.md section 3.1), in order:

  1. test crawler reachability (this also fetches the page as a browser)
  2. detect the platform           3. fetch robots.txt as a bot
  4. check the render              5. read existing structured data
  6. apply the decision rules      7. write findings
  8. (AI) explain the verdict, only if a business is given
  9. write the report: files always (clients/<domain>/), database rows when the database is reachable
 10. on a decline, record it and stop

and, between 3 and 4 (PLAN.md step 25): the CDN / host / framework seen in the headers, the sitemap check, the
www / http address check, and the old-address check when `old_urls` is given. These use their own request count
(see Fetcher.sibling), so the budget of the main diagnosis is unchanged.

Nothing here sends, publishes or deploys anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from core.checkpoints import CheckpointError, check_checkpoint
from core.db import get_connection
from diagnose import explain, platform_data as pd, report, store
from diagnose.crawler import BROWSER_UA, challenge_reason, looks_like_bot_check, run_crawler_test
from diagnose.facts import Facts
from diagnose.fetch import Fetcher, RequestBudgetExceeded
from diagnose.findings import build_findings
from diagnose.html_utils import parse_page
from diagnose.not_checked import assess_block, blocked_entry, entry
from diagnose.infrastructure import detect_infrastructure
from diagnose.platform_detect import detect
from diagnose.render import check_render
from diagnose.robots import check_robots
from diagnose.rules import Verdict, decide
from diagnose.site_checks import (MAX_OLD_URL_REQUESTS, SITE_CHECK_INTERVAL, SITE_CHECK_REQUESTS, SiteCheckError,
                                  SiteChecks, check_hosts, check_old_urls, check_sitemap, load_old_urls)
from diagnose.structured_data import analyse


class DiagnoseError(Exception):
    """A problem the user can fix (bad URL, unknown business, database down)."""


@dataclass
class DiagnosisOutcome:
    facts: Facts
    verdict: Verdict
    findings: list[dict]
    readiness: float | None
    report_dir: Path                      # clients/<domain>/generated
    report_path: Path                     # the .md report inside it
    data: dict
    diagnosis_id: str | None = None
    ai_explanation: str | None = None
    decline_result: str | None = None     # declined | already_declined | not_a_prospect | None
    warnings: list[str] = field(default_factory=list)
    run_number: int = 1


def normalise_url(raw: str) -> str:
    url = raw.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise DiagnoseError(f"'{raw}' is not a web address I can check (need http:// or https://)")
    return url


def run_diagnosis(
    url: str,
    *,
    business_id: str | None = None,
    checkpoint: str | None = None,
    fetcher: Fetcher | None = None,
    clients_root: Path | None = None,     # where clients/ is; only tests change it
    explain_client=None,
    use_ai: bool = True,
    now: datetime | None = None,
    old_urls: str | Path | None = None,    # a file of old addresses, one per line; without it that check does not run
) -> DiagnosisOutcome:
    url = normalise_url(url)
    try:
        check_checkpoint(checkpoint)          # before any request is made
    except CheckpointError as exc:
        raise DiagnoseError(str(exc)) from exc
    if old_urls is not None and not Path(old_urls).is_file():       # before any request is made
        raise DiagnoseError(f"the old-address file {old_urls} does not exist")
    warnings: list[str] = []

    # A business needs the database (to check it exists and to log the work).
    business = None
    conn = None
    if business_id:
        try:
            conn = get_connection()
            business = store.load_business(conn, business_id)
        except Exception as exc:  # noqa: BLE001 - tell the user plainly, whatever went wrong
            raise DiagnoseError(f"could not look up business {business_id}: {exc}") from exc
        if business is None:
            conn.close()
            raise DiagnoseError(f"no business with id {business_id}")

    try:
        return _run(url, business, conn, checkpoint, fetcher or Fetcher(), clients_root,
                    explain_client, use_ai, now or datetime.now(timezone.utc), warnings, old_urls)
    finally:
        if conn is not None:
            conn.close()


def _run(url, business, conn, checkpoint, fetcher, clients_root, explain_client, use_ai, now, warnings, old_urls=None):
    # 1-2. Crawler test (its browser fetch doubles as the page we analyse).
    crawler = run_crawler_test(fetcher, url)
    page = crawler.control
    reachable = page.status is not None and not page.error
    final_url = page.final_url or url
    if reachable and urlsplit(final_url).hostname != urlsplit(url).hostname:
        warnings.append(f"{url} redirected to {final_url}; the checks ran against the redirected address.")

    html = page.text if reachable else ""
    parsed = parse_page(html)
    detection = detect(headers=page.headers, html=html, final_url=final_url, page=parsed)

    facts = Facts(url=url, page=page, detection=detection, business=business, crawler=crawler,
                  site_reachable=reachable, jsonld_raw=parsed.jsonld_raw)

    # Only analyse the page itself if we actually got a page (not an error or a challenge).
    usable = reachable and page.status < 400 and not challenge_reason(page)

    # 3. robots.txt as a bot, and /llms.txt.
    llms_blocked = None
    if reachable:
        try:
            facts.robots = check_robots(fetcher, final_url, page.headers)
        except RequestBudgetExceeded as exc:
            warnings.append(f"Stopped early: {exc}.")

    # Were we blocked? If so, nothing that depends on reading the site may be claimed (step 25c): the checks are
    # skipped, listed under "Not checked", and left out of `checks_ran` so that compare does not call them fixed or new.
    blocked = assess_block(page, facts.crawler, facts.robots) if reachable else None
    if reachable and blocked is None:
        try:
            origin = f"{urlsplit(final_url).scheme}://{urlsplit(final_url).netloc}"
            facts.llms = fetcher.get(f"{origin}/llms.txt", BROWSER_UA)
            llms_blocked = looks_like_bot_check(facts.llms)
            if llms_blocked:
                facts.llms = None
        except RequestBudgetExceeded as exc:
            warnings.append(f"Stopped early: {exc}.")

    # 3b. Step 25. What is in front of the site (information only), then the checks that apply to any website.
    facts.infrastructure = detect_infrastructure(page.headers) if reachable else None
    if reachable:
        facts.site_checks = run_site_checks(fetcher, facts, final_url, old_urls, warnings, blocked=blocked)
        if blocked is None and not usable:
            # The page did not load (a 404 or a server error, not a bot check): there is nothing to read.
            why = f"the page returned HTTP {page.status}, so we couldn't read it"
            facts.site_checks.not_checked += [entry("render", why), entry("structured_data", why)]
        elif blocked is not None:
            facts.site_checks.not_checked += [blocked_entry(c, blocked) for c in ("render", "structured_data", "llms")]
        if llms_blocked:
            facts.site_checks.not_checked.append(blocked_entry("llms", llms_blocked))
        elif blocked is None and facts.llms is not None:
            facts.site_checks.content_ran.append("llms")

    # 4-5. Render check and structured data.
    if usable and blocked is None:
        b = business or {}
        facts.render = check_render(parsed, html, names=facts_names(business), phone=b.get("phone"),
                                    address=b.get("address"))
        facts.schema = analyse(parsed.jsonld_raw, facts.business_names)
        facts.site_checks.content_ran += ["render", "structured_data"]

    # 6-7. Rules decide; findings explain.
    verdict = decide(facts)
    findings = build_findings(facts)
    readiness = pd.readiness_score(detection.platform, detection.plan, verdict.verdict == "decline")

    # 8. The one AI step. Only with a business, because the spend has to be logged against one.
    ai_text = None
    if business and use_ai:
        try:
            ai_text = explain.explain_verdict(
                business_id=business["id"], url=url, verdict=verdict.verdict, reason=verdict.reason,
                findings=findings, not_checked=(facts.site_checks.not_checked if facts.site_checks else []),
                client=explain_client)
        except Exception as exc:  # noqa: BLE001 - a failed explanation must not lose the diagnosis
            warnings.append(f"The model-written summary failed ({exc}); the report has the findings without it.")

    # 9. Save. The database is the record, the files are for people; both get written.
    data = report.build_json(
        facts=facts, verdict=verdict, findings=findings, readiness=readiness, checkpoint=checkpoint,
        checked_at=now, ai_explanation=ai_text,
        requests_made=fetcher.requests_made + (facts.site_checks.requests_made if facts.site_checks else 0))
    slug = report.slugify_domain(business["domain"] if business else final_url)

    diagnosis_id = decline_result = None
    db_error: Exception | None = None
    try:
        own_conn = conn is None
        db = conn or get_connection()
        try:
            diagnosis_id = str(store.insert_diagnosis(
                db, business_id=business["id"] if business else None, checkpoint=checkpoint, url=url,
                checked_at=now, platform=detection.platform, platform_version=detection.version,
                platform_plan=detection.plan, robots_txt=facts.robots.raw if facts.robots else None,
                existing_jsonld=parsed.jsonld_raw, crawler_access=crawler.to_json(),
                render=facts.render.to_json() if facts.render else None, findings=findings,
                verdict=verdict.verdict, verdict_reason=verdict.reason, readiness_score=readiness,
                detected_cdn=facts.infrastructure.cdn if facts.infrastructure else None,
                detected_host=facts.infrastructure.host if facts.infrastructure else None,
                detected_framework=facts.infrastructure.framework if facts.infrastructure else None,
                site_checks=facts.site_checks.to_json() if facts.site_checks else None))
            if business:
                store.journal_diagnosed(db, business_id=business["id"], diagnosis_id=diagnosis_id, url=url,
                                        verdict=verdict.verdict, platform=detection.platform,
                                        finding_count=len(findings))
                # 10. Decline: record it, then stop. No pitch is generated.
                if verdict.verdict == "decline":
                    decline_result = store.apply_decline(db, business_id=business["id"],
                                                        diagnosis_id=diagnosis_id, reason=verdict.reason)
                    store.journal_decline_recommended(db, business_id=business["id"],
                                                      diagnosis_id=diagnosis_id, reason=verdict.reason)
        finally:
            if own_conn:
                db.close()
    except Exception as exc:  # noqa: BLE001
        db_error = exc

    data["diagnosis_id"] = diagnosis_id
    # The exact bytes of the no-JavaScript control fetch: the same page the render check and
    # structured-data extraction just read. It goes to evidence/ (it came in); the report to generated/.
    old_urls_evidence = (facts.site_checks.old_urls.to_json(full=True)
                         if facts.site_checks and facts.site_checks.old_urls else None)
    folder, run_number, report_path = report.write_report(data=data, slug=slug, day=now.date(),
                                                          raw_page=page.body or None, root=clients_root,
                                                          old_urls=old_urls_evidence)

    if db_error is not None:
        if business:
            raise DiagnoseError(f"report files were written to {folder}, but the database write failed: {db_error}") from db_error
        warnings.append(f"Database not reachable ({db_error}); the report was written to files only.")

    return DiagnosisOutcome(facts=facts, verdict=verdict, findings=findings, readiness=readiness,
                            report_dir=folder, report_path=report_path, data=data,
                            diagnosis_id=diagnosis_id,
                            ai_explanation=ai_text, decline_result=decline_result, warnings=warnings,
                            run_number=run_number)


def run_site_checks(fetcher: Fetcher, facts: Facts, final_url: str, old_urls, warnings: list[str],
                    blocked: str | None = None) -> SiteChecks:
    """The step 25 checks, on the address the diagnosis ended at. A failure to connect is a result, not an error.

    When we were blocked, no request is made at all: every check is recorded as not checked. A check whose own requests
    come back as a bot check is also dropped (not just left without findings), so its absence means "not checked"."""
    parts = urlsplit(final_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    interval = min(fetcher.min_interval, SITE_CHECK_INTERVAL)
    checks = SiteChecks()
    if blocked is not None:
        wanted = ("sitemap", "hosts") + (("old_urls",) if old_urls is not None else ())
        checks.not_checked += [blocked_entry(c, blocked) for c in wanted]
        return checks
    robots = facts.robots
    robots_ok = robots is not None and robots.outcome in ("ok", "missing")
    quick = fetcher.sibling(max_requests=SITE_CHECK_REQUESTS, min_interval=interval)
    checks.sitemap = check_sitemap(quick, origin, robots.raw if robots_ok else None, robots_ok)
    checks.hosts = check_hosts(quick, parts.hostname or "")
    checks.requests_made = quick.requests_made
    for name in ("sitemap", "hosts"):
        why = getattr(getattr(checks, name), "blocked", None)
        if why:
            checks.not_checked.append(blocked_entry(name, why))
            setattr(checks, name, None)
    if old_urls is not None:
        try:
            urls, info = load_old_urls(old_urls, origin)
        except SiteCheckError as exc:
            raise DiagnoseError(str(exc)) from exc
        if info["over_cap"]:
            warnings.append(f"The old-address file has {info['total_in_file']} addresses; only the first "
                            f"{len(urls)} were checked (the limit).")
        if info["ignored_lines"]:
            warnings.append(f"{len(info['ignored_lines'])} line(s) of the old-address file were skipped because they are "
                            "neither a full address nor a path starting with /.")
        slow = fetcher.sibling(max_requests=MAX_OLD_URL_REQUESTS, min_interval=interval)
        checks.old_urls = check_old_urls(slow, urls, info)
        checks.requests_made += slow.requests_made
    return checks


def facts_names(business: dict | None) -> list[str] | None:
    if not business:
        return None
    names = [n for n in [business.get("name"), *(business.get("alternate_names") or [])] if n]
    return names or None
