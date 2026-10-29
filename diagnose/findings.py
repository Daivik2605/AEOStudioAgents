"""Turns the measured facts into findings (PLAN.md section 3.4).

Every finding has a stable code (diagnose/codes.py), a severity, what is
wrong, the evidence, whether the platform can fix it, where the fix goes, and
a source. This is plain code: the same facts always produce the same list.
"""

from __future__ import annotations

from diagnose import platform_data as pd
from diagnose.codes import SEVERITIES, make_finding
from diagnose.crawler import BOTS, FAILING
from diagnose.facts import Facts
from diagnose.rules import NEVER_DELIBERATE, crawl_inconclusive
from diagnose.site_checks import EXAMPLES_SHOWN, OLD_URL_CODES, example_line

_PD = "research/PLATFORM_DELIVERY.md"


def _short(text: str, n: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def build_findings(facts: Facts) -> list[dict]:
    out: list[dict] = []
    platform = facts.platform
    det = facts.detection
    label = pd.PLATFORM_LABELS.get(platform or "", "this platform")

    # ---- platform ----------------------------------------------------------
    if platform in pd.DECLINE_TABLE:
        row = pd.DECLINE_TABLE[platform]
        out.append(make_finding(
            "PLATFORM_UNSERVEABLE",
            what=f"{label} cannot carry on-page AEO work: {row['why']}",
            evidence="; ".join(f"{h['signal']} ({h['evidence']})" for h in det.hits)[:300],
            fixable_on_platform=False,
            where_to_fix="Not fixable on the platform. Migration to a platform that can carry the work; a person must approve before any pitch is written.",
            source=row["source"]))
    elif platform is None and det.ambiguous_between:
        out.append(make_finding(
            "PLATFORM_AMBIGUOUS", what="Two platforms matched equally well, so none is reported",
            evidence=", ".join(det.ambiguous_between), fixable_on_platform=None,
            where_to_fix="Ask the client what the site is built on.", source="PLAN.md §4"))
    elif platform is None:
        out.append(make_finding(
            "PLATFORM_UNDETECTED", what="No known platform was recognised from the headers or HTML",
            evidence="no Tier 1 or Tier 2 signal, and fewer than two Tier 3 signals", fixable_on_platform=None,
            where_to_fix="Ask the client what the site is built on. The crawler and render checks below do not depend on it.",
            source="PLAN.md §4 (never report a platform on a Tier 3 signal alone)"))

    gate = pd.PLAN_GATES.get(platform or "")
    if gate:
        if det.plan in gate["plans"]:
            out.append(make_finding(
                "PLATFORM_PLAN_TOO_LOW", what=gate["blocker"],
                evidence=det.plan_basis or f"plan: {det.plan}", fixable_on_platform=True,
                where_to_fix=gate["fix"], source=gate["source"]))
        elif det.plan is None:
            out.append(make_finding(
                "PLATFORM_PLAN_UNCONFIRMED",
                what=f"{label} has a plan tier that blocks this work, and the plan cannot be seen from outside",
                evidence="plan is not visible in the headers or HTML", fixable_on_platform=True,
                where_to_fix=pd.PLAN_GATE_QUESTION[platform], source=gate["source"]))

    if "wp_engine" in det.hosting:
        w = pd.CONDITIONAL_WP_ENGINE
        out.append(make_finding(
            "HOST_WP_ENGINE_DETECTED", what="The site is hosted on WP Engine, which is known to rate-limit AI crawlers",
            evidence="X-WPE-Loopback-Upstream-Addr header present", fixable_on_platform=False,
            where_to_fix=w["fix"], source=w["source"]))

    # ---- crawler ---------------------------------------------------------------
    if facts.crawler:
        cloudflare = "cloudflare" in det.hosting
        # Every bot that was not given the page gets a finding. Only some of them also
        # change the verdict (see rules.blocking_bots); a training bot refused with a plain
        # "no" is recorded as low, because that is often the owner's choice.
        for name, (verdict, detail) in facts.crawler.verdicts.items():
            if verdict not in FAILING:
                continue
            bot = next(b for b in BOTS if b.name == name)
            if "wp_engine" in det.hosting and verdict == "rate_limited":
                fixable, where, src = False, pd.CONDITIONAL_WP_ENGINE["fix"], pd.CONDITIONAL_WP_ENGINE["source"]
            elif cloudflare:
                fixable, src = True, f"{_PD} §3"
                where = ("Cloudflare dashboard. Three separate systems can each block AI bots and all three "
                         "need checking: AI Crawl Control, Managed robots.txt, and Bot Fight Mode.")
            else:
                fixable, src = None, f"{_PD} §7"
                where = f"Hosting / CDN / firewall settings: ask whoever manages the hosting to allow {bot.token}, then re-test."
            out.append(make_finding(
                f"CRAWLER_BLOCKED_{bot.code_suffix}",
                what=f"{name} is {verdict.replace('_', ' ')}: the browser gets the page and this bot does not",
                evidence=_short(detail), fixable_on_platform=fixable, where_to_fix=where, source=src,
                # A training bot that is rate-limited or challenged was not "politely refused".
                severity="medium" if bot.kind == "training" and verdict in NEVER_DELIBERATE else None))
        if crawl_inconclusive(facts):
            detail = next((d for v, d in facts.crawler.verdicts.values() if v in ("blanket_blocked", "inconclusive")), "")
            out.append(make_finding(
                "CRAWLER_TEST_INCONCLUSIVE", what="The crawler test could not tell us anything about AI crawlers",
                evidence=_short(detail), fixable_on_platform=None,
                where_to_fix="Re-run from another network. Do not report this as an AI block.",
                source=f"{_PD} §7"))
    elif not facts.site_reachable:
        out.append(make_finding(
            "CRAWLER_TEST_INCONCLUSIVE", what="The site did not give us a page, so nothing could be tested",
            evidence=_short(facts.page.error or f"HTTP {facts.page.status}"), fixable_on_platform=None,
            where_to_fix="Check the address, then re-run. Do not report this as an AI block.", source=f"{_PD} §7"))

    # ---- robots.txt -------------------------------------------------------------
    r = facts.robots
    if r:
        if r.total_disallow:
            if r.bot_fetch_failed_but_browser_ok:
                out.append(make_finding(
                    "ROBOTS_TXT_BOT_FETCH_FAILED",
                    what="robots.txt fails for bots but works for a browser. Under RFC 9309 that is a complete disallow, invisible from a browser",
                    evidence=f"bot: HTTP {r.status} ({r.outcome}); browser: HTTP {r.browser_status}",
                    fixable_on_platform=None,
                    where_to_fix="CDN / firewall: let bot user agents fetch /robots.txt normally.",
                    source="RFC 9309 §2.3.1; research/PLATFORM_DELIVERY.md §3"))
            else:
                out.append(make_finding(
                    "ROBOTS_TXT_UNREACHABLE", what="robots.txt could not be fetched for bots or for a browser",
                    evidence=f"HTTP {r.status} ({r.outcome})", fixable_on_platform=None,
                    where_to_fix="Check that the site and /robots.txt are up, then re-run.",
                    source="RFC 9309 §2.3.1"))
        else:
            fixable, how = pd.delivery_route(platform, "robots")
            for bot in BOTS:
                if not r.allowed.get(bot.token, True):
                    out.append(make_finding(
                        f"ROBOTS_DISALLOWS_{bot.code_suffix}",
                        what=(f"robots.txt disallows {bot.token}"
                              + (", a bot that fetches pages to answer questions" if bot.kind == "retrieval"
                                 else ", a training bot. Blocking it is often deliberate and does not stop AI search")),
                        evidence=f"{bot.token} may not fetch {facts.page.final_url or facts.url}",
                        fixable_on_platform=fixable, where_to_fix=how,
                        source="RFC 9309; research/PLATFORM_DELIVERY.md §6"))
        if r.served_as_html:
            out.append(make_finding(
                "ROBOTS_TXT_SERVED_AS_HTML", what="robots.txt is served as an HTML page, so a crawler may read it as junk",
                evidence=_short(r.raw or ""), fixable_on_platform=None,
                where_to_fix="Make /robots.txt return plain text (a hosting setting on single-page-app hosts).",
                source=f"{_PD} §7"))
        if r.content_signal:
            from diagnose.robots import parse_content_signal
            if parse_content_signal(r.content_signal).get("ai-input") == "no":
                fixable, how = pd.delivery_route(platform, "robots")
                out.append(make_finding(
                    "CONTENT_SIGNAL_AI_INPUT_NO",
                    what="Content-Signal says ai-input=no: AI answer engines are asked not to use this content",
                    evidence=f"Content-Signal: {r.content_signal} ({r.content_signal_source})",
                    fixable_on_platform=fixable, where_to_fix=how, source=f"{_PD} §7 (Content-Signal)"))
    if facts.llms is not None:
        got = facts.llms
        present = got.status == 200 and "html" not in got.headers.get("content-type", "").lower() \
            and not got.text.lstrip()[:100].lower().startswith(("<!doctype", "<html"))
        if not present:
            fixable, how = pd.delivery_route(platform, "llms")
            out.append(make_finding(
                "LLMS_TXT_MISSING",
                what="There is no /llms.txt. Cheap hygiene, but it is one of the weakest levers in the evidence",
                evidence=f"/llms.txt returned HTTP {got.status}" if got.status else _short(got.error or "no answer"),
                fixable_on_platform=fixable, where_to_fix=how, source="research/AEO_PLAYBOOK.md §3 (P2, unproven)"))

    # ---- render ---------------------------------------------------------------------
    rc = facts.render
    if rc:
        if rc.verdict == "content_missing":
            out.append(make_finding(
                "RENDER_CONTENT_MISSING",
                what="The readable content is not in the server HTML (client-rendered). AI crawlers that do not run JavaScript see an empty page",
                evidence=_short("; ".join(rc.reasons)),
                fixable_on_platform=None if platform in pd.SERVER_RENDERING_PLATFORMS else False,
                where_to_fix=("This platform normally server-renders, so check this by hand before reporting it."
                              if platform in pd.SERVER_RENDERING_PLATFORMS
                              else "Server-side rendering or static generation: a separate project, priced separately."),
                source=pd.CONDITIONAL_SPA["source"]))
        elif rc.verdict == "likely_missing" and platform not in pd.SERVER_RENDERING_PLATFORMS:
            # (Wix, Squarespace, Shopify and the like always server-render, so on them this
            # ratio test is our heuristic misfiring, not a real finding: PLATFORM_DELIVERY.md §7.)
            out.append(make_finding(
                "RENDER_CONTENT_LIKELY_MISSING", what="Very little of the HTML is readable text, which suggests client-side rendering",
                evidence=_short("; ".join(rc.reasons)), fixable_on_platform=None,
                where_to_fix="Check the page with JavaScript off. If the content is missing, it needs server-side rendering.",
                source=f"{_PD} §7"))
        names = {"name": ("RENDER_BUSINESS_NAME_MISSING", "The business name"),
                 "phone": ("RENDER_PHONE_MISSING", "The phone number"),
                 "address": ("RENDER_ADDRESS_MISSING", "The street address")}
        for key, (code, text) in names.items():
            d = rc.details.get(key, {})
            if d.get("checked") and not d["in_readable_text"]:
                where = "in the raw HTML only (structured data / hidden), not in readable text" if d["in_raw_html"] else "not anywhere in the HTML"
                out.append(make_finding(
                    code, what=f"{text} is not in the readable text of the no-JavaScript page",
                    evidence=f"looked for {d['expected']!r}: {where}", fixable_on_platform=True,
                    where_to_fix="On the page itself, in visible text (site editor).", source=f"{_PD} §7"))
        if not rc.h1_texts:
            out.append(make_finding(
                "RENDER_H1_MISSING", what="The page has no <h1> heading", evidence="0 <h1> tags in the HTML",
                fixable_on_platform=True, where_to_fix="On the page itself: give the page one clear heading (site editor).",
                source=f"{_PD} §7"))
        elif rc.h1_nonempty == 0:
            out.append(make_finding(
                "RENDER_H1_EMPTY", what="The page has <h1> tags but none has any text",
                evidence=f"{len(rc.h1_texts)} <h1> tag(s), all empty", fixable_on_platform=True,
                where_to_fix="On the page itself: put the heading text in the <h1> (site editor).",
                source=f"{_PD} §7"))
        if len(rc.h1_texts) > 1:
            out.append(make_finding(
                "RENDER_H1_MULTIPLE", what="The page has more than one <h1>",
                evidence=f"{len(rc.h1_texts)} <h1> tags: {rc.h1_texts!r}"[:300], fixable_on_platform=True,
                where_to_fix="On the page itself: keep one <h1> (site editor).", source=f"{_PD} §7"))

    # ---- structured data --------------------------------------------------------------
    if facts.schema:
        fixable, place = pd.delivery_route(platform, "jsonld")
        for issue in facts.schema.issues:
            if fixable is None:   # platform unknown: `place` is already a whole sentence
                where = place
            elif issue.code == "SCHEMA_NONE_FOUND":
                where = f"Add JSON-LD in: {place}."
            else:
                where = f"Edit the existing structured data. On this platform it goes in: {place}."
            src = f"{pd.DELIVERY_SOURCE}; research/AEO_PLAYBOOK.md §3"
            if issue.code == "SCHEMA_NAME_IS_LEGAL_ENTITY" and platform == "squarespace":
                where, src = "Squarespace → Settings → Business Information → Business Name", "PLAN.md §3.4 (worked example)"
            out.append(make_finding(
                issue.code, what=issue.what, evidence=_short(issue.evidence),
                fixable_on_platform=fixable, where_to_fix=where, source=src))

    out += site_check_findings(facts)

    rank = {s: i for i, s in enumerate(SEVERITIES)}
    out.sort(key=lambda f: (rank[f["severity"]], f["code"]))
    return out


# ---- the step 25 checks: sitemap, addresses, old addresses -----------------------------------------------------------------

_SITEMAP_SOURCE = "sitemaps.org protocol; PLAN.md step 25"
_HOST_SOURCE = "RFC 9110 §15.4 (redirection status codes); PLAN.md step 25"
_OLD_SOURCE = "RFC 9110 §15.4 (301/308 permanent, 302/307 temporary); PLAN.md step 25"
_ASK_HOST = "At the domain or hosting settings (or the CDN in front of the site): ask whoever manages them."


def site_check_findings(facts: Facts) -> list[dict]:
    sc = facts.site_checks
    if sc is None:
        return []
    out: list[dict] = []

    # ---- sitemap ----
    s = sc.sitemap
    if s is not None:
        where = ("The site's SEO settings or its sitemap feature (many site builders make /sitemap.xml for you); "
                 "otherwise ask whoever manages the site.")
        tried = ", ".join(f"{r['url']} -> " + (f"HTTP {r['status']}" if r["status"] else "no answer") for r in s.fetched[:4])
        if not s.found:
            out.append(make_finding(
                "SITEMAP_MISSING", what="The site has no sitemap: /sitemap.xml and the sitemaps named in robots.txt did not answer",
                evidence=_short(tried or "nothing to try"), fixable_on_platform=None, where_to_fix=where, source=_SITEMAP_SOURCE))
        elif not s.valid:
            bad = next((r for r in s.fetched if r["status"] == 200), s.fetched[0])
            why = ("is not valid XML" if bad["kind"] in (None, "not_xml", "other_xml") else "lists no pages")
            out.append(make_finding(
                "SITEMAP_INVALID", what=f"A sitemap was found but it {why}, so crawlers cannot use it",
                evidence=_short(f"{bad['url']} -> HTTP {bad['status']}, {bad['content_type'] or 'no content type'}"),
                fixable_on_platform=None, where_to_fix=where, source=_SITEMAP_SOURCE))
        else:
            if s.in_robots is False:
                out.append(make_finding(
                    "SITEMAP_NOT_IN_ROBOTS", what="The sitemap works but robots.txt does not list it",
                    evidence=_short(f"robots.txt has {len(s.robots_listed)} Sitemap line(s)" if s.robots_listed
                                    else "robots.txt has no Sitemap: line"),
                    fixable_on_platform=None,
                    where_to_fix="Add a line to robots.txt: Sitemap: <the sitemap address>. " + pd.delivery_route(facts.platform, "robots")[1],
                    source=_SITEMAP_SOURCE))
            if s.mismatched_urls:
                out.append(make_finding(
                    "SITEMAP_HOST_MISMATCH",
                    what=f"The sitemap lists {s.mismatched_urls} page(s) on a different form of the address than the site uses ({s.canonical_host})",
                    evidence=_short("; ".join(s.mismatch_examples[:3])), fixable_on_platform=None,
                    where_to_fix="Regenerate the sitemap so every address uses " + s.canonical_host + " (the site's SEO or domain settings).",
                    source=_SITEMAP_SOURCE))

    # ---- www / non-www and http / https ----
    h = sc.hosts
    if h is not None:
        rows = [h.apex, h.www]
        if all(r.answered_200 for r in rows):
            out.append(make_finding(
                "HOST_DUPLICATE", what=f"Both {h.apex.host} and {h.www.host} answer with the site, and neither sends visitors to the other",
                evidence=f"{h.apex.host}: HTTP {h.apex.status}; {h.www.host}: HTTP {h.www.status}", fixable_on_platform=None,
                where_to_fix="Pick one address and add a permanent (301) redirect from the other. " + _ASK_HOST, source=_HOST_SOURCE))
        for r in rows:
            other = h.www if r is h.apex else h.apex
            if r.status in TEMPORARY_STATUSES and r.redirects_to_host == other.host.lower():
                out.append(make_finding(
                    "HOST_REDIRECT_NOT_PERMANENT",
                    what=f"{r.host} sends visitors to {other.host} with a temporary redirect (HTTP {r.status}), not a permanent one",
                    evidence=f"{r.host} -> {r.location} (HTTP {r.status})", fixable_on_platform=None,
                    where_to_fix="Change the redirect to a permanent one (301 or 308). " + _ASK_HOST, source=_HOST_SOURCE))
        for r in rows:
            other = h.www if r is h.apex else h.apex
            if r.failed_to_connect and not r.dns_failure and not other.failed_to_connect:
                out.append(make_finding(
                    "HOST_WWW_UNREACHABLE", what=f"{r.host} exists but does not answer, while {other.host} does",
                    evidence=_short(r.error or "no answer"), fixable_on_platform=None,
                    where_to_fix="Point it at the site, or redirect it permanently to " + other.host + ". " + _ASK_HOST,
                    source=_HOST_SOURCE))
        if h.http_ends_at_https is False:
            out.append(make_finding(
                "HTTP_NOT_REDIRECTED", what="Opening the site over plain http does not end up on https",
                evidence=f"http://{h.canonical_host}/ ended at {h.http_final_url} (HTTP {h.http_status})",
                fixable_on_platform=None,
                where_to_fix="Turn on 'force HTTPS' or add a permanent redirect from http to https. " + _ASK_HOST, source=_HOST_SOURCE))

    # ---- old addresses (only when asked for) ----
    o = sc.old_urls
    if o is not None:
        total = len(o.results)
        what = {
            "OLD_URL_404": "end at a page that is not there (404 or 410)",
            "OLD_URL_NOT_PERMANENT": "redirect with a temporary redirect (302 or 307)",
            "OLD_URL_CHAIN": "go through more than one redirect before reaching a page",
            "OLD_URL_TO_HOME": "are all sent to the homepage instead of a matching page",
        }
        fix = {
            "OLD_URL_404": "Add a permanent (301) redirect from each old address to the matching new page.",
            "OLD_URL_NOT_PERMANENT": "Change these redirects to permanent ones (301 or 308).",
            "OLD_URL_CHAIN": "Point each old address straight at its final page with a single permanent redirect.",
            "OLD_URL_TO_HOME": "Redirect each old address to its matching new page, not to the homepage.",
        }
        for code in OLD_URL_CODES:
            hit = o.with_issue(code)
            if not hit:
                continue
            examples = [example_line(r) for r in hit[:EXAMPLES_SHOWN]]
            more = f" (and {len(hit) - EXAMPLES_SHOWN} more in the evidence file)" if len(hit) > EXAMPLES_SHOWN else ""
            out.append(make_finding(
                code, what=f"{len(hit)} of {total} old address(es) checked {what[code]}",
                evidence="; ".join(examples) + more, fixable_on_platform=None,
                where_to_fix=fix[code] + " " + _ASK_HOST, source=_OLD_SOURCE))
    return out


TEMPORARY_STATUSES = (302, 307)
