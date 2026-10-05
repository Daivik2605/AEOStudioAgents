"""Platform detection: the three signal tiers and the two named discriminations (PLAN.md §4)."""

from __future__ import annotations

import pytest

from diagnose.html_utils import parse_page
from diagnose.platform_detect import detect, has_same_origin_plugins


def run(headers=None, html="<html><head></head><body></body></html>", url="https://example.com/"):
    return detect(headers={k.lower(): v for k, v in (headers or {}).items()}, html=html, final_url=url,
                  page=parse_page(html))


# ---- Tier 1: infrastructure-emitted, one hit is enough -----------------------------

@pytest.mark.parametrize("headers,html,expected", [
    ({"X-Wix-Request-Id": "abc"}, "<html></html>", "wix"),
    ({"Server": "Squarespace"}, "<html></html>", "squarespace"),
    ({"x-hs-hub-id": "53"}, "<html></html>", "hubspot"),
    ({}, '<link href="https://cdn.shopify.com/s/files/x.css">', "shopify"),
    ({}, '<script src="https://framerusercontent.com/x.js"></script>', "framer"),
    ({}, '<img src="https://cdn.prod.website-files.com/a.png">', "webflow"),
    ({}, '<script src="https://dd-cdn.multiscreensite.com/x.js"></script>', "duda"),
])
def test_tier1_single_signal_is_enough(headers, html, expected):
    assert run(headers, html).platform == expected


def test_tier1_hits_are_recorded_with_their_tier():
    result = run({"X-Wix-Request-Id": "abc"})
    assert result.hits == [{"tier": 1, "signal": "X-Wix-* response header", "evidence": "x-wix-request-id: abc"}]


# ---- Tier 2: structural ---------------------------------------------------------------

@pytest.mark.parametrize("headers,html,expected", [
    ({}, '<link href="/wp-content/themes/x/style.css">', "wordpress"),
    ({"Link": '<https://x.com/wp-json/>; rel="https://api.w.org/"'}, "<html></html>", "wordpress"),
    ({}, '<html data-wf-site="123"></html>', "webflow"),
    ({"Expires": "Sun, 19 Nov 1978 05:00:00 GMT"}, "<html></html>", "drupal"),
    ({}, '<script src="/components/com_content/x.js"></script>', "joomla"),
])
def test_tier2_single_signal_is_enough(headers, html, expected):
    assert run(headers, html).platform == expected


# ---- Tier 3: trivially forged, never enough alone --------------------------------------

def test_a_tier3_signal_alone_never_reports_a_platform():
    html = '<html><head><meta name="generator" content="WordPress 6.5"></head></html>'
    assert run(html=html).platform is None
    assert run({"X-Powered-By": "Next.js"}).platform is None
    assert run({"X-Pingback": "https://x.com/xmlrpc.php"}).platform is None


def test_two_independent_tier3_signals_are_enough():
    html = '<html><head><meta name="generator" content="WordPress 6.5"></head></html>'
    result = run({"X-Pingback": "https://x.com/xmlrpc.php"}, html)
    assert result.platform == "wordpress"
    assert result.version == "6.5"


def test_the_same_tier3_signal_seen_twice_is_not_two_signals():
    html = ('<meta name="generator" content="WordPress 6.5">'
            '<meta name="generator" content="WordPress 6.5">')
    assert run(html=html).platform is None


# ---- Discrimination 1: WordPress.com vs self-hosted -----------------------------------------

JETPACK_CDN = '<script src="https://s0.wp.com/wp-content/js/x.js"></script>'
OWN_PLUGIN = '<link href="/wp-content/plugins/contact-form/style.css">'


def test_wpcom_hostnames_without_own_plugins_is_wordpress_com():
    html = f'<link href="/wp-content/themes/t/style.css">{JETPACK_CDN}'
    result = run(html=html, url="https://myblog.example/")
    assert result.platform == "wordpress_com"


def test_wpcom_hostnames_WITH_own_plugins_is_self_hosted_wordpress():
    # Jetpack puts wp.com hostnames on self-hosted sites too. Plugin files on the
    # site's own address are what give it away.
    html = f'{JETPACK_CDN}{OWN_PLUGIN}'
    result = run(html=html, url="https://example.com/")
    assert result.platform == "wordpress"


def test_plugins_loaded_from_another_origin_do_not_count_as_own():
    html = '<link href="https://s1.wp.com/wp-content/plugins/jetpack/x.css"><link href="/wp-content/themes/t/s.css">'
    assert has_same_origin_plugins(html, "myblog.example") is False
    assert has_same_origin_plugins('<link href="https://www.example.com/wp-content/plugins/a/b.css">', "example.com") is True
    assert has_same_origin_plugins('<link href="//example.com/wp-content/plugins/a/b.css">', "example.com") is True


def test_hosted_by_wpcom_but_running_plugins_is_reported_as_business_plan():
    html = f'<link href="/wp-content/themes/t/s.css">{OWN_PLUGIN}'
    result = run({"host-header": "WordPress.com"}, html, url="https://example.com/")
    assert result.platform == "wordpress"
    assert result.plan == "business_or_higher"
    assert "wordpress_com" in result.hosting


def test_wordpress_com_subdomain_is_inferred_free_but_says_it_is_an_inference():
    html = f'<link href="/wp-content/themes/t/s.css">{JETPACK_CDN}'
    result = run(html=html, url="https://myblog.wordpress.com/")
    assert result.platform == "wordpress_com"
    assert result.plan == "free"
    assert "inferred" in result.plan_basis


def test_wordpress_com_on_a_custom_domain_has_no_plan_guess():
    html = f'<link href="/wp-content/themes/t/s.css">{JETPACK_CDN}'
    assert run(html=html, url="https://myshop.example/").plan is None


# ---- Discrimination 2: HubSpot CMS vs HubSpot tracking only ---------------------------------------

def test_hubspot_tracking_script_alone_is_not_hubspot():
    html = ('<link href="/wp-content/themes/t/s.css">'
            '<script>var _hsq = window._hsq = window._hsq || [];</script>'
            '<script src="//js.hs-scripts.com/123.js"></script>')
    result = run(html=html)
    assert result.platform == "wordpress"
    assert any("tracking" in n for n in result.notes)


def test_tracking_script_on_an_unknown_site_is_still_not_hubspot():
    result = run(html='<script>window._hsq = [];</script>')
    assert result.platform is None


def test_hub_id_header_means_hubspot_even_with_tracking_present():
    assert run({"x-hs-hub-id": "53"}, "<script>window._hsq = [];</script>").platform == "hubspot"


# ---- Guards against wrongly declining a site ----------------------------------------------------

def test_godaddy_image_host_alone_is_not_godaddy_builder():
    # GoDaddy-hosted WordPress uses the same image host.
    html = '<link href="/wp-content/themes/t/s.css"><img src="https://img1.wsimg.com/a.png">'
    assert run(html=html).platform == "wordpress"
    assert run(html='<img src="https://img1.wsimg.com/a.png">').platform is None


def test_godaddy_builder_needs_both_the_generator_and_the_image_host():
    html = ('<meta name="generator" content="Starfield Technologies; Go Daddy Website Builder 8.0">'
            '<img src="https://img1.wsimg.com/a.png">')
    assert run(html=html).platform == "godaddy_builder"


def test_a_cms_beats_a_stray_vendor_mention():
    # A WordPress site with one Shopify buy-button must still be WordPress.
    html = ('<link href="/wp-content/themes/t/s.css"><script src="/wp-includes/js/a.js"></script>'
            '<script src="https://cdn.shopify.com/buy-button.js"></script>')
    assert run(html=html).platform == "wordpress"


def test_a_cms_beats_nextjs():
    html = '<script src="/_next/static/a.js"></script><script src="https://framerusercontent.com/x.js"></script>'
    assert run(html=html).platform == "framer"


def test_nextjs_alone_is_reported_as_a_custom_site():
    assert run(html='<script src="/_next/static/a.js"></script>').platform == "nextjs"


def test_equal_matches_are_reported_as_ambiguous_not_guessed():
    html = '<img src="https://cdn.shopify.com/a.png"><img src="https://framerusercontent.com/b.png">'
    result = run(html=html)
    assert result.platform is None
    assert result.ambiguous_between == ["framer", "shopify"]


# ---- Decline-table platforms, hosting layers, versions ----------------------------------------------

def test_notion_published_page_by_address_and_by_shell_text():
    assert run(url="https://acme.notion.site/Home-123").platform == "notion"
    shell = "<noscript>JavaScript must be enabled in order to use Notion.</noscript>"
    assert run(html=shell).platform == "notion"


def test_google_sites_by_address():
    assert run(url="https://sites.google.com/view/acme").platform == "google_sites"


def test_weebly_by_infrastructure_header():
    assert run({"x-host": "grn153.sf2p.intern.weebly.net"}).platform == "weebly"


def test_hosting_layers_are_reported_separately_from_the_platform():
    result = run({"server": "cloudflare", "X-WPE-Loopback-Upstream-Addr": "127.0.0.1",
                  "x-vercel-id": "iad1::abc"}, '<link href="/wp-content/themes/t/s.css">')
    assert result.platform == "wordpress"
    assert set(result.hosting) == {"cloudflare", "wp_engine", "vercel"}


def test_squarespace_version_comes_from_the_page_context():
    html = '<script>Static.SQUARESPACE_CONTEXT = {"templateVersion":"7.1"}</script>'
    result = run({"server": "Squarespace"}, html)
    assert (result.platform, result.version) == ("squarespace", "7.1")
    assert result.plan is None  # the plan is not visible from outside
