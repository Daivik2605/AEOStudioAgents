"""Render check, structured data, and the finding-code registry."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from diagnose import codes
from diagnose.crawler import BOTS
from diagnose.findings import build_findings
from diagnose.html_utils import parse_page
from diagnose.render import check_render
from diagnose.structured_data import analyse
from tests.helpers_diagnose import SPA_HTML, make_facts, page


def render(html, **kw):
    return check_render(parse_page(html), html, **kw)


# ---- HTML reading ---------------------------------------------------------------------------------------

def test_parser_reads_h1s_words_and_jsonld_exactly():
    block = '{"@context": "https://schema.org",\n  "@type": "Organization" }'
    html = page(10, h1="Hello <span>there</span>", jsonld=block)
    parsed = parse_page(html)
    assert parsed.h1_texts == ["Hello there"]
    assert parsed.jsonld_raw == [block]            # byte-for-byte, not re-serialised
    assert parsed.word_count_main == 12            # "Hello there" + 10 words; script/title not counted


def test_empty_h1_is_kept_as_an_empty_string():
    assert parse_page("<html><body><h1> </h1><h1><span></span></h1></body></html>").h1_texts == ["", ""]


def test_navigation_words_are_not_main_content():
    html = "<html><body><nav>home about contact</nav><div>real words here</div></body></html>"
    assert parse_page(html).word_count_main == 3


# ---- Render check --------------------------------------------------------------------------------------------

def test_a_normal_server_rendered_page_is_fine():
    assert render(page(600)).verdict == "fine"


def test_an_empty_app_shell_is_content_missing():
    r = render(SPA_HTML)
    assert r.verdict == "content_missing" and r.empty_mount_div


def test_a_javascript_required_notice_confirms_it():
    html = "<html><body><noscript>JavaScript must be enabled in order to use Notion.</noscript></body></html>"
    r = render(html)
    assert r.noscript_warning and r.verdict == "content_missing"


def test_business_facts_are_looked_for_in_the_readable_text():
    html = page(50, h1="Welcome", extra_body="<p>Call 514-555-0199. Visit us at 1234 Rue Saint-Denis, Montréal.</p>")
    r = render(html, names=["Acme Water"], phone="(514) 555 0199", address="1234 rue Saint-Denis, Montreal QC")
    assert r.details["name"]["in_readable_text"] is False       # the name appears nowhere on this page
    assert r.details["phone"]["in_readable_text"] is True       # digits compared, formatting ignored
    assert r.details["address"]["in_readable_text"] is True     # accents and case ignored


def test_the_business_name_in_the_h1_counts_as_found():
    r = render(page(50, h1="Acme Water Stations"), names=["Acme Water"])
    assert r.details["name"]["in_readable_text"] is True


def test_facts_only_in_structured_data_are_reported_as_raw_html_only():
    html = page(50, jsonld='{"telephone": "514-555-0199"}')
    d = render(html, phone="514-555-0199").details["phone"]
    assert (d["in_readable_text"], d["in_raw_html"]) == (False, True)


def test_unknown_facts_are_not_checked_not_assumed_missing():
    assert render(page(50)).details["phone"] == {"checked": False}


def test_missing_facts_become_findings_with_stable_codes():
    business = {"id": "x", "name": "Zed Corp", "phone": "999-111-2222", "address": "9 Nowhere Lane, Faraway",
                "alternate_names": []}
    facts = make_facts(platform="wordpress", business=business)
    found = {f["code"] for f in build_findings(facts)}
    assert {"RENDER_BUSINESS_NAME_MISSING", "RENDER_PHONE_MISSING", "RENDER_ADDRESS_MISSING"} <= found


def test_h1_findings():
    none = {f["code"] for f in build_findings(make_facts(html="<html><body>" + "word " * 500 + "</body></html>"))}
    empty = {f["code"] for f in build_findings(make_facts(html="<html><body><h1></h1><h1> </h1>" + "word " * 500 + "</body></html>"))}
    assert "RENDER_H1_MISSING" in none
    assert {"RENDER_H1_EMPTY", "RENDER_H1_MULTIPLE"} <= empty


# ---- Structured data ----------------------------------------------------------------------------------------------

# Modelled on the worked example in PLAN.md section 14.
OLAND_LIKE = [
    json.dumps({"@context": "https://schema.org", "@type": "Organization", "legalName": "11297775 Canada Inc",
                "sameAs": ["https://www.linkedin.com/company/oland?trk=public_profile"]}),
    json.dumps({"@context": "https://schema.org", "@type": "LocalBusiness", "name": "11297775 Canada Inc",
                "address": {"@type": "PostalAddress", "addressLocality": "Montreal"},
                "openingHours": ", , , , , , "}),
    json.dumps({"@context": "https://schema.org", "@type": "WebSite", "name": "O'land", "url": "https://x.example"}),
]


def issue_codes(raw, names=None):
    return {i.code for i in analyse(raw, names).issues}


def test_the_plan_14_example_gives_the_plan_14_findings():
    assert issue_codes(OLAND_LIKE) == {
        "SCHEMA_ORG_MISSING_NAME", "SCHEMA_NAME_IS_LEGAL_ENTITY", "SCHEMA_LOCALBUSINESS_NO_AREASERVED",
        "SCHEMA_OPENING_HOURS_EMPTY", "SCHEMA_SAMEAS_HAS_TRACKING_PARAMS", "SCHEMA_ENTITIES_UNLINKED"}


def test_no_jsonld_at_all():
    assert issue_codes([]) == {"SCHEMA_NONE_FOUND"}


def test_invalid_json_is_reported_and_other_blocks_still_read():
    assert issue_codes(["{not json", OLAND_LIKE[2]]) == {"SCHEMA_INVALID_JSON"}


def test_a_clean_block_has_no_issues():
    clean = json.dumps({"@type": "Organization", "name": "Acme Water", "sameAs": ["https://example.com/acme"]})
    assert issue_codes([clean], ["Acme Water"]) == set()


def test_a_legal_suffix_is_only_flagged_when_it_differs_from_the_known_brand():
    block = json.dumps({"@type": "Organization", "name": "Acme Water Inc"})
    assert issue_codes([block], ["Acme Water"]) == set()
    other = json.dumps({"@type": "Organization", "name": "Zeta Holdings Inc"})
    assert issue_codes([other], ["Acme Water"]) == {"SCHEMA_NAME_IS_LEGAL_ENTITY"}


def test_blocks_linked_by_id_are_not_flagged_as_unlinked():
    graph = json.dumps({"@graph": [
        {"@type": "Organization", "@id": "https://x.example/#org", "name": "Acme"},
        {"@type": "WebSite", "name": "Acme", "publisher": {"@id": "https://x.example/#org"}}]})
    assert "SCHEMA_ENTITIES_UNLINKED" not in issue_codes([graph])


def test_the_squarespace_legal_name_fix_uses_the_route_from_plan_3_4():
    facts = make_facts(platform="squarespace", html=page(500, jsonld=OLAND_LIKE[1]))
    f = next(f for f in build_findings(facts) if f["code"] == "SCHEMA_NAME_IS_LEGAL_ENTITY")
    assert f["where_to_fix"] == "Squarespace → Settings → Business Information → Business Name"


def test_a_closed_platform_gets_a_platform_specific_route_and_unknown_platforms_say_so():
    sq = next(f for f in build_findings(make_facts(platform="squarespace", html=page(500)))
              if f["code"] == "SCHEMA_NONE_FOUND")
    assert "Code Injection" in sq["where_to_fix"] and sq["fixable_on_platform"] is True
    unk = next(f for f in build_findings(make_facts(platform=None, html=page(500)))
               if f["code"] == "SCHEMA_NONE_FOUND")
    assert unk["fixable_on_platform"] is None


def test_squarespace_robots_txt_is_reported_as_not_fixable():
    facts = make_facts(platform="squarespace", robots_allowed={"OAI-SearchBot": False})
    f = next(f for f in build_findings(facts) if f["code"] == "ROBOTS_DISALLOWS_OAI_SEARCHBOT")
    assert f["fixable_on_platform"] is False and "never" in f["where_to_fix"]


# ---- Finding codes ---------------------------------------------------------------------------------------------------------

def test_findings_have_exactly_the_plan_3_4_shape():
    for f in build_findings(make_facts(platform="squarespace", html=page(500))):
        assert list(f) == ["code", "severity", "what", "evidence", "fixable_on_platform", "where_to_fix", "source"]
        assert f["severity"] in codes.SEVERITIES and f["where_to_fix"] and f["source"]


def test_unregistered_codes_are_refused():
    with pytest.raises(ValueError, match="REGISTRY"):
        codes.make_finding("MADE_UP_CODE", what="x", evidence="x", fixable_on_platform=None,
                           where_to_fix="x", source="x")


def test_every_code_in_the_source_is_registered():
    """A typo in a code string anywhere in diagnose/ would otherwise only show up at run time."""
    text = "".join(p.read_text() for p in Path("diagnose").glob("*.py") if p.name != "codes.py")
    used = set(re.findall(r'"((?:PLATFORM|HOST|CRAWLER|ROBOTS|CONTENT|LLMS|RENDER|SCHEMA)_[A-Z_]+[A-Z])"', text))
    assert used and used <= set(codes.REGISTRY)


def test_every_bot_has_its_own_crawler_and_robots_codes():
    for bot in BOTS:
        assert f"CRAWLER_BLOCKED_{bot.code_suffix}" in codes.REGISTRY
        assert f"ROBOTS_DISALLOWS_{bot.code_suffix}" in codes.REGISTRY


def test_codes_look_like_codes():
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]+", c) for c in codes.REGISTRY)
