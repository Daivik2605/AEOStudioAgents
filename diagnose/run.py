"""Runs one diagnosis from start to finish (PLAN.md section 3.1), in order:

  1. test crawler reachability (this also fetches the page as a browser)
  2. detect the platform           3. fetch robots.txt as a bot
  4. check the render              5. read existing structured data
  6. apply the decision rules      7. write findings
  8. (AI) explain the verdict, only if a business is given
  9. write the report: files always, database rows when the database is reachable
 10. on a decline, record it and stop

Nothing here sends, publishes or deploys anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from core.db import get_connection
from diagnose import explain, platform_data as pd, report, store
from diagnose.crawler import BROWSER_UA, challenge_reason, run_crawler_test
from diagnose.facts import Facts
from diagnose.fetch import Fetcher, RequestBudgetExceeded
from diagnose.findings import build_findings
from diagnose.html_utils import parse_page
from diagnose.platform_detect import detect
from diagnose.render import check_render
from diagnose.robots import check_robots
from diagnose.rules import Verdict, decide
from diagnose.structured_data import analyse


class DiagnoseError(Exception):
    """A problem the user can fix (bad URL, unknown business, database down)."""


@dataclass
class DiagnosisOutcome:
    facts: Facts
    verdict: Verdict
    findings: list[dict]
    readiness: float | None
    report_dir: Path
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
    reports_root: Path = report.REPORTS_ROOT,
    explain_client=None,
    use_ai: bool = True,
    now: datetime | None = None,
) -> DiagnosisOutcome:
    url = normalise_url(url)
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
        return _run(url, business, conn, checkpoint, fetcher or Fetcher(), reports_root,
                    explain_client, use_ai, now or datetime.now(timezone.utc), warnings)
    finally:
        if conn is not None:
            conn.close()


def _run(url, business, conn, checkpoint, fetcher, reports_root, explain_client, use_ai, now, warnings):
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
    if reachable:
        try:
            facts.robots = check_robots(fetcher, final_url, page.headers)
            origin = f"{urlsplit(final_url).scheme}://{urlsplit(final_url).netloc}"
            facts.llms = fetcher.get(f"{origin}/llms.txt", BROWSER_UA)
        except RequestBudgetExceeded as exc:
            warnings.append(f"Stopped early: {exc}.")

    # 4-5. Render check and structured data.
    if usable:
        b = business or {}
        facts.render = check_render(parsed, html, names=facts_names(business), phone=b.get("phone"),
                                    address=b.get("address"))
        facts.schema = analyse(parsed.jsonld_raw, facts.business_names)

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
                findings=findings, client=explain_client)
        except Exception as exc:  # noqa: BLE001 - a failed explanation must not lose the diagnosis
            warnings.append(f"The model-written summary failed ({exc}); the report has the findings without it.")

    # 9. Save. The database is the record, the files are for people; both get written.
    data = report.build_json(
        facts=facts, verdict=verdict, findings=findings, readiness=readiness, checkpoint=checkpoint,
        checked_at=now, ai_explanation=ai_text, requests_made=fetcher.requests_made)
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
                verdict=verdict.verdict, verdict_reason=verdict.reason, readiness_score=readiness))
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
    # structured-data extraction just read.
    folder, run_number = report.write_report(data=data, slug=slug, checkpoint=checkpoint, day=now.date(),
                                             raw_page=page.body or None, root=reports_root)

    if db_error is not None:
        if business:
            raise DiagnoseError(f"report files were written to {folder}, but the database write failed: {db_error}") from db_error
        warnings.append(f"Database not reachable ({db_error}); the report was written to files only.")

    return DiagnosisOutcome(facts=facts, verdict=verdict, findings=findings, readiness=readiness,
                            report_dir=folder, data=data, diagnosis_id=diagnosis_id,
                            ai_explanation=ai_text, decline_result=decline_result, warnings=warnings,
                            run_number=run_number)


def facts_names(business: dict | None) -> list[str] | None:
    if not business:
        return None
    names = [n for n in [business.get("name"), *(business.get("alternate_names") or [])] if n]
    return names or None
