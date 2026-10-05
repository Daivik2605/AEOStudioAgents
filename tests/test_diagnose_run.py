"""End to end: `run_diagnosis` and the `aeo diagnose` command, against the real
test database, with a fake website (no network) and a fake Anthropic client."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from typer.testing import CliRunner

from aeo import cli
from core import journal
from core.db import get_connection
from diagnose import run as run_module
from diagnose.run import DiagnoseError, run_diagnosis
from tests.conftest import FakeAnthropicClient, text_response
from tests.helpers_diagnose import is_bot, make_fetcher, page, response, site

URL = "https://acme.example/"
NOTION_SHELL = "<html><body><noscript>JavaScript must be enabled in order to use Notion.</noscript></body></html>"
ROBOTS = "User-agent: *\nAllow: /\n"
SHOPIFY_PAGE = page(600, extra_head='<link href="https://cdn.shopify.com/s/x.css">')


def good_site(body=SHOPIFY_PAGE):
    return site({"/": body, "/robots.txt": ROBOTS, "/llms.txt": response(404, "nope")})


def rows(sql, *params):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def journal_rows(business_id):
    return rows("SELECT entry_type, details, id, related_table, related_id, created_at FROM client_journal "
                "WHERE business_id = %s ORDER BY created_at, entry_type", business_id)


def diagnose(handler, tmp_path, **kw):
    kw.setdefault("explain_client", FakeAnthropicClient([text_response("Plain words about the site.")]))
    return run_diagnosis(URL, fetcher=make_fetcher(handler), reports_root=tmp_path, **kw)


# ---- The decline flow (PLAN.md §3.1, §5, §7) ------------------------------------------------------

def test_decline_sets_the_status_and_writes_both_journal_entries_and_the_linked_reason(business_id, tmp_path):
    client = FakeAnthropicClient([text_response("This site cannot carry the work.")])
    out = diagnose(good_site(NOTION_SHELL), tmp_path, business_id=business_id, checkpoint="old_site",
                   explain_client=client)

    assert out.verdict.verdict == "decline"
    assert out.decline_result == "declined"
    assert rows("SELECT status FROM businesses WHERE id = %s", business_id) == [("declined",)]

    entries = journal_rows(business_id)
    by_type = {}
    for e in entries:
        by_type.setdefault(e[0], []).append(e)
    assert sorted(by_type) == ["decline_recommended", "diagnosed", "note", "status_change"]
    assert all(len(v) == 1 for v in by_type.values())

    # The trigger wrote the status_change; the reason is a note LINKED to it (PLAN.md §7).
    status_change, note = by_type["status_change"][0], by_type["note"][0]
    assert status_change[1] == {"from": "prospect", "to": "declined"}
    assert note[1] == {"reason_for": "declined", "reason": out.verdict.reason}
    assert (note[3], note[4]) == ("client_journal", status_change[2])
    assert "Notion" in note[1]["reason"] and "10 Aug 2026" in note[1]["reason"]   # sourced reason from the table

    # The diagnosis row is linked from both diagnosis journal entries.
    diag_id = rows("SELECT id FROM diagnoses WHERE business_id = %s", business_id)[0][0]
    assert by_type["diagnosed"][0][4] == diag_id and by_type["decline_recommended"][0][4] == diag_id
    assert by_type["decline_recommended"][0][1]["pitch_status"] == "pending_human_approval"

    # Order: diagnosed, then the status change and its reason, then decline_recommended.
    order = [e[0] for e in sorted(entries, key=lambda e: e[5])]
    assert order[0] == "diagnosed" and order[-1] == "decline_recommended"


def test_decline_writes_no_pitch_and_says_so(business_id, tmp_path):
    out = diagnose(good_site(NOTION_SHELL), tmp_path, business_id=business_id)
    names = sorted(p.name for p in out.report_dir.iterdir())
    assert [n for n in names if "pitch" in n.lower()] == []      # only the diagnosis files, no pitch
    assert {"diagnosis.json", "diagnosis.md", "raw_page.html"} <= set(names) and len(names) == 6
    md = (out.report_dir / "diagnosis.md").read_text()
    assert "No pitch has been written" in md and "A person must type `yes` first" in md


def test_only_a_prospect_is_declined_automatically(business_id, tmp_path):
    conn = get_connection()
    conn.execute("UPDATE businesses SET status = 'client' WHERE id = %s", (business_id,))
    conn.close()
    out = diagnose(good_site(NOTION_SHELL), tmp_path, business_id=business_id)
    assert out.decline_result == "not_a_prospect"
    assert rows("SELECT status FROM businesses WHERE id = %s", business_id) == [("client",)]
    types = [e[0] for e in journal_rows(business_id)]
    assert "status_change" in types            # only the one from the manual UPDATE above
    assert types.count("status_change") == 1 and "note" not in types
    assert "decline_recommended" in types      # the recommendation is still recorded


def test_diagnosing_an_already_declined_business_does_not_decline_it_twice(business_id, tmp_path):
    diagnose(good_site(NOTION_SHELL), tmp_path, business_id=business_id,
             explain_client=FakeAnthropicClient([text_response("a")]))
    out = diagnose(good_site(NOTION_SHELL), tmp_path, business_id=business_id,
                   explain_client=FakeAnthropicClient([text_response("b")]))
    assert out.decline_result == "already_declined"
    types = [e[0] for e in journal_rows(business_id)]
    assert types.count("status_change") == 1 and types.count("note") == 1
    assert types.count("diagnosed") == 2


def test_a_decline_without_a_business_prints_and_writes_files_but_changes_nothing_else(tmp_path):
    client = FakeAnthropicClient([])   # any model call would fail the test
    out = diagnose(good_site(NOTION_SHELL), tmp_path, explain_client=client)
    assert out.verdict.verdict == "decline" and out.decline_result is None
    assert client.messages.calls == []
    assert (out.report_dir / "diagnosis.md").exists()


# ---- The AI step ------------------------------------------------------------------------------------------

def test_the_model_only_explains_and_the_spend_is_logged(business_id, tmp_path):
    client = FakeAnthropicClient([text_response("Your site is in good shape.", input_tokens=1000, output_tokens=100)])
    out = diagnose(good_site(), tmp_path, business_id=business_id, explain_client=client)

    assert out.verdict.verdict == "serve" and out.ai_explanation == "Your site is in good shape."
    sent = json.loads(client.messages.calls[0]["messages"][0]["content"])
    assert sent["verdict"] == "serve"                                     # the rules' answer is handed to the model
    assert client.messages.calls[0]["model"] == "claude-sonnet-5"
    runs = rows("SELECT agent, model, prompt_version, status, input_tokens, output_tokens, cost_usd FROM runs "
                "WHERE business_id = %s", business_id)
    assert len(runs) == 1 and runs[0][:4] == ("diagnose", "claude-sonnet-5", "v1", "success")
    assert float(runs[0][6]) == pytest.approx(1000 / 1e6 * 2.0 + 100 / 1e6 * 10.0)   # price from model_prices.json
    assert "Your site is in good shape." in (out.report_dir / "diagnosis.md").read_text()


def test_the_model_can_not_change_the_verdict(business_id, tmp_path):
    client = FakeAnthropicClient([text_response("Actually this site is perfect and should be declined.")])
    out = diagnose(good_site(), tmp_path, business_id=business_id, explain_client=client)
    assert out.verdict.verdict == "serve"
    assert rows("SELECT verdict FROM diagnoses WHERE business_id = %s", business_id) == [("serve",)]


def test_a_failed_model_call_does_not_lose_the_diagnosis_and_is_logged_as_failed(business_id, tmp_path):
    class Boom:
        class messages:  # noqa: N801
            @staticmethod
            def create(**_):
                raise RuntimeError("api down")
    out = diagnose(good_site(), tmp_path, business_id=business_id, explain_client=Boom())
    assert out.ai_explanation is None and any("api down" in w for w in out.warnings)
    assert rows("SELECT status, error FROM runs WHERE business_id = %s", business_id) == [("failed", "api down")]
    assert rows("SELECT verdict FROM diagnoses WHERE business_id = %s", business_id) == [("serve",)]
    assert "No model-written summary" in (out.report_dir / "diagnosis.md").read_text()


def test_no_business_means_no_model_call_at_all(tmp_path):
    client = FakeAnthropicClient([])
    out = diagnose(good_site(), tmp_path, explain_client=client)
    assert client.messages.calls == [] and out.ai_explanation is None


# ---- Storage -------------------------------------------------------------------------------------------------------

def test_the_diagnoses_row_holds_the_raw_data(business_id, tmp_path):
    ld = '{"@type": "Organization",\n "name": "Acme"}'
    html = page(600, extra_head='<link href="https://cdn.shopify.com/s/x.css">', jsonld=ld)
    out = diagnose(good_site(html), tmp_path, business_id=business_id, checkpoint="new_site_no_aeo")
    (row,) = rows("SELECT checkpoint, url, platform, robots_txt, existing_jsonld, crawler_access, render, findings, "
                  "verdict, verdict_reason, readiness_score, business_id FROM diagnoses WHERE business_id = %s",
                  business_id)
    checkpoint, url, platform, robots_txt, jsonld, crawler, render, findings, verdict, reason, readiness, bid = row
    assert (checkpoint, url, platform, verdict) == ("new_site_no_aeo", URL, "shopify", "serve")
    assert robots_txt == ROBOTS                                  # raw, exactly as served
    assert jsonld == [ld]                                        # raw block, untouched (newline and all)
    assert set(crawler["bots"]) == {"OAI-SearchBot", "PerplexityBot", "Claude-SearchBot", "GPTBot", "ClaudeBot"}
    assert render["word_count"] > 500 and findings == out.findings
    assert float(readiness) == 97.5 and str(bid) == business_id and reason == out.verdict.reason


def test_a_bare_url_is_stored_with_no_business(tmp_path):
    out = diagnose(good_site(), tmp_path)
    (row,) = rows("SELECT business_id, checkpoint FROM diagnoses WHERE id = %s", out.diagnosis_id)
    assert row == (None, None)


def test_report_files_use_the_plan_10_layout(business_id, tmp_path):
    domain = rows("SELECT domain FROM businesses WHERE id = %s", business_id)[0][0]
    out = diagnose(good_site(), tmp_path, business_id=business_id, checkpoint="old_site")
    assert out.report_dir == tmp_path / domain / "old_site"
    data = json.loads((out.report_dir / "diagnosis.json").read_text())
    assert data["verdict"] == "serve" and data["diagnosis_id"] == out.diagnosis_id
    assert data["findings"] == out.findings and data["limits"]


def test_without_a_business_the_slug_is_the_bare_domain_and_the_checkpoint_defaults(tmp_path):
    out = diagnose(good_site(), tmp_path)
    assert out.report_dir == tmp_path / "acme.example" / "unspecified"


def test_a_hostile_checkpoint_name_cannot_escape_the_reports_folder(tmp_path):
    out = diagnose(good_site(), tmp_path, checkpoint="../../etc")
    assert tmp_path in out.report_dir.parents


def test_rerunning_a_checkpoint_adds_a_numbered_run_and_keeps_the_old_one(tmp_path):
    day1 = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    day2 = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    first = diagnose(good_site(), tmp_path, checkpoint="q1", now=day1)
    second = diagnose(good_site(), tmp_path, checkpoint="q1", now=day2)
    assert (first.run_number, second.run_number) == (1, 2)
    names = {p.name for p in second.report_dir.iterdir()}
    assert names == {
        "diagnosis.md", "diagnosis.json", "raw_page.html",
        "diagnosis_run1_2026-10-02.md", "diagnosis_run1_2026-10-02.json", "raw_page_run1_2026-10-02.html",
        "diagnosis_run2_2026-10-05.md", "diagnosis_run2_2026-10-05.json", "raw_page_run2_2026-10-05.html"}
    d = second.report_dir
    # "Latest" is the newest run; the numbered files are each run as it was.
    assert (d / "diagnosis.md").read_text() == (d / "diagnosis_run2_2026-10-05.md").read_text()
    assert "2026-10-02" in (d / "diagnosis_run1_2026-10-02.md").read_text()
    assert "2026-10-05" in (d / "diagnosis_run2_2026-10-05.md").read_text()


def test_raw_page_is_the_exact_bytes_of_the_control_fetch(tmp_path):
    # Non-ASCII on purpose: the file must hold the bytes as served, not a re-encoded copy.
    body = SHOPIFY_PAGE.replace("</body>", "<p>caf\u00e9 \u2014 d\u00e9j\u00e0 vu</p></body>")
    served = body.encode("utf-8")
    out = diagnose(good_site(body), tmp_path, checkpoint="q1", now=datetime(2026, 10, 5, 9, tzinfo=timezone.utc))
    assert (out.report_dir / "raw_page.html").read_bytes() == served
    assert (out.report_dir / "raw_page_run1_2026-10-05.html").read_bytes() == served
    assert out.facts.crawler.control.body == served          # the same fetch that fed the render check


def test_no_raw_page_is_written_when_nothing_came_back(tmp_path):
    def down(request):
        raise httpx.ConnectError("no route")
    out = diagnose(down, tmp_path, checkpoint="q1")
    assert not (out.report_dir / "raw_page.html").exists()
    assert (out.report_dir / "diagnosis.md").exists()


def test_files_are_still_written_when_the_database_is_unreachable(tmp_path, monkeypatch):
    def down():
        raise RuntimeError("connection refused")
    monkeypatch.setattr(run_module, "get_connection", down)
    out = diagnose(good_site(), tmp_path)
    assert out.diagnosis_id is None and (out.report_dir / "diagnosis.md").exists()
    assert any("Database not reachable" in w for w in out.warnings)


def test_an_unknown_business_is_refused_before_anything_is_fetched(tmp_path):
    fetched = []
    def handler(request):
        fetched.append(request.url)
        return response(200, page())
    with pytest.raises(DiagnoseError, match="no business"):
        run_diagnosis(URL, business_id="00000000-0000-0000-0000-000000000000",
                      fetcher=make_fetcher(handler), reports_root=tmp_path)
    assert fetched == []


def test_a_bad_address_is_refused():
    with pytest.raises(DiagnoseError):
        run_diagnosis("ftp://example.com", fetcher=make_fetcher(lambda r: response(200, "")))


def test_a_scheme_is_added_when_missing(tmp_path):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return good_site()(request)
    run_diagnosis("acme.example", fetcher=make_fetcher(handler), reports_root=tmp_path)
    assert seen[0].rstrip("/") == "https://acme.example"


# ---- Whole-run behaviour -----------------------------------------------------------------------------------------------

def test_a_blocked_site_is_conditional_and_leaves_the_status_alone(business_id, tmp_path):
    def handler(request):
        if is_bot(request):
            return response(403, "Forbidden")
        return good_site()(request)
    out = diagnose(handler, tmp_path, business_id=business_id)
    assert out.verdict.verdict == "conditional" and out.decline_result is None
    assert rows("SELECT status FROM businesses WHERE id = %s", business_id) == [("prospect",)]
    assert "decline_recommended" not in [e[0] for e in journal_rows(business_id)]
    assert "diagnosed" in [e[0] for e in journal_rows(business_id)]


def test_a_dead_site_gives_a_conditional_inconclusive_verdict_not_a_crash(tmp_path):
    def handler(request):
        raise httpx.ConnectError("no route to host")
    out = diagnose(handler, tmp_path)
    assert out.verdict.verdict == "conditional"
    assert out.verdict.reasons[0].code == "CRAWLER_TEST_INCONCLUSIVE"
    assert out.facts.robots is None and out.facts.render is None


def test_a_normal_run_stays_within_the_request_cap(tmp_path):
    fetcher = make_fetcher(good_site())
    run_diagnosis(URL, fetcher=fetcher, reports_root=tmp_path, use_ai=False)
    assert fetcher.requests_made == 8     # control, 5 bots, robots.txt, llms.txt


def test_the_profile_gives_the_render_check_something_to_look_for(business_id, tmp_path):
    conn = get_connection()
    conn.execute("UPDATE businesses SET name = 'Acme Water' WHERE id = %s", (business_id,))
    conn.execute("INSERT INTO business_profiles (business_id, version, source, phone, address, alternate_names) "
                 "VALUES (%s, 1, 'website', '(514) 555-0199', '1 Main St, Montreal', ARRAY['Acme'])", (business_id,))
    conn.close()
    html = page(600, h1="Hello", extra_head='<link href="https://cdn.shopify.com/s/x.css">',
                extra_body="<p>Call 514 555 0199</p>")
    out = diagnose(good_site(html), tmp_path, business_id=business_id)
    codes = {f["code"] for f in out.findings}
    assert "RENDER_BUSINESS_NAME_MISSING" in codes and "RENDER_ADDRESS_MISSING" in codes
    assert "RENDER_PHONE_MISSING" not in codes


# ---- Journal types and the CLI --------------------------------------------------------------------------------------------

def test_python_and_database_agree_on_the_journal_entry_types():
    (definition,) = rows("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                         "WHERE conname = 'client_journal_entry_type_check'")[0]
    in_db = set(__import__("re").findall(r"'([a-z_]+)'::text", definition))
    assert in_db == journal.ENTRY_TYPES
    assert {"diagnosed", "decline_recommended"} <= in_db


@pytest.fixture
def cli_site(tmp_path, monkeypatch):
    """Points the CLI at a fake website and a temp reports folder."""
    state = {"handler": good_site()}
    real = run_module.run_diagnosis
    def wrapped(url, **kw):
        return real(url, fetcher=make_fetcher(state["handler"]), reports_root=tmp_path,
                    explain_client=FakeAnthropicClient([text_response("Plain words.")]), **kw)
    monkeypatch.setattr(cli, "run_diagnosis", wrapped)
    return state


def test_cli_diagnose_prints_the_verdict_and_findings(cli_site):
    result = CliRunner().invoke(cli.app, ["diagnose", URL])
    assert result.exit_code == 0, result.output
    assert "Verdict: SERVE" in result.output and "Platform: Shopify" in result.output
    assert "LLMS_TXT_MISSING" in result.output and "diagnosis.md" in result.output


def test_cli_shows_each_finding_as_a_plain_sentence_then_the_code(cli_site):
    long_name = "11297775 Canada Inc"
    block = '{"@type": "Organization", "name": "%s"}' % long_name
    cli_site["handler"] = good_site(page(600, extra_head='<link href="https://cdn.shopify.com/s/x.css">', jsonld=block))
    out = CliRunner().invoke(cli.app, ["diagnose", URL], terminal_width=200).output
    lines = out.splitlines()
    start = next(i for i, l in enumerate(lines) if "finding(s)" in l)
    findings = [l for l in lines[start + 1:] if l.startswith("  [") or l.startswith(" " * 13)]
    # The plain sentence is there (unwrapped, whitespace collapsed), not just the code.
    flat = " ".join(" ".join(findings).split())
    assert "There is no /llms.txt." in flat and "(LLMS_TXT_MISSING)" in flat
    first = next(l for l in findings if "no /llms.txt" in l)
    assert first.startswith("  [low     ] There is no /llms.txt")      # sentence follows the severity
    assert first.index("There is no") < flat.index("(LLMS_TXT_MISSING)")  # code comes after, not before
    # Wrapped to ~100 columns, with continuation lines indented under the first word.
    assert all(len(l) <= cli.TERMINAL_WIDTH for l in findings if l.strip())
    wrapped = [l for l in findings if l.startswith(" " * 13) and l.strip()]
    assert wrapped and all(l[13] != " " for l in wrapped)   # lines up under the first word of the sentence
    # A code is never split across two lines.
    assert "(SCHEMA_NAME_IS_LEGAL_ENTITY)" in flat and any("(SCHEMA_NAME_IS_LEGAL_ENTITY)" in l for l in findings)


def test_cli_decline_prints_the_pitch_outline_and_stops(cli_site, business_id):
    cli_site["handler"] = good_site(NOTION_SHELL)
    result = CliRunner().invoke(cli.app, ["diagnose", URL, "--business", business_id, "--checkpoint", "old_site"])
    assert result.exit_code == 0, result.output
    assert "Verdict: DECLINE" in result.output and "A migration pitch would cover" in result.output
    assert "No pitch has been written" in result.output and "set to 'declined'" in result.output
    assert rows("SELECT status FROM businesses WHERE id = %s", business_id) == [("declined",)]


def test_cli_unknown_business_exits_with_an_error(cli_site):
    result = CliRunner().invoke(cli.app, ["diagnose", URL, "--business", "00000000-0000-0000-0000-000000000000"])
    assert result.exit_code == 1 and "no business" in result.output


def test_the_command_is_named_diagnose_not_the_default():
    help_text = CliRunner().invoke(cli.app, ["--help"]).output
    assert "diagnose" in help_text
