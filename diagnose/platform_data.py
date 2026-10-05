"""Reference data about website platforms. DATA, not logic, not prompt text.

Everything here comes from PLAN.md section 5 (the decision rules) or
research/PLATFORM_DELIVERY.md (the capability matrix). Each entry names its
source so the report can cite it. Update this file when the research is
re-checked; the code that reads it does not change.

The table EXPLAINS a result and supplies the right remediation text. It never
predicts one: the live crawler test always wins (PLAN.md section 5).
"""

from __future__ import annotations

# Platforms whose pages come back with the text already in the HTML. A
# "client-rendered" verdict on one of these is almost certainly our heuristic
# misfiring, so the SPA rule ignores them (PLATFORM_DELIVERY.md §7).
SERVER_RENDERING_PLATFORMS = {
    "wix", "squarespace", "shopify", "webflow", "framer", "wordpress",
    "wordpress_com", "drupal", "joomla", "hubspot", "duda",
}

PLATFORM_LABELS = {
    "wix": "Wix",
    "squarespace": "Squarespace",
    "shopify": "Shopify",
    "webflow": "Webflow",
    "framer": "Framer",
    "hubspot": "HubSpot CMS",
    "duda": "Duda",
    "wordpress": "WordPress (self-managed)",
    "wordpress_com": "WordPress.com",
    "drupal": "Drupal",
    "joomla": "Joomla",
    "nextjs": "Next.js (custom site)",
    "google_sites": "Google Sites",
    "notion": "Notion-published",
    "weebly": "Weebly",
    "godaddy_builder": "GoDaddy Website Builder",
}

# ---- PLAN.md §5: Decline — the work cannot be done ------------------------
DECLINE_TABLE = {
    "google_sites": {
        "why": (
            "Code embeds are iframed, so JSON-LD never reaches the parent document. "
            "No head access. No meta description field at all. No robots.txt control."
        ),
        "source": (
            "Google embed docs; Steegle SEO guide, adapted from Google's own because much of it "
            "\"is not possible with Google Sites, due to the security restrictions in place\""
        ),
    },
    "notion": {
        "why": (
            "A no-JavaScript fetch returns a 95-character shell: \"JavaScript must be enabled in "
            "order to use Notion.\" No body text, no headings, no JSON-LD."
        ),
        "source": "Dated first-hand test, 10 Aug 2026, superblog.ai",
    },
    "weebly": {
        "why": "Sites were unpublished 27 Sep 2026; the platform ends 2 Jan 2027 across 67 countries.",
        "source": "Network Solutions discontinuation notice",
    },
    "godaddy_builder": {
        "why": (
            "No site-wide head field. Custom HTML sections are almost certainly iframed. "
            "No robots.txt control."
        ),
        "source": "GoDaddy support 27252, 28025",
    },
}

# ---- PLAN.md §5: Conditional — platform plan gates ------------------------
# `plans` are the plan names (as detect.py reports them) that trigger the rule.
PLAN_GATES = {
    "squarespace": {
        "plans": {"basic"},
        "blocker": "Squarespace Basic has no code injection at all.",
        "fix": "Upgrade to Core ($29/mo billed annually).",
        "source": "\"Code injection is available on the Core, Plus, Advanced, and some legacy billing plans\"",
    },
    "wordpress_com": {
        "plans": {"free"},
        "blocker": "WordPress.com Free has no plugins, no custom code and no meta control.",
        "fix": "Upgrade to Personal (about $48/yr).",
        "source": "wordpress.com plan features",
    },
    "framer": {
        "plans": {"free", "basic"},
        "blocker": "On Framer Free/Basic, robots.txt and llms.txt are Pro-gated.",
        "fix": "Upgrade to Pro.",
        "source": "Framer: robots.txt via Static Files, \"Pro and Enterprise\"",
    },
}

# Platforms with a plan gate where the plan is not visible from outside.
# When we cannot see the plan, we say so rather than guess (finding PLATFORM_PLAN_UNCONFIRMED).
PLAN_GATE_QUESTION = {
    "squarespace": "Ask the client which plan they are on. Basic needs an upgrade to Core ($29/mo billed annually).",
    "wordpress_com": "Ask the client which plan they are on. Free needs an upgrade to Personal (about $48/yr).",
    "framer": "Ask the client which plan they are on. Free/Basic needs an upgrade to Pro.",
}

# ---- PLAN.md §5: Conditional — other situations ---------------------------
CONDITIONAL_CRAWLER = {
    "blocker": "AI crawlers are blocked: a bot user agent is refused or challenged where a browser is not.",
    "fix": "Fix at the edge (CDN / firewall / host) first.",
    "source": "Our own measurement (live crawler test)",
}
CONDITIONAL_SPA = {
    "blocker": "The page is client-rendered: the business facts are not in the no-JavaScript HTML.",
    "fix": "Server-side rendering or static generation. This is a separate project, priced separately.",
    "source": (
        "Vercel: \"none of the major AI crawlers currently render JavaScript… OpenAI, Anthropic, "
        "Meta, ByteDance, Perplexity\""
    ),
}
CONDITIONAL_WP_ENGINE = {
    "blocker": (
        "WP Engine rate-limits AI crawlers (ClaudeBot got 429 on 60 of 60 requests) and states "
        "this \"can't be selectively disabled per bot\"."
    ),
    "fix": "Escalate to WP Engine, or migrate host.",
    "source": "Search Engine Land, April 2026 testing",
}

# ---- research/PLATFORM_DELIVERY.md §1: capability matrix -------------------
# "yes" works on every plan; "gated" works only on a higher plan or with a
# plugin; "no" cannot be done. For 'jsonld' the text is the PLACE structured data
# goes (a noun phrase); for 'robots' and 'llms' it is a full instruction.
DELIVERY = {
    "shopify": {
        "jsonld": ("yes", "theme.liquid (works on every plan except Starter/Lite)"),
        "robots": ("yes", "Edit robots.txt.liquid in the theme."),
        "llms": ("yes", "Shopify serves /llms.txt natively; override it in the theme if needed."),
        "min_plan": "Basic (not Starter/Lite)",
    },
    "wordpress": {
        "jsonld": ("yes", "an SEO/schema plugin, a snippet plugin, or the theme"),
        "robots": ("yes", "Edit the virtual robots.txt (SEO plugin) or the physical file."),
        "llms": ("yes", "Use one of the llms.txt plugins, or upload the file to the site root."),
        "min_plan": "any",
    },
    "drupal": {
        "jsonld": ("yes", "a module or the theme templates"),
        "robots": ("yes", "Edit robots.txt in the site root."),
        "llms": ("yes", "Hand-write /llms.txt in the site root (Drupal llms.txt modules are immature)."),
        "min_plan": "any",
    },
    "joomla": {
        "jsonld": ("yes", "a plugin or the template"),
        "robots": ("yes", "Edit robots.txt in the site root."),
        "llms": ("yes", "Hand-write /llms.txt in the site root (Joomla llms.txt modules are immature)."),
        "min_plan": "any",
    },
    "webflow": {
        "jsonld": ("yes", "Site Settings or Page Settings → Custom Code, pasted by hand (the API cannot do this)"),
        "robots": ("yes", "Edit robots.txt in Site Settings (paid plans)."),
        "llms": ("yes", "Upload llms.txt in the dashboard (custom domain only)."),
        "min_plan": "Basic",
    },
    "wix": {
        "jsonld": ("yes", "the Wix editor (Advanced SEO / custom code)"),
        "robots": ("yes", "Edit robots.txt in the Wix SEO tools."),
        "llms": ("gated", "Wix generates llms.txt automatically but the rollout is gated. Test /llms.txt first."),
        "min_plan": "Light ($17/mo billed annually)",
    },
    "hubspot": {
        "jsonld": ("yes", "the page head HTML (also available through the API)"),
        "robots": ("yes", "Edit robots.txt in HubSpot settings."),
        "llms": ("no", "HubSpot has no native llms.txt support."),
        "min_plan": "any",
    },
    "duda": {
        "jsonld": ("yes", "site-wide or per-page head HTML in the Duda editor"),
        "robots": ("yes", "Replace the robots.txt file in Duda settings."),
        "llms": ("yes", "Duda generates llms.txt automatically."),
        "min_plan": "depends on the reseller",
    },
    "framer": {
        "jsonld": ("yes", "Site Settings → Custom Code (check that it appears in the no-JavaScript HTML)"),
        "robots": ("gated", "Framer robots.txt is Pro-gated (Static Files)."),
        "llms": ("gated", "Framer llms.txt is Pro-gated and added by hand."),
        "min_plan": "Pro for robots.txt / llms.txt",
    },
    "squarespace": {
        "jsonld": ("gated", "Settings → Advanced → Code Injection (Core plan or higher only)"),
        "robots": ("no", "Squarespace never lets you edit robots.txt. The only control is the all-or-nothing AI crawler checkbox in Settings → Crawlers."),
        "llms": ("gated", "Squarespace 7.1 only; we write the file by hand."),
        "min_plan": "Core ($29/mo billed annually)",
    },
    "wordpress_com": {
        "jsonld": ("gated", "a schema plugin (Personal plan or higher)"),
        "robots": ("gated", "Needs the Personal plan or higher (through a plugin)."),
        "llms": ("gated", "Needs the Personal plan or higher (through a plugin)."),
        "min_plan": "Personal (about $48/yr)",
    },
}
DELIVERY_SOURCE = "research/PLATFORM_DELIVERY.md §1"

# ---- research/PLATFORM_DELIVERY.md §1: "Deliverable share of a standard fix list"
# readiness_score = the middle of the research's range for the platform's tier.
# PLAN.md does not define readiness_score, so this is a decision (flagged in the
# hand-over); it uses only numbers the research already states.
SHARE_RANGES = {
    "easy": (95, 100),
    "good": (85, 90),
    "workable": (70, 85),
    "constrained": (60, 70),
    "blocked_by_plan": (0, 25),
}
PLATFORM_SHARE_TIER = {
    "shopify": "easy", "wordpress": "easy", "drupal": "easy", "joomla": "easy",
    "webflow": "good", "wix": "good",
    "hubspot": "workable", "duda": "workable", "framer": "workable",
    "wordpress_com": "constrained", "squarespace": "constrained",
}
READINESS_SOURCE = "research/PLATFORM_DELIVERY.md §1, 'Deliverable share of a standard fix list'"


def readiness_score(platform: str | None, plan: str | None, declined: bool) -> float | None:
    """Middle of the researched 'deliverable share' range, or None if unknown.

    Un-serveable platforms score 0: none of the on-page work can be delivered.
    A plan the research calls 'blocked by plan' uses that tier's range.
    Unknown or custom platforms have no researched figure, so None.
    """
    if declined:
        return 0.0
    if platform is None:
        return None
    gate = PLAN_GATES.get(platform)
    if gate and plan in gate["plans"] and platform in ("squarespace", "wordpress_com"):
        low, high = SHARE_RANGES["blocked_by_plan"]
        return (low + high) / 2
    tier = PLATFORM_SHARE_TIER.get(platform)
    if tier is None:
        return None
    low, high = SHARE_RANGES[tier]
    return (low + high) / 2


def delivery_route(platform: str | None, thing: str) -> tuple[bool | None, str]:
    """Where a change goes on this platform: (fixable_on_platform, where_to_fix).

    `thing` is 'jsonld', 'robots' or 'llms'. For 'jsonld' the text is only the
    place structured data goes (the caller builds the sentence); for the others
    it is a full instruction. fixable_on_platform is True for
    'yes' and 'gated' (possible, perhaps after a plan upgrade), False for 'no',
    and None when we do not know the platform.
    """
    entry = DELIVERY.get(platform or "")
    if entry is None:
        return None, "Depends on the platform: confirm what the site is built on, then see research/PLATFORM_DELIVERY.md §1."
    level, text = entry[thing]
    return (level != "no"), text
