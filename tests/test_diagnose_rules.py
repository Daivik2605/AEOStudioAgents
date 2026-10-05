"""The decision rules (PLAN.md §5): decline, conditional, serve, and the override rule."""

from __future__ import annotations

import pytest

from diagnose import platform_data as pd
from diagnose.findings import build_findings
from diagnose.rules import decide
from tests.helpers_diagnose import SPA_HTML, make_facts, page


# ---- Decline table -----------------------------------------------------------------------------

@pytest.mark.parametrize("platform", ["google_sites", "notion", "weebly", "godaddy_builder"])
def test_every_platform_on_the_decline_table_is_declined_with_its_source(platform):
    verdict = decide(make_facts(platform=platform))
    row = pd.DECLINE_TABLE[platform]
    assert verdict.verdict == "decline"
    assert row["why"] in verdict.reason
    assert row["source"] in verdict.reason


def test_a_decline_also_produces_the_unserveable_finding():
    findings = build_findings(make_facts(platform="notion"))
    f = next(f for f in findings if f["code"] == "PLATFORM_UNSERVEABLE")
    assert (f["severity"], f["fixable_on_platform"]) == ("critical", False)
    assert f["source"] == pd.DECLINE_TABLE["notion"]["source"]


def test_decline_wins_even_when_the_crawler_test_also_fails():
    facts = make_facts(platform="weebly", bot_verdicts={"OAI-SearchBot": "ua_blocked"})
    assert decide(facts).verdict == "decline"


# ---- Conditional table ---------------------------------------------------------------------------

@pytest.mark.parametrize("platform,plan", [("squarespace", "basic"), ("wordpress_com", "free"),
                                           ("framer", "free"), ("framer", "basic")])
def test_plan_gated_platforms_are_conditional_with_the_upgrade_named(platform, plan):
    verdict = decide(make_facts(platform=platform, plan=plan))
    gate = pd.PLAN_GATES[platform]
    assert verdict.verdict == "conditional"
    assert gate["fix"] in verdict.reason and gate["source"] in verdict.reason
    assert verdict.reasons[0].code == "PLATFORM_PLAN_TOO_LOW"


def test_squarespace_with_an_unseen_plan_is_served_but_the_plan_is_flagged():
    facts = make_facts(platform="squarespace", plan=None)
    assert decide(facts).verdict == "serve"
    codes = [f["code"] for f in build_findings(facts)]
    assert "PLATFORM_PLAN_UNCONFIRMED" in codes and "PLATFORM_PLAN_TOO_LOW" not in codes


def test_a_known_paid_plan_clears_the_gate():
    assert decide(make_facts(platform="wordpress", plan="business_or_higher")).verdict == "serve"


def test_a_blocked_retrieval_bot_is_conditional_fix_the_edge_first():
    verdict = decide(make_facts(platform="wordpress", bot_verdicts={"OAI-SearchBot": "ua_blocked"}))
    assert verdict.verdict == "conditional"
    assert "Fix at the edge" in verdict.reason
    assert verdict.reasons[0].code == "CRAWLER_BLOCKED_OAI_SEARCHBOT"


def test_a_client_rendered_page_is_conditional_when_name_and_phone_are_missing():
    business = {"id": "x", "name": "Acme Water", "phone": "514-555-0199", "address": None, "alternate_names": []}
    facts = make_facts(platform=None, html=SPA_HTML, business=business)
    verdict = decide(facts)
    assert verdict.verdict == "conditional"
    assert verdict.reasons[0].code == "RENDER_CONTENT_MISSING"
    assert "separate project" in verdict.reason


def test_the_client_rendering_rule_is_ignored_on_platforms_that_always_server_render():
    # On Wix/Squarespace/etc. a 'content missing' result is our heuristic misfiring.
    facts = make_facts(platform="wix", html=SPA_HTML)
    assert facts.render.verdict == "content_missing"
    assert decide(facts).verdict == "serve"


def test_wp_engine_rate_limiting_uses_the_wp_engine_row():
    facts = make_facts(platform="wordpress", hosting=["wp_engine"], bot_verdicts={"ClaudeBot": "rate_limited"})
    verdict = decide(facts)
    assert verdict.verdict == "conditional"
    assert pd.CONDITIONAL_WP_ENGINE["source"] in verdict.reason
    f = next(f for f in build_findings(facts) if f["code"] == "CRAWLER_BLOCKED_CLAUDEBOT")
    assert f["fixable_on_platform"] is False


def test_robots_txt_blocking_a_retrieval_bot_is_conditional():
    facts = make_facts(platform="shopify", robots_allowed={"OAI-SearchBot": False})
    verdict = decide(facts)
    assert verdict.verdict == "conditional"
    assert verdict.reasons[0].code == "ROBOTS_DISALLOWS_OAI_SEARCHBOT"


def test_robots_failing_for_bots_but_not_browsers_is_conditional():
    facts = make_facts(platform="shopify", robots_outcome="unreachable", browser_ok=True)
    verdict = decide(facts)
    assert verdict.reasons[0].code == "ROBOTS_TXT_BOT_FETCH_FAILED"


def test_blocking_only_training_bots_is_a_low_finding_not_a_condition():
    facts = make_facts(platform="shopify", bot_verdicts={"GPTBot": "ua_blocked", "ClaudeBot": "ua_blocked"},
                       robots_allowed={"GPTBot": False, "ClaudeBot": False})
    assert decide(facts).verdict == "serve"
    sev = {f["code"]: f["severity"] for f in build_findings(facts)}
    assert sev["CRAWLER_BLOCKED_GPTBOT"] == "low" and sev["ROBOTS_DISALLOWS_CLAUDEBOT"] == "low"


def test_a_rate_limited_training_bot_still_counts():
    # Unlike a plain 'no', a 429 is not a stated choice.
    assert decide(make_facts(platform="shopify", bot_verdicts={"GPTBot": "rate_limited"})).verdict == "conditional"


def test_an_inconclusive_crawler_test_is_conditional_and_never_called_an_ai_block():
    facts = make_facts(platform="wordpress", bot_verdicts={b: "blanket_blocked" for b in
                                                           ("OAI-SearchBot", "PerplexityBot", "Claude-SearchBot", "GPTBot", "ClaudeBot")})
    verdict = decide(facts)
    assert verdict.verdict == "conditional"
    assert verdict.reasons[0].code == "CRAWLER_TEST_INCONCLUSIVE"
    codes = [f["code"] for f in build_findings(facts)]
    assert "CRAWLER_TEST_INCONCLUSIVE" in codes and not any(c.startswith("CRAWLER_BLOCKED_") for c in codes)


# ---- Serve ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("platform", ["shopify", "wordpress", "drupal", "joomla", "webflow", "wix",
                                      "hubspot", "duda", "squarespace"])
def test_platforms_that_can_carry_the_work_are_served_when_crawlers_get_in(platform):
    verdict = decide(make_facts(platform=platform))
    assert verdict.verdict == "serve"
    assert pd.PLATFORM_LABELS[platform] in verdict.reason


def test_a_custom_server_rendered_site_is_served():
    verdict = decide(make_facts(platform=None))
    assert verdict.verdict == "serve" and "custom site" in verdict.reason


# ---- The rule that overrides the table ---------------------------------------------------------------------

def test_the_crawler_test_beats_the_platform_table():
    """Squarespace Core+ is on the 'serve' side of the table. A failing crawler test is still the finding."""
    clean = make_facts(platform="squarespace", plan="core")
    blocked = make_facts(platform="squarespace", plan="core", bot_verdicts={"PerplexityBot": "challenged"})
    assert decide(clean).verdict == "serve"
    verdict = decide(blocked)
    assert verdict.verdict == "conditional"
    assert verdict.reasons[0].code == "CRAWLER_BLOCKED_PERPLEXITYBOT"       # the measured result comes first
    assert verdict.reason.startswith("Possible after one thing changes. AI crawlers are blocked")


def test_the_crawler_result_leads_when_a_plan_gate_also_applies():
    facts = make_facts(platform="squarespace", plan="basic", bot_verdicts={"OAI-SearchBot": "ua_blocked"})
    verdict = decide(facts)
    assert [r.code for r in verdict.reasons] == ["CRAWLER_BLOCKED_OAI_SEARCHBOT", "PLATFORM_PLAN_TOO_LOW"]


def test_the_same_facts_always_give_the_same_verdict():
    facts = make_facts(platform="squarespace", plan="basic", bot_verdicts={"OAI-SearchBot": "ua_blocked"})
    assert decide(facts) == decide(facts)
    assert build_findings(facts) == build_findings(facts)


# ---- Readiness lookup ------------------------------------------------------------------------------------------

def test_readiness_uses_only_the_researched_ranges():
    assert pd.readiness_score("shopify", None, False) == 97.5
    assert pd.readiness_score("squarespace", None, False) == 65.0
    assert pd.readiness_score("squarespace", "basic", False) == 12.5   # 'blocked by plan' range
    assert pd.readiness_score("notion", None, True) == 0.0
    assert pd.readiness_score(None, None, False) is None                # unknown platform: no invented number
