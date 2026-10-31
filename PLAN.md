# AEOStudioAgents — Plan

**Version 4.9 · 29 October 2026.** Changes since 4.5 are in the changelog at the end.

Read this file before doing anything in this repository. It is the source of truth. When a decision is not covered here, ask rather than assume.

Two research documents sit behind this plan and should be read before building the thing they cover:
- `research/AEO_PLAYBOOK.md` — what actually improves AI visibility, with evidence tiers
- `research/PLATFORM_DELIVERY.md` — what can be delivered on each website platform

---

## 1. What this is

AEO Studio helps businesses get named — and described correctly — by AI assistants: ChatGPT, Perplexity, Gemini, Google AI Overviews, Claude.

This repository is the tooling that does the repeatable work: checking whether a business can be helped at all, measuring what AI says about them, producing the fixes, and proving the change over time.

**It must work for any business with a website.** Nothing hardcoded to one industry, city or language. Canada first, English and French.

**People stay in charge.** The tools draft, measure and record. They never send, publish or deploy. Nothing that costs money runs without a typed `yes`.

### What changed in v4, and why

v3.2 described four AI agents. Most of that work does not need judgement, and wrapping it in AI added cost, latency and non-determinism for nothing. **Platform detection is pattern matching. Crawler testing is HTTP status codes. Scoring is arithmetic. Building a robots.txt is a template.**

v4 replaces four agents with **three scripts**, and uses AI only where the task genuinely needs judgement:
- extracting which businesses were named in an AI answer
- checking a claim against the client's confirmed facts
- writing prose a person will read

Everything else is plain Python.

---

## 2. The journey

This is the commercial flow. Everything in the repo serves it.

```
1. DIAGNOSE          free, seconds, code only
                     → internal report: can we help them, and how
                     → verdict: serve / conditional / decline
   ↓
   decline → document it → ASK BEFORE pitching a migration
   conditional → tell them the one thing that must change first
   ↓
2. DEPOSIT           status: prospect → client
   ↓
3. TRUTH DOCUMENT    questionnaire pre-filled from the diagnosis; the client
                     confirms, corrects, adds → new profile version
                     without this we cannot judge accuracy
   ↓
3b. QUESTION SET     AI drafts candidate buyer questions from the truth
                     document + diagnosis; a person picks 32 and the
                     targets; frozen BEFORE any audit runs
   ↓
4. AUDIT             the paid work: ask the engines, record everything
   ↓
5. AUDIT REPORT      where they stand. Every question, every answer.
                     NO fix plan in it.
   ↓
6. RECOMMEND         the fix list, ordered by what the audit showed
   ↓
7. THEY DEPLOY       we never touch a live site
   ↓
8. WAIT 4–6 WEEKS    engines need to re-read the site
   ↓
9. RE-AUDIT          same frozen questions. Before vs after.
   ↓
10. QUARTERLY        same questions again, forever
```

**The deposit moved.** In v3.2 the audit was free, given to prospects to win them. It is now paid work, because in manual mode it is hours of a person's time. The free hook is the diagnosis, which costs us nothing.

**Most diagnosis fixes also help ordinary SEO.** Say so in the pitch — it widens the sale and it is true.

### Prototype phases A / B / C (v4.9)

The prototype has to run, for any business, in three phases. Anything specific to one client or one vendor is deferred (§13).

| Phase | When | Commands |
|---|---|---|
| **A. Before we land them** | Prospect: free or nearly free | `aeo add` · `aeo diagnose` · spot check (brand questions asked once on each engine, §10) · `aeo dossier` · `aeo client-report` (starting point) · `aeo status` |
| **B. After we land them** | Client: the paid work | `aeo truth draft` / `import` · `aeo questions draft` · `aeo audit` / `audit import` / `audit report` · `aeo recommend` · `aeo brief` · `aeo log` · `aeo event` · `aeo evidence add` / `list` · `aeo timeline` |
| **C. Showcasing results** | After the client deploys and the engines re-read | `aeo compare` · `aeo client-report` (work done, results, quarterly check) |

### Engagement types (v4.7)

Not every client follows the whole journey. Each business records one engagement type and the access we have; the steps that apply follow from them.

| Type | What we're hired for | Checkpoints |
|---|---|---|
| **A. AEO only** | Client keeps their site; we optimise it | `before` → `after_aeo` → `check` … |
| **B. Web dev + AEO** | We build a new site, then optimise (O'land) | `before` (old site) → `after_site` → `after_aeo` → `check` … |
| **C. Web dev only** | New site, no AEO yet — a later upsell | `before` → `after_site` |
| **D. Migration pitch** | Diagnosis says the platform can't carry the work; we propose a move | `before`; becomes B if they agree |
| **E. Prospect only** | Diagnosis before payment; may never sign | `before` |
| **F. Declined / rejected** | We or they said no | whatever exists; evidence of why is kept |

**Access level:** `none` · `cms` (builder login) · `hosting` (DNS/CDN/host dashboard) · `full` (we built or control it). With less access, checks we can't do ourselves become requests to the client, and their screenshot is stored as evidence.

---

## 3. The three scripts

### 3.1 `aeo diagnose <url>`

**Plain Python. No AI except one step. No cost. Runs on any URL without the owner's cooperation.**

What it does:

1. **Detect the platform** from response headers and page source (section 4).
2. **Test crawler reachability** — fetch as each retrieval bot plus a browser control, compare status, length and body hash.
3. **Fetch robots.txt *as a bot***, parse it to RFC 9309, extract `Content-Signal`.
4. **Check the render** — is the business name, phone, address and an `<h1>` in the no-JavaScript HTML?
5. **Read existing structured data** — store the raw blocks.
6. **Apply the decision rules** (section 5) → verdict.
7. **Write findings** with stable codes (section 3.4).
8. **Write the report** — a markdown file for us, plus a row in `diagnoses`.

**The one AI step: explaining the verdict.** The rules decide; a model writes the paragraph. This split is deliberate — if the model decides, the answer changes between runs. The verdict is a lookup with a cited source attached; the model turns it into something sendable.

**On a `decline` verdict the script stops.** It writes the report and the journal entry, sets the business to `declined` with a reason, and prints what a migration pitch would cover. It does **not** generate the pitch. A person types `yes` first.

### 3.2 `aeo audit --business <id>`

Two modes. **Manual is the default.**

- **`manual`** — prints the frozen question set with the location to use. A person asks each engine by hand and pastes the answer back, naming the engine. No engine API cost.
- **`batch`** — calls engine APIs directly. Requires a cost card and a typed `yes`. Built only after manual has run cleanly ten times.

**Batch is not a better manual.** API answers overlap the real consumer interface by only 15–32% on which brands get named, and for Google surfaces source attribution is impossible through the API. Batch is a separate, cheaper, clearly-labelled series. **It is never merged into the same number as manual.** (`AEO_PLAYBOOK.md` §6.5.)

Either mode: store the raw answer permanently, then derive. AI does two jobs — pull out which businesses were named, and mark each claim against profile v2 as correct / incorrect / unverifiable.

**What the extraction pulls out of each answer (v4.3).** Still one AI call per answer, no extra cost — it just returns more:
- every business named, **in the order it appears** (first vs fifth is a different result; across answers this becomes share of voice)
- **the reason given** for each recommendation ("because they serve all of Canada") — tells the client which selling points AI rewards
- **the words used to describe** each business ("Montreal-based", "premium") — surfaces positioning problems
- whether anyone was named at all (a commercial question nobody wins is an open field)

These all come from the audit, never the diagnosis — diagnosis reads the website and never asks an AI anything.

**Sources need their own box.** Copying an answer out of ChatGPT or Perplexity usually drops the links. The capture asks for the cited links separately and stores them in `probe_results.sources_cited`, per engine. `aeo brief` depends on this.

**Answers go in through a fill-in file, not the terminal.** `aeo audit --mode manual` writes a fill-in sheet generated from one template in the code. A header (business, question-set version, checkpoint, date, operator, logged in/out, location) is filled once. Then one block per question × engine × repeat, **every item on its own line so it can be copied or pasted with one triple-click**:

```markdown
## Q3 · ChatGPT · run 1 of 3

QUESTION (copy this):
eco-friendly hydration station suppliers for weddings

MODE (e.g. free-Instant, Thinking):


SEARCHED THE WEB (y / n / unclear):


ANSWER (paste the full answer below this line):


LINKS IT SHOWED (one per line):


---
```

A person fills it in any editor — two people can split it, and it can be stopped and resumed. `aeo audit import <file>` checks every box and lists exactly what is missing before saving anything. Typing answers into the terminal stays available but is no longer the main path.

**Cost gate.** Before the AI extraction calls run, print a cost card (number of calls, estimated cost from `core/model_prices.json`) and require a typed `yes`, per CLAUDE.md. Manual capture itself costs nothing; only the analysis does. **A person must be at the keyboard:** with no terminal (piped, scripted, scheduled) the audit refuses. There is no `--yes` flag or any other way around it.

### 3.3 `aeo recommend --business <id>`

**Scope: on-page AEO.** The files that go on the client's own site, plus the document explaining them.

Reads: profile v2, the audit, the open diagnosis findings, the live HTML.

Writes:
- `robots.txt`, `llms.txt`, validated JSON-LD
- a markdown document: priority fixes in order, where each one goes **on their specific platform**, and what cannot be done there

The platform routing comes from `research/PLATFORM_DELIVERY.md` as data, not from a prompt. A recommendation is allowed to be a refusal:

> *"This is a Squarespace site. robots.txt cannot be edited at any plan tier. Revert to client: the only options are the all-or-nothing AI crawler checkbox in Settings → Crawlers, or putting Cloudflare in front of the domain."*

**Priority order comes from the audit, not from our instincts.** The two deliverables everyone sells — JSON-LD and llms.txt — are the weakest levers in the evidence (`AEO_PLAYBOOK.md` §3 P2). They ship as cheap hygiene, last, labelled as unproven.

Off-site work — earned media, directories, review platforms, Google Business Profile — is **out of scope for this script**. It is real work and it is where most of the evidence points, but it is human work, not file generation. The recommendations document names it, informed by `aeo brief`'s outreach list (§3.5); the script does not do it.

**Before a drafted page is marked done, it is checked against the citability rules from §6 (one AI check, the rest mechanical):**

- business name appears in the first sentence
- at least one real, sourced statistic
- at least one named quote
- directly answers the target question in the first paragraph
- **distinctive, not consensus** — does not just restate what `aeo brief` shows the already-cited pages saying (the one sub-check that needs a model, since "distinctive" is a judgement call)

A draft that fails is flagged, not blocked — a human decides whether to revise or ship anyway.

### 3.4 Findings have stable codes

Every diagnosis finding carries an identifier that never changes:

```json
{
  "code": "SCHEMA_NAME_IS_LEGAL_ENTITY",
  "severity": "high",
  "what": "Structured data names the business '11297775 Canada Inc', not the brand",
  "evidence": "\"name\": \"11297775 Canada Inc\"",
  "fixable_on_platform": true,
  "where_to_fix": "Squarespace → Settings → Business Information → Business Name",
  "source": "research/PLATFORM_DELIVERY.md §4"
}
```

**Why codes matter:** at each checkpoint we re-run and diff by code. "Eleven findings at baseline, eight closed, three open" — automatic, deterministic, no judgement, no AI, no sampling statistics. Same input, same answer, every time. This is the cheapest honest before/after proof we have.

`fixable_on_platform` and `where_to_fix` are what make a finding actionable rather than academic.

### 3.5 `aeo brief --business <id>`

**Plain SQL/Python. No AI. No new data collection — it only reads what `aeo audit` already stored.**

This closes the two steps of "working a target prompt" (§6) that nothing else covers: *finding what's cited instead*, and *finding who to get mentioned by*. Both are the same query: group `probe_results.sources_cited` by question and count.

For each target question, it prints:

```
"best water refill station suppliers for events in Canada" — 15 answers, O'land named in 2
Cited: eventsupplierdirectory.ca ×7 · competitor-a.com ×6 · reddit.com/r/eventplanning ×4 · competitor-b.com ×3
Not cited: olandstations.com
```

That one list is both deliverables:
- **The content brief** — what the client's page has to beat to win that question.
- **The outreach list** — candidate domains for `recommendations.off_site_sources`. Getting listed or mentioned on an already-cited domain is step 4 of working a target prompt, and it is still human work (outreach, pitching, submitting) — the script only tells us where to aim it.

Run after an `aeo audit`, before `aeo recommend`, so the recommendation document's on-site fixes and its named off-site targets both come from the same evidence.

---

## 4. Platform detection

From response headers and raw HTML. No browser.

**Tier 1 — infrastructure-emitted, the owner cannot remove:** `X-Wix-*` · `Server: Squarespace` · `x-hs-hub-id` · `x-shopify-stage` · `x-vercel-id` · `X-WPE-Loopback-Upstream-Addr` · vendor CDN hostnames (`static.parastorage.com`, `cdn.shopify.com`, `framerusercontent.com`, `dd-cdn.multiscreensite.com`, `*.website-files.com`, `/_next/static/`)

**Tier 2 — structural, removable only by breaking the site:** `/wp-content/` · `Link: rel="https://api.w.org/"` · `html[data-wf-site]` · `meta[name=shopify-checkout-api-token]` · `/components/com_` · `Expires: 19 Nov 1978`

**Tier 3 — trivially stripped or forged:** `meta generator` · `X-Powered-By` · `X-Pingback`

**Never report a platform on a Tier 3 signal alone.** Require one Tier 1/2 hit, or two independent Tier 3 hits.

**We write our own rules** for the platforms we serve. The maintained public fingerprint database is GPL-3.0, which is a licensing problem for commercial software, and it has gaps for exactly our list. *(Decision, 2 Oct 2026.)* The AI bot list we use (`ai-robots-txt/ai.robots.txt`) is MIT and safe to bundle.

**Two discriminations that matter:** WordPress.com vs self-hosted (Jetpack injects the same CDN hostnames — require absence of same-origin `/wp-content/plugins/`), and HubSpot CMS vs a site merely running HubSpot tracking (`x-hs-hub-id` means hosted; `_hsq` alone does not).

---

## 5. Decision rules

Every rule carries its source. The script cites the source in its output.

### Decline — the work cannot be done

| Platform | Why | Source |
|---|---|---|
| **Google Sites** | Code embeds are iframed, so JSON-LD never reaches the parent document. No head access. No meta description field at all. No robots.txt control | Google embed docs; Steegle SEO guide, adapted from Google's own because much of it "is not possible with Google Sites, due to the security restrictions in place" |
| **Notion-published** | A no-JavaScript fetch returns a 95-character shell: "JavaScript must be enabled in order to use Notion." No body text, no headings, no JSON-LD | Dated first-hand test, 10 Aug 2026, superblog.ai |
| **Weebly** | Sites unpublished 27 Sep 2026; platform ends 2 Jan 2027 across 67 countries | Network Solutions discontinuation notice |
| **GoDaddy Website Builder** | No site-wide head field. Custom HTML sections are almost certainly iframed — GoDaddy's own troubleshooting warns an AdSense snippet there "violates Google's policy," which only makes sense if iframed. No robots.txt control | GoDaddy support 27252, 28025 |

### Conditional — possible after one specific thing changes

| Situation | Blocker | Fix | Source |
|---|---|---|---|
| **Squarespace Basic** | No code injection at all | Upgrade to **Core, $29/mo annual** | "Code injection is available on the Core, Plus, Advanced, and some legacy billing plans" |
| **WordPress.com Free** | No plugins, no custom code, no meta control | Upgrade to **Personal, ~$48/yr** | wordpress.com plan features |
| **Framer Free/Basic** | robots.txt and llms.txt are Pro-gated | Upgrade to Pro | Framer: robots.txt via Static Files, "Pro and Enterprise" |
| **AI crawlers blocked** | Bot UA gets 403/429, browser gets 200 | Fix at the edge first | Our own measurement |
| **Client-rendered SPA** | Business name and phone absent from no-JS HTML | SSR/SSG — a separate project, priced separately | Vercel: "none of the major AI crawlers currently render JavaScript… OpenAI, Anthropic, Meta, ByteDance, Perplexity" |
| **WP Engine hosting** | ClaudeBot got 429 on 60 of 60 requests; WP Engine states rate limiting "can't be selectively disabled per bot" | Escalate, or migrate host | Search Engine Land, April 2026 testing |

### Serve

Everything else: Shopify (not Starter/Lite), self-hosted WordPress on a cooperative host, Drupal, Joomla, Webflow paid, Wix Light+, HubSpot, Duda, Framer Pro, Squarespace Core+, and any custom site that server-renders.

### The rule that overrides the table

**Whatever the platform is, if the crawler test fails, that is the finding.** Cloudflare changed its defaults twice in fifteen months. **Measure, never look up.** The table explains a result and generates the right remediation text; it never predicts one.

### Declining is a recorded decision

`businesses.status = declined` with a reason (section 7), a `diagnoses` row kept forever, and a journal entry. **Nothing is deleted.** A migration pitch is a separate, human-approved action.

---

## 6. What we can and cannot promise

This section exists so nobody on this project ever writes a sentence we cannot defend.

### We cannot guarantee an AI will name a business. Nobody can.

- Ask the identical question twice and **roughly half the recommended brands change** (Fractl, 11,573 answers)
- Add one buyer qualifier and **62% of brands vanish** (Clovion, 69,120 conversations)
- ChatGPT cited Reddit in **~60% of responses in Aug 2025 and ~10% by mid-Sep 2025**; nothing changed on Reddit (Semrush, 230,000 prompts)
- **OpenAI has told a regulator it cannot correct false output** — it can block a term, not fix a fact

### What we can do, and it is the biggest lever that exists

When a company's page was **in the engine's retrieved set**, it appeared in **85–100%** of answers. When it was absent, model memory rescued it in **0–17 of 48** runs. Training-data volume explains only ~3.5% of what a model knows about a company. *(Fractl, 6,000+ controlled retrieval runs.)*

And this applies where it matters: ChatGPT ran a live search on **86.5% of commercial questions** and **0.9% of informational ones** (Cloro, 634 responses).

**So the job is not "make AI say our name." It is "when the engine goes looking, make sure our client's page is what it finds and reads."** That is a retrieval problem, and retrieval problems are fixable.

### Working a target prompt

1. **Pick commercial prompts.** Informational ones rarely trigger a search at all.
2. **Find what is cited instead.** The audit records every answer's sources. That list is the brief.
3. **Build the page that answers that exact question better** — name the business in the first sentence (96% of cited passages do, vs 82% of uncited), include real numbers and named quotes (statistics +33%, quotations +41%), and say something nobody else says. Restating the standard answer gets you **absorbed without being named** (61% of cited passages were distinctive vs 82% of uncited being consensus).
4. **Get mentioned on the pages already being cited.** Earned media is 84% of AI citations; paid is 0.3%. Brand mentions correlate 0.664 with visibility; backlinks 0.218.
5. **Re-measure on the frozen set.**

**Optimise narrow, measure broad.** Targeting 5–10 prompts is correct for optimisation and wrong for measurement. Reporting only the prompts you optimised guarantees a flattering number — the most common way this industry misleads its own clients. The full frozen set is reported every time, with targets called out inside it.

**This is enforced structurally, not by memory.** Every question in the frozen set carries an `intent` (commercial / informational / navigational) and an `is_target` flag (§9). `aeo audit` warns if a target is set on an informational question — those almost never trigger a search at all (0.9% vs 86.5%, Cloro) — and every report shows the full set with targets marked inside it, so there is no version of the report that shows only the 5–10 we worked on.

### The sentence we use

> *"Today AI says these specific wrong things about you, and names these competitors instead of you. We will fix what your site tells AI, get the correct version onto the sources AI actually reads, and measure the same questions again in three months. We cannot control what the models say — nobody can — but we can make sure that when they go looking, they find the truth."*

### We refuse

Fake or incentivised reviews · astroturfing Reddit or forums · undisclosed paid Wikipedia editing · buying placement in "independent" listicles · fake or keyword-stuffed Google Business Profiles · hidden text, cloaking or prompt injection aimed at crawlers · attacking competitors · mass-generated content whose only purpose is to create mentions · **promising outcomes we cannot deliver.** Full reasoning in `AEO_PLAYBOOK.md` §10.

---

## 7. Statuses

`prospect` · `client` · `delivered` · `rejected` (**they** said no) · `declined` (**we** said no)

**Every move to `declined` or `rejected` needs a reason.** `aeo status --business <id> declined|rejected --reason "..."` refuses to run without `--reason`. The trigger writes the `status_change` row; the reason is a `note` entry in `client_journal` linked to it, with `details = {"reason_for": "<status>", "reason": "..."}`.

Progress within a stage is not a status — each step is recorded in its own table.

---

## 8. Measurement rules

The scores are easy to compute and easy to overstate. These rules exist to stop us overstating them.

### The five scores — plain Python in `scoring/scoring.py`, never AI

- `visibility_score` = answers naming the business ÷ total × 100
- `gap_score` = answers naming a competitor but not the business ÷ total × 100
- `opportunity_score` = 0.5 × gap + 0.3 × fixability + 0.2 × business reality
- `accuracy_score` = correct ÷ (correct + incorrect) × 100 — unverifiable not counted
- `recognition_rate` = answers where AI knew the business ÷ total brand answers × 100

Every score stores its `formula_version`. Weights and tiers in `scoring/config.yaml`.

**Tiers, by lower edge** (scores are decimals; 19.4 is Invisible): below 20 Invisible · below 40 Barely Visible · below 60 Partially Visible · below 80 Visible · 80–100 Dominant.

### Three things every score must record

- **Brand-free rate.** An answer naming nobody is not the same as one naming a competitor. Report both denominators — all answers, and answers naming at least one business. The gap between them runs 1.4×–3.4×.
- **Mentioned vs recommended.** Listed as an also-ran and proposed as the solution are different products. `recommendation_rate` is the one with commercial meaning.
- **Retrieval activation.** Did the engine actually search? Without it we cannot tell a retrieval failure from a ranking failure.

### Two designs, and we never confuse them

**Snapshot — the prospect-stage audit.** ~12 questions, up to 5 engines, one run. ~60 answers, about an hour. Labelled as a snapshot. **One overall figure with a visible range. No per-question claims, ever.**

**Before/after — the client-stage measurement.** 32 frozen questions, 2 engines (ChatGPT for reach, Perplexity because it always retrieves and so diagnoses cleanly), 3 runs each, same day, logged out, fresh session. **192 answers per checkpoint**, 3–4 hours by hand. About 10 of the 32 target parts of the site we deliberately do not touch — that is the control.

**This detects roughly a 20-point change, and not less.** Detecting 10 points needs ~750 answers per checkpoint. **We say so up front.** A single day's per-brand rate carries a ±63 percentage point confidence interval; 7–8 same-day repeats is the published minimum for a per-question estimate (Schulte et al., arXiv:2604.07585).

**Every score ships with a range. We never report per-question numbers as findings.**

### The frozen question set

One versioned set, written **before** looking at where the client appears. Never edited — a change means a new version with an annotation. Every checkpoint reuses it. Paraphrasing a question changes the answer set more than re-asking it does, so the question string is part of the measurement.

### Two kinds of question, never mixed (v4.8)

- **Discovery questions** — the buyer has never heard of the client: "water station rental for a festival in Toronto". The brand never appears (rejected in code). These, and only these, measure visibility, recommendation rate, share of voice and gap.
- **Brand questions** — the buyer already knows the name: "What does O'land Stations do?", or with the URL: "Tell me about olandstations.com". These measure **recognition** (does AI know them?) and **accuracy** (does it get the facts right?). Putting them into visibility would inflate it — AI always "names" a business you asked about by name.

**How questions are written:**
- In the buyer's words, not the client's slogans. Vary the vocabulary buyers actually use (water station / refill station / hydration station / bottle filling station).
- One ask per question. No private information, ever.
- Three lengths, tagged: **short** (6 words or fewer, search-box style; anything under 3 words counts as short), **medium** (one sentence of 7–30 words), **long** (two or more sentences, or more than 30 words: a real situation with place, size, date, constraint). An explicit length tag on a question always wins over the count. The rule lives in one function, `core/question_length.py`. Small specialists tend to win or lose on the long, specific ones; adding one qualifier changes most of the brands named.
- Built from personas × places × needs (rent vs buy, branding, scale, sustainability, price). Places the client actually serves, including outside their home city.
- In the buyer's language: French for Quebec buyers.
- A few informational questions, untouched by our work, act as the control.
- Rough mix for a 32-question set: ~24 discovery (40% short, 40% medium, 20% long), ~8 brand (half with the URL).

### What is deterministic, and therefore our strongest proof

The **diagnosis findings diff**. Same input, same answer, no sampling. "Eight of eleven findings closed" needs no confidence interval.

Second strongest: **accuracy**. Two independent studies found AI gets ~9–10% of facts about a business wrong (pricing worst at 13.5%), and 75% of brands checking 20+ facts found at least one error. That gives us ground truth and a published base rate. Visibility scores have neither.

---

## 9. Database — 13 tables

All tables use uuid primary keys, link to `businesses`, and have `created_at`. Fixed value sets are enforced by the database. **Nothing is deleted.** Migrations are append-only and protected by SHA-256 checksums.

Unchanged from v3.2: `businesses`, `business_profiles`, `gate_checks`, `probe_runs`, `probe_results`, `visibility_scores`, `audits`, `recommendations`, `content_pieces`, `runs`, `client_journal`, `human_queue`.

### New: `diagnoses`

```
id, business_id, checkpoint, url, checked_at,
platform, platform_version, platform_plan,
robots_txt           text,    -- raw, exactly as served
existing_jsonld      jsonb,   -- raw blocks, as found
crawler_access       jsonb,   -- per bot: UA, status, verdict
render               jsonb,   -- word count, h1 texts, which details present
findings             jsonb,   -- [{code, severity, what, evidence, fixable_on_platform, where_to_fix, source}]
verdict              (serve/conditional/decline),
verdict_reason       text,
readiness_score, created_at
```

**Raw stored permanently; findings derived.** Same rule the probe already follows. If we improve a check in six months we re-run it against old raw data instead of losing history.

### No `questions` table (v4.2 revision)

v4.1 planned a `questions` database table just to hold two flags (`intent`, `is_target`) per question. Simplified: those two fields live directly in the frozen question-set YAML file instead (`question_sets/<slug>_v<N>.yaml`), next to each question's text — no table, no migration, no foreign key:

```yaml
questions:
  - text: "best water refill stations for outdoor events in Canada"
    intent: commercial
    is_target: true
  - text: "what is a water refill station"
    intent: informational
    is_target: false
```

`aeo audit` reads this file and warns before running if `is_target: true` is set on an `intent: informational` question. `aeo brief` and every audit report read the same file so the full set, with targets marked, is what always gets shown. The file is already version-locked once used (a hash of its questions is recorded at the start of each run); that same lock now covers `intent`/`is_target` too, so they can't quietly change after a baseline is measured.

### Changes to `probe_results`

Add: `retrieval_activated`, `engine_version` (model **and** reasoning mode — Instant and Thinking share only 25.6% of sources and are effectively different engines), `logged_in_state`, `question_set_version`, `named_any_business`, `recommended`. (Already done — migration 016. No `question_id` column; `question_set_version` plus the stored `query_text` is enough without a `questions` table.)

### Changes to `probe_runs`

Add `checkpoint` — one of four fixed values, the same for every client: `before` · `after_site` · `after_aeo` · `check` (see §10). The date tells repeated `check`s apart.

### Fix to `recommendations`

Migration 008 has `accuracy_probe_run_id NOT NULL`, which contradicts this plan. Make it nullable.

### Journal entry types

Existing 16, plus `diagnosed` and `decline_recommended`.

### Local setup

Postgres 16 in Docker on **port 5434** (5432 and 5433 taken). Dev database `aeo`, test database `aeo_test`. **Tests refuse to run without `TEST_DATABASE_URL`.** Supabase later; same SQL.

---

## 10. Storage

**Rewritten in v4.7.** Folders named after one client's story (`new_site_no_aeo`) don't fit an AEO-only client. The rule is now simpler.

**The database is the record.** Files are one of two kinds:

| Kind | What | Recreatable? |
|---|---|---|
| **Evidence** | Things that came **in**: host/CDN reports (e.g. Cloudflare AI Crawl Control), screenshots of AI answers, filled audit sheets, `raw_page.html`, client emails and approvals | **No — keep forever, back up** |
| **Generated** | Things the tool **made** from the database: diagnosis reports, audit reports, client reports, robots.txt / llms.txt / schema files | **Yes — delete and rebuild any time; never hand-edit** |

```
clients/<domain>/
    evidence/      <YYYY-MM-DD>_<name>.<ext>            originals, never overwritten
    generated/     <YYYY-MM-DD>_<kind>[_runN].<ext>     tool output
    work_log.md    regenerated from the journal
    timeline.md    regenerated from the journal
```

No stage folders — sorting by date shows the client's whole history. **`clients/` is gitignored; client data never goes into git.**

### Checkpoints are labels, not folders

Every diagnosis, audit run and evidence record carries one checkpoint label in the database. Four values, the same for every client: `before` (before any of our work) · `after_site` (we built the site, no AEO yet — web-dev clients only) · `after_aeo` · `check` (each 3-monthly check; the date tells them apart). Comparisons pick by label: "`before` vs `after_aeo`". Anything else is refused.

O'land's existing data moves across: `old_site` → `before`, `new_site_no_aeo` → `after_site`.

### Evidence records

`aeo evidence add` copies the file into `evidence/` and records eight things: **what** it is (type, e.g. `cdn_report`, `ai_answer_screenshot`, `client_email`), **from** whom (`us` or `client`), **captured** date and the **period it covers**, **checkpoint**, **note** (what we understood from it), the **finding code** it relates to (optional), whether the **client may see it**, and **who** added it. Client reports include only evidence marked client-visible, so internal notes never leak.

### Work log and events

`aeo log` records each piece of work done for the client (dated, who, optional finding code it closes, optional file). `aeo event` records things outside our control that can move the numbers: the client changed their own site, an engine shipped a new model, the client approved something. Both feed the client report and the timeline.

### The baseline dossier (v4.8) — internal, assembled before any work

One internal document per client, regenerated whenever new evidence arrives, so we always know what stage they are at. Sections:
1. **Business snapshot** — what they sell, to whom, where; public facts from their site; confirmed facts from the truth document; claims that need checking.
2. **Stage** — engagement type, access level, what's done, what's open.
3. **How AI sees the site — before and after** — for each diagnosis: what a bot can reach, the words it actually reads (from `raw_page.html`), the structured data it finds (verbatim), what is missing. Old site and new site side by side.
4. **Crawler activity** — AI bots allowed / blocked / erroring, 404s, most-requested paths (CDN or host report).
5. **Search and traffic** — 6 months of organic search (Search Console: clicks, impressions, top queries), visits from AI assistants (Analytics referrals: chatgpt.com, perplexity.ai, copilot, gemini), Copilot citations and the searches it ran (Bing Webmaster Tools AI Performance). No engine publishes how many people asked about a business; we never imply otherwise.
6. **What AI says today** — a **spot check**: brand questions run once on each engine, full answers kept. Labelled as a snapshot, not a measurement.
7. **Problems** — one ranked list across all of the above.
8. **Proposed question set** — discovery + brand, tagged, not yet frozen.
9. **Questions for the client** and **what happens next**.

### Evidence catalogue — what we collect for every client

| Evidence | Source | Needs | Each checkpoint? |
|---|---|---|---|
| Diagnosis + raw page + structured data | our tool | nothing | yes |
| AI crawler report | Cloudflare / host / CDN | hosting access or client screenshot | yes |
| Organic search, 6 months | Google Search Console export | client grants access | yes |
| Site traffic incl. AI referrals | Google Analytics (or the builder's analytics) | client grants access | yes |
| Copilot citations + grounding queries | Bing Webmaster Tools AI Performance | site verified (DNS, meta tag or GSC import) | yes |
| AI spot check (brand questions) | ChatGPT, Perplexity, Gemini, Copilot — logged out | nothing | at start, then in each audit |
| Full audit answers | our audit | the frozen set | yes |
| Screenshots of AI answers | us | nothing | when notable |
| Client confirmations, approvals | email | — | as they happen |

### Long run

Now: Postgres in Docker on Daivik's Mac, files in `clients/` on the Mac. With the frontend: the database moves to Supabase and `evidence/` + `generated/` move to object storage (Supabase Storage or Cloudflare R2), same layout. **Only the database and `evidence/` need backing up** — `generated/` can always be rebuilt.

**When a client wants a new website, diagnose and audit the old one first** — if budget allows and the cost is reasonable. Once the new site is live the old state is gone and no before/after is possible.

---

## 11. Technical rules

1. **Scripts, not agents.** AI only for: extracting named businesses, checking claims, writing prose.
2. **No frameworks.** Anthropic SDK directly.
3. **Every AI call is logged** in `runs` with prompt version, tokens, cost and latency — including failures, before returning.
4. **Structured output everywhere.** On validation failure: retry once with the error, then `human_queue` and stop.
5. **Store raw, compute later.** Raw AI answers, raw robots.txt, raw JSON-LD. Everything else is derived and re-runnable.
6. **Scores are code, never prompts.**
7. **Manual is the default** and stays the primary instrument permanently. Batch is a separate labelled series.
8. **Nothing costs money without a cost card and a typed `yes`.**
9. **Nothing is scheduled** until it has run cleanly by hand ten times.
10. **Migrations are append-only.**
11. **Nothing hardcoded to an industry, city or language.** O'land exists only in seed data — never in code, prompts or templates.
12. **Everything analysable with SQL.** Fixed value sets, numbers as numbers, dates on everything, nothing deleted.
13. **Never write to a client's live site.** We generate files; people deploy them.
14. **Language follows the buyer.**

---

## 12. Human checkpoints

| Always a person | Why |
|---|---|
| Approving any spend | Controls all cost |
| **Approving a migration pitch after a decline** | A decline is documented automatically; the pitch is a decision |
| Declining a business | Our decision, recorded with a reason |
| Sending any report | Their name is on it |
| Confirming facts (questionnaire) | Accuracy is only measured against client-confirmed facts |
| Deploying anything | A broken tag on a live site is a real incident |
| Publishing any content draft | Unedited AI content under a client's name loses the client |
| Correcting facts on third-party sites | Needs their accounts and judgement |
| Using a case study | Written consent first |

---

## 13. Build order

| # | Step | Status |
|---|---|---|
| 0 | Scaffold, migrations 001–013, triggers | ✅ Done |
| 1 | `core/`: db, llm, journal, runner, model prices | ✅ Done |
| 2 | `scoring/`: five formulas, tier tests, test DB | ✅ Done |
| 3 | Fixes: migration 014, lower-edge tiers, `.gitignore` | ✅ Done |
| 4 | `aeo diagnose` — detection, crawler test, robots, render, findings, report, raw page | ✅ Done |
| 5 | Migration 015: `diagnoses` table, 2 journal types | ✅ Done |
| 6 | `aeo add` (duplicate-safe) + question-set YAML (with `intent`/`is_target`) | ✅ Done |
| 7 | `aeo audit --mode manual` (terminal capture), migration 016, numbered run history | ✅ Done |
| 8 | O'land old-site baseline | ✅ Diagnosis done (business-linked). Audit answers only in the manual snapshot sheet — by decision, the first tool audit runs after the new site is live |
| 9 | Richer extraction (order, reasons, descriptors) + sources box — prompt v2 | ✅ Done (tested with fakes; first real run after the new site is live) |
| 10 | **Fill-in file** + `aeo audit import` | ✅ Done (`--partial`, resumable) |
| 11 | **Cost gate** on audit's AI calls | ✅ Done. Refuses with no person at the keyboard; no override flag (§3.2) |
| | | **Core path — build these first (decided 10 Oct 2026). ✅ Complete 16 Oct 2026** | |
| 13c | **Storage layout + engagement + evidence + work log** (§10, §2): `clients/<domain>/evidence\|generated`, fixed checkpoint labels, move O'land's files across, engagement type + access level on the business, `aeo evidence add/list`, `aeo log`, `aeo event`, `aeo timeline` | ✅ Done (migration 017). Evidence rows only from `aeo evidence add` / `aeo log --file`; raw page, filled sheet and migrated files are files only. `core/report_files.py` now unused |
| 13 | **Truth document** — `aeo truth draft` (questionnaire pre-filled from the diagnosis) + `aeo truth import` (confirmed facts + story → new profile version). Replaces the v4.4 "client facts file" | ✅ Done (migration 018: `facts` + `story` on `business_profiles`, saved versions can't be edited or deleted). Follow-up: map confirmed facts into the columns the diagnose render check reads |
| 15 | Audit report output (scores with ranges, share of voice, reasons, wrong facts) | ✅ Done. No AI; bootstrap ranges resample questions (seed in config.yaml); snapshot shows overall figures only; partial reports never save scores. Not yet seen on a real run |
| 19 | **`aeo recommend`** — fix files (robots.txt, llms.txt, JSON-LD from confirmed facts only) + the ordered fix list with platform routing from data. No AI | ✅ Done (migration 019; two audit finding codes added). Follow-ups: ask for the address in parts (street, city, province, postal code) in the truth document so JSON-LD can use structured fields; merge `recommend/platforms.yaml` with `diagnose/platform_data.py`; store the CDN on the diagnosis instead of inferring it |
| 20 | **Compare** — moved up in v4.7. Any two checkpoints: diagnosis findings closed/open/new by code, audit numbers side by side | ✅ Done. Paired bootstrap; "real change" only when the range of the difference excludes zero; refuses mismatched question sets/designs; lists work and events in between. No AI, no DB write |
| 22 | **Client report** — one designed HTML file (prints to PDF) per stage: starting point, work done, results, quarterly check. Python builds numbers and tables; AI writes only short summaries; only client-visible evidence; numbered versions; saves the data it used so it can be rebuilt identically | ✅ Done. Data from the same functions as report/compare/recommend; AI summaries rejected if they contain a number not in the data or promise an outcome; `--no-ai`; `--from-json` rebuilds byte-identical. Not yet opened in a browser or printed |
| 22b | **Client wording** — client sentence + fix title for every finding code, plain checkpoint/platform/evidence labels, "Fixed by the new website", honest "N of M closed · K new" headline, §6 sentence only when an audit backs it, no forced page breaks | ✅ Done. Not yet seen on O'land's real report |
| 22c | **Honest "closed"** — a finding whose subject no longer exists is not "closed". When B has no structured data, every schema finding from A is "gone with the old structured data — not yet replaced", not "fixed". Each finding code declares what it depends on (e.g. all `SCHEMA_*` depend on JSON-LD existing). Compare, client report and timeline use three outcomes: fixed · no longer applies (subject removed) · still open. O'land's "8 of 9 closed" is really 2 fixed, 6 removed with the old schema | ✅ Done (DEPENDS_ON in codes.py; four outcomes; `closed` kept as an alias of fixed) |
| 22d | **Two kinds of question** (§8): every question is `discovery` (brand-free — the buyer doesn't know the client) or `brand` (names the client, with or without the URL). Visibility, recommendation rate, share of voice and gap are computed on **discovery questions only**; brand questions feed recognition and accuracy only. Each question also carries `length` (short · medium · long) and `language`. Reports show results by length and language | ✅ Done. Built: visibility, recommendation rate, share of voice and gap on **discovery questions only**; **recognition** on brand questions; **accuracy two ways** — brand questions only, and overall including brand mentions; **descriptions of the client from brand questions only**; loader accepts `type`/`length`/`language`/`control`/`spot_check`; a discovery question that names the client is refused at audit start; untagged types are derived and the report says so; results by length and language for the 32×2×3 design. **Open follow-up:** compare does not show a recognition change yet. The dossier still reads the YAML with its own forgiving reader |
| 22e | **Baseline dossier** — `aeo dossier --business <b>`: the internal, before-work document (§10). Everything we know before work starts, assembled from the database and evidence; gaps listed as "not yet collected" | ✅ Done. No AI. Hand-written text lives in `clients/<domain>/dossier_notes.md`, never overwritten. Evidence types added: search_console, analytics, bing_wmt, gbp, site_move. Spot-check table is internal only |
| 22f | **Dossier design pass** — cover summary (status tiles, key numbers), linked index, status and severity chips, consistent cards, print-ready. Internal look, professional finish | ✅ Done. Cover tiles, linked index, chips with text + glyph, `aeo dossier --from-json`. Print header/footer needs Chrome 131+ |
| | | **Prototype — a working loop for any business, in this order (re-scoped 29 Oct 2026, v4.9)** | |
| 12 | **`aeo status`** — change status with a required reason (§7). The decline flow already tells people to use it | ✅ Done |
| 25 | **Diagnose additions, generic parts only:** **sitemap check** (a missing or 404 sitemap is a finding — O'land's new site had none at the apex on 15 Oct, which the diagnosis did not flag); **apex/www consistency** (both must serve the same site, or one must 301 to the other); **CDN/host detection from response headers**; **optional old-site redirect check** (fetch the old site's sitemap, confirm every old URL redirects) | ✅ Done |
| 25c | **Make Phase A output trustworthy** — a blocked check claims nothing (a "Not checked" list, compare treats it as not run); repeated findings shown once with a count and examples; a crawler that cannot connect is retried once, then reported as unconfirmed; control questions are in no score; one length rule from §8; tests refuse to run against the dev database | ✅ Done |
| 25d | **Question drafts that keep everything, an honest cost card, linked diagnoses** — a judgement-call question is kept and flagged `needs_review` (never in the spot check), a discovery question that names the business is re-tagged brand, exact duplicates are dropped; the prompt states exact counts per length and one top-up call fixes a bucket more than 3 under (at most 3 model calls in all); the cost card shows the worst case and a typical figure from logged runs; `aeo add` links earlier diagnoses saved for the same domain | ✅ Done |
| 13b | **`aeo questions draft`** — AI drafts candidate buyer questions from the truth document + diagnosis (§16); a person picks and freezes. Also suggests an 8-question spot-check subset | ✅ Done |
| 17a | **`aeo backup`**, local only — dump the database to the backup folder (§16) | ⏭ Next |
| 18 | **`aeo brief`** — citation aggregation, content brief + outreach list | |
| 14 | **`aeo guide`** — plain-language runbook of the three prototype phases (A / B / C, §2): every command, when to use it, in what order | |
| 27 | **Real-data trial** — Phase A on O'land plus 3 unrelated businesses; fix what breaks | |
| | | **Deferred — specific to one client or one vendor** | |
| 25b | **AI crawler report from Cloudflare** — with a read-only API token from the client (or our own account): per-bot requests by day, allowed vs blocked, status codes, most-requested pages, the exact 404 paths (feeds the redirect list), and which important pages each AI search bot fetched in the last 30 days. Saved per checkpoint, compared before/after. Falls back to a client screenshot or export with no access. **Until built: screenshot + `aeo evidence add`** | |
| 19b | **Draft validator** — `aeo recommend check-draft`: mechanical citability checks + the one AI "distinctive" check behind the cost gate. Split out of 19 in v4.7 | |
| — | **§14 O'land open items** — client work, not build | |
| | | **Later — scale and convenience** | |
| 16 | `aeo show` (one business's history in plain sentences) + `aeo export` (context pack for Claude) | |
| 17b | **Supabase move** (see §16) — split out of 17 in v4.9 | |
| 21 | Frontend + hosting (see §16) | |
| 23 | **Explain budget** — every command ends with "what this means / what to do next"; runs without asking under `AEO_EXPLAIN_BUDGET` per command; Python supplies every number and the next step, AI only words them; saved into the report | |
| 24 | **`aeo next`** + checklist — the next step for a business from its saved progress and engagement type; manual checks (Cloudflare, Bing WMT, redirects) ticked with `aeo check … --note` | |
| 26 | **AEO Studio Coach** — a Claude skill + one Claude Project per client (§16) | |
| — | **Batch mode** — engine APIs behind a cost card; only after manual has run cleanly ten times (§3.2, §15) | |

**Build in the order listed, not by number.** Step numbers are kept so old references still work. The core path is what the product exists for, and what justifies the price: diagnose → audit → report → the fix list → evidence of the work → compare → the client report. Question sets are still written by hand until `aeo questions draft` (13b) is built; 13b is in the Prototype group. `aeo recommend` names off-site work in general terms until `aeo brief` (18) exists.

Stop after each step for review. Do not build ahead.

### CLI

```
aeo guide                                   every command, when to use it, in order
aeo diagnose <url> [--business <id>] [--checkpoint <name>]
aeo audit --business <id> [--mode manual|batch] [--checkpoint <name>]
aeo audit import <file>
aeo evidence add --business <id> --checkpoint <label> <file> --note "..." [--from us|client] [--internal]
aeo evidence list --business <id>
aeo log --business <id> "what was done" [--finding <CODE>] [--file <path>]
aeo event --business <id> "what happened"
aeo timeline --business <id>                everything for a client, in date order, with labels
aeo compare --business <id> <label> <label>
aeo truth draft --business <id>             questionnaire for the client, pre-filled from the diagnosis
aeo truth import --business <id> <file>     confirmed facts + story → new profile version
aeo questions draft --business <id>         AI-drafted candidate questions to pick from
aeo show --business <id>                    history and next step, plain sentences
aeo export --business <id>                  one markdown context pack for Claude
aeo backup                                  dump the database to the backup folder
aeo brief --business <id>
aeo recommend --business <id>
aeo add <url>
aeo seed <folder>
aeo status --business <id> <status> [--reason "text"]
aeo mark-sent | mark-deployed | mark-published --business <id>
aeo note --business <id> "text"
aeo queue
```

---

## 14. First test businesses

| Business | industry | schema_type | customer_type | reach |
|---|---|---|---|---|
| **O'land Stations** (olandstations.com) — first client | Event water refill stations | Organization | businesses | national (US + Canada) |
| A dental practice | Dentistry | Dentist | consumers | local |
| An online store | Outdoor footwear | OnlineStore | consumers | online |
| A digital agency | Branding and design | *(specific subtype — `ProfessionalService` is deprecated)* | businesses | national |

**O'land's diagnosis, 2 Oct 2026, old Squarespace site** — the first real findings, and the shape of what `diagnose` must produce:

- Squarespace 7.1. Crawlers reachable, nothing blocked, AI checkbox off. `/llms.txt` 404s.
- Structured data names the business **"11297775 Canada Inc"**, not the brand. The `Organization` block has `legalName` but **no `name` at all**.
- `@type: LocalBusiness` with a Montreal address and **no `areaServed`** — for a business serving the US and Canada. ChatGPT says they only work with Montreal clients. The site is the most likely source.
- Two `<h1>` tags, both apparently empty. 710 words on the homepage.
- `openingHours: ", , , , , , "`. `sameAs` LinkedIn URL carries search tracking parameters. Three unlinked entity blocks.


**O'land — open items before Audit 1 (16 Oct 2026).** Site-move work, logged under `after_site`, not AEO work:
- **On hold:** add a sitemap to the Next.js site — none at `/sitemap.xml` on either host (404).
- **Host split:** `olandstations.com` and `www.olandstations.com` both return 200. Pick one; 301 the other.
- **Old-URL 404s:** 1,380 AI-crawler requests hit 404s in the week to 14 Oct (Cloudflare report, in evidence). Needs the list of failing paths, then 301s.
- Then re-run `aeo diagnose … --checkpoint after_site`, write and freeze the 32 questions, fill the truth document with O'land, run Audit 1.
---

## 15. Later (decided, not now)

`confidence`/`evidence` fields and routing · batch mode and budgets · off-site execution (earned media, directories, review platforms) · outreach, proposals, weekly reporter, case study generator · `business-profiler` · any scheduling.

---

## 16. Team, help and hosting

### The truth document (v4.5 — replaces the v4.4 "client facts file")
Claim checking — and so `accuracy_score`, our strongest measure — only runs against facts the client has confirmed. The truth document is how we get them, after the deposit.

**`aeo truth draft` writes the questionnaire, pre-filled from the diagnosis**, so the client confirms rather than writes from scratch. Each finding code maps to a direct question — plain code, no AI. For O'land: "Your site's data names the business '11297775 Canada Inc'. What name should AI use?" · "Your site only lists a Montreal address. Where do you actually serve?" · "Opening hours are blank. Do you operate year-round, including winter?" Then a fixed standard list: founding year, services, typical customers, price ranges, languages, goals, and **"what do customers ask you before they buy?"**

**One file, two parts, because they do different jobs:**
- **Confirmed facts** — short and checkable: name variants, service area, seasons, services, languages, contact details, founding year. Each records **who confirmed it and when**. Only the client fills these, never us. This is what the audit checks AI's claims against.
- **The story** — what they do, goals, ideal customers, what makes them different, competitors they see, what customers ask. Feeds the question writer and `aeo recommend`. Never used for scoring.

`aeo truth import` writes a **new** `business_profiles` version (source `client`) each time. Old versions are never edited. `apparent_competitors` is our guess and stays out.

### The question writer — `aeo questions draft`
Writing plausible buyer questions is judgement, so it is an AI job (the fourth, alongside extraction, claim checking and prose). It drafts; a person decides. Guardrails:
- **Scenarios = personas × places.** Personas from the truth document's ideal customers ("planning a summer festival", "corporate event planner", "wedding planner avoiding plastic"); places **only from the confirmed service area**, never guessed. Up to 5 unique questions per scenario.
- **French for Quebec.** One French version of each Montreal/Quebec scenario — Quebec buyers ask in French and get different answers (`AEO_PLAYBOOK.md`, Canada/Quebec section).
- **Never the brand name — enforced in Python.** Any draft containing the client's name, name variants or domain is rejected. Brand questions ("What is O'land Stations?") are added deliberately and separately.
- **Buyer's words, not the client's.** Built only from the client's own description, questions drift into the client's vocabulary ("sustainable hydration stations") and flatter the result. The prompt says so explicitly: write how a buyer with a problem asks ("how do I keep 5,000 people hydrated at a festival without plastic bottles?").
- **Output is a draft YAML**, each question tagged `intent` and `source: generated`. A person picks the final 32, marks the targets, mixes in real questions (`source: customer`, `source: bing grounding query`, `source: reddit`) — then it is frozen, **before any audit runs**.
- Cost card and typed `yes` before the call, as for every paid step.

The `source:` field is optional on every question in the set; it answers "why these questions?" when a client asks.

### Real-question sources (free)
- The truth document's "what do customers ask" answers — closest thing to real prompts.
- **Bing Webmaster Tools → AI Performance**: which pages Copilot cites, how often, and the *grounding queries* it ran. Needs the site verified — without code access, via: the client adding us as a read-only user, importing from their Google Search Console, one DNS TXT record, or a meta tag pasted into their builder's settings field. Paid stage, client's permission.
- Google "People also ask", autocomplete, Reddit threads in the client's category.
- Paid prompt-volume tools (licensed, sampled, modelled data) — not worth it at our stage.

### `aeo guide`
`aeo --help` lists commands; it doesn't say when to use them. `aeo guide` prints the workflow in plain sentences, in order — new prospect → `aeo add` → `aeo diagnose` → (deposit) → question set → `aeo audit` → `aeo audit import` → report → `aeo recommend` → re-audit — with one line per command on when to use it and one example. Its text lives in one file so it's updated whenever a command is added. The frontend's help page reuses it.

### Making the tool readable to other AI tools
`aeo export --business <id>` writes one markdown file: a short header saying what each part is, then the latest diagnosis, the raw page, the audit answers and the scores. Drop it into Claude and ask "what should we fix first?" or "draft the client email." An MCP connector (Claude querying the database directly) is deferred until there are several clients.

### AEO Studio Coach (v4.7, later — step 26)
A Claude skill, plus one Claude Project per client, that Daivik and Anikait use alongside the tool: it knows every command, AEO/GEO in depth, the fixed process and its manual checks, a fix playbook per platform and per host/CDN, how to talk to clients, and AEO Studio's programs (what work belongs in which). After each step we give it the diagnosis, audit or evidence. Its files live in the repo under `coach/`; **whenever a command is added or changed, `coach/tool_guide.md` is updated in the same step** so it never goes stale.

### Who ran what
Every journal entry records the person, not the script: `AEO_OPERATOR=daivik` (or `anikait`) in each person's `.env`.

### Hosting — two phases

**Now: local only.** Daivik runs everything on his laptop. Postgres in Docker. Nothing hosted, no cost.

**With the frontend: hosted, code never shared.** Anikait uses the tool through a browser; the code, the database credentials and the Anthropic key stay on the server. Nobody but Daivik needs the repository.

| Piece | Choice | Cost |
|---|---|---|
| Database | Supabase free tier (500 MB, 2 projects; pauses after a week idle; **no backups on free**) | $0 |
| App (backend + frontend) | Render Starter (always on) — or Render free if a ~1-minute wake-up after 15 idle minutes is acceptable; or a Hetzner CX23 VPS | $7/mo, $0, or ~€6/mo |
| Login | Cloudflare Access in front of the app (free tier, up to 50 users — confirm it covers a self-hosted app before relying on it) | $0 |
| Report files | Supabase storage (1 GB free) or the server's disk | $0 |
| Backups | `aeo backup` on a schedule, to storage we control | $0 |
| AI calls | Anthropic API, per use | pennies per audit; shown on every cost card |

**Logic stays in plain Python functions; the CLI and the frontend are thin wrappers over the same functions.** This is what makes the frontend cheap to build later and keeps the two from ever disagreeing.

---

## Changelog

- **7 Oct 2026 — v4.5.** "Client facts file" replaced by the **truth document**: questionnaire pre-filled from the diagnosis (`aeo truth draft`), one file with confirmed facts (who confirmed, when) + the story, imported as a new profile version (`aeo truth import`). New **`aeo questions draft`**: AI drafts buyer questions from personas × confirmed places, French for Quebec, brand name rejected in code, buyer's words not the client's, person picks 32 and freezes before any audit. Optional `source:` per question. Fill-in file layout fixed with every item on its own line. Bing Webmaster Tools verification without code access. Journey (§2) updated.
- **5 Oct 2026 — v4.4.** Build order: step 9 (extraction v2 + sources box) done. Added two missing steps found in review: `aeo status` (the decline flow already refers to it) and a client facts file + `aeo facts import` (without it, claim checking and `accuracy_score` can never run). Renumbered steps 10–21.
- **5 Oct 2026 — v4.3.** Audit extraction widened in the same single AI call: businesses in order (share of voice), reasons given for recommendations, descriptor words, and a separate sources box in capture (§3.2). Fill-in file + `aeo audit import` replaces terminal pasting as the main path. Cost gate before audit's AI calls. New commands: `aeo guide`, `aeo show`, `aeo export`, `aeo backup`, `aeo audit import` (§13). New §16: help, context pack for Claude, operator name in the journal, and two-phase hosting — local now; with the frontend, a hosted app so Anikait uses a browser and the code is never shared (free–$7/mo plus API use). Build order rewritten with real statuses.

- **5 Oct 2026 — v4.2.** Dropped the `questions` database table from v4.1. `intent` and `is_target` now live as fields directly in the frozen question-set YAML file (§9), not a separate table — same protection (warn against targeting an informational question, always show the full set with targets marked), no new migration or foreign key. `probe_results` keeps `question_set_version` + `query_text` instead of a `question_id` FK.
- **5 Oct 2026 — implementation note.** Steps 6–7 (`aeo add`, `aeo audit --mode manual`) built and used migration number 016, which §13⟦step 9.5⟧ had reserved for the future `questions` table. That table is now migration 017. No plan content changed, just the number.
- **2 Oct 2026 — v4.1.** Added `aeo brief --business <id>` (§3.5) — pure SQL/Python aggregation of `sources_cited` already captured by `aeo audit`, giving both the content brief (what's winning a target question) and the outreach list (candidate domains for `recommendations.off_site_sources`), closing steps 2 and 4 of "working a target prompt" (§6) without any new AI calls. Added a `questions` table (§9) carrying `intent` (commercial/informational/navigational) and `is_target`, so targeting is tagged data rather than memory, and `aeo audit` warns against targeting informational questions. Added a draft-citability checklist to `aeo recommend` (§3.3) — name up front, a statistic, a quote, a direct answer, and one model-assisted distinctiveness check against `aeo brief`'s findings. Build order and CLI updated (§13).
- **2 Oct 2026 — v4.0.** Four agents replaced by three scripts (`diagnose`, `audit`, `recommend`); AI only for extraction, claim-checking and prose. Diagnosis added as a free, code-only qualification step with sourced serve/conditional/decline rules (§5); a decline is documented automatically and a migration pitch needs human approval. Deposit moved before the audit (§2). `diagnoses` table added, `probe_results` and `probe_runs` extended (§9). Measurement honesty rules made explicit — two designs, brand-free rate, mentioned vs recommended, retrieval activation, ranges on every score, the 20-point detection floor (§8). What we can and cannot promise written down (§6). Storage and checkpoints made a first-class requirement (§10). `business-profiler` deferred. Research documents added under `research/`.
- **29 Sep 2026 — v3.2.** Reason required for `declined`/`rejected`, recorded as a journal note linked to the status change. `aeo status --reason`. Gate declines journal the reason.

- **10 Oct 2026 — v4.6.** Step 11 (cost gate) done: refuses with no person at the keyboard, no override flag (§3.2). Build order split into a core path (13 truth document → 15 audit report → 19 recommend) and everything else after. Question sets written by hand for now.

- **14 Oct 2026 — v4.7.** Storage rewritten (§10): the database is the record; files are **evidence** (kept forever) or **generated** (rebuildable), in `clients/<domain>/evidence|generated`, date-named, no stage folders. Checkpoints become four fixed labels for every client — `before` · `after_site` · `after_aeo` · `check`; O'land's `old_site` → `before`, `new_site_no_aeo` → `after_site`. Engagement types A–F and access level added (§2). Evidence records with eight fields and a client-visible flag; `aeo log`, `aeo event`, `aeo timeline`. Core path now: **13c storage/evidence/work log (first, so everything after writes into the new layout)** → truth document → audit report → recommend → **compare (moved up)** → **client report (new)**. Later: explain budget, `aeo next` + checklist, CDN detection + migration redirect check, AEO Studio Coach.

- **18 Oct 2026 — v4.8.** Found on O'land's first real client report: "8 of 9 closed" counted six structured-data findings as closed because the new site has no structured data at all — new step 22c separates fixed from no-longer-applies. Two kinds of question (§8): discovery vs brand, never mixed; visibility on discovery only; length and language tagged (22d). Baseline dossier and evidence catalogue (§10, step 22e). Writing rules for questions.

- **29 Oct 2026 — v4.9.** Re-scoped to a working prototype that runs for any business in three phases: **A** before we land them (add, diagnose, spot check, dossier, starting-point report, status), **B** after we land them (truth document, questions, audits, report, recommend, brief, work log, evidence), **C** showcasing results (compare, client report). Anything specific to one client or one vendor is deferred. §2 gains the phase block; §13 is regrouped into Prototype / Deferred / Later with every step number and ✅ row kept. Prototype, in order: 12 `aeo status` (next) · 25 diagnose additions, generic parts only · 13b `aeo questions draft` (+ 8-question spot-check subset) · 17a local `aeo backup` · 18 `aeo brief` · 14 `aeo guide` · 27 real-data trial (Phase A on O'land plus 3 unrelated businesses). Deferred: 25b Cloudflare crawler report (fallback: screenshot + `aeo evidence add`), 19b draft validator, §14 O'land items. Later: 16, 17b Supabase move (split from 17), 21, 23, 24, 26, batch mode. Step 22d row now records what was built (discovery-only visibility, recognition, accuracy brand-only and overall, descriptions from brand questions only) and its open follow-up (compare shows no recognition change yet).

*Changes to this plan are deliberate and dated.*
