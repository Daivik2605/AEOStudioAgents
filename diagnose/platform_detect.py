"""Works out which website platform a site is built on, from response
headers and raw HTML only. No browser, no AI.

The rules are ours (PLAN.md section 4). We deliberately do NOT use the public
Wappalyzer / webappanalyzer fingerprint database: it is GPL-3.0, a licensing
problem for commercial software. Every rule below was written from the
platform's own behaviour; the ones marked VERIFIED were checked against a
live site on 4 Oct 2026, the ones marked (research) come from
research/PLATFORM_DELIVERY.md and were not independently re-checked.

Signal reliability tiers (PLAN.md section 4):
  Tier 1  infrastructure-emitted; the site owner cannot remove it
  Tier 2  structural; removable only by breaking the site
  Tier 3  trivially stripped or forged (meta generator, X-Powered-By...)

NEVER report a platform on a Tier 3 signal alone. A platform needs one
Tier 1/2 hit, or two independent Tier 3 hits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from diagnose.html_utils import ParsedPage


@dataclass(frozen=True)
class Rule:
    platform: str
    tier: int
    label: str      # what the signal is; also what makes two hits "independent"
    kind: str       # header_prefix | header_regex | html_regex | meta_regex | host_suffix
    a: str          # header name / regex / meta name / host suffix
    b: str = ""     # header value regex / meta content regex


PLATFORM_RULES: list[Rule] = [
    # ---- Wix ---------------------------------------------------------------
    Rule("wix", 1, "X-Wix-* response header", "header_prefix", "x-wix-"),                     # VERIFIED
    Rule("wix", 1, "static.parastorage.com asset host", "html_regex", r"static\.parastorage\.com"),  # VERIFIED
    Rule("wix", 2, "wixBiSession page global", "html_regex", r"wixBiSession"),                # VERIFIED
    Rule("wix", 3, "meta generator: Wix", "meta_regex", "generator", r"^Wix\.com"),           # VERIFIED
    # ---- Squarespace ---------------------------------------------------------
    Rule("squarespace", 1, "Server: Squarespace", "header_regex", "server", r"^Squarespace"), # VERIFIED
    Rule("squarespace", 1, "squarespace asset host", "html_regex", r"(?:static1|assets)\.squarespace\.com"),  # VERIFIED
    Rule("squarespace", 2, "SQUARESPACE_CONTEXT page global", "html_regex", r"SQUARESPACE_CONTEXT"),  # VERIFIED
    # ---- Shopify -------------------------------------------------------------
    Rule("shopify", 1, "x-shopify-stage header", "header_prefix", "x-shopify-stage"),         # (research)
    Rule("shopify", 1, "cdn.shopify.com asset host", "html_regex", r"cdn\.shopify\.com"),     # VERIFIED
    Rule("shopify", 2, "Shopify.shop page global", "html_regex", r"Shopify\.shop\b"),         # VERIFIED
    Rule("shopify", 2, "shopify-checkout-api-token meta", "meta_regex", "shopify-checkout-api-token", r".*"),
    # ---- Webflow -------------------------------------------------------------
    Rule("webflow", 1, "website-files.com asset host", "html_regex", r"website-files\.com"),  # VERIFIED
    Rule("webflow", 2, "data-wf-site attribute", "html_regex", r"\bdata-wf-site="),           # VERIFIED
    Rule("webflow", 3, "meta generator: Webflow", "meta_regex", "generator", r"^Webflow"),
    # ---- Framer --------------------------------------------------------------
    Rule("framer", 1, "Server: Framer", "header_regex", "server", r"^Framer"),                # VERIFIED
    Rule("framer", 1, "framerusercontent.com asset host", "html_regex", r"framerusercontent\.com"),  # VERIFIED
    Rule("framer", 3, "meta generator: Framer", "meta_regex", "generator", r"^Framer\b"),     # VERIFIED
    # ---- HubSpot CMS ---------------------------------------------------------
    # x-hs-hub-id means the page is SERVED by HubSpot. The _hsq tracking global
    # and hs-scripts.com do NOT: those just mean a HubSpot tracking script, and
    # are deliberately not rules (PLAN.md section 4).
    Rule("hubspot", 1, "x-hs-hub-id header", "header_prefix", "x-hs-hub-id"),                 # VERIFIED
    Rule("hubspot", 3, "X-Powered-By: HubSpot", "header_regex", "x-powered-by", r"HubSpot"),
    Rule("hubspot", 3, "meta generator: HubSpot", "meta_regex", "generator", r"^HubSpot"),    # VERIFIED
    # ---- Duda ----------------------------------------------------------------
    Rule("duda", 1, "dd-cdn.multiscreensite.com asset host", "html_regex", r"dd-cdn\.multiscreensite\.com"),  # (research)
    # ---- WordPress (self-managed or WordPress.com; see _discriminate_wordpress) --
    Rule("wordpress", 2, "/wp-content/ paths", "html_regex", r"/wp-content/"),                # VERIFIED
    Rule("wordpress", 2, "/wp-includes/ paths", "html_regex", r"/wp-includes/"),              # VERIFIED
    Rule("wordpress", 2, "Link header rel=api.w.org", "header_regex", "link", r'rel="https://api\.w\.org/"'),  # VERIFIED
    Rule("wordpress", 3, "meta generator: WordPress", "meta_regex", "generator", r"^WordPress"),  # VERIFIED
    Rule("wordpress", 3, "X-Pingback header", "header_prefix", "x-pingback"),
    # ---- Drupal --------------------------------------------------------------
    Rule("drupal", 2, "Expires: 19 Nov 1978 (Drupal's sentinel date)", "header_regex", "expires", r"19 Nov 1978"),
    Rule("drupal", 2, "Drupal.settings / drupalSettings page global", "html_regex", r"Drupal\.settings|drupalSettings"),
    Rule("drupal", 3, "meta generator: Drupal", "meta_regex", "generator", r"^Drupal"),
    Rule("drupal", 3, "X-Generator: Drupal", "header_regex", "x-generator", r"Drupal"),
    # ---- Joomla --------------------------------------------------------------
    Rule("joomla", 2, "/components/com_ paths", "html_regex", r"/components/com_"),
    Rule("joomla", 2, "/media/system/js/ paths", "html_regex", r"/media/system/js/"),
    Rule("joomla", 3, "meta generator: Joomla", "meta_regex", "generator", r"^Joomla"),       # VERIFIED
    # ---- Next.js (a custom site, not a CMS) ------------------------------------
    Rule("nextjs", 1, "/_next/static/ asset path", "html_regex", r"/_next/static/"),
    Rule("nextjs", 2, "__NEXT_DATA__ page global", "html_regex", r"__NEXT_DATA__"),
    Rule("nextjs", 3, "X-Powered-By: Next.js", "header_regex", "x-powered-by", r"Next\.js"),  # VERIFIED
    # ---- The four platforms on the decline table ---------------------------------
    Rule("google_sites", 1, "served from sites.google.com", "host_suffix", "sites.google.com"),
    Rule("notion", 1, "served from notion.site", "host_suffix", ".notion.site"),
    Rule("notion", 1, "Notion's no-JavaScript shell text", "html_regex", r"JavaScript must be enabled in order to use Notion"),  # VERIFIED
    Rule("weebly", 1, "x-host header on weebly.net", "header_regex", "x-host", r"weebly\.net"),  # VERIFIED
    Rule("weebly", 1, "editmysite.com asset host", "html_regex", r"editmysite\.com"),         # VERIFIED
    Rule("weebly", 2, "_W.configDomain page global", "html_regex", r"_W\.configDomain"),     # (research)
    # GoDaddy's wsimg.com image host is ALSO used by GoDaddy-hosted WordPress, so on
    # its own it proves nothing; it is Tier 3 and needs the Website Builder generator
    # tag next to it. This stops a WordPress site on GoDaddy being wrongly declined.
    Rule("godaddy_builder", 3, "meta generator: GoDaddy Website Builder", "meta_regex", "generator", r"Go ?Daddy Website Builder"),
    Rule("godaddy_builder", 3, "wsimg.com image host", "html_regex", r"\bwsimg\.com"),
]
# Not verified against a live site: google_sites (only the sites.google.com
# address is recognised, so a Google Site on a custom domain is NOT detected),
# weebly _W.configDomain, godaddy_builder, duda, shopify x-shopify-stage, joomla paths.

# Hosting / edge layers. Not platforms, so they never compete with the above.
HOST_RULES = [
    ("wp_engine", "X-WPE-Loopback-Upstream-Addr header", "header_prefix", "x-wpe-loopback-upstream-addr", ""),
    ("vercel", "x-vercel-id header", "header_prefix", "x-vercel-id", ""),
    ("cloudflare", "Server: cloudflare", "header_regex", "server", r"^cloudflare"),
    ("cloudflare", "cf-ray header", "header_prefix", "cf-ray", ""),
]

# Page-level weights used only to pick between two platforms that both matched.
_WEIGHT = {1: 3, 2: 2, 3: 1}

_FRAMEWORKS = {"nextjs"}  # lose to a CMS/builder when both match


@dataclass
class Detection:
    platform: str | None = None
    version: str | None = None
    plan: str | None = None
    plan_basis: str | None = None       # why we believe the plan, in words
    hits: list[dict] = field(default_factory=list)   # every signal that fired for the winner
    candidates: dict[str, int] = field(default_factory=dict)  # platform -> score
    ambiguous_between: list[str] = field(default_factory=list)
    hosting: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _fires(rule_kind: str, a: str, b: str, headers: dict[str, str], html: str,
           page: ParsedPage, host: str) -> str | None:
    """Returns a short evidence string if the signal is present, else None."""
    if rule_kind == "header_prefix":
        for name, value in headers.items():
            if name.startswith(a):
                return f"{name}: {value[:60]}"
    elif rule_kind == "header_regex":
        value = headers.get(a)
        if value is not None and re.search(b, value, re.I):
            return f"{a}: {value[:80]}"
    elif rule_kind == "html_regex":
        m = re.search(a, html, re.I)
        if m:
            return f"HTML contains '{m.group(0)[:60]}'"
    elif rule_kind == "meta_regex":
        value = page.meta.get(a)
        if value is not None and re.search(b, value, re.I):
            return f'<meta name="{a}" content="{value[:60]}">'
    elif rule_kind == "host_suffix":
        if host == a.lstrip(".") or host.endswith(a if a.startswith(".") else "." + a):
            return f"served from {host}"
    return None


def has_same_origin_plugins(html: str, host: str) -> bool:
    """True if the page loads anything from /wp-content/plugins/ on ITS OWN host.

    Jetpack puts the same wp.com asset hostnames on self-hosted sites, so the
    hostnames alone cannot tell WordPress.com from self-hosted WordPress. Real
    plugin files on the site's own address can (PLAN.md section 4).
    """
    bare = host.removeprefix("www.")
    for m in re.finditer(r"(?:(?:https?:)?//([^/\"'\s)]+))?/wp-content/plugins/", html):
        origin = m.group(1)
        if origin is None or origin.lower().removeprefix("www.") == bare:
            return True
    return False


def detect(*, headers: dict[str, str], html: str, final_url: str, page: ParsedPage) -> Detection:
    host = (urlsplit(final_url).hostname or "").lower()
    result = Detection()

    # 1. Fire every platform rule.
    fired: dict[str, list[tuple[Rule, str]]] = {}
    for rule in PLATFORM_RULES:
        evidence = _fires(rule.kind, rule.a, rule.b, headers, html, page, host)
        if evidence:
            fired.setdefault(rule.platform, []).append((rule, evidence))

    # 2. A platform is "detected" only on one Tier 1/2 hit, or two independent Tier 3 hits.
    accepted: dict[str, int] = {}
    for platform, hits in fired.items():
        strong = any(r.tier in (1, 2) for r, _ in hits)
        weak = len({r.label for r, _ in hits if r.tier == 3})
        if strong or weak >= 2:
            accepted[platform] = sum(_WEIGHT[r.tier] for r in {r.label: r for r, _ in hits}.values())
    result.candidates = dict(accepted)

    # 3. Frameworks (Next.js) lose to any CMS or builder that also matched.
    non_frameworks = {p: s for p, s in accepted.items() if p not in _FRAMEWORKS}
    pool = non_frameworks or accepted

    if pool:
        best = max(pool.values())
        winners = [p for p, s in pool.items() if s == best]
        if len(winners) == 1:
            result.platform = winners[0]
        else:
            result.ambiguous_between = sorted(winners)
    if result.platform:
        result.hits = [{"tier": r.tier, "signal": r.label, "evidence": ev} for r, ev in fired[result.platform]]

    # 4. WordPress.com vs self-managed WordPress.
    if result.platform == "wordpress":
        _discriminate_wordpress(result, headers, html, host, page)

    # 5. Version and plan, only from evidence we can state.
    _version_and_plan(result, html, host, page)

    # 6. Hosting / edge layers and notes.
    for name, label, kind, a, b in HOST_RULES:
        if _fires(kind, a, b, headers, html, page, host) and name not in result.hosting:
            result.hosting.append(name)
    if "hubspot" != result.platform and re.search(r"\b_hsq\b|js\.hs-scripts\.com", html):
        result.notes.append(
            "HubSpot tracking script present, but no x-hs-hub-id header, so the site is not hosted on HubSpot."
        )
    return result


def _discriminate_wordpress(result: Detection, headers: dict[str, str], html: str,
                            host: str, page: ParsedPage) -> None:
    host_header = headers.get("host-header", "").lower() == "wordpress.com"         # VERIFIED
    generator_wpcom = page.meta.get("generator", "").lower().startswith("wordpress.com")  # VERIFIED
    cdn = bool(re.search(r"//s[0-2]\.wp\.com|files\.wordpress\.com", html))
    on_wpcom_address = host.endswith(".wordpress.com")
    wpcom_evidence = host_header or generator_wpcom or cdn or on_wpcom_address
    own_plugins = has_same_origin_plugins(html, host)

    if wpcom_evidence and not own_plugins:
        result.platform = "wordpress_com"
        result.hits.append({
            "tier": 1 if host_header else 2,
            "signal": "WordPress.com markers, and no plugin files on the site's own address",
            "evidence": "host-header: WordPress.com" if host_header else "wp.com hostnames / generator",
        })
    elif host_header and own_plugins:
        # Hosted by WordPress.com but running its own plugins: that only happens on
        # the Business plan or higher, which is effectively self-managed WordPress.
        result.hosting.append("wordpress_com")
        result.plan = "business_or_higher"
        result.plan_basis = "hosted by WordPress.com but loads plugin files from its own address, which needs the Business plan or higher"
    elif cdn:
        result.notes.append(
            "wp.com hostnames present but plugin files load from the site's own address: treated as "
            "self-managed WordPress (Jetpack adds the same hostnames)."
        )


def _version_and_plan(result: Detection, html: str, host: str, page: ParsedPage) -> None:
    p = result.platform
    if p == "squarespace":
        m = re.search(r'"templateVersion"\s*:\s*"(\d+(?:\.\d+)?)"', html)          # VERIFIED
        if m:
            result.version = m.group(1)
    elif p == "wordpress":
        m = re.match(r"WordPress\s+([\d.]+)", page.meta.get("generator", ""))
        if m:
            result.version = m.group(1)
    elif p == "drupal":
        m = re.match(r"Drupal\s+(\d+)", page.meta.get("generator", ""))
        if m:
            result.version = m.group(1)

    # Plans are rarely visible from outside. We only name one when there is evidence.
    if p == "wordpress_com":
        if host.endswith(".wordpress.com"):
            result.plan = "free"
            result.plan_basis = (
                "inferred: the site is on a *.wordpress.com address. A paid plan can still use that "
                "address, so confirm with the client."
            )
    elif p == "framer":
        if host.endswith(".framer.website"):
            result.plan = "free"
            result.plan_basis = (
                "inferred: the site is on a *.framer.website address (no custom domain). "
                "Confirm with the client."
            )
