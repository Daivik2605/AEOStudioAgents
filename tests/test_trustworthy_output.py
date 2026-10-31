"""PLAN.md step 25c: Phase A output you can trust. Mocked HTTP only; the AI is the scripted fake client.

  1. a check that was blocked claims nothing        3. a crawler that cannot connect is unconfirmed, not a block
  2. repeated findings are shown once               4. a control question never changes a score
(Item 6, the guard that keeps the tests off the dev database, is in test_db_guard.py. Item 5 is not built yet.)"""

from __future__ import annotations

import copy
import json
import re

import httpx
import pytest
from typer.testing import CliRunner

from aeo import cli
from audit import report_data, store
from audit.report_render import render_markdown
from compare.diagnosis import compare_diagnoses, pick_diagnosis
from core.db import get_connection
from diagnose import codes
from diagnose.codes import REGISTRY, check_for, codes_for_check, explanation_for, make_finding
from diagnose.crawler import BOTS, CONNECT_RETRY_SECONDS
from diagnose.display import group_findings
from diagnose.fetch import Fetcher
from diagnose.not_checked import BLOCKED, content_checks_ran, site_checks_ran
from diagnose.report import build_markdown
from diagnose.run import run_diagnosis
from dossier.data import diagnosis_column
from tests.conftest import FakeAnthropicClient, text_response
from tests.helpers_diagnose import is_bot, page, response
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_audit_report import CLIENT, build_run, claim, nb
from tests.test_client_report import html_of, make
from tests.test_compare import RIVAL, add_diagnosis, add_run, compare, finding as plain_finding, make_qs, measure
from tests.test_site_checks import HOME, good_site, no_real_network, serve  # noqa: F401  (no_real_network is an autouse guard)

URL = "https://acme.example/"
CAPTCHA = "<html><body>Sorry, we just need to make sure you are not a robot. Please solve this captcha.</body></html>"
CLIENT_RENDERED = re.compile(r"client[- ]?(side[- ])?render|server[- ]?side render|needs? (ssr|server)", re.I)
BARE_PAGE = page(300, h1="", title="Acme")                  # an empty <h1> and no structured data: there is something to report


def handler_for(routes: dict, override=None, seen: list | None = None):
    base = serve(routes, seen)

    def handler(request: httpx.Request) -> httpx.Response:
        if override is not None:
            got = override(request)
            if got is not None:
                return got
        return base(request)
    return handler


def diagnose(routes, tmp_path, override=None, **kw):
    seen = kw.pop("seen", None)
    sleeps: list = []
    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(handler_for(routes, override, seen)), follow_redirects=True),
                      max_requests=15, min_interval=0, sleep=sleeps.append)
    out = run_diagnosis(URL, fetcher=fetcher, clients_root=tmp_path, use_ai=kw.pop("use_ai", False), **kw)
    return out, sleeps


def codes_in(out) -> set[str]:
    return {f["code"] for f in out.findings}


def not_checked(out) -> dict[str, dict]:
    return {n["check"]: n for n in out.facts.site_checks.not_checked}


def content_codes(found: set[str]) -> set[str]:
    return {c for c in found if check_for(c) is not None}


# ---- 1. a blocked check claims nothing -----------------------------------------------------------------------------------------

def test_a_page_that_comes_back_as_a_bot_check_gives_no_content_findings(tmp_path):
    seen: list = []
    routes = good_site(**{"https://acme.example/": response(200, BARE_PAGE)})
    out, _ = diagnose(routes, tmp_path, seen=seen, override=lambda r: response(503, CAPTCHA) if r.url.path == "/" else None)
    found = codes_in(out)
    assert not {c for c in found if c.startswith(("RENDER_", "SITEMAP_", "HOST_DUPLICATE", "HTTP_NOT"))} and "SCHEMA_NONE_FOUND" not in found
    assert "LLMS_TXT_MISSING" not in found and not any(c.startswith("OLD_URL_") for c in found)
    assert set(not_checked(out)) == {"render", "structured_data", "llms", "sitemap", "hosts"}
    assert all(BLOCKED in n["reason"] for n in not_checked(out).values())
    assert "RENDER_H1_EMPTY" in not_checked(out)["render"]["codes"] and "SITEMAP_MISSING" in not_checked(out)["sitemap"]["codes"]
    assert not any(url.endswith("/sitemap.xml") or "www." in url for url, _ in seen)       # no request was even made
    assert out.data["site_checks"]["checks_ran"] == [] and out.data["site_checks"]["content_ran"] == []


def test_the_verdict_never_says_client_rendered_when_the_page_could_not_be_read(tmp_path):
    out, _ = diagnose(good_site(**{"https://acme.example/": response(200, BARE_PAGE)}), tmp_path,
                      override=lambda r: response(503, CAPTCHA) if r.url.path == "/" else None)
    assert not CLIENT_RENDERED.search(out.verdict.reason) and out.facts.render is None
    assert not CLIENT_RENDERED.search(build_markdown(out.data).split("## Findings")[0])
    assert out.verdict.verdict == "conditional" and out.verdict.reasons[0].code == "CRAWLER_TEST_INCONCLUSIVE"


def test_the_browser_fetch_alone_coming_back_as_a_bot_check_is_enough(tmp_path):
    """Only the page we read as a browser is turned away (the bots are let in): that alone means we could not read the site."""
    routes = good_site(**{"https://acme.example/": response(200, BARE_PAGE)})
    out, _ = diagnose(routes, tmp_path, override=lambda r: response(403, CAPTCHA) if r.url.path == "/" and not is_bot(r) else None)
    assert content_codes(codes_in(out)) == set() and "SCHEMA_NONE_FOUND" not in codes_in(out)
    assert "the page itself came back as a bot check (HTTP 403)" in not_checked(out)["render"]["reason"]
    assert out.facts.render is None and not CLIENT_RENDERED.search(out.verdict.reason)


def test_robots_failing_for_bots_but_not_for_a_browser_counts_as_blocked(tmp_path):
    routes = good_site(**{"https://acme.example/": response(200, BARE_PAGE)})
    out, _ = diagnose(routes, tmp_path, override=lambda r: response(503, "busy") if r.url.path == "/robots.txt" and is_bot(r) else None)
    assert "ROBOTS_TXT_BOT_FETCH_FAILED" in codes_in(out)                    # that finding is the point, and it stays
    assert content_codes(codes_in(out)) == set() and "SCHEMA_NONE_FOUND" not in codes_in(out)
    assert {"render", "sitemap", "structured_data"} <= set(not_checked(out)) and "robots.txt could not be fetched as a bot" in not_checked(out)["render"]["reason"]


def test_every_crawler_turned_away_the_same_way_counts_as_blocked(tmp_path):
    routes = good_site(**{"https://acme.example/": response(200, BARE_PAGE)})
    out, _ = diagnose(routes, tmp_path, override=lambda r: response(403, "forbidden") if r.url.path == "/" and is_bot(r) else None)
    assert {f"CRAWLER_BLOCKED_{b.code_suffix}" for b in BOTS} <= codes_in(out)
    assert content_codes(codes_in(out)) == set()
    assert "every crawler test was turned away" in not_checked(out)["render"]["reason"]


def test_one_crawler_refused_is_not_a_block_on_the_whole_site(tmp_path):
    routes = good_site(**{"https://acme.example/": response(200, BARE_PAGE)})
    out, _ = diagnose(routes, tmp_path, override=lambda r: response(403, "no") if r.url.path == "/" and "GPTBot" in r.headers["user-agent"] else None)
    assert "CRAWLER_BLOCKED_GPTBOT" in codes_in(out) and "RENDER_H1_EMPTY" in codes_in(out) and "SCHEMA_NONE_FOUND" in codes_in(out)
    assert out.facts.site_checks.not_checked == [] and set(out.facts.site_checks.content_ran) == {"render", "structured_data", "llms"}


def test_a_page_that_is_simply_down_is_not_called_blocked(tmp_path):
    routes = good_site()
    out, _ = diagnose(routes, tmp_path, override=lambda r: response(500, "oops") if r.url.path == "/" else None)
    reason = not_checked(out)["render"]["reason"]
    assert "HTTP 500" in reason and BLOCKED not in reason and "sitemap" not in not_checked(out)


def test_a_check_whose_own_requests_were_turned_away_is_dropped_not_reported_missing(tmp_path):
    from diagnose.site_checks import SITE_CHECK_UA
    ours = lambda r: r.headers.get("user-agent") == SITE_CHECK_UA
    routes = good_site(**{"https://acme.example/": response(200, BARE_PAGE)})
    out, _ = diagnose(routes, tmp_path, override=lambda r: response(403, "forbidden") if ours(r) and r.url.path == "/sitemap.xml" else None)
    assert "SITEMAP_MISSING" not in codes_in(out) and "sitemap" in not_checked(out) and "sitemap" not in out.data["site_checks"]["checks_ran"]
    assert "hosts" in out.data["site_checks"]["checks_ran"] and "RENDER_H1_EMPTY" in codes_in(out)         # the rest still ran
    out2, _ = diagnose(routes, tmp_path, override=lambda r: response(403, "forbidden") if ours(r) and str(r.url).startswith("http://") else None)
    assert "HTTP_NOT_REDIRECTED" not in codes_in(out2) and "hosts" in not_checked(out2)


def test_an_llms_file_behind_a_bot_check_is_not_reported_missing_but_a_404_still_is(tmp_path):
    routes = good_site()
    blocked, _ = diagnose(routes, tmp_path, override=lambda r: response(429, "slow down") if r.url.path == "/llms.txt" else None)
    assert "LLMS_TXT_MISSING" not in codes_in(blocked) and "llms" in not_checked(blocked)
    missing, _ = diagnose(routes, tmp_path)
    assert "LLMS_TXT_MISSING" in codes_in(missing)


def test_a_healthy_site_has_nothing_in_the_not_checked_list(tmp_path):
    out, _ = diagnose(good_site(), tmp_path)
    assert out.facts.site_checks.not_checked == [] and "not_checked" in out.data["site_checks"]
    assert set(out.data["site_checks"]["content_ran"]) == {"render", "structured_data", "llms"}
    assert "## Not checked" not in build_markdown(out.data)


def test_the_report_lists_what_was_not_checked_and_why(tmp_path):
    out, _ = diagnose(good_site(), tmp_path, override=lambda r: response(503, CAPTCHA) if r.url.path == "/" else None)
    md = build_markdown(out.data)
    assert "## Not checked" in md and BLOCKED in md and "Their absence below means unknown, not fine" in md
    assert "Sitemap" in md.split("## Not checked")[1]


def test_the_model_cannot_claim_client_side_rendering_about_a_page_it_was_told_was_not_read(business_id, tmp_path):
    for text, expect_kept in (("The site is client-rendered, so crawlers see an empty page.", False),
                              ("We were blocked, so the page could not be checked. Run it again.", True)):
        client = FakeAnthropicClient([text_response(text)])
        out, _ = diagnose(good_site(), tmp_path, business_id=business_id, use_ai=True, explain_client=client,
                          override=lambda r: response(503, CAPTCHA) if r.url.path == "/" else None)
        sent = json.loads(client.messages.calls[0]["messages"][0]["content"])
        assert {n["check"] for n in sent["not_checked"]} >= {"Page content (headings, words in the page, name and phone)"}
        assert (out.ai_explanation is not None) == expect_kept
        assert expect_kept or any("the page could not be read" in w for w in out.warnings)


def test_a_readable_page_may_still_be_called_client_rendered(business_id, tmp_path):
    client = FakeAnthropicClient([text_response("The page content is client-rendered.")])
    out, _ = diagnose(good_site(), tmp_path, business_id=business_id, use_ai=True, explain_client=client)
    assert out.ai_explanation == "The page content is client-rendered."
    assert "not_checked" not in json.loads(client.messages.calls[0]["messages"][0]["content"])


def test_the_checks_map_to_the_codes_that_depend_on_them():
    assert check_for("RENDER_H1_EMPTY") == "render" and check_for("SCHEMA_NONE_FOUND") == "structured_data"
    assert check_for("SITEMAP_MISSING") == "sitemap" and check_for("LLMS_TXT_MISSING") == "llms" and check_for("ROBOTS_DISALLOWS_GPTBOT") is None
    assert "SCHEMA_NONE_FOUND" in codes_for_check("structured_data") and all(c.startswith("RENDER_") for c in codes_for_check("render"))


def test_compare_calls_a_blocked_check_not_run_not_fixed(business_id, tmp_path):
    a, _ = diagnose(good_site(**{"https://acme.example/": response(200, BARE_PAGE)}), tmp_path, business_id=business_id, checkpoint="before")
    b, _ = diagnose(good_site(**{"https://acme.example/": response(200, BARE_PAGE)}), tmp_path, business_id=business_id, checkpoint="check",
                    override=lambda r: response(503, CAPTCHA) if r.url.path == "/" else None)
    assert {"RENDER_H1_EMPTY", "SCHEMA_NONE_FOUND"} <= codes_in(a) and not codes_in(b) & {"RENDER_H1_EMPTY", "SCHEMA_NONE_FOUND"}
    conn = get_connection()
    try:
        da, db = pick_diagnosis(conn, business_id, "before", None), pick_diagnosis(conn, business_id, "check", None)
    finally:
        conn.close()
    result = compare_diagnoses(da, db, [], "2026-10-29")
    fixed = {c["code"] for c in result["fixed"]}
    assert not fixed & {"RENDER_H1_EMPTY", "SCHEMA_NONE_FOUND"} and {"RENDER_H1_EMPTY", "SCHEMA_NONE_FOUND"} <= set(result["not_rechecked"])
    assert any("cannot be called fixed" in c for c in result["caveats"])
    back = compare_diagnoses(db, da, [], "2026-10-29")                                    # the other way round
    assert not {c["code"] for c in back["new"]} & {"RENDER_H1_EMPTY", "SCHEMA_NONE_FOUND"}
    assert {"RENDER_H1_EMPTY", "SCHEMA_NONE_FOUND"} <= set(back["not_compared"])


def test_an_older_diagnosis_is_taken_to_have_run_its_page_checks():
    assert content_checks_ran(None) == ["render", "structured_data", "llms"] and site_checks_ran(None) == []
    new = {"checks_ran": ["sitemap"], "content_ran": ["render"], "not_checked": []}
    assert content_checks_ran(new) == ["render"] and site_checks_ran(new) == ["sitemap"]


# ---- 2. repeated findings are shown once -------------------------------------------------------------------------------------------

def repeats(n=4):
    return [make_finding("SCHEMA_LOCALBUSINESS_NO_AREASERVED", what=f"A block of type ['LocalBusiness'] has no areaServed ({i})",
                         evidence=f"types ['LocalBusiness']; address: street {i}", fixable_on_platform=True, where_to_fix="w", source="s")
            for i in range(n)]


def test_repeated_findings_become_one_line_with_a_count_and_three_examples():
    found = repeats(4) + [plain_finding("RENDER_H1_MISSING", "low")]
    before = copy.deepcopy(found)
    groups = group_findings(found)
    assert found == before                                                            # the input is not touched
    assert [(g["code"], g["count"]) for g in groups] == [("SCHEMA_LOCALBUSINESS_NO_AREASERVED", 4), ("RENDER_H1_MISSING", 1)]
    g = groups[0]
    assert g["what"].startswith("4 structured-data blocks have no areaServed") and len(g["examples"]) == 3
    assert groups[1]["examples"] == [] and groups[1]["what"] == "RENDER_H1_MISSING what"
    assert len(g["findings"]) == 4


def test_a_code_without_its_own_plural_text_says_how_many_times():
    g = group_findings([plain_finding("RENDER_H1_MISSING", "low")] * 3)[0]
    assert g["what"].endswith("(3 times)") and g["count"] == 3 and len(g["examples"]) == 1          # the same evidence is not repeated


def test_the_most_serious_severity_wins_in_a_group():
    f = [plain_finding("RENDER_H1_MISSING", "low"), plain_finding("RENDER_H1_MISSING", "high")]
    assert group_findings(f)[0]["severity"] == "high"


THREE_BLOCKS = "".join('<script type="application/ld+json">%s</script>' % json.dumps(
    {"@context": "https://schema.org", "@type": "LocalBusiness", "name": f"Acme {i}", "address": f"{i} Main St"}) for i in range(3))


def test_the_stored_findings_and_compare_are_unchanged_by_grouping(business_id, tmp_path):
    routes = good_site(**{"https://acme.example/": response(200, page(300, extra_body=THREE_BLOCKS))})
    routes["https://acme.example/"] = response(200, page(300).replace("</head>", THREE_BLOCKS + "</head>"))
    out, _ = diagnose(routes, tmp_path, business_id=business_id, checkpoint="before")
    code = "SCHEMA_LOCALBUSINESS_NO_AREASERVED"
    assert [f["code"] for f in out.findings].count(code) == 3                              # the diagnosis really holds three
    conn = get_connection()
    try:
        stored = conn.execute("SELECT findings FROM diagnoses WHERE id = %s", (out.diagnosis_id,)).fetchone()[0]
        da = pick_diagnosis(conn, business_id, "before", None)
    finally:
        conn.close()
    assert [f["code"] for f in stored].count(code) == 3 and stored == out.findings          # stored exactly as made, repeats and all
    # what people read shows it once
    md = build_markdown(out.data)
    assert md.count(f"### `{code}`") == 1 and "3 structured-data blocks have no areaServed" in md and "counting repeats" in md
    assert len(out.data["findings"]) == len(out.findings)                                   # the report's JSON keeps every record
    # and compare still matches on the code alone, exactly as before
    before = copy.deepcopy(da)
    same = compare_diagnoses(da, da, [], "2026-10-29")
    assert da == before and [c["code"] for c in same["still_open"]].count(code) == 1 and same["new"] == [] and same["fixed"] == []
    gone = compare_diagnoses(da, {**da, "findings": [f for f in da["findings"] if f["code"] != code]}, [], "2026-10-29")
    assert [c["code"] for c in gone["fixed"]].count(code) == 1 and gone["fixed_count"] >= 1


def test_the_terminal_shows_a_repeated_finding_once(business_id, tmp_path, monkeypatch):
    routes = good_site(**{"https://acme.example/": response(200, page(300).replace("</head>", THREE_BLOCKS + "</head>"))})
    out, _ = diagnose(routes, tmp_path)
    monkeypatch.setattr(cli, "run_diagnosis", lambda *a, **k: out)
    text = CliRunner().invoke(cli.app, ["diagnose", URL]).output
    assert text.count("SCHEMA_LOCALBUSINESS_NO_AREASERVED") == 1 and "3 structured-data blocks have no areaServed" in text
    assert "e.g. types" in text and "counting repeats" in text


def test_the_client_report_shows_a_repeated_finding_once_with_how_often(biz, tmp_path):
    add_diagnosis(biz, "before", repeats(4))
    html = html_of(make(biz, tmp_path, "starting_point"))
    from client_report.strings import STRINGS
    from html import escape
    wording = STRINGS["en"]["finding.SCHEMA_LOCALBUSINESS_NO_AREASERVED"]
    assert html.count(escape(wording)) == 1                                       # the sentence appears once, not four times
    assert "This was found in 4 places." in html


def test_the_dossier_lists_a_repeated_finding_once(biz, tmp_path):
    from dossier.run import make_dossier
    from datetime import date
    add_diagnosis(biz, "before", repeats(4))
    res = make_dossier(biz, clients_root=tmp_path / "clients", today=date(2026, 10, 29), question_dir=tmp_path / "q")
    problems = next(s for s in res.data["sections"] if s["key"] == "problems")
    items = [i for i in problems["data"]["items"] if i["source"].endswith("SCHEMA_LOCALBUSINESS_NO_AREASERVED")]
    assert len(items) == 1 and items[0]["text"].startswith("4 structured-data blocks have no areaServed") and "e.g." in items[0]["text"]


# ---- 3. a crawler that cannot connect ------------------------------------------------------------------------------------------

def gptbot(request) -> bool:
    return "GPTBot" in request.headers.get("user-agent", "") and "OAI" not in request.headers.get("user-agent", "")


def test_a_connection_failure_is_retried_once_after_five_seconds(tmp_path):
    calls = {"n": 0}

    def flaky(request):
        if request.url.path == "/" and gptbot(request):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("[Errno 61] Connection refused")
        return None
    out, sleeps = diagnose(good_site(), tmp_path, override=flaky)
    assert calls["n"] == 2 and CONNECT_RETRY_SECONDS == 5 and 5 in sleeps
    assert out.facts.crawler.verdicts["GPTBot"][0] == "allowed" and "CRAWLER_CONNECT_UNCONFIRMED" not in codes_in(out)


def test_a_connection_that_still_fails_is_unconfirmed_not_blocked(tmp_path):
    calls = {"n": 0}

    def down(request):
        if request.url.path == "/" and gptbot(request):
            calls["n"] += 1
            raise httpx.ConnectError("[Errno 61] Connection refused")
        return None
    out, sleeps = diagnose(good_site(), tmp_path, override=down)
    assert calls["n"] == 2 and sleeps.count(5) == 1                                        # one retry, one pause
    f = next(f for f in out.findings if f["code"] == "CRAWLER_CONNECT_UNCONFIRMED")
    assert f["unconfirmed"] is True and f["what"] == "Could not connect as GPTBot; may be temporary; re-run to confirm" and f["severity"] == "low"
    assert "CRAWLER_BLOCKED_GPTBOT" not in codes_in(out)
    assert out.verdict.verdict == "serve" and "Not confirmed: we could not connect as GPTBot" in out.verdict.reason
    assert "unconfirmed" in build_markdown(out.data).split("## Findings")[1]
    assert explanation_for("CRAWLER_CONNECT_UNCONFIRMED") and "CRAWLER_CONNECT_UNCONFIRMED" in REGISTRY


def test_an_unconfirmed_connection_is_never_the_only_reason_for_a_conditional_verdict(tmp_path):
    def all_down(request):
        if request.url.path == "/" and is_bot(request):
            raise httpx.ConnectError("[Errno 61] Connection refused")
        return None
    out, _ = diagnose(good_site(), tmp_path, override=all_down)
    assert [f["code"] for f in out.findings].count("CRAWLER_CONNECT_UNCONFIRMED") == 5
    assert out.verdict.verdict == "serve" and out.verdict.reasons == []
    grouped = [g for g in group_findings(out.findings) if g["code"] == "CRAWLER_CONNECT_UNCONFIRMED"]
    assert len(grouped) == 1 and grouped[0]["count"] == 5 and grouped[0]["unconfirmed"] and "5 AI crawlers" in grouped[0]["what"]


def test_other_real_reasons_still_make_the_verdict_conditional(tmp_path):
    def mixed(request):
        if request.url.path == "/" and "OAI-SearchBot" in request.headers.get("user-agent", ""):
            return response(403, "forbidden")
        if request.url.path == "/" and gptbot(request):
            raise httpx.ConnectError("[Errno 61] Connection refused")
    out, _ = diagnose(good_site(), tmp_path, override=mixed)
    assert out.verdict.verdict == "conditional" and [r.code for r in out.verdict.reasons] == ["CRAWLER_BLOCKED_OAI_SEARCHBOT"]
    assert "GPTBOT" not in out.verdict.reason.upper().replace("OAI", "")


def test_a_refusal_or_a_challenge_is_never_retried_and_stays_a_block(tmp_path):
    calls = {"refused": 0, "challenged": 0}

    def answers(request):
        ua = request.headers.get("user-agent", "")
        if request.url.path == "/" and gptbot(request):
            calls["refused"] += 1
            return response(403, "forbidden")
        if request.url.path == "/" and "PerplexityBot" in ua:
            calls["challenged"] += 1
            return response(403, CAPTCHA, {"cf-mitigated": "challenge"})
    out, sleeps = diagnose(good_site(), tmp_path, override=answers)
    assert calls == {"refused": 1, "challenged": 1} and 5 not in sleeps
    assert {"CRAWLER_BLOCKED_GPTBOT", "CRAWLER_BLOCKED_PERPLEXITYBOT"} <= codes_in(out) and "CRAWLER_CONNECT_UNCONFIRMED" not in codes_in(out)


def test_the_dossier_says_a_crawler_could_not_connect():
    rows = diagnosis_column({"findings": [], "crawler": {"bots": {"GPTBot": {"verdict": "connection_unconfirmed"}}}, "render": None,
                             "jsonld": [], "checks_ran": [], "site_checks": None, "cdn": None, "host": None, "framework": None})
    assert rows["GPTBot (training bot)"] == "could not connect (unconfirmed, may be temporary)"


# ---- 4. a control question never changes a score ------------------------------------------------------------------------------------

QUESTIONS = ["which supplier covers events?", "who rents water stations in town?", "best refill option for festivals",
             "cheapest water service for venues", "How long do tortoises live in captivity?"]


def control_run(b, control_answers, *, checkpoint="before", control=True):
    questions = QUESTIONS + [f"What does {b['name']} do?"]
    named = lambda i: [nb(CLIENT, client=True, rec=True)] if i in (0, 2) else [nb("Rival Co")]
    specs = []
    for q in range(6):
        for e in ("chatgpt", "perplexity"):
            if q == 4:
                specs.append({"q": q, "e": e, **control_answers(e)})
            else:
                specs.append({"q": q, "e": e, "named": [nb(CLIENT, client=True, rec=True)] if q == 5 else named(q),
                              "claims": [claim("phone is 999", "Phone", "incorrect")] if q == 5 else None})
    return build_run(b, specs, questions=questions, tags={4: {"control": True}} if control else None, checkpoint=checkpoint)


def report_for(b, run_id):
    conn = get_connection()
    try:
        return report_data.build_report(conn, b, report_data.select_run(conn, b["id"], run=run_id)[0], partial=False)
    finally:
        conn.close()


def scored_parts(data: dict) -> str:
    keep = ("headline", "share_of_voice", "reasons", "descriptors", "wrong_facts", "sources", "brand", "by_engine", "by_length",
            "by_language", "targets_vs_control")
    return json.dumps({k: data[k] for k in keep}, sort_keys=True, default=str)


def test_a_control_question_never_changes_any_score(biz):
    confirm_facts(biz, Phone="514-555-0100")
    quiet = control_run(biz, lambda e: {"named": []})
    loud = control_run(biz, lambda e: {"named": [nb(CLIENT, client=True, rec=True, desc=["premium"]), nb("Rival Co")],
                                       "claims": [claim("phone is 000", "Phone", "incorrect")],
                                       "sources": [{"url": f"https://{biz['domain']}/x", "domain": biz["domain"], "origin": "pasted"}]})
    a, b = report_for(biz, quiet), report_for(biz, loud)
    assert scored_parts(a) == scored_parts(b)                                       # every score, range, count and list is identical
    vis = a["headline"]["measures"]["visibility"]
    assert (vis["num"], vis["den"]) == (4, 8)                                      # 4 discovery questions x 2 engines; the control is not in the 8
    assert a["question_mix"]["control_questions"] == 1 and a["question_mix"]["discovery_questions"] == 4
    assert a["headline"]["measures"]["accuracy"]["den"] == b["headline"]["measures"]["accuracy"]["den"]


def test_the_control_questions_have_their_own_section_that_shows_the_answers(biz):
    run_id = control_run(biz, lambda e: {"named": [nb("Reptile House")], "text": "Tortoises can live more than 100 years. " * 12})
    data = report_for(biz, run_id)
    ctl = data["controls"]
    assert ctl["questions"] == 1 and len(ctl["answers"]) == 2 and ctl["answers"][0]["businesses_named"] == ["Reptile House"]
    assert ctl["answers"][0]["excerpt"].startswith("Tortoises can live") and ctl["answers"][0]["excerpt"].endswith("…")
    md = render_markdown(json.loads(json.dumps(data, default=str)))
    assert "## Control questions" in md and "not part of any figure" in md and "named: Reptile House" in md
    assert next(q for q in data["appendix"] if q["question"] == QUESTIONS[4])["control"] is True


def test_a_control_is_unscored_even_if_the_model_could_not_read_it(biz):
    run_id = control_run(biz, lambda e: {"named": None})
    data = report_for(biz, run_id)
    assert data["controls"]["answers"][0]["businesses_named"] is None
    assert "not read by the model" in render_markdown(json.loads(json.dumps(data, default=str)))


def test_without_the_control_tag_the_same_question_is_scored(biz):
    run_id = control_run(biz, lambda e: {"named": []}, control=False)
    vis = report_for(biz, run_id)["headline"]["measures"]["visibility"]
    assert (vis["num"], vis["den"]) == (4, 10)                                    # now it is just another discovery question


def test_a_control_question_never_changes_a_comparison(biz, tmp_path):
    qs = make_qs(10)
    qs.items[9].control = True
    named = lambda: [{"name": CLIENT, "domain": None, "is_client": True, "recommended": True, "position": 1, "reason": None, "descriptors": []}]

    def answers(control_names_client):
        def fn(q, e, r):
            if q == 9:
                return {"named": named if control_names_client else (lambda: [])}
            return {"named": named if q % 2 == 0 else RIVAL}
        return fn
    add_run(biz, qs, answers(True), checkpoint="before")
    add_run(biz, qs, answers(False), checkpoint="after_aeo")
    res = compare(biz, tmp_path)
    vis = measure(res)
    assert vis["a"]["value"] == vis["b"]["value"] == pytest.approx(10 / 18 * 100) and vis["change"]["value"] == 0   # q0, 2, 4, 6, 8 name the client
    assert vis["a"]["den"] == vis["b"]["den"] == 18                                              # 9 discovery questions x 2 engines
    assert res.data["audit"]["headline"]["discovery_questions"] == 9
