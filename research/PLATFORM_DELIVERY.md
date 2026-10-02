# Platform delivery — what we can actually do, and where

**Research date: 2 October 2026.** Companion to `AEO_PLAYBOOK.md`. Reference document, not a decision document. `PLAN.md` is the source of truth. Section 8 lists what this implies for the gate check and the recommendations agent.

Re-check quarterly. Plan tiers and prices move, and Cloudflare changed its defaults twice in fifteen months.

---

## 0. The headline

**Three findings reshape the service.**

**1. None of the big website builders block AI crawlers by default.** Wix ships a permissive robots.txt. Squarespace's "block AI crawlers" checkbox is **off by default** and Squarespace's own guidance says to leave it off. Framer explicitly allows all AI and search crawlers and documents it. Shopify's default robots.txt has no AI-bot entries at all. The common claim that "AI can't see builder sites" is **false in 2026**.

**2. They also nearly all server-render.** Wix states it officially. Squarespace, Shopify, Webflow and Framer all return body text and headings on a no-JavaScript fetch. Framer goes further and serves markdown natively on request. The dangerous cases are not the builders — they are **headless setups, client-rendered React apps, and Notion-published pages**.

**3. The real blockers are a plan tier, an edge layer, and a missing API.**
- **Plan tier:** Squarespace Basic has no code injection at all, so we cannot add a single line of JSON-LD. WordPress.com Free has no plugins. Framer gates robots.txt and llms.txt behind Pro.
- **Edge layer:** Cloudflare and at least one managed WordPress host block AI crawlers in ways invisible from a browser.
- **Missing API:** Squarespace has no content or SEO API at all. Wix cannot rewrite body copy programmatically. Webflow cannot push raw JSON-LD through its API. These are labour-cost findings, not capability findings — they decide whether an engagement is automated or hand-typed.

---

## 1. Capability matrix

Legend: ✅ full · ⚠️ limited or gated · ❌ impossible

| Platform | Custom JSON-LD | robots.txt | llms.txt | Server-rendered | Multilingual (real URLs + hreflang) | Automation | Min. plan |
|---|---|---|---|---|---|---|---|
| **Shopify** | ✅ `theme.liquid`, all plans | ✅ `robots.txt.liquid` | ✅ native `/agents.md`, `/llms.txt`, template-overridable | ✅ Liquid | ✅ auto hreflang, crawlers exempt from geo-redirect | ✅ deepest — GraphQL incl. theme files | Basic (not Starter/Lite) |
| **Self-hosted WordPress** | ✅ plugin, snippet or theme | ✅ virtual or physical | ✅ several plugins, 40k+ installs | ✅ PHP | ✅ Polylang free, WPML €99/yr | ✅ REST + app passwords | any |
| **Drupal / Joomla** | ✅ | ✅ | ⚠️ modules immature — hand-write it | ✅ | ✅ | ✅ Drush / CLI | any |
| **Webflow** | ✅ 50k chars head, site + page | ✅ paid plans, **plus `Content-Signal` header** | ✅ manual upload, custom domain only | ✅ pre-rendered to CDN | ✅ Localize, $9–29/mo | ⚠️ **cannot push raw JSON-LD via API** | Basic |
| **Wix** | ✅ two routes, officially endorsed | ✅ **full editor** | ⚠️ auto-generated but **rollout-gated — test the URL** | ✅ officially stated | ✅ native, auto hreflang | ⚠️ metadata only, primary language only | Light, $17/mo annual |
| **HubSpot Content Hub** | ✅ all plans, **`headHtml` via API** | ✅ all plans | ❌ **no native support** | ✅ HubL | ✅ auto hreflang | ✅ best per-page head API after Shopify | any |
| **Duda** | ✅ site-wide **and per-page head** | ✅ replace the file | ✅ **auto-generated** — only platform that does | ✅ documented since 2019 | ⚠️ exists, hreflang unverified | ⚠️ API injects end-of-body only | reseller-dependent |
| **Framer** | ✅ four injection points | ⚠️ **Pro+** | ⚠️ **Pro+**, manual | ✅ officially pre-rendered **+ native markdown** | ⚠️ real paths, **hreflang unverified**, +$20/locale/mo | ❌ **no server-side API** | Pro for robots/llms |
| **Squarespace 7.1** | ⚠️ **Core+ only** | ❌ **never, any tier** | ⚠️ 7.1 only, we write it | ✅ verified for text | ❌ **no native** — Weglot w/ subdomains or manual `/fr/` | ❌ **no content or SEO API** | **Core, $29/mo annual** |
| **WordPress.com** | ⚠️ Personal+ via plugin schema engine | ⚠️ Personal+ via plugin | ⚠️ Personal+ via plugin | ✅ PHP | ✅ Personal+ | ⚠️ REST; no WP-CLI below Business | **Personal, ~$48/yr** |
| **Squarespace 7.0** | ⚠️ Core+ | ❌ | ❌ | ✅ | ❌ | ⚠️ Developer Platform (Git) | Core |
| **Carrd** | ✅ head via hidden embed | ❌ | ❌ | ⚠️ unverified | ❌ | ❌ | Pro Standard |
| **GoDaddy Builder** | ❌ body-only, **likely iframed** | ❌ | ❌ | ⚠️ unverified | ❌ | ❌ | — |
| **Weebly / Square Online** | ⚠️ site-wide header field only | ❌ toggles only | ❌ | ⚠️ unverified | ❌ | ❌ | **platform shutting down** |
| **Notion-published** | ❌ | ❌ | ❌ | ❌ **95-char JS shell** | ❌ | ❌ | — |
| **Google Sites** | ❌ **iframed and unindexed** | ❌ | ❌ | ⚠️ suggestive only | ❌ | ❌ | — |

### Deliverable share of a standard fix list

| Tier | Platforms | Share |
|---|---|---|
| **Easy** | Shopify · self-hosted WordPress (cooperative host) · Drupal/Joomla | **95–100%** |
| **Good** | Webflow · Wix | **85–90%** |
| **Workable** | HubSpot · Duda · Framer | **70–85%** |
| **Constrained** | WordPress.com Personal · Squarespace 7.1 Core+ | **60–70%** |
| **Blocked by plan** | Squarespace Basic · WordPress.com Free | **0–25%** |
| **Un-serveable** | GoDaddy Builder · Weebly · Notion · Google Sites | **scope as migration** |

---

## 2. The hard gates — check these before quoting

**Squarespace Basic.** No code injection of any kind. We cannot add one line of structured data. The client must move to **Core ($29/mo annual)** or there is no engagement. This is the single most common hard stop we will hit.

**WordPress.com Free.** No plugins, no custom code, no meta control. Only the native verification meta-tag field works. Needs **Personal (~$48/yr)**.

**Framer below Pro.** robots.txt and llms.txt are both Pro-gated. The defaults are permissive and good, but we cannot change them.

**Squarespace robots.txt — never, at any tier.** There is one all-or-nothing "block AI crawlers" checkbox and nothing else. Worse, that checkbox's list covers **training** bots but misses the **retrieval** bots that actually drive citations — `OAI-SearchBot`, `ChatGPT-User`, `Claude-SearchBot`, `PerplexityBot`. So a client who ticked it for IP reasons has a different exposure than they think, and we cannot fine-tune it. The only override is putting Cloudflare in front of the domain.

**Squarespace multilingual.** No native support at any tier. For a Quebec client under Bill 96 this is a genuine blocker: either Weglot **with language subdomains** (without subdomains it's client-side switching and the French does not exist as a URL), or a hand-built parallel `/fr/` page tree with hand-written hreflang. **If the client can still choose a platform and needs French, recommend Wix** — native multilingual, automatic hreflang, per-language SEO fields.

**Shopify Starter/Lite.** Not a theme-editable storefront. Out of scope.

**Weebly.** Sites were unpublished 27 September 2026; the platform ends 2 January 2027 across 67 countries. Do not sell a retainer.

---

## 3. The silent killers

These make a site invisible while looking perfectly healthy in a browser and in Google.

### Cloudflare

**15 September 2026** — the legacy "Block AI Bots" toggle and Managed Robots.txt were replaced by three independent policy categories: **Search**, **Training**, **Agent**, driven by Bot Preference Sync. Reported defaults from that date:

| Zone | Search | Training | Agent |
|---|---|---|---|
| New ad-free | Allow | Allow | Allow |
| **New ad-supported** | Allow | **Disallow training** | **Block on pages with ads** |
| **Existing zones that had "Block AI bots" on** | migrated to the ad-supported combination | | |
| Other existing zones | unchanged, owner asked to review | | |

*(The policy table comes from consistent secondary reporting; Cloudflare's own announcement post and the AI Crawl Control policy docs both 404'd during research. Treat the shape as reliable and the detail as unconfirmed — and measure the live behaviour rather than looking it up.)*

**Confirmed from Cloudflare's own docs:** a blocked crawler gets a **configurable `403` or `402 Payment Required`**, implemented as a WAF custom rule on the zone. A `402` is an unambiguous, deliberate AI-crawler block wired to Pay Per Crawl — report that as a commercial stance, not a misconfiguration.

Three Cloudflare systems can each block AI bots independently, and all three need checking: **AI Crawl Control**, **Managed robots.txt** (which emits `Content-signal: search=yes, ai-train=no` by default), and **Bot Fight Mode**.

### WP Engine — measured, and not fixable by us

Search Engine Land's April 2026 testing (60 rapid requests per user agent, differing only by UA on identical URLs):

| User agent | Result |
|---|---|
| **ClaudeBot** | **60/60 → HTTP 429** |
| GPTBot | 8/10 → 429 |
| Amazonbot | 10/10 → 429 |
| Bytespider | 10/10 → 520 |
| Browser UA | **200** |

WP Engine's own statement: platform-wide rate limiting on certain bots **"can't be selectively disabled per bot."** Not fixable via the customer-facing rules engine.

**This can make an engagement undeliverable regardless of our work. Qualify for it before signing, and price migration as a separate line.**

### Kinsta — opt-in, but two settings bite

Bot Protection has four levels plus a separate **"Block AI Crawlers"** toggle. Kinsta allows verified search engines through even under challenges — **but AI crawlers are not search engines**, so at level 3 ("Challenge bots") or level 4, GPTBot and ClaudeBot get challenged while Googlebot sails through and Search Console looks perfect. **Levels 3 and 4 are incompatible with this work.**

### Others
- **Hostinger** — a single-case, vendor-published allegation of IP-based challenging of AI crawlers, not confirmed by Hostinger. If real, **UA spoofing from our infrastructure would not detect it** — only server logs would. Treat as a hypothesis to test.
- **SiteGround, Cloudways, Flywheel, GoDaddy Managed WP** — no data either way. Test empirically.
- **Wordfence and Sucuri** — rate-limiting rules, custom blocklist patterns, and "blanket bot rules added by a previous developer" are all documented failure routes. Diagnose via Wordfence → Tools → Live Traffic.
- **Vercel** — confirmed from its own docs: both bot rulesets are **inactive by default**, AI bots default to Allow.
- **AWS WAF** — the `CategoryAI` rule ships in **Count mode** (monitor only). Default block response is `403`.

### A robots.txt trap worth its own line

Per **RFC 9309**, a robots.txt that returns **5xx or is challenged** means **complete disallow** for a compliant crawler — even if the file's contents are permissive. So a WAF that challenges `GPTBot` on `/robots.txt` silently blocks the entire site by spec. **Always fetch robots.txt with the bot user agent, not just a browser one.**

---

## 4. Automation: what we can script, what we type by hand

This decides engagement cost more than anything else.

| Platform | Scriptable | Hand work |
|---|---|---|
| **Shopify** | Metafields (`global.title_tag`, `global.description_tag`, `seo.hidden`), products, collections, pages, **and theme files via `themeFilesUpsert`** — including `robots.txt.liquid` and `agents.md.liquid` | Protected-scope approval for `write_themes`; theme QA |
| **HubSpot** | **`headHtml` and `footerHtml` per page via API** — raw JSON-LD, batch endpoint available. Best per-page head API here after Shopify | llms.txt (no native support), robots.txt (UI only), template structure |
| **Self-hosted WordPress** | REST + Application Passwords: pages, posts, media, taxonomies, meta where `show_in_rest` is registered | Theme/plugin files, physical root files, plugin installation, **page-builder layouts — writing to REST `content` can corrupt Divi/Elementor** |
| **Webflow** | CMS items, **bulk meta titles/descriptions/OG**, page settings, locale content, publishing | **JSON-LD must be hand-placed** — the Custom Code API accepts JavaScript only and strips `<script>` tags, so API-injected schema would be client-side and invisible to AI crawlers. llms.txt upload is dashboard-only. robots.txt API is Enterprise |
| **Wix** | Item SEO Tags API — title, description, meta tags, keywords, **bulk writes** | **Body copy cannot be rewritten via any API** — Wix's own notes call this "a full architectural gap with no known solution." Structured data, robots.txt, llms.txt, alt text (no bulk tool), and **all non-primary-language metadata** are UI work |
| **Duda** | Site-wide HTML injection via Integration Hub — **end-of-body only** | Head injection is UI-only, so a large per-page schema rollout is manual |
| **Framer** | Nothing server-side — the Plugin API runs inside the editor | **Everything.** Worst platform for a large rollout |
| **Squarespace** | **Nothing relevant.** The API covers Forms, Orders, Inventory, Transactions, Contacts, Discounts, Products, Webhooks. No Pages, Content, SEO or settings API | **Every AEO change, on every page.** Per-blog-post schema has to be a body code block in each post, one at a time |

**Two practical consequences.**

First, **on Wix and Squarespace we hold the credentials and do the work ourselves.** Handing over copy-paste instructions has a high error rate and we cannot verify the result. Note Squarespace Basic allows only 2 contributors; Core and above are unlimited.

Second, **per-page schema cost scales with page count on the closed platforms and is near-free on the open ones.** A 200-page schema rollout is an afternoon on Shopify or HubSpot and a week on Squarespace. Price it from the platform, not from the page count.

---

## 5. Platform-specific wins worth knowing

**Shopify has gone furthest.** It serves `/agents.md` natively, with `/llms.txt` and `/llms-full.txt` mirroring it, and since **28 May 2026** all three are template-overridable. The default `agents.md` carries a UCP discovery endpoint, a per-store MCP endpoint, a documented agent purchase flow, and a "checkout requires human approval" rule.

Two Shopify notes nobody else seems to have made:
- The default `agents.md` contains **Shopify promotional content**, pushing agents toward `shop.app/SKILL.md` and linking to shopify.com. A client may not want their agent-discovery file marketing the platform. The fix is overriding `agents.md.liquid`.
- The agent templates run in a **restricted Liquid context** — only `request` and `agents` objects, no `products` or `collections`. So you **cannot** build a catalogue-wide `llms-full.txt` from the template.

**Shopify's real constraint is authentication, not robots.txt.** From **30 May 2026**, bots hitting Shopify storefronts are expected to sign requests with **Web Bot Auth**, and unsigned bots "are subject to the strictest limits." For our own auditing there is a first-party answer: **crawler access keys**, generated in admin. **Put requesting these in onboarding** — it's the difference between a clean audit and a rate-limited one.

**Framer has the best defaults of any platform.** Officially pre-rendered server-side, all AI and search crawlers allowed by default and documented as such, and **native markdown** via `Accept: text/markdown` or appending `?md` to any URL. No other platform does the markdown trick.

One open question that would change the assessment: **nobody documents whether Framer custom-code JSON-LD lands in the pre-rendered HTML or is inserted client-side.** If client-side, Framer's schema story collapses. Test it first on any Framer engagement — fetch with JS disabled and grep for `ld+json`.

**Webflow is the only platform supporting the `Content-Signal` header**, letting a site separate AI training from search indexing from AI-answer input. Genuinely useful.

**Duda is the only platform that auto-generates llms.txt**, rolled out June 2025, including live URLs with meta descriptions and excluding drafts and noindex pages. It also fixed its store-page rendering to server-side back in 2019 and documented it — better evidence than anything available for GoDaddy or Weebly.

**HubSpot AEO costs $50/month standalone** with no plan requirement — against Webflow's AEO, which is Enterprise-only (and listed under a $2,500/mo Team plan). Both are measurement and recommendation only; neither emits schema or llms.txt for you.

**GBP Q&A seeding is dead.** Google deprecated the API in autumn 2025 and Q&A disappeared from profiles around 3 November 2025, replaced by Gemini-generated "Ask Maps" answers drawn from GBP data, reviews, the website and connected social profiles. Many 2026 checklists still recommend seeding it. Don't.

---

## 6. Myths this research kills

| Claim | Verdict |
|---|---|
| "AI crawlers can't read Wix/Squarespace/builder sites" | **False.** Both server-render; verified by no-JS fetch |
| "Shopify blocks AI bots by default" | **False.** A live fetch of a Shopify store's robots.txt shows no GPTBot/ClaudeBot/PerplexityBot entries. The article making this claim quotes language from `agents.md`, not robots.txt |
| "Squarespace blocks AI crawlers by default" | **False.** The checkbox is off by default, and Squarespace's own guidance says leave it off |
| "Framer is bad for AI crawlers" | **False and backwards.** Best defaults on the list |
| "Elementor/Divi hide content from crawlers" | **No evidence found.** All the mainstream WordPress page builders compile to server-rendered HTML. The real risks are headless setups and lazy-loaded sections |
| "Notion is fine as a website" | **False, and this is the best-evidenced finding in the set.** A dated no-JS test returned a 95-character shell reading "JavaScript must be enabled in order to use Notion" — no body text, no headings, no JSON-LD |
| "Blocking GPTBot removes you from ChatGPT" | **False.** Search uses `OAI-SearchBot` |

---

## 7. Building the detection — engineering notes

### Platform fingerprinting

**Don't take a library dependency. Vendor the data and write the matcher.** `python-Wappalyzer` is archived (last release 2020). `builtwith` is dead. `reppy` is seven years stale.

The live fingerprint database is **`enthec/webappanalyzer`** — active, with the maintainer committed to keeping it public. Pin a commit and pull `src/technologies/*.json`.

**⚠️ Licensing, and this is load-bearing: `enthec/webappanalyzer` is GPL-3.0, and so is `wappalyzer-next`.** Bundling GPL-3.0 fingerprint data in a closed commercial product is a genuine legal question. Options: get legal review, fetch the JSON at runtime rather than redistributing it, or **write our own ruleset for the ~17 platforms we care about — about a day's work, and it sidesteps the issue.** The database has real gaps for our list anyway (no `assets.squarespace.com`, weak WordPress.com, no App-Router Next.js, and a `Framer Motion` vs `Framer Sites` false-positive trap).

**Most `js` globals appear as plain text in the initial HTML**, so a no-browser matcher recovers most of the detection power: `wixBiSession`, `__NEXT_DATA__`, `Shopify.shop`, `Static.SQUARESPACE_CONTEXT`, `_W.configDomain`, `Drupal.settings`.

**Signal reliability tiers — never report a platform on a Tier 3 signal alone:**

- **Tier 1, infrastructure-emitted, owner cannot remove:** `X-Wix-*` headers · `Server: Squarespace` · `x-hs-hub-id` · `x-shopify-stage` · `x-vercel-id` · `X-WPE-Loopback-Upstream-Addr` · vendor CDN hostnames (`static.parastorage.com`, `cdn.shopify.com`, `framerusercontent.com`, `dd-cdn.multiscreensite.com`, `*.website-files.com`, `/_next/static/`)
- **Tier 2, structural, removable only by breaking the site:** `/wp-content/` · `/wp-includes/` · `Link: rel="https://api.w.org/"` · `html[data-wf-site]` · `meta[name=shopify-checkout-api-token]` · `/components/com_` · `Expires: 19 Nov 1978` (Drupal's sentinel date)
- **Tier 3, trivially stripped or forged:** `meta generator` · `X-Powered-By` · `X-Pingback` · generic `Server`

**Two discriminations that matter for us:**
- **WordPress.com vs self-hosted** — WordPress.com serves assets from `s0–s2.wp.com`, `*.files.wordpress.com`. But Jetpack injects the same hostnames on self-hosted sites, so require the *absence* of same-origin `/wp-content/plugins/` paths before concluding WordPress.com.
- **HubSpot CMS vs HubSpot tracking on another site** — `x-hs-hub-id` or `x-powered-by: HubSpot` means the site is on HubSpot. `_hsq` alone means a WordPress site with HubSpot tracking. Reporting the second as "HubSpot" would send completely wrong instructions.

### Crawler reachability

**User agent strings drift — fetch them at build time, don't hardcode.** As of October 2026 OpenAI is on `OAI-SearchBot/1.4` and `GPTBot/1.4`; widely-circulated articles still show 1.0 and 1.2.

Three things worth knowing:
- **`OAI-SearchBot` has a separate robots.txt-fetch variant** with an extra `robots.txt;` token in the string.
- **Anthropic does not publish full UA strings** — only the robots tokens `ClaudeBot`, `Claude-User`, `Claude-SearchBot`. Since robots.txt matching is on the product token per RFC 9309, send a token-bearing string and note the uncertainty in our output.
- **`Google-Extended` and `Applebot-Extended` have no user agent at all.** They are robots.txt-only control tokens. **They cannot be UA-tested** — only parsed out of robots.txt.

**Machine-readable bot list: `ai-robots-txt/ai.robots.txt`** — **MIT licensed**, active, 180 bots with per-bot robots.txt-compliance notes. Safe to bundle, unlike the fingerprint data. Normalise to lowercase and dedupe; it has case-variant duplicates.

**Always fetch with a control UA too.** Differential comparison — status, content length, normalised body hash across UAs — is far more informative than any single response.

**Interpreting the result:**

| Observation | Meaning |
|---|---|
| `200`, body length ≈ browser UA | Allowed |
| `200`, body much shorter than browser UA | Soft block or cloaking |
| **`200` with `cf-mitigated: challenge`** | **Challenged** — the worst case, because naive tooling records "200 OK" while the crawler got a puzzle |
| `403` + Cloudflare `Error 1010` in body | **UA-based ban** — highly diagnostic |
| `403` + `Error 1020` | WAF rule |
| **`402`** | Deliberate AI-crawler block wired to Pay Per Crawl |
| `429` | Rate limited — wait `Retry-After` and retry once |
| `404` for bot UA, `200` for browser | UA-conditional block disguised as a 404 |
| `403` for **both** UAs | Blanket block — our IP or ASN. **Not an AI-crawler finding; don't report it as one** |

**Any `200` with fewer than ~80 words must go through the challenge check before being called "allowed."**

**Honest limit to disclose in our own output:** **Web Bot Auth** (RFC 9421 HTTP message signatures) is now supported by Cloudflare and Vercel. Verification increasingly uses IP range plus reverse DNS plus cryptographic signature, **not the UA string**. A site can allow the real `OAI-SearchBot` while blocking our spoofed one. So a UA test probes only the UA-matching layer. Report it as indicative, cross-check against robots.txt, and say so.

**Ethics and rate limits.** Require proof of site ownership before running UA tests — that turns the whole question into authorised testing. Send a composite UA that carries the target token *and* identifies us with a contact URL. Cap the whole audit at ~15 requests, serialised, ≤1/second per origin. Honour `429` and `Retry-After`. **Never attempt to solve, bypass or retry past a challenge** — record "challenged" as the result and stop. That is the line between auditing and circumvention.

### Detecting client-side rendering without a browser

Useful research finding: breakage is **bimodal** — features break completely or not at all, with "short transitions between 0% and 100%." **That justifies a binary classifier rather than a continuous score.**

Signals to compute per page: html bytes, extracted text bytes, text-to-HTML ratio, word count, h1 count, p count, presence of `ld+json`, an empty framework mount div (`root`, `app`, `__next`, `__nuxt`, `___gatsby`), and a `<noscript>` "enable JavaScript" warning.

Draft rules, to calibrate on our own labelled set:

| Verdict | Condition |
|---|---|
| **Content missing** | empty mount div **and** word count < 150 |
| **Content missing** | word count < 100 **and** html bytes > 20,000 |
| **Likely missing** | text ratio < 0.05 **and** html bytes > 5,000 |
| **Strong confirmation** | a `<noscript>` "enable JavaScript" message |
| **Fine** | word count > 400 **and** at least one h1 |

**Two refinements that matter more than the ratios.**

**Gate the check on platform detection.** Wix, Squarespace, Shopify, Webflow and Framer all server-render; a "client-rendered" verdict on those is almost certainly our heuristic misfiring. Google Sites and Notion are true positives. This is the main payoff from doing detection and readability in one tool.

**Check for the actual business facts.** Rather than relying on generic ratios, test whether the things an AI answer would need to cite are present in the no-JS HTML: business name, phone (digits normalised), street address, city, prices, service names, the h1 text. **If the business name and phone are absent, that is the headline finding regardless of what any ratio says.**

**Score JSON-LD separately.** A client-rendered page that nonetheless server-renders `Organization` or `LocalBusiness` JSON-LD is partially readable — an AI crawler gets structured facts even with no prose. "Prose missing but structured data present" is meaningfully better than nothing.

**And say what we actually measured.** The finding is "this content is not in the server HTML," not "no AI system can read this." Googlebot renders, so Google's AI surfaces may see content that ChatGPT and Claude cannot. Without a browser we cannot distinguish "arrives via XHR 200ms later" from "never arrives" — but for a crawler that doesn't execute JavaScript those are the same thing, which is the point.

### robots.txt parsing

**Use `protego`** — BSD-3-Clause, actively released (0.7.0, September 2026), implements RFC 9309 properly. **Avoid `urllib.robotparser`**: no `$` anchor support, no longest-match precedence (it returns the first matching rule in file order, which is wrong), and it doesn't merge multiple matching groups. For a tool whose value is accurate robots verdicts, it will produce wrong answers.

**Pass the product token, not the full UA string.**

**The rule most implementations get wrong, and that we must flag in audits:** if a specific user-agent group exists, the `*` group is **ignored entirely**, not merged. So `User-agent: *` / `Disallow: /private` followed by `User-agent: GPTBot` / `Disallow: /` means GPTBot's only rule is `Disallow: /` — and conversely, **adding any GPTBot group silently voids all `*` rules for GPTBot.** This is the most common real-world robots.txt error.

Others to detect: `Disallow:` with an empty value means allow everything · robots.txt served as `text/html` (common on SPA hosts — a crawler parses HTML as directives) · a blank line splitting a group · robots.txt per-origin, so `www.` and apex are different files · `Crawl-delay` above ~10 on an AI bot is a soft throttle worth flagging · `Disallow: /_next/` or `/assets/` breaks rendering for crawlers that do render.

**`Content-Signal`** (launched 24 September 2025, auto-injected by Cloudflare on 3.8M+ domains) is **not an access control** — a crawler seeing `ai-train=no` with `Allow: /` is still allowed to fetch. Don't report it as a block. Protego ignores it, so parse it separately, scoped to the right user-agent group.

**For our purposes, `search=yes, ai-train=no` is the desirable configuration** — discoverable in AI search, excluded from training. Score it positively. **The one to flag is `ai-input=no`**, which asks AI answer engines not to use the content for grounding. That is the signal that directly harms AI-search visibility.

---

## 8. What this means for PLAN.md — decisions needed

Nothing here has been applied to `PLAN.md`.

### 8.1 The gate check becomes a real capability check

`PLAN.md` §8 asks three questions, one of which is "is the site editable?" That was a judgement call. It is now measurable, in plain Python, with no AI:

1. **Platform and plan detection** from the HTML and headers.
2. **Crawler reachability** — fetch with each retrieval bot's UA plus a control UA, compare, and classify: allowed / challenged / UA-blocked / blanket-blocked / rate-limited.
3. **robots.txt** — fetch *with the bot UA*, parse with `protego`, extract `Content-Signal`, flag the `*`-group shadowing bug.
4. **Render check** — is the business name, phone, address and h1 in the no-JS HTML?
5. **Capability score** — what share of our fix list this platform and plan can carry.

All five feed `fixability_signal` in `opportunity_score` with something real instead of a guess.

### 8.2 Three new profile fields

`platform` (free text, like `industry`), `platform_plan` where detectable, and `platform_capabilities` as jsonb holding the matrix row. The recommendations agent reads these. Keeping them on the profile rather than in a new table fits the existing versioning.

### 8.3 The recommendations agent needs per-platform instructions

This is the biggest change. A generic "add this JSON-LD" is useless to a Squarespace client and wrong for a Webflow one. The agent needs to emit the **route** as well as the content: which screen, which plan tier, which file, whether we can script it or must type it.

That means a per-platform instruction template set, which is reference data, not prompt text — so it can live beside the prompts and be tested without AI calls.

### 8.4 Some businesses are un-serveable, and we already built for that

A Google Sites or Notion-published site cannot carry this work. Squarespace Basic cannot until the client upgrades. WP Engine may cap the ceiling regardless of what we do.

**This is exactly what `declined` with a reason is for** (PLAN v3.2, §5). The gate check should be able to recommend declining, with the reason recorded. It should also be able to return "serveable, conditional on an upgrade" and name the cost — `$29/mo` for Squarespace Core, `~$48/yr` for WordPress.com Personal.

### 8.5 Two cost-model consequences

**Per-page schema cost scales with the platform, not the page count.** Estimating effort needs the platform first.

**Platform upgrade is sometimes a precondition, not an upsell.** The cost card should be able to show it.

### 8.6 Licensing decision

If we vendor the Wappalyzer fingerprint data we inherit **GPL-3.0**. For the ~17 platforms we care about, writing our own ruleset is roughly a day of work and avoids the question entirely. The bot list (`ai.robots.txt`) is MIT and safe to bundle. **Worth deciding before Claude Code writes the detector.**

---

## 9. Open questions, ranked by how much they'd change delivery

1. **Does Framer custom-code JSON-LD reach the pre-rendered HTML, or is it inserted client-side?** Undocumented. If client-side, Framer's schema story collapses. Test per engagement.
2. **Wix llms.txt eligibility.** Three official Wix sources conflict — "upgrade and connect a domain" vs "premium eCommerce only" vs a Wix staffer saying US-English-eCommerce-only with everything else returning 404. **Test the live URL; never promise it.**
3. **Does Webflow's Cloudflare edge block AI crawlers?** One anecdotal source claims Bot Fight Mode is on by default and blocks AI bots network-wide. Single author, no reproducible test, contradicted in spirit by Webflow's own docs. **Verify per site.**
4. **WordPress.com's own docs contradict each other** on whether Personal and Premium permit raw `<script>` tags. Mostly moot if we deliver schema through a plugin's schema engine, which is server-side — **so write every WordPress.com deliverable that way.**
5. **Framer's "optimized pages"** — markdown is only generated for them, and the term is undefined. Possibly tied to plan page counts (30 Basic, 150 Pro). Determines whether deep pages get markdown at all.
6. **Cloudflare's multi-purpose crawler handling.** One source says Googlebot is exempt as "Accountable"; another says the strictest applicable policy wins, which would block it. Unresolved from primary sources.
7. **Server-rendering status for GoDaddy Builder and Square Online.** No tests, studies or statements found.
8. **Duda hreflang and multilingual URL structure.** Unverified.
9. **HubSpot's multilingual URL mechanism, language caps and tier gating.** Undocumented.
10. **A working llms.txt on HubSpot.** No documented or demonstrated method. Don't promise it.

---

## 10. Sources

**Official platform documentation**
- Shopify: robots.txt.liquid · agents.md/llms.txt templates (changelog 28 May 2026) · storefront SEO metafields · Markets SEO and languages · `themeFilesUpsert` · Web Bot Auth changelog (7 May 2026) · crawler access keys (29 Aug 2025) — shopify.dev, help.shopify.com, changelog.shopify.com
- Wix: SSR statement in SEO best practices · custom code and JSON-LD endorsement · robots.txt editor · llms.txt · NLWeb · preset markups · Item SEO Tags API · frontend security (Dec 2025) — dev.wix.com, support.wix.com
- Squarespace: code injection · Basic/Core/Plus/Advanced plan change · AI crawler exclusion (off by default) · "Optimize your site for AI-powered search engines" · llms.txt creation · Weglot multilingual · API keys — support.squarespace.com
- Webflow: custom code (50k chars) · robots.txt rules incl. `Content-Signal` · llms.txt upload · `.well-known` files · hosting overview · Data API · Custom Code API · AEO for Enterprise (Apr/May 2026) — help.webflow.com, developers.webflow.com, webflow.com
- Framer: "Make your site readable by AI agents" · custom code · static files · llms.txt · robots.txt · localization — framer.com/help, framer.com/developers
- HubSpot: code snippets · robots.txt customisation · multi-language content · website pages API (`headHtml`) · serverless functions · HubSpot AEO (14 Apr 2026) — knowledge.hubspot.com, developers.hubspot.com
- WordPress: `do_robots()` · `robots_txt` filter · REST API reference and Application Passwords · `WordPress/mcp-adapter` (successor to the archived `Automattic/wordpress-mcp`) · wordpress.com pricing and plan features
- Duda: custom code locations · blocking AI crawlers · auto llms.txt (Jun 2025) · store SEO rendering (2019) · AI Visibility (Aug 2026) — support.duda.co, duda.co, developer.duda.co
- Cloudflare: Content Signals Policy (24 Sep 2025) · AI Crawl Control manage-crawlers (403/402) · challenge response detection (`cf-mitigated`) · managed robots.txt
- Vercel bot protection (defaults inactive) · AWS WAF Bot Control (`CategoryAI` in Count mode) · Kinsta Bot Protection · Apple Applebot · OpenAI bots · Perplexity bots · Anthropic ClaudeBot · Google common crawlers

**Studies and trade press**
- Search Engine Land, "Managed WordPress blocking AI bots" (Jun 2026) — the WP Engine measurements
- Vercel, "The rise of the AI crawler" (17 Dec 2024) — crawler JS execution. **~22 months old; no primary 2026 replication found**
- Fouquet, Laperdrix & Rouvoy, "Breaking Bad: Quantifying the Addiction of Web Elements to JavaScript", ACM TOIT 2023
- HTTP Archive Web Almanac 2025, Generative AI chapter — 4.5% of sites mention GPTBot; 2.1% have a valid llms.txt
- Steady Demand / Ben Fisher — the Foursquare debunk (2,880 prompts, 0.00%)
- RFC 9309 (robots.txt) · RFC 9421 (HTTP message signatures)
- superblog.ai — the dated Notion no-JS test (10 Aug 2026)

**Libraries**
- `enthec/webappanalyzer` — GPL-3.0, active, fingerprint JSON
- `protego` — BSD-3-Clause, 0.7.0 (Sep 2026), RFC 9309 compliant
- `ai-robots-txt/ai.robots.txt` — MIT, 180 bots
- Dead or dying: `python-Wappalyzer` (archived 2024), `builtwith` (2020), `reppy` (2019)
