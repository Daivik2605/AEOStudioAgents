"""PLAN.md step 25: the checks that apply to any website: sitemap, www / http addresses, CDN / host / framework from the
headers, and the optional old-address check. Mocked HTTP only: every request goes to a fake transport, and a guard below
fails any test that tries to open a real connection."""

from __future__ import annotations

import gzip
import json
import socket
from datetime import datetime, timezone

import httpx
import pytest

from aeo import cli
from compare.diagnosis import compare_diagnoses
from core.db import get_connection
from diagnose import codes
from diagnose import infrastructure as infra
from diagnose.codes import CHECK_FOR_CODE, DEPENDS_ON, EXPLANATIONS, REGISTRY, make_finding
from diagnose.fetch import Fetcher, MAX_REDIRECT_HOPS, REQUEST_TIMEOUT_SECONDS, make_client
from diagnose.report import build_markdown
from diagnose.run import DiagnoseError, run_diagnosis
from diagnose.site_checks import (EXAMPLES_SHOWN, OLD_URL_CAP, SITE_CHECK_UA, parse_sitemap, robots_sitemaps)
from recommend import fixes
from tests.helpers_diagnose import page, response

URL = "https://acme.example/"
HOME = page(600)
NEW_CODES = [c for c in CHECK_FOR_CODE]
SITEMAP = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</urlset>')


def urlset(*addresses: str) -> str:
    return SITEMAP.format("".join(f"<url><loc>{a}</loc></url>" for a in addresses))


def index(*children: str) -> str:
    return ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<sitemap><loc>{c}</loc></sitemap>" for c in children) + "</sitemapindex>")


def moved(to: str, status: int = 301) -> httpx.Response:
    return httpx.Response(status, headers={"location": to})


def xml(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "application/xml"})


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """The database is reached through libpq, not Python sockets, so blocking Python's own sockets is safe."""
    def refuse(self, *args, **kwargs):
        raise RuntimeError("a test tried to open a real network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def good_site(**overrides):
    """A healthy site: one host, www redirects permanently, http goes to https, a listed and valid sitemap."""
    routes = {
        "https://acme.example/": response(200, HOME),
        "https://acme.example/robots.txt": response(200, "User-agent: *\nAllow: /\nSitemap: https://acme.example/sitemap.xml\n"),
        "https://acme.example/sitemap.xml": xml(urlset("https://acme.example/", "https://acme.example/about", "https://acme.example/contact")),
        "https://www.acme.example/": moved("https://acme.example/"),
        "http://acme.example/": moved("https://acme.example/"),
    }
    routes.update(overrides)
    return routes


def serve(routes: dict, seen: list | None = None):
    """A handler for `routes` (full address -> Response, callable, or an Exception to raise). Anything else is a 404."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((str(request.url), request.headers.get("user-agent", "")))
        item = routes.get(str(request.url))
        if item is None:
            return response(404, "not found")
        if isinstance(item, Exception):
            raise item
        return item(request) if callable(item) else item
    return handler


def fetcher_for(routes: dict, seen: list | None = None, **kw) -> Fetcher:
    client = httpx.Client(transport=httpx.MockTransport(serve(routes, seen)), follow_redirects=True)
    return Fetcher(client, max_requests=kw.pop("max_requests", 15), min_interval=kw.pop("min_interval", 0),
                   sleep=kw.pop("sleep", lambda s: None), **kw)


def diagnose(routes, tmp_path, **kw):
    seen = kw.pop("seen", None)
    return run_diagnosis(URL, fetcher=fetcher_for(routes, seen), clients_root=tmp_path, use_ai=False, **kw)


def codes_of(out) -> set[str]:
    return {f["code"] for f in out.findings}


def new_codes_of(out) -> set[str]:
    return codes_of(out) & set(NEW_CODES)


# ---- the healthy site -----------------------------------------------------------------------------------------------------

def test_a_healthy_site_raises_none_of_the_new_findings(tmp_path):
    out = diagnose(good_site(), tmp_path)
    assert new_codes_of(out) == set()
    sc = out.data["site_checks"]
    assert sc["checks_ran"] == ["sitemap", "hosts"]
    assert sc["sitemap"]["found"] and sc["sitemap"]["valid"] and sc["sitemap"]["urls"] == 3
    assert sc["sitemap"]["in_robots"] is True and sc["sitemap"]["host_matches_canonical"] is True
    assert sc["hosts"]["http"]["ends_at_https"] is True


# ---- sitemap ---------------------------------------------------------------------------------------------------------------

def test_a_sitemap_that_is_found_records_status_count_and_robots(tmp_path):
    s = diagnose(good_site(), tmp_path).data["site_checks"]["sitemap"]
    assert s["fetched"][0]["url"] == "https://acme.example/sitemap.xml" and s["fetched"][0]["status"] == 200
    assert s["robots_listed"] == ["https://acme.example/sitemap.xml"]


def test_a_missing_sitemap_is_SITEMAP_MISSING(tmp_path):
    routes = good_site(**{"https://acme.example/robots.txt": response(200, "User-agent: *\nAllow: /\n")})
    del routes["https://acme.example/sitemap.xml"]
    out = diagnose(routes, tmp_path)
    assert "SITEMAP_MISSING" in codes_of(out) and "SITEMAP_INVALID" not in codes_of(out)
    f = next(f for f in out.findings if f["code"] == "SITEMAP_MISSING")
    assert f["severity"] == "medium" and "/sitemap.xml" in f["evidence"]


@pytest.mark.parametrize("body", [page(50), "<html>not xml", urlset(), '<?xml version="1.0"?><rss><channel/></rss>'])
def test_a_sitemap_that_is_not_xml_or_lists_nothing_is_SITEMAP_INVALID(tmp_path, body):
    routes = good_site(**{"https://acme.example/sitemap.xml": response(200, body)})
    out = diagnose(routes, tmp_path)
    assert "SITEMAP_INVALID" in codes_of(out) and "SITEMAP_MISSING" not in codes_of(out)
    assert "SITEMAP_NOT_IN_ROBOTS" not in codes_of(out)        # an unusable sitemap is not also "not listed"


def test_a_sitemap_index_is_followed_into_its_children(tmp_path):
    children = [f"https://acme.example/sm{i}.xml" for i in range(3)]
    routes = good_site(**{"https://acme.example/sitemap.xml": xml(index(*children))})
    for i, c in enumerate(children):
        routes[c] = xml(urlset(f"https://acme.example/p{i}a", f"https://acme.example/p{i}b"))
    s = diagnose(routes, tmp_path).data["site_checks"]["sitemap"]
    assert s["valid"] and s["urls"] == 6 and s["children_followed"] == 3
    assert [r["child"] for r in s["fetched"]] == [False, True, True, True]


def test_at_most_ten_child_sitemaps_are_followed(tmp_path):
    children = [f"https://acme.example/sm{i}.xml" for i in range(12)]
    routes = good_site(**{"https://acme.example/sitemap.xml": xml(index(*children))})
    for c in children:
        routes[c] = xml(urlset(f"{c}#a", f"{c}#b"))
    s = diagnose(routes, tmp_path).data["site_checks"]["sitemap"]
    assert s["children_followed"] == 10 and s["children_not_followed"] == 2 and s["urls"] == 20


def test_a_sitemap_robots_does_not_list_is_SITEMAP_NOT_IN_ROBOTS_and_low(tmp_path):
    routes = good_site(**{"https://acme.example/robots.txt": response(200, "User-agent: *\nAllow: /\n")})
    out = diagnose(routes, tmp_path)
    f = next(f for f in out.findings if f["code"] == "SITEMAP_NOT_IN_ROBOTS")
    assert f["severity"] == "low" and out.data["site_checks"]["sitemap"]["in_robots"] is False


def test_a_sitemap_found_only_through_robots_is_found(tmp_path):
    routes = good_site(**{"https://acme.example/robots.txt": response(200, "Sitemap: https://acme.example/maps/all.xml\n")})
    del routes["https://acme.example/sitemap.xml"]
    routes["https://acme.example/maps/all.xml"] = xml(urlset("https://acme.example/a"))
    out = diagnose(routes, tmp_path)
    assert "SITEMAP_MISSING" not in codes_of(out) and "SITEMAP_NOT_IN_ROBOTS" not in codes_of(out)


def test_a_sitemap_on_the_other_form_of_the_host_is_SITEMAP_HOST_MISMATCH(tmp_path):
    routes = good_site(**{"https://acme.example/sitemap.xml": xml(urlset("https://www.acme.example/", "https://www.acme.example/about"))})
    out = diagnose(routes, tmp_path)
    assert "SITEMAP_HOST_MISMATCH" in codes_of(out)
    assert out.data["site_checks"]["sitemap"]["host_matches_canonical"] is False


def test_sitemap_parsing_handles_gzip_namespaces_and_garbage():
    body = urlset("https://acme.example/a").encode()
    assert parse_sitemap(gzip.compress(body)) == ("urlset", ["https://acme.example/a"])
    assert parse_sitemap(b"<urlset><url><loc>https://x/a</loc></url></urlset>")[0] == "urlset"      # no namespace
    assert parse_sitemap(b"\x1f\x8bnot really gzip")[0] == "not_xml"
    assert parse_sitemap(b"")[0] == "not_xml"
    assert robots_sitemaps("sitemap: /a.xml\nSITEMAP: https://x/b.xml\nSitemap: /a.xml", "https://acme.example") == [
        "https://acme.example/a.xml", "https://x/b.xml"]


# ---- www / non-www and http / https ----------------------------------------------------------------------------------------

def test_both_hosts_answering_200_is_HOST_DUPLICATE(tmp_path):
    out = diagnose(good_site(**{"https://www.acme.example/": response(200, HOME)}), tmp_path)
    assert "HOST_DUPLICATE" in codes_of(out)


def test_a_proper_301_between_the_hosts_raises_nothing(tmp_path):
    out = diagnose(good_site(), tmp_path)
    assert not {"HOST_DUPLICATE", "HOST_REDIRECT_NOT_PERMANENT", "HOST_WWW_UNREACHABLE"} & codes_of(out)
    www = out.data["site_checks"]["hosts"]["www"]
    assert (www["status"], www["location"]) == (301, "https://acme.example/") and www["followed_status"] == 200


@pytest.mark.parametrize("status", [302, 307])
def test_a_temporary_redirect_between_the_hosts_is_HOST_REDIRECT_NOT_PERMANENT(tmp_path, status):
    out = diagnose(good_site(**{"https://www.acme.example/": moved("https://acme.example/", status)}), tmp_path)
    assert "HOST_REDIRECT_NOT_PERMANENT" in codes_of(out) and "HOST_DUPLICATE" not in codes_of(out)


@pytest.mark.parametrize("status", [308])
def test_a_308_is_permanent(tmp_path, status):
    out = diagnose(good_site(**{"https://www.acme.example/": moved("https://acme.example/", status)}), tmp_path)
    assert "HOST_REDIRECT_NOT_PERMANENT" not in codes_of(out)


def test_a_host_that_exists_but_does_not_connect_is_HOST_WWW_UNREACHABLE_and_low(tmp_path):
    err = httpx.ConnectError("[Errno 61] Connection refused")
    out = diagnose(good_site(**{"https://www.acme.example/": err}), tmp_path)        # recorded, never a crash
    f = next(f for f in out.findings if f["code"] == "HOST_WWW_UNREACHABLE")
    assert f["severity"] == "low" and "Connection refused" in f["evidence"]


def test_a_host_that_does_not_exist_is_not_a_finding(tmp_path):
    """Only one host exists and it works: no finding."""
    err = httpx.ConnectError("[Errno -2] Name or service not known")
    out = diagnose(good_site(**{"https://www.acme.example/": err}), tmp_path)
    assert "HOST_WWW_UNREACHABLE" not in codes_of(out)
    assert out.data["site_checks"]["hosts"]["www"]["dns_failure"] is True


def test_plain_http_that_stays_on_http_is_HTTP_NOT_REDIRECTED(tmp_path):
    out = diagnose(good_site(**{"http://acme.example/": response(200, HOME)}), tmp_path)
    assert "HTTP_NOT_REDIRECTED" in codes_of(out)


def test_plain_http_that_does_not_answer_is_recorded_but_not_a_finding(tmp_path):
    out = diagnose(good_site(**{"http://acme.example/": httpx.ConnectError("[Errno 61] Connection refused")}), tmp_path)
    assert "HTTP_NOT_REDIRECTED" not in codes_of(out)
    assert out.data["site_checks"]["hosts"]["http"]["ends_at_https"] is None


# ---- network manners --------------------------------------------------------------------------------------------------------

def test_the_new_checks_use_their_own_polite_user_agent_and_the_limits(tmp_path):
    seen: list = []
    diagnose(good_site(), tmp_path, seen=seen)
    for url, ua in seen:
        if url.endswith("/sitemap.xml") or url.startswith(("https://www.", "http://")):
            assert ua == SITE_CHECK_UA and "AEO-Studio" in ua
    assert REQUEST_TIMEOUT_SECONDS == 10 and MAX_REDIRECT_HOPS == 5
    client = make_client()
    assert client.timeout.read == 10 and client.max_redirects == 5
    client.close()


def test_the_new_checks_make_no_more_than_two_requests_a_second(tmp_path):
    sleeps: list = []
    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(serve(good_site())), follow_redirects=True),
                      max_requests=15, min_interval=1.0, sleep=sleeps.append, clock=lambda: 0.0)
    run_diagnosis(URL, fetcher=fetcher, clients_root=tmp_path, use_ai=False)
    assert 0.5 in sleeps and max(sleeps) <= 1.0          # the main checks keep their one second; the new ones wait 0.5
    assert fetcher.requests_made == 8                    # the main diagnosis budget is untouched by the new checks


def test_a_failure_to_connect_anywhere_never_crashes_the_run(tmp_path):
    boom = httpx.ConnectError("[Errno 61] Connection refused")
    routes = good_site(**{"https://acme.example/sitemap.xml": boom, "https://www.acme.example/": boom, "http://acme.example/": boom})
    out = diagnose(routes, tmp_path)
    assert "SITEMAP_MISSING" in codes_of(out)
    assert out.data["site_checks"]["sitemap"]["fetched"][0]["error"]


# ---- CDN / host / framework ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("headers, expected", [
    ({"cf-ray": "8a1b2c-YYZ", "server": "cloudflare"}, ("Cloudflare", None, None)),
    ({"x-vercel-id": "iad1::abcde", "x-powered-by": "Next.js"}, (None, "Vercel", "Next.js")),
    ({"x-nf-request-id": "01HX", "server": "Netlify"}, (None, "Netlify", None)),
    ({"x-amz-cf-id": "abc", "server": "AmazonS3"}, ("Amazon CloudFront", "Amazon S3", None)),
    ({"server": "nginx", "x-served-by": "cache-iad-kiad7000001-IAD"}, ("Fastly", None, None)),
    ({"fly-request-id": "01H-yyz"}, (None, "Fly.io", None)),
    ({"x-azure-ref": "20261029T"}, ("Azure Front Door", None, None)),
    ({"server": "Apache", "via": "1.1 something"}, (None, None, None)),               # an unknown host: all unknown
    ({}, (None, None, None)),
])
def test_cdn_host_and_framework_come_from_the_headers(headers, expected):
    found = infra.detect_infrastructure(headers)
    assert (found.cdn, found.host, found.framework) == expected
    assert found.summary().count("unknown") == expected.count(None)


def test_header_names_are_not_case_sensitive():
    assert infra.detect_infrastructure({"CF-Ray": "x", "X-Vercel-Id": "y"}).cdn == "Cloudflare"


def test_a_rule_added_to_the_data_file_is_used_with_no_code_change(tmp_path):
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text("rules:\n  - {header: x-acme-edge, pattern: '^v[0-9]+', label: Acme Edge, kind: cdn}\n"
                          "  - {header: x-acme-app, label: Acme Cloud, kind: host}\n")
    rules = infra.load_rules(rules_file)
    found = infra.detect_infrastructure({"x-acme-edge": "v2", "x-acme-app": "1"}, rules)
    assert (found.cdn, found.host) == ("Acme Edge", "Acme Cloud") and found.evidence["cdn"] == "x-acme-edge: v2"
    assert infra.detect_infrastructure({"x-acme-edge": "beta"}, rules).cdn is None       # the pattern has to match


@pytest.mark.parametrize("body", ["rules: nope", "rules:\n  - {header: x, label: y, kind: weird}",
                                  "rules:\n  - {header: x, label: y}", "rules:\n  - {header: x, label: y, kind: cdn, pattern: '('}"])
def test_a_broken_rules_file_is_refused_with_a_message(tmp_path, body):
    path = tmp_path / "bad.yaml"
    path.write_text(body)
    with pytest.raises(infra.InfrastructureRulesError):
        infra.load_rules(path)


def test_the_shipped_rules_file_loads_and_covers_every_kind():
    rules = infra.load_rules()
    assert {r.kind for r in rules} == {"cdn", "host", "framework"}


def test_the_diagnosis_reports_and_stores_the_setup(business_id, tmp_path):
    routes = good_site(**{"https://acme.example/": response(200, HOME, {"cf-ray": "8a1b2c", "x-vercel-id": "iad1::x"})})
    out = diagnose(routes, tmp_path, business_id=business_id)
    assert out.facts.infrastructure.summary() == "CDN: Cloudflare · Host: Vercel · Framework: unknown"
    conn = get_connection()
    try:
        row = conn.execute("SELECT detected_cdn, detected_host, detected_framework, site_checks FROM diagnoses WHERE id = %s",
                           (out.diagnosis_id,)).fetchone()
    finally:
        conn.close()
    assert row[:3] == ("Cloudflare", "Vercel", None) and row[3]["checks_ran"] == ["sitemap", "hosts"]
    md = build_markdown(out.data)
    assert "## Site setup" in md and "CDN: Cloudflare" in md and "Framework: unknown" in md and "not a finding" in md
    assert not any(f["code"] == "CDN_DETECTED" for f in out.findings)       # information, not a finding


# ---- old addresses -----------------------------------------------------------------------------------------------------------

OLD = {
    "https://acme.example/gone": response(404, "nope"),
    "https://acme.example/gone-for-good": response(410, "gone"),
    "https://acme.example/old-302": moved("https://acme.example/new", 302),
    "https://acme.example/old-307": moved("https://acme.example/new", 307),
    "https://acme.example/chain-a": moved("https://acme.example/chain-b"),
    "https://acme.example/chain-b": moved("https://acme.example/new"),
    "https://acme.example/to-home": moved("https://acme.example/"),
    "https://acme.example/fine": moved("https://acme.example/new"),
    "https://acme.example/new": response(200, HOME),
}


def old_urls_file(tmp_path, lines):
    path = tmp_path / "old_urls.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


ALL_OLD = ["/gone", "/gone-for-good", "/old-302", "/old-307", "/chain-a", "/to-home", "/fine"]


def test_all_four_old_address_findings(tmp_path):
    out = diagnose(good_site(**OLD), tmp_path, old_urls=old_urls_file(tmp_path, ALL_OLD))
    by = {f["code"]: f for f in out.findings if f["code"].startswith("OLD_URL_")}
    assert set(by) == {"OLD_URL_404", "OLD_URL_NOT_PERMANENT", "OLD_URL_CHAIN", "OLD_URL_TO_HOME"}
    assert by["OLD_URL_404"]["what"].startswith("2 of 7") and by["OLD_URL_NOT_PERMANENT"]["what"].startswith("2 of 7")
    assert by["OLD_URL_CHAIN"]["what"].startswith("1 of 7") and by["OLD_URL_TO_HOME"]["what"].startswith("1 of 7")
    assert "https://acme.example/gone" in by["OLD_URL_404"]["evidence"]
    assert out.data["site_checks"]["checks_ran"] == ["sitemap", "hosts", "old_urls"]


def test_without_the_option_nothing_runs_and_nothing_is_reported(tmp_path):
    seen: list = []
    out = diagnose(good_site(**OLD), tmp_path, seen=seen)
    assert not any(c.startswith("OLD_URL_") for c in codes_of(out))
    assert out.data["site_checks"]["old_urls"] is None and "old_urls" not in out.data["site_checks"]["checks_ran"]
    assert not any("/gone" in url or "/chain-a" in url for url, _ in seen)
    assert not list((tmp_path / "acme.example" / "evidence").glob("*old_urls*"))


def test_old_urls_are_capped_at_200(tmp_path):
    lines = [f"https://acme.example/old/{i}" for i in range(250)]
    out = diagnose(good_site(), tmp_path, old_urls=old_urls_file(tmp_path, lines))
    o = out.data["site_checks"]["old_urls"]
    assert OLD_URL_CAP == 200 and o["checked"] == 200 and o["over_cap"] == 50 and o["total_in_file"] == 250
    assert any("only the first 200" in w for w in out.warnings)


def test_blank_lines_comments_and_junk_are_ignored(tmp_path):
    lines = ["# the old shop", "", "   ", "/gone", "  https://acme.example/gone-for-good  ", "not an address", "# /fine", "/gone"]
    out = diagnose(good_site(**OLD), tmp_path, old_urls=old_urls_file(tmp_path, lines))
    o = out.data["site_checks"]["old_urls"]
    assert o["checked"] == 2 and o["counts"]["OLD_URL_404"] == 2           # the repeated /gone is checked once
    assert o["ignored_lines"] == ["not an address"]
    assert any("1 line(s)" in w for w in out.warnings)


def test_the_findings_show_a_count_and_ten_examples_and_the_evidence_file_has_all(tmp_path):
    routes = good_site(**{f"https://acme.example/dead/{i}": response(404, "x") for i in range(14)})
    lines = [f"/dead/{i}" for i in range(14)]
    out = diagnose(routes, tmp_path, old_urls=old_urls_file(tmp_path, lines))
    f = next(f for f in out.findings if f["code"] == "OLD_URL_404")
    assert f["what"].startswith("14 of 14") and f["evidence"].count("-> ") == EXAMPLES_SHOWN and "and 4 more" in f["evidence"]
    assert len(out.data["site_checks"]["old_urls"]["examples"]["OLD_URL_404"]) == EXAMPLES_SHOWN
    assert "results" not in out.data["site_checks"]["old_urls"]            # the report and database hold examples only
    evidence = list((tmp_path / "acme.example" / "evidence").glob("*_old_urls_run1.json"))
    assert len(evidence) == 1
    full = json.loads(evidence[0].read_text())
    assert len(full["results"]) == 14 and full["results"][0]["issues"] == ["OLD_URL_404"]


def test_a_redirect_loop_stops_after_five_hops(tmp_path):
    routes = good_site(**{"https://acme.example/loop-a": moved("https://acme.example/loop-b"),
                          "https://acme.example/loop-b": moved("https://acme.example/loop-a")})
    out = diagnose(routes, tmp_path, old_urls=old_urls_file(tmp_path, ["/loop-a"]))
    r = out.data["site_checks"]["old_urls"]
    assert r["checked"] == 1 and "OLD_URL_CHAIN" in {f["code"] for f in out.findings}


def test_an_old_address_that_does_not_connect_is_recorded_not_raised(tmp_path):
    routes = good_site(**{"https://acme.example/down": httpx.ConnectError("[Errno 61] Connection refused")})
    out = diagnose(routes, tmp_path, old_urls=old_urls_file(tmp_path, ["/down", "/gone"]))
    o = out.data["site_checks"]["old_urls"]
    assert o["checked"] == 2 and o["unreachable"] == 1


def test_a_missing_old_address_file_is_a_plain_error_before_any_request(tmp_path):
    seen: list = []
    with pytest.raises(DiagnoseError, match="does not exist"):
        diagnose(good_site(), tmp_path, old_urls=tmp_path / "nope.txt", seen=seen)
    assert seen == []


def test_the_command_passes_the_option_through(monkeypatch, tmp_path):
    captured = {}

    def stub(url, **kw):
        captured.update(kw)
        raise DiagnoseError("stop here")
    monkeypatch.setattr(cli, "run_diagnosis", stub)
    path = old_urls_file(tmp_path, ["/gone"])
    from typer.testing import CliRunner
    CliRunner().invoke(cli.app, ["diagnose", URL, "--old-urls", str(path)])
    assert str(captured["old_urls"]) == str(path)
    captured.clear()
    CliRunner().invoke(cli.app, ["diagnose", URL])
    assert captured["old_urls"] is None


# ---- the codes ------------------------------------------------------------------------------------------------------------------

def test_every_new_code_has_an_explanation_a_severity_and_a_check():
    assert set(EXPLANATIONS) == set(CHECK_FOR_CODE) and len(NEW_CODES) == 12
    for code in NEW_CODES:
        assert code in REGISTRY and REGISTRY[code] in codes.SEVERITIES and CHECK_FOR_CODE[code] in ("sitemap", "hosts", "old_urls")
        text = EXPLANATIONS[code]
        assert len(text.split()) >= 20 and ("crawler" in text.lower() or "AI" in text or "search" in text.lower())
    assert REGISTRY["SITEMAP_NOT_IN_ROBOTS"] == "low" and REGISTRY["HOST_WWW_UNREACHABLE"] == "low"


def test_dependencies_name_real_codes_and_a_missing_sitemap_hides_the_others():
    for code in ("SITEMAP_INVALID", "SITEMAP_NOT_IN_ROBOTS", "SITEMAP_HOST_MISMATCH", "HOST_DUPLICATE", "HOST_REDIRECT_NOT_PERMANENT"):
        dep = DEPENDS_ON[code]
        assert all(g in REGISTRY for g in dep["gone_when"]) and code not in dep["gone_when"]
    assert "SITEMAP_MISSING" in DEPENDS_ON["SITEMAP_HOST_MISMATCH"]["gone_when"]


def test_the_report_gives_the_explanation_under_each_new_finding(tmp_path):
    out = diagnose(good_site(**{"https://www.acme.example/": response(200, HOME)}), tmp_path)
    md = build_markdown(out.data)
    assert "Why it matters: " + EXPLANATIONS["HOST_DUPLICATE"] in md and "## Sitemap and addresses" in md


# ---- compare, recommend ---------------------------------------------------------------------------------------------------------

def finding(code, severity="medium"):
    return make_finding(code, what=f"{code} happened", evidence="e", fixable_on_platform=None, where_to_fix="x", source="y", severity=severity)


def diag(codes_, ran=None, platform="wix"):
    d = {"findings": [finding(c) for c in codes_], "platform": platform, "plan": None, "verdict": "serve"}
    if ran is not None:
        d["checks_ran"] = ran
    return d


def test_compare_gives_the_new_codes_the_four_outcomes():
    a = diag(["SITEMAP_MISSING", "HOST_DUPLICATE", "HTTP_NOT_REDIRECTED"], ["sitemap", "hosts"])
    b = diag(["HTTP_NOT_REDIRECTED", "SITEMAP_INVALID"], ["sitemap", "hosts"])
    d = compare_diagnoses(a, b, [], "2026-10-29")
    assert {c["code"] for c in d["fixed"]} == {"HOST_DUPLICATE", "SITEMAP_MISSING"}
    assert [c["code"] for c in d["still_open"]] == ["HTTP_NOT_REDIRECTED"] and [c["code"] for c in d["new"]] == ["SITEMAP_INVALID"]


def test_compare_says_no_longer_applies_when_the_sitemap_itself_is_gone():
    a = diag(["SITEMAP_NOT_IN_ROBOTS", "SITEMAP_INVALID"], ["sitemap"])
    b = diag(["SITEMAP_MISSING"], ["sitemap"])
    d = compare_diagnoses(a, b, [], "2026-10-29")
    assert {c["code"] for c in d["no_longer_applies"]} == {"SITEMAP_NOT_IN_ROBOTS", "SITEMAP_INVALID"} and d["fixed"] == []
    assert all(c["replaced_by"][0]["code"] == "SITEMAP_MISSING" for c in d["no_longer_applies"])


def test_an_old_address_finding_is_not_fixed_when_the_later_diagnosis_did_not_check_old_addresses():
    a = diag(["OLD_URL_404", "SITEMAP_MISSING"], ["sitemap", "hosts", "old_urls"])
    b = diag([], ["sitemap", "hosts"])
    d = compare_diagnoses(a, b, [], "2026-10-29")
    assert [c["code"] for c in d["fixed"]] == ["SITEMAP_MISSING"] and d["not_rechecked"] == ["OLD_URL_404"]
    assert any("OLD_URL_404" in c and "cannot be called fixed" in c for c in d["caveats"])


def test_a_finding_is_not_new_when_the_earlier_diagnosis_predates_the_check():
    a = diag([], [])                                   # made before step 25: none of the new checks ran
    b = diag(["SITEMAP_MISSING"], ["sitemap", "hosts"])
    d = compare_diagnoses(a, b, [], "2026-10-29")
    assert d["new"] == [] and d["not_compared"] == ["SITEMAP_MISSING"]
    assert any("cannot be called new" in c for c in d["caveats"])


def test_plain_data_without_a_record_of_which_checks_ran_is_compared_as_before():
    d = compare_diagnoses(diag(["SITEMAP_MISSING"]), diag(["HOST_DUPLICATE"]), [], "2026-10-29")
    assert [c["code"] for c in d["fixed"]] == ["SITEMAP_MISSING"] and [c["code"] for c in d["new"]] == ["HOST_DUPLICATE"]
    assert d["not_rechecked"] == [] and d["not_compared"] == []


def test_the_recommendations_carry_the_new_findings_as_ordinary_fixes():
    diagnosis = {"findings": [finding("SITEMAP_MISSING"), finding("OLD_URL_404", "high")], "platform": "wix", "platform_plan": None}
    got = fixes.remaining_fixes(diagnosis, set())
    assert [f["finding_code"] for f in got] == ["OLD_URL_404", "SITEMAP_MISSING"]          # by severity, as for every finding
    assert all(f["section"] == "finding" and f["route"]["where"] == "x" for f in got)


def test_the_dossier_shows_the_setup_and_the_sitemap_result():
    from dossier.data import diagnosis_column
    base = {"findings": [], "crawler": {}, "render": None, "jsonld": [], "checks_ran": ["sitemap", "hosts"],
            "site_checks": {"checks_ran": ["sitemap", "hosts"]}, "cdn": "Cloudflare", "host": None, "framework": "Next.js"}
    rows = diagnosis_column(base)
    assert rows["CDN / host / framework"] == "Cloudflare / unknown / Next.js" and rows["Sitemap and host consistency"] == "no problems found"
    base["findings"] = [finding("SITEMAP_MISSING")]
    assert diagnosis_column(base)["Sitemap and host consistency"] == "see findings: SITEMAP_MISSING"
    old = {**base, "findings": [], "site_checks": None, "checks_ran": [], "cdn": None, "host": None, "framework": None}
    rows = diagnosis_column(old)
    assert rows["CDN / host / framework"] == "not recorded yet" and rows["Sitemap and host consistency"] == "not checked yet"


def test_the_compare_loader_reads_which_checks_a_stored_diagnosis_ran(business_id, tmp_path):
    from compare.diagnosis import pick_diagnosis
    diagnose(good_site(), tmp_path, business_id=business_id, checkpoint="before")
    conn = get_connection()
    try:
        conn.execute("INSERT INTO diagnoses (business_id, checkpoint, url, verdict) VALUES (%s, 'check', 'https://x.example', 'serve')",
                     (business_id,))
        assert pick_diagnosis(conn, business_id, "before", None)["checks_ran"] == ["sitemap", "hosts"]
        assert pick_diagnosis(conn, business_id, "check", None)["checks_ran"] == []        # made without the new checks
    finally:
        conn.close()
