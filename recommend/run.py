"""`aeo recommend`: reads the diagnosis, the audit, the truth document and the live robots.txt/sitemap, and writes
files into clients/<domain>/generated/ for a person to deploy (PLAN.md section 3.3, step 19).

Nothing here changes a live site. No AI is used.

Files, all sharing one run number so they can be told apart from other days' runs:
    <date>_recommendations_runN.md / .json     the fix list
    <date>_robots_runN.txt                     the robots.txt to deploy
    <date>_llms_runN.txt                       the llms.txt to deploy
    <date>_schema_runN.json                    the JSON-LD (only if it passed validation)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from psycopg.types.json import Jsonb

from core import client_files
from core.db import get_connection
from core.journal import write_journal
from core.operator import operator_name
from core.profiles import confirmed_facts, latest_profile
from diagnose.fetch import Fetcher
from recommend import fixes as fx, routing
from recommend.audit_input import NoFinishedAudit, load_finished_audit
from recommend.jsonld import build_jsonld
from recommend.live import main_pages, read_live_site
from recommend.llms_file import build_llms
from recommend.render import render_markdown
from recommend.robots_file import TRAINING_MODES, build_robots

WRONG_FACTS, TARGETS = "wrong_fact", "target_question"


class RecommendError(Exception):
    """The recommendations cannot be made. The message says what to do."""


@dataclass
class RecommendResult:
    data: dict
    paths: dict[str, Path]
    recommendation_id: str
    robots_diff: str
    warnings: list[str] = field(default_factory=list)


def _load_diagnosis(conn, business_id: str, diagnosis_id: str | None) -> dict:
    cols = ("id, checkpoint, url, checked_at, platform, platform_plan, robots_txt, existing_jsonld, findings, "
            "verdict, verdict_reason")
    with conn.cursor() as cur:
        if diagnosis_id:
            cur.execute(f"SELECT {cols}, business_id FROM diagnoses WHERE id = %s", (diagnosis_id,))
        else:
            cur.execute(f"SELECT {cols}, business_id FROM diagnoses WHERE business_id = %s "
                        "ORDER BY checked_at DESC, created_at DESC, id LIMIT 1", (business_id,))
        r = cur.fetchone()
    if r is None or (diagnosis_id and str(r[-1]) != business_id):
        if diagnosis_id:
            raise RecommendError(f"diagnosis {diagnosis_id} does not exist for this business")
        raise RecommendError("this business has no diagnosis yet. Run `aeo diagnose <url> --business <id>` first.")
    names = ["id", "checkpoint", "url", "checked_at", "platform", "platform_plan", "robots_txt", "existing_jsonld",
             "findings", "verdict", "verdict_reason"]
    d = dict(zip(names, r))
    d["id"] = str(d["id"])
    d["findings"] = d["findings"] or []
    if d["verdict"] == "decline":
        raise RecommendError(f"the diagnosis verdict is 'decline' ({d['verdict_reason']}). There is nothing to recommend "
                             "on this platform; see the diagnosis report. A migration pitch needs a person's yes first.")
    return d


def _business_row(conn, business_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, domain, website_url FROM businesses WHERE id = %s", (business_id,))
        r = cur.fetchone()
    return {"id": str(r[0]), "name": r[1], "domain": r[2], "website_url": r[3]}


def make_recommendations(business: dict, *, run: str | None = None, diagnosis: str | None = None,
                         no_audit: bool = False, training_bots: str = "keep", fetcher: Fetcher | None = None,
                         clients_root: Path | None = None, today: date | None = None) -> RecommendResult:
    if training_bots not in TRAINING_MODES:
        raise RecommendError(f"--training-bots must be one of: {', '.join(TRAINING_MODES)}")
    if no_audit and run:
        raise RecommendError("give --run or --no-audit, not both")
    today = today or date.today()

    conn = get_connection()
    try:
        biz = _business_row(conn, business["id"])
        diag = _load_diagnosis(conn, biz["id"], diagnosis)

        audit = None
        if not no_audit:
            try:
                audit = load_finished_audit(conn, biz["id"], run)
            except NoFinishedAudit as exc:
                raise RecommendError(
                    f"{exc} The fix list is ordered by an audit, so it will not guess. Finish an audit "
                    "(`aeo audit`), or add --no-audit to get a list that says it was not prioritised by one.") from exc

        profile = latest_profile(conn, biz["id"])
        if profile is None:
            raise RecommendError("this business has no profile. Run `aeo truth draft` and `aeo truth import` first.")
        all_facts = profile["facts"]
        confirmed = confirmed_facts(all_facts)
        description = (profile["story"] or {}).get("description")

        site_url = biz["website_url"] or diag["url"]
        live = read_live_site(fetcher or Fetcher(), site_url, diag["robots_txt"])
        pages = main_pages(live.origin, live.sitemap_urls)
        robots = build_robots(live.robots_text, check_urls=pages, training=training_bots, sitemap_url=live.sitemap_url)
        llms = build_llms(business_name=biz["name"] or biz["domain"], facts=confirmed, description=description, pages=pages)
        schema = build_jsonld(confirmed, all_facts)

        platform, plan = diag["platform"], diag["platform_plan"]
        cdn = fx.cdn_from_findings(diag["findings"])
        content_route = routing.route(platform, plan, "content")

        access = fx.access_fixes(diag, cdn)
        wrong = fx.wrong_fact_fixes(audit, confirmed, biz["domain"], content_route) if audit else []
        targets = fx.target_question_fixes(audit, biz["domain"], content_route) if audit else []
        hygiene = fx.hygiene_fixes(diag, schema_status=schema.status, schema_errors=schema.errors, llms_written=True)
        hygiene_codes = {f["finding_code"] for f in hygiene if f["finding_code"]}
        covered = {f["finding_code"] for f in access} | hygiene_codes
        remaining = fx.remaining_fixes(diag, covered)
        all_fixes = fx.number(access + wrong + targets + remaining + hygiene)

        # File names are fixed before anything is written so the document can name them.
        folder = client_files.generated_dir(biz["domain"], clients_root)
        run_n = client_files.next_run_number(folder, "recommendations", "md")
        names = {"recommendations": client_files.dated_name(today, "recommendations", "md", run_n),
                 "recommendations_data": client_files.dated_name(today, "recommendations", "json", run_n),
                 "robots": client_files.dated_name(today, "robots", "txt", run_n),
                 "llms": client_files.dated_name(today, "llms", "txt", run_n)}
        if schema.document is not None:
            names["schema"] = client_files.dated_name(today, "schema", "json", run_n)

        data = {
            "tool": {"name": "aeo recommend"},
            "generated_on": today.isoformat(),
            "business": {"id": biz["id"], "name": biz["name"], "domain": biz["domain"]},
            "prioritised_by_audit": audit is not None,
            "inputs": {
                "diagnosis": {"id": diag["id"], "checked_at": diag["checked_at"].isoformat(), "url": diag["url"],
                              "checkpoint": diag["checkpoint"], "verdict": diag["verdict"], "platform": platform,
                              "platform_label": routing.platform_label(platform), "plan": plan, "cdn": cdn,
                              "findings": len(diag["findings"])},
                "audit": None if audit is None else {
                    "run_id": audit["run"]["id"], "checkpoint": audit["run"]["checkpoint"],
                    "answers_read": len(audit["answers"]), "question_tags_recorded": audit["tags_recorded"],
                    "target_questions": len(audit["targets"]), "profile_version_at_run": audit["run_profile_version"]},
                "truth_document": {"profile_version": profile["version"], "confirmed_facts": len(confirmed),
                                   "not_confirmed": len(schema.left_out)},
                "live_site": {"origin": live.origin, "robots_txt": live.robots_source,
                              "sitemap_pages_found": len(live.sitemap_urls), "main_pages": pages, "notes": live.notes},
            },
            "files": names,
            "robots": {"training_bots": training_bots, "built_from_live_file": robots.live_used,
                       "changes": robots.changes, "sitemap_line_added": robots.sitemap_added,
                       "notes": robots.notes, "diff": robots.diff, "may_fetch_after": robots.final_access},
            "llms": {"name_source": llms["name_source"], "facts_used": llms["used_facts"],
                     "description_from_story": bool(description and description.strip()),
                     "pages_listed": len(pages)},
            "jsonld": {"status": schema.status, "type": schema.business_type, "errors": schema.errors,
                       "notes": schema.notes, "left_out_because_not_confirmed": schema.left_out},
            "fixes": all_fixes,
            "off_site": {"available": audit is not None,
                         "domains": fx.off_site_domains(audit, biz["domain"]) if audit else []},
            "notes": ([] if audit is None or audit["tags_recorded"] else
                      ["The audit run did not record which questions are targets, so the target-question section is empty."]),
        }
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        data = json.loads(text)          # the markdown is made from exactly what the .json holds
        md = render_markdown(data)

        files = {names["recommendations"]: md, names["recommendations_data"]: text,
                 names["robots"]: robots.text, names["llms"]: llms["text"]}
        if schema.text is not None:
            files[names["schema"]] = schema.text
        paths = {}
        for key, name in names.items():
            paths[key] = client_files.write_new(folder, name, files[name].encode("utf-8"))

        recommendation_id = _save(conn, biz=biz, diag=diag, audit=audit, profile=profile, data=data, robots=robots,
                                  llms_text=llms["text"], schema=schema, names=names, wrong=wrong,
                                  others=[f for f in all_fixes if f["section"] != WRONG_FACTS])
        write_journal(
            business_id=biz["id"], entry_type="recommendations_generated", actor=operator_name(), conn=conn,
            description=("Recommendations written" + ("" if audit else " (not prioritised by an audit)")
                         + f": {len(all_fixes)} fix(es), robots.txt, llms.txt"
                         + (", schema.json" if schema.text else ", no schema.json (validation failed)")),
            related_table="recommendations", related_id=recommendation_id,
            details={"recommendation_id": recommendation_id, "diagnosis_id": diag["id"],
                     "probe_run_id": audit["run"]["id"] if audit else None, "profile_version": profile["version"],
                     "prioritised_by_audit": audit is not None, "training_bots": training_bots,
                     "fixes": len(all_fixes), "schema": schema.status,
                     "files": {k: f"generated/{p.name}" for k, p in paths.items()}})
    finally:
        conn.close()
    return RecommendResult(data=data, paths=paths, recommendation_id=recommendation_id, robots_diff=robots.diff,
                           warnings=list(live.notes))


def _save(conn, *, biz, diag, audit, profile, data, robots, llms_text, schema, names, wrong, others) -> str:
    schema_issues = [f for f in diag["findings"] if f["code"].startswith("SCHEMA_")]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO recommendations (business_id, business_profile_id, visibility_probe_run_id, priority_fixes, "
            "fact_fixes, off_site_sources, existing_jsonld, existing_schema_issues, generated_jsonld, llms_txt, "
            "validation_status, validation_errors, status, diagnosis_id, robots_txt, files, prioritised_by_audit) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s) RETURNING id",
            (biz["id"], profile["id"], audit["run"]["id"] if audit else None, Jsonb(others), Jsonb(wrong),
             Jsonb(data["off_site"]), Jsonb(diag["existing_jsonld"]) if diag["existing_jsonld"] is not None else None,
             Jsonb(schema_issues), Jsonb(schema.document) if schema.document is not None else None, llms_text,
             schema.status, Jsonb(schema.errors), diag["id"], robots.text, Jsonb(names), audit is not None))
        return str(cur.fetchone()[0])
