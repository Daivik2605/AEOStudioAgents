"""Tests for `aeo recommend` (PLAN.md step 19). Real Postgres (aeo_test), a fake website (no network), no AI."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timezone

import httpx
import pytest
from protego import Protego
from typer.testing import CliRunner

from aeo import cli
from core import evidence as ev
from core.db import get_connection
from core.profiles import confirmed_facts, save_new_version
from diagnose import store as dstore
from diagnose.codes import REGISTRY, make_finding
from recommend import routing
from recommend.jsonld import build_jsonld, validate_jsonld
from recommend.llms_file import build_llms
from recommend.render import render_markdown
from recommend.robots_file import build_robots
from recommend.run import RecommendError, make_recommendations
from tests.helpers_diagnose import make_fetcher, response, site
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_audit_report import CLIENT, SQ, build_run, claim, link, nb, snapshot_specs

TODAY = date(2026, 10, 16)
SEARCH_BOTS = ("OAI-SearchBot", "PerplexityBot", "Claude-SearchBot")
URLS = ["https://a.example/", "https://a.example/about"]

FULL_FACTS = {
    "Name_as_customers_know_it": "Acme Co", "Location_1_address": "12 Rue Principale, Montreal QC",
    "Location_1_phone": "514-555-0100", "Location_1_hours": "Mon-Fri 9:00-17:00",
    "Website": "https://acme.example", "Services": "Water refill; event rentals", "Areas_we_serve": "Montreal; Laval",
}


@pytest.fixture
def conn():
    c = get_connection()
    yield c
    c.close()


def finding(code, severity=None, *, fixable=None, where="fix it", what=None):
    return make_finding(code, what=what or f"{code} happened", evidence="e", fixable_on_platform=fixable,
                        where_to_fix=where, source="test", severity=severity)


def add_diagnosis(biz, findings, *, platform="wordpress", plan=None, verdict="serve", robots_txt=None,
                  checked=None, jsonld=None):
    conn = get_connection()
    try:
        return str(dstore.insert_diagnosis(
            conn, business_id=biz["id"], checkpoint="before", url=f"https://{biz['domain']}/",
            checked_at=checked or datetime(2026, 10, 1, tzinfo=timezone.utc), platform=platform,
            platform_version=None, platform_plan=plan, robots_txt=robots_txt, existing_jsonld=jsonld or [],
            crawler_access={}, render=None, findings=findings, verdict=verdict, verdict_reason="because",
            readiness_score=None))
    finally:
        conn.close()


def set_profile(biz, *, story=None, **confirmed):
    entries = [{"label": k.replace("_", " "), "value": v, "status": "confirmed", "confirmed_by": "Jean",
                "confirmed_on": "2026-10-01"} for k, v in confirmed.items()]
    conn = get_connection()
    try:
        return save_new_version(conn, biz["id"], facts=entries, story=story or {})
    finally:
        conn.close()


def fake_site(robots="User-agent: *\nDisallow:\n", sitemap=True, seen=None):
    paths = ["/", "/about", "/services", "/contact", "/blog/a/b/c", "/img/x.jpg", "/p?id=2"]

    def serve(request):
        if seen is not None:
            seen.append((request.method, str(request.url)))
        return None

    def handler(request):
        serve(request)
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text=robots, headers={"content-type": "text/plain"}) if robots is not None else response(404, "no")
        if path == "/sitemap.xml" and sitemap:
            host = request.url.host      # the sitemap lists the client's own host, as a real one does
            sm = "<urlset>" + "".join(f"<url><loc>https://{host}{p}</loc></url>" for p in paths) + "</urlset>"
            return httpx.Response(200, text=sm, headers={"content-type": "application/xml"})
        return response(404, "nope")
    return make_fetcher(handler)


def rec(biz, tmp_path, *, fetcher=None, **kw):
    return make_recommendations(biz, fetcher=fetcher or fake_site(), clients_root=tmp_path / "clients", today=TODAY, **kw)


def fix_ids(res, section=None):
    return [f["id"] for f in res.data["fixes"] if section is None or f["section"] == section]


def codes(res):
    return [f["finding_code"] for f in res.data["fixes"]]


def can_fetch(text, token, url="https://a.example/"):
    return Protego.parse(text).can_fetch(url, token)


# ---- robots.txt ----------------------------------------------------------------------------------------------------------------

LIVE_WILDCARD = "User-agent: *\nDisallow: /private\nDisallow: /\n\nUser-agent: Bingbot\nAllow: /\n"


def test_robots_keeps_every_existing_rule_and_only_adds():
    live = "# my file\nUser-agent: *\nDisallow: /\nCrawl-delay: 5\n\nUser-agent: Bingbot\nAllow: /\n\nSitemap: https://a.example/s.xml\n"
    out = build_robots(live, check_urls=URLS).text
    for line in live.splitlines():
        assert line in out.splitlines(), f"lost: {line!r}"
    assert out.startswith(live.rstrip("\n"))          # additions come after, nothing is reordered


@pytest.mark.parametrize("live", [
    LIVE_WILDCARD,
    "User-agent: OAI-SearchBot\nDisallow: /\nUser-agent: PerplexityBot\nDisallow: /\nUser-agent: Claude-SearchBot\nDisallow: /\n",
    "User-agent: PerplexityBot\nDisallow: /*\n",
    "User-agent: *\nDisallow: /$\n",
    "",
    None,
])
def test_search_bots_are_always_allowed(live):
    build = build_robots(live, check_urls=URLS)
    for bot in SEARCH_BOTS:
        assert can_fetch(build.text, bot), bot
        assert build.final_access[bot] is True


def test_a_search_bot_that_is_blocked_by_its_own_group_gets_an_allow_line_not_a_deleted_rule():
    live = "User-agent: OAI-SearchBot\nDisallow: /\n"
    build = build_robots(live, check_urls=URLS)
    assert "Disallow: /" in build.text.splitlines()                 # the original rule is still there
    assert build.changes[0]["kind"] == "added_allow"


def test_a_rule_that_cannot_be_overridden_is_commented_out_and_reported_never_silently_dropped():
    build = build_robots("User-agent: PerplexityBot\nDisallow: /*\n", check_urls=URLS)
    assert "# removed by AEO Studio (was): Disallow: /*" in build.text
    assert build.changes[0]["kind"] == "removed_rule" and "Disallow: /*" in build.changes[0]["detail"]


def test_training_bots_keep_changes_nothing_about_them():
    live = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: ClaudeBot\nAllow: /\n"
    build = build_robots(live, check_urls=URLS, training="keep")
    assert not can_fetch(build.text, "GPTBot") and can_fetch(build.text, "ClaudeBot")
    assert not any(c["bot"] in ("GPTBot", "ClaudeBot") for c in build.changes)
    # and with no file at all, keep does not invent a training-bot rule
    assert "GPTBot" not in build_robots(None, check_urls=URLS).text


def test_training_bots_allow():
    live = "User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nDisallow: /\n"
    build = build_robots(live, check_urls=URLS, training="allow")
    assert can_fetch(build.text, "GPTBot") and can_fetch(build.text, "ClaudeBot")


def test_training_bots_block():
    live = "User-agent: GPTBot\nAllow: /\n"
    build = build_robots(live, check_urls=URLS, training="block")
    assert not can_fetch(build.text, "GPTBot") and not can_fetch(build.text, "ClaudeBot")
    assert all(can_fetch(build.text, b) for b in SEARCH_BOTS)       # blocking training never blocks search


def test_an_unknown_training_mode_is_refused():
    with pytest.raises(ValueError, match="keep, allow, block"):
        build_robots("", check_urls=URLS, training="maybe")


def test_the_sitemap_line_is_added_only_when_missing():
    added = build_robots("User-agent: *\nDisallow:\n", check_urls=URLS, sitemap_url="https://a.example/sitemap.xml")
    assert added.sitemap_added and "Sitemap: https://a.example/sitemap.xml" in added.text
    has = build_robots("User-agent: *\nDisallow:\nSitemap: https://a.example/other.xml\n", check_urls=URLS,
                       sitemap_url="https://a.example/sitemap.xml")
    assert not has.sitemap_added and "sitemap.xml" not in has.text


def test_the_diff_shows_exactly_what_changed():
    build = build_robots(LIVE_WILDCARD, check_urls=URLS)
    plus = [l for l in build.diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    minus = [l for l in build.diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    assert "+User-agent: OAI-SearchBot" in plus and "+Allow: /" in plus
    assert minus == []                                              # nothing was removed
    assert "live robots.txt" in build.diff and "generated robots.txt" in build.diff


def test_no_change_means_no_diff():
    build = build_robots("User-agent: *\nDisallow:\n", check_urls=URLS)
    assert build.diff == "" and not build.changed


# ---- JSON-LD -------------------------------------------------------------------------------------------------------------------

def facts(**kw):
    return [{"field": k.replace("_", " "), "value": v} for k, v in {**FULL_FACTS, **kw}.items()]


def test_jsonld_is_built_from_confirmed_facts_and_validates():
    r = build_jsonld(facts(), [])
    assert r.status == "valid" and r.errors == []
    node = r.document["@graph"][0]
    assert node["@type"] == "LocalBusiness" and node["name"] == "Acme Co"
    assert node["address"] == FULL_FACTS["Location_1_address"] and node["telephone"] == "514-555-0100"
    assert node["openingHours"] == "Mon-Fri 9:00-17:00"
    assert validate_jsonld(r.text, facts()) == []


def test_unconfirmed_facts_never_go_in_and_are_listed_as_missing():
    all_facts = [{"label": "Location 1 phone", "value": "999-UNCONFIRMED", "status": "unconfirmed"},
                 {"label": "Email", "value": "", "status": "unconfirmed"},
                 {"label": "Name as customers know it", "value": "Acme Co", "status": "confirmed"}]
    confirmed = [f for f in facts() if f["field"] != "Location 1 phone"]
    r = build_jsonld(confirmed, all_facts)
    assert "999-UNCONFIRMED" not in r.text and "telephone" not in r.document["@graph"][0]
    assert {x["label"] for x in r.left_out} == {"Location 1 phone", "Email"}
    assert "nobody has confirmed" in next(x["why"] for x in r.left_out if x["label"] == "Location 1 phone")


def test_no_confirmed_name_means_no_document():
    r = build_jsonld([f for f in facts() if f["field"] != "Name as customers know it"], [])
    assert r.document is None and r.text is None and r.status == "invalid"
    assert any("not a confirmed fact" in e for e in r.errors)


def test_no_confirmed_address_means_no_document():
    r = build_jsonld([f for f in facts() if "address" not in f["field"]], [])
    assert r.document is None and any("No location address" in e for e in r.errors)


def test_the_validator_rejects_values_that_are_not_confirmed_facts():
    good = build_jsonld(facts(), []).document
    for prop, bad in (("name", "Acme Corp"), ("address", "1 Fake St"), ("telephone", "000"), ("openingHours", "24/7")):
        doc = json.loads(json.dumps(good))
        doc["@graph"][0][prop] = bad
        errors = validate_jsonld(json.dumps(doc), facts())
        assert errors and any(prop in e or "name" in e for e in errors), prop


def test_the_validator_rejects_broken_documents():
    assert validate_jsonld("{not json", facts())[0].startswith("It is not valid JSON")
    assert validate_jsonld(json.dumps({"@context": "https://schema.org", "@graph": []}), facts())
    assert any("@context" in e for e in validate_jsonld(json.dumps({"@context": "x", "@graph": [{}]}), facts()))
    no_addr = {"@context": "https://schema.org", "@graph": [{"@type": "LocalBusiness", "name": "Acme Co"}]}
    assert any("address is missing" in e for e in validate_jsonld(json.dumps(no_addr), facts()))


def test_a_confirmed_business_type_gives_a_more_specific_type_and_an_unknown_one_falls_back():
    assert build_jsonld(facts(Business_type="dentist"), []).document["@graph"][0]["@type"] == "Dentist"
    assert build_jsonld(facts(Business_type="Auto Repair"), []).document["@graph"][0]["@type"] == "AutoRepair"
    r = build_jsonld(facts(Business_type="Space Elevator"), [])
    assert r.document["@graph"][0]["@type"] == "LocalBusiness" and any("not a schema.org" in n for n in r.notes)


def test_one_entry_per_location():
    r = build_jsonld(facts(Location_2_address="3 Av. Laval, Laval QC", Location_2_phone="450-555-0101"), [])
    graph = r.document["@graph"]
    assert [n["address"] for n in graph] == [FULL_FACTS["Location_1_address"], "3 Av. Laval, Laval QC"]
    assert graph[1]["telephone"] == "450-555-0101" and "openingHours" not in graph[1]
    assert len({n["@id"] for n in graph}) == 2


def test_a_run_writes_nothing_for_schema_when_validation_fails(biz, tmp_path):
    set_profile(biz, Location_1_phone="514-555-0100")                      # no name, no address
    add_diagnosis(biz, [finding("SCHEMA_NONE_FOUND")])
    res = rec(biz, tmp_path, no_audit=True)
    assert "schema" not in res.paths
    folder = tmp_path / "clients" / biz["domain"] / "generated"
    assert not list(folder.glob("*schema*"))
    assert res.data["jsonld"]["status"] == "invalid" and res.data["jsonld"]["errors"]
    md = res.paths["recommendations"].read_text()
    assert "**Not written.**" in md and "Confirm the missing facts" in md
    row = query_one(biz, "SELECT validation_status, generated_jsonld, validation_errors FROM recommendations WHERE business_id = %s")
    assert row[0] == "invalid" and row[1] is None and row[2]


# ---- llms.txt ------------------------------------------------------------------------------------------------------------------

def test_llms_txt_follows_the_llmstxt_org_format():
    pages = ["https://a.example/", "https://a.example/about-us", "https://a.example/services"]
    out = build_llms(business_name="Fallback", facts=facts(), description="We refill water at events.", pages=pages)
    lines = out["text"].splitlines()
    assert lines[0] == "# Acme Co"                                          # one H1, first
    assert sum(1 for l in lines if l.startswith("# ")) == 1
    assert lines[2] == "> We refill water at events."                       # the summary blockquote
    assert "## Main pages" in lines
    links = [l for l in lines if l.startswith("- [")]
    assert links == ["- [Home](https://a.example/)", "- [About us](https://a.example/about-us)", "- [Services](https://a.example/services)"]
    assert all(re.fullmatch(r"- \[[^\]]+\]\(https?://[^)]+\)", l) for l in links)
    assert out["text"].endswith("\n") and not out["text"].endswith("\n\n")


def test_llms_txt_uses_confirmed_facts_only_and_says_when_the_name_is_not_confirmed():
    out = build_llms(business_name="Record Name", facts=[{"field": "Services", "value": "Refills"}], description=None, pages=[])
    assert out["text"].startswith("# Record Name") and "not confirmed" in out["name_source"]
    assert "Services: Refills" in out["text"] and ">" not in out["text"]


# ---- the whole run: inputs, refusals, labels --------------------------------------------------------------------------------

def query_one(biz, sql):
    c = get_connection()
    try:
        with c.cursor() as cur:
            cur.execute(sql, (biz["id"],))
            return cur.fetchone()
    finally:
        c.close()


def test_it_refuses_without_a_diagnosis(biz, tmp_path):
    with pytest.raises(RecommendError, match="no diagnosis"):
        rec(biz, tmp_path, no_audit=True)


def test_it_refuses_a_decline_verdict(biz, tmp_path):
    add_diagnosis(biz, [], verdict="decline", platform="notion")
    with pytest.raises(RecommendError, match="decline"):
        rec(biz, tmp_path, no_audit=True)


def test_without_a_finished_audit_it_refuses_unless_no_audit_is_given(biz, tmp_path):
    add_diagnosis(biz, [])
    with pytest.raises(RecommendError, match="--no-audit"):
        rec(biz, tmp_path)
    assert not (tmp_path / "clients").exists()                              # nothing was written


def test_an_unfinished_audit_is_not_good_enough(biz, tmp_path):
    add_diagnosis(biz, [])
    build_run(biz, snapshot_specs(biz)[:3], questions=SQ, status="running", expected=8)
    with pytest.raises(RecommendError, match="not finished"):
        rec(biz, tmp_path)


def test_no_audit_labels_the_list_and_leaves_audit_sections_empty(biz, tmp_path):
    add_diagnosis(biz, [finding("LLMS_TXT_MISSING")])
    res = rec(biz, tmp_path, no_audit=True)
    md = res.paths["recommendations"].read_text()
    assert md.index("Not prioritised by an audit") < md.index("## The fix list")
    assert md.splitlines()[2].startswith("> **Not prioritised by an audit.**")   # right under the title
    assert res.data["prioritised_by_audit"] is False and res.data["inputs"]["audit"] is None
    assert fix_ids(res, "wrong_fact") == [] and fix_ids(res, "target_question") == []
    assert "Not available: this needs an audit." in md
    assert query_one(biz, "SELECT prioritised_by_audit, visibility_probe_run_id FROM recommendations WHERE business_id = %s") == (False, None)


def test_with_an_audit_there_is_no_not_prioritised_label(biz, tmp_path):
    add_diagnosis(biz, [])
    build_run(biz, snapshot_specs(biz), questions=SQ, targets=(0, 1))
    res = rec(biz, tmp_path)
    assert "Not prioritised" not in res.paths["recommendations"].read_text()
    assert res.data["prioritised_by_audit"] is True and res.data["inputs"]["audit"]["answers_read"] == 8


def test_run_and_no_audit_together_are_refused(biz, tmp_path):
    add_diagnosis(biz, [])
    with pytest.raises(RecommendError, match="not both"):
        rec(biz, tmp_path, no_audit=True, run=str(uuid.uuid4()))


def test_a_diagnosis_of_another_business_is_refused(biz, tmp_path):
    other = add_diagnosis(biz, [])
    from core.businesses import add_business
    b2 = add_business(f"https://other-{uuid.uuid4().hex[:8]}.example", name=f"Other {uuid.uuid4().hex[:8]}")
    with pytest.raises(RecommendError, match="does not exist for this business"):
        rec(b2, tmp_path, no_audit=True, diagnosis=other)


def test_the_latest_diagnosis_is_used_unless_one_is_given(biz, tmp_path):
    old = add_diagnosis(biz, [], platform="wix", checked=datetime(2026, 9, 1, tzinfo=timezone.utc))
    new = add_diagnosis(biz, [], platform="shopify", checked=datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert rec(biz, tmp_path, no_audit=True).data["inputs"]["diagnosis"]["id"] == new
    assert rec(biz, tmp_path, no_audit=True, diagnosis=old).data["inputs"]["diagnosis"]["id"] == old


# ---- priority order, on fixture data ---------------------------------------------------------------------------------------

@pytest.fixture
def full(biz, tmp_path):
    """A client with confirmed facts, a diagnosis with findings of every kind, and an audit with a wrong fact and a target gap."""
    set_profile(biz, story={"description": "Water refill for events."}, Areas_we_serve="Montreal", Phone="514-555-0100",
                **{k: v for k, v in FULL_FACTS.items() if k != "Areas_we_serve"})
    add_diagnosis(biz, [
        finding("LLMS_TXT_MISSING", "low"),
        finding("SCHEMA_NONE_FOUND", "medium"),
        finding("RENDER_H1_MULTIPLE", "low"),
        finding("RENDER_PHONE_MISSING", "medium"),
        finding("ROBOTS_DISALLOWS_GPTBOT", "low"),
        finding("CRAWLER_BLOCKED_OAI_SEARCHBOT", "high", fixable=True, where="Cloudflare dashboard. Three systems..."),
        finding("ROBOTS_DISALLOWS_PERPLEXITYBOT", "high", fixable=True),
        finding("PLATFORM_PLAN_UNCONFIRMED", "medium"),
    ], platform="wordpress", robots_txt="User-agent: *\nDisallow: /\n")
    build_run(biz, snapshot_specs(biz), questions=SQ, targets=(0, 1))
    return rec(biz, tmp_path, fetcher=fake_site(robots="User-agent: PerplexityBot\nDisallow: /\n"))


def test_the_priority_order_is_access_then_wrong_facts_then_targets_then_findings_then_hygiene(full):
    sections = [f["section"] for f in full.data["fixes"]]
    order = ["access", "wrong_fact", "target_question", "finding", "hygiene"]
    assert sections == sorted(sections, key=order.index)
    assert set(sections) == set(order)
    assert fix_ids(full) == [f"R{i}" for i in range(1, len(sections) + 1)]


def test_access_blockers_come_first_by_severity_and_include_only_search_bots(full):
    access = [f for f in full.data["fixes"] if f["section"] == "access"]
    assert [f["finding_code"] for f in access] == ["CRAWLER_BLOCKED_OAI_SEARCHBOT", "ROBOTS_DISALLOWS_PERPLEXITYBOT"]
    assert "ROBOTS_DISALLOWS_GPTBOT" not in [f["finding_code"] for f in access]       # a training bot is not a blocker


def test_remaining_findings_are_by_severity_and_do_not_repeat_other_fixes(full):
    rest = [f for f in full.data["fixes"] if f["section"] == "finding"]
    assert [f["finding_code"] for f in rest] == ["PLATFORM_PLAN_UNCONFIRMED", "RENDER_PHONE_MISSING", "RENDER_H1_MULTIPLE", "ROBOTS_DISALLOWS_GPTBOT"]
    assert [f["severity"] for f in rest] == ["medium", "medium", "low", "low"]
    assert "SCHEMA_NONE_FOUND" not in [f["finding_code"] for f in rest]                 # the hygiene fix owns it


def test_hygiene_is_last_labelled_cheap_and_unproven(full):
    hygiene = [f for f in full.data["fixes"] if f["section"] == "hygiene"]
    assert [f["finding_code"] for f in hygiene] == ["SCHEMA_NONE_FOUND", "LLMS_TXT_MISSING"]
    assert full.data["fixes"][-2:] == hygiene
    md = full.paths["recommendations"].read_text()
    assert "5. Hygiene (cheap, unproven)" in md and "**Cheap, unproven.**" in md and "Cheap, unproven. Cheap" not in md
    assert md.index("### 4. Remaining open findings") < md.index("### 5. Hygiene")


def test_wrong_facts_show_the_quote_the_confirmed_fact_and_where_ai_got_it(full):
    wrong = [f for f in full.data["fixes"] if f["section"] == "wrong_fact"]
    assert len(wrong) == 1
    w = wrong[0]
    assert w["quote"] == "Only serves Ontario" and w["fact_label"] == "Areas we serve"
    assert w["confirmed_fact"] == "Montreal"
    assert w["likely_sources"] == [{"domain": "news.example", "answers": 1}]     # the outside source, not the client's own site
    assert w["client_pages_cited"] and w["finding_code"] == "AUDIT_WRONG_FACT"
    md = full.paths["recommendations"].read_text()
    assert "“Only serves Ontario”" in md and "news.example" in md and "Montreal" in md


def test_unverifiable_claims_are_never_a_wrong_fact(biz, tmp_path):
    set_profile(biz, Areas_we_serve="Montreal")
    add_diagnosis(biz, [])
    specs = [dict(q=0, e="chatgpt", named=[nb(CLIENT, client=True)], claims=[claim("Founded in 2015", None, "unverifiable")])]
    build_run(biz, specs, questions=SQ[:1], engines=("chatgpt",))
    assert fix_ids(rec(biz, tmp_path), "wrong_fact") == []


def test_target_gaps_list_sources_reasons_and_the_page_needed_but_write_no_page(full):
    targets = [f for f in full.data["fixes"] if f["section"] == "target_question"]
    assert [t["question"] for t in targets] == [SQ[0]]                          # q1 has no answer with rivals and no client
    t = targets[0]
    assert t["answers"] == 2 and t["answers_client_absent_competitors_named"] == 1 and t["answers_client_named"] == 1
    assert {"domain": "rival.example", "answers": 1} in t["sources_cited"]
    assert [c["name"] for c in t["competitors"]][0] == "Rival Co"
    assert any(r["quote"] == "cheapest" for r in t["reasons_given"])
    assert t["page_needed"].startswith("Improve the page already being cited")   # the client's page is in its sources
    assert "<html" not in json.dumps(full.data) and "draft" not in t["page_needed"].lower()


def test_with_no_question_marked_as_a_target_the_section_says_none(biz, tmp_path):
    add_diagnosis(biz, [])
    build_run(biz, snapshot_specs(biz), questions=SQ, targets=())
    res = rec(biz, tmp_path)
    assert fix_ids(res, "target_question") == []
    assert "None: no target question" in res.paths["recommendations"].read_text()


def test_off_site_lists_domains_cited_where_the_client_was_absent_and_nothing_else(full):
    domains = {d["domain"]: d["answers"] for d in full.data["off_site"]["domains"]}
    assert domains == {"rival.example": 1, "news.example": 1}
    assert not any(d.endswith(full.data["business"]["domain"]) for d in domains)          # never the client's own site
    md = full.paths["recommendations"].read_text()
    assert "Off-site work (named only, not done here)" in md and "none of it happens here" in md


# ---- platform routing -------------------------------------------------------------------------------------------------------

def test_routing_comes_from_the_data_file_and_covers_the_matrix():
    assert routing.route("shopify", None, "robots")["status"] == "can"
    assert "robots.txt.liquid" in routing.route("shopify", None, "robots")["where"]
    assert routing.route("webflow", None, "jsonld")["by_hand"] is True
    assert routing.route("hubspot", None, "llms")["status"] == "cannot"
    assert routing.route("framer", "free", "robots")["status"] == "blocked_by_plan"
    assert routing.route("framer", "pro", "robots")["status"] == "gated"
    assert routing.route("squarespace", "basic", "jsonld")["status"] == "blocked_by_plan"
    assert routing.route(None, None, "robots")["status"] == "unknown"
    assert routing.edge_route("cloudflare")["status"] == "can" and "AI Crawl Control" in routing.edge_route("cloudflare")["where"]
    assert routing.edge_route("wp_engine")["status"] == "cannot"
    with pytest.raises(ValueError):
        routing.route("wix", None, "cookies")


def test_squarespace_robots_is_a_cannot_do_with_an_alternative(biz, tmp_path):
    set_profile(biz, **FULL_FACTS)
    add_diagnosis(biz, [finding("ROBOTS_DISALLOWS_OAI_SEARCHBOT", "high", fixable=False)], platform="squarespace", plan="core")
    res = rec(biz, tmp_path, no_audit=True)
    f = res.data["fixes"][0]
    assert f["finding_code"] == "ROBOTS_DISALLOWS_OAI_SEARCHBOT" and f["cannot_do"] is True
    assert "never lets you edit robots.txt" in f["route"]["where"]
    assert "Settings → Crawlers" in f["route"]["alternative"] and "Cloudflare" in f["route"]["alternative"]
    md = res.paths["recommendations"].read_text()
    assert "**Cannot be done on Squarespace.**" in md and "**Instead:** Revert to the client" in md
    assert fix_ids(res)[0] == "R1"


def test_a_cloudflare_block_is_routed_to_the_edge_and_wp_engine_is_a_cannot_do(biz, tmp_path):
    add_diagnosis(biz, [finding("CRAWLER_BLOCKED_CLAUDE_SEARCHBOT", "high", fixable=True, where="Cloudflare dashboard. x")])
    f = rec(biz, tmp_path, no_audit=True).data["fixes"][0]
    assert f["route"]["where"].startswith("Cloudflare dashboard") and not f["cannot_do"]

    add_diagnosis(biz, [finding("CRAWLER_BLOCKED_CLAUDE_SEARCHBOT", "high"), finding("HOST_WP_ENGINE_DETECTED", "low")],
                  checked=datetime(2026, 10, 9, tzinfo=timezone.utc))
    f = next(x for x in rec(biz, tmp_path, no_audit=True).data["fixes"] if x["finding_code"] == "CRAWLER_BLOCKED_CLAUDE_SEARCHBOT")
    assert f["cannot_do"] and "move to a different host" in f["route"]["alternative"]


def test_hygiene_llms_on_hubspot_is_a_cannot_do_and_a_plan_gate_is_reported(biz, tmp_path):
    set_profile(biz, **FULL_FACTS)
    add_diagnosis(biz, [finding("LLMS_TXT_MISSING"), finding("SCHEMA_NONE_FOUND")], platform="hubspot")
    fixes = {f["finding_code"]: f for f in rec(biz, tmp_path, no_audit=True).data["fixes"]}
    assert fixes["LLMS_TXT_MISSING"]["cannot_do"] is True and not fixes["SCHEMA_NONE_FOUND"]["cannot_do"]

    add_diagnosis(biz, [finding("SCHEMA_NONE_FOUND")], platform="squarespace", plan="basic", checked=datetime(2026, 10, 9, tzinfo=timezone.utc))
    res = rec(biz, tmp_path, no_audit=True)
    schema_fix = next(f for f in res.data["fixes"] if f["finding_code"] == "SCHEMA_NONE_FOUND")
    assert schema_fix["route"]["status"] == "blocked_by_plan"
    assert "cannot be done on the plan the client is on" in res.paths["recommendations"].read_text()


# ---- finding codes link back; the .json matches the .md -----------------------------------------------------------------------

def test_every_fix_carries_a_registered_finding_code_that_aeo_log_accepts(full):
    for f in full.data["fixes"]:
        if f["finding_code"] is not None:
            assert f["finding_code"] in REGISTRY
            assert ev.check_finding_code(f["finding_code"]) == f["finding_code"]
    diag_codes = {"CRAWLER_BLOCKED_OAI_SEARCHBOT", "ROBOTS_DISALLOWS_PERPLEXITYBOT", "ROBOTS_DISALLOWS_GPTBOT", "PLATFORM_PLAN_UNCONFIRMED",
                  "RENDER_PHONE_MISSING", "RENDER_H1_MULTIPLE", "SCHEMA_NONE_FOUND", "LLMS_TXT_MISSING"}
    assert diag_codes <= set(codes(full))                                       # every diagnosis finding is on the list exactly once
    assert len([c for c in codes(full) if c in diag_codes]) == len(diag_codes)


def test_logging_a_fix_with_its_code_works_end_to_end(full, biz):
    conn = get_connection()
    try:
        added = ev.add_work(conn, business=biz, text="Allowed PerplexityBot", operator="Sam", finding="ROBOTS_DISALLOWS_PERPLEXITYBOT")
        assert added["id"]
    finally:
        conn.close()


def test_the_json_file_is_exactly_what_the_markdown_was_made_from(full):
    data = json.loads(full.paths["recommendations_data"].read_text())
    assert data == full.data
    assert render_markdown(data) == full.paths["recommendations"].read_text()


def test_the_other_files_are_the_ones_the_document_names(full):
    names = full.data["files"]
    for key, p in full.paths.items():
        assert p.name == names[key] and p.parent.name == "generated"
    assert names["robots"] == "2026-10-16_robots_run1.txt" and names["schema"] == "2026-10-16_schema_run1.json"
    assert full.paths["llms"].read_text().startswith("# Acme Co\n")
    assert json.loads(full.paths["schema"].read_text())["@graph"][0]["name"] == "Acme Co"
    robots = full.paths["robots"].read_text()
    assert all(can_fetch(robots, b) for b in SEARCH_BOTS)
    assert f"Sitemap: https://{full.data['business']['domain']}/sitemap.xml" in robots


def test_a_second_run_gets_the_next_number_and_overwrites_nothing(biz, tmp_path):
    add_diagnosis(biz, [])
    first = rec(biz, tmp_path, no_audit=True)
    second = rec(biz, tmp_path, no_audit=True)
    assert first.paths["recommendations"].name.endswith("_run1.md") and second.paths["recommendations"].name.endswith("_run2.md")
    assert first.paths["recommendations"].read_text() == first.paths["recommendations"].read_text()
    assert first.paths["robots"].exists() and second.paths["robots"].exists()


def test_the_page_content_of_the_main_pages_is_picked_from_the_sitemap_sensibly(biz, tmp_path):
    add_diagnosis(biz, [])
    pages = rec(biz, tmp_path, no_audit=True).data["inputs"]["live_site"]["main_pages"]
    base = f"https://{biz['domain']}"
    assert pages[0] == base + "/"
    assert not any(p.endswith(".jpg") or "?" in p for p in pages)               # images and query pages are not "main pages"
    assert pages.index(base + "/about") < pages.index(base + "/blog/a/b/c")


# ---- database, journal and the live site ---------------------------------------------------------------------------------------

def test_the_fixes_are_stored_in_the_recommendations_table(full, biz):
    c = get_connection()
    try:
        with c.cursor() as cur:
            cur.execute("SELECT priority_fixes, fact_fixes, off_site_sources, generated_jsonld, llms_txt, validation_status, "
                        "robots_txt, files, status, diagnosis_id, visibility_probe_run_id, business_profile_id "
                        "FROM recommendations WHERE business_id = %s", (biz["id"],))
            rows = cur.fetchall()
    finally:
        c.close()
    assert len(rows) == 1
    priority, wrong, off, schema, llms, status, robots, files, state, diag_id, run_id, profile_id = rows[0]
    assert [f["id"] for f in wrong] == [f["id"] for f in full.data["fixes"] if f["section"] == "wrong_fact"]
    assert [f["id"] for f in priority] == [f["id"] for f in full.data["fixes"] if f["section"] != "wrong_fact"]
    assert schema["@graph"][0]["name"] == "Acme Co" and status == "valid" and state == "draft"
    assert llms == full.paths["llms"].read_text() and robots == full.paths["robots"].read_text()
    assert files["robots"] == full.data["files"]["robots"]
    assert str(diag_id) == full.data["inputs"]["diagnosis"]["id"] and str(run_id) == full.data["inputs"]["audit"]["run_id"]


def test_a_journal_entry_is_written(full, biz):
    row = query_one(biz, "SELECT description, details, related_table FROM client_journal WHERE business_id = %s "
                         "AND entry_type = 'recommendations_generated' ORDER BY created_at DESC LIMIT 1")
    assert "Recommendations written" in row[0] and row[2] == "recommendations"
    assert row[1]["fixes"] == len(full.data["fixes"]) and row[1]["prioritised_by_audit"] is True
    assert row[1]["files"]["robots"].startswith("generated/")


def test_the_live_site_is_only_ever_read(biz, tmp_path):
    add_diagnosis(biz, [])
    seen = []
    rec(biz, tmp_path, fetcher=fake_site(seen=seen), no_audit=True)
    assert seen and {m for m, _ in seen} == {"GET"}
    assert {urlpath for _, urlpath in seen} <= {"https://" + biz["domain"] + "/robots.txt", "https://" + biz["domain"] + "/sitemap.xml"}


def test_when_the_live_robots_cannot_be_read_the_diagnosis_copy_is_used(biz, tmp_path):
    add_diagnosis(biz, [], robots_txt="User-agent: *\nDisallow: /\n")
    res = rec(biz, tmp_path, no_audit=True, fetcher=fake_site(robots=None))
    assert res.data["inputs"]["live_site"]["robots_txt"] == "diagnosis"
    assert "Disallow: /" in res.paths["robots"].read_text() and all(can_fetch(res.paths["robots"].read_text(), b) for b in SEARCH_BOTS)


def test_with_no_robots_anywhere_the_file_starts_from_scratch(biz, tmp_path):
    add_diagnosis(biz, [])
    res = rec(biz, tmp_path, no_audit=True, fetcher=fake_site(robots=None))
    assert res.data["inputs"]["live_site"]["robots_txt"] == "none" and res.data["robots"]["built_from_live_file"] is False


def test_training_bots_flag_reaches_the_file(biz, tmp_path):
    add_diagnosis(biz, [])
    res = rec(biz, tmp_path, no_audit=True, training_bots="block", fetcher=fake_site(robots="User-agent: *\nDisallow:\n"))
    robots = res.paths["robots"].read_text()
    assert not can_fetch(robots, "GPTBot") and can_fetch(robots, "OAI-SearchBot")
    with pytest.raises(RecommendError, match="keep, allow, block"):
        rec(biz, tmp_path, no_audit=True, training_bots="maybe")


# ---- the command -----------------------------------------------------------------------------------------------------------------

def test_the_command_prints_the_diff_and_where_the_files_went(biz, tmp_path, monkeypatch):
    set_profile(biz, **FULL_FACTS)
    add_diagnosis(biz, [finding("LLMS_TXT_MISSING")])
    monkeypatch.setattr("recommend.run.Fetcher", lambda: fake_site(robots="User-agent: *\nDisallow: /\n"))
    result = CliRunner().invoke(cli.app, ["recommend", "--business", biz["domain"], "--no-audit"])
    assert result.exit_code == 0, result.output
    assert "NOT PRIORITISED BY AN AUDIT" in result.output
    assert "+User-agent: OAI-SearchBot" in result.output and "robots.txt compared with the live file" in result.output
    assert "Nothing was sent or deployed" in result.output


def test_the_command_refuses_without_an_audit(biz):
    add_diagnosis(biz, [])
    result = CliRunner().invoke(cli.app, ["recommend", "--business", biz["domain"]])
    assert result.exit_code == 1 and "--no-audit" in result.output
