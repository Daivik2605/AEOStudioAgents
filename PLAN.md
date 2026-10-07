# AEOStudioAgents — Plan

**Version 4.5 · 7 October 2026.** Changes since 4.4 are in the changelog at the end.

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

**Cost gate.** Before the AI extraction calls run, print a cost card (number of calls, estimated cost from `core/model_prices.json`) and require a typed `yes`, per CLAUDE.md. Manual capture itself costs nothing; only the analysis does.

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

Add `checkpoint` — `old_site` · `new_site_no_aeo` · `post_aeo` · `quarterly_YYYY_QN`.

### Fix to `recommendations`

Migration 008 has `accuracy_probe_run_id NOT NULL`, which contradicts this plan. Make it nullable.

### Journal entry types

Existing 16, plus `diagnosed` and `decline_recommended`.

### Local setup

Postgres 16 in Docker on **port 5434** (5432 and 5433 taken). Dev database `aeo`, test database `aeo_test`. **Tests refuse to run without `TEST_DATABASE_URL`.** Supabase later; same SQL.

---

## 10. Storage

**Every report is kept, forever, for every checkpoint.** Before and after is not optional — it is the product.

```
reports/<business-slug>/<checkpoint>/
    diagnosis.md
    diagnosis.json
    audit.md
    audit.pdf
    recommendations.md
    generated/            robots.txt, llms.txt, schema.json
```

Files are for humans. The database is the record. Both are written; neither is authoritative alone.

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
| 10 | **Fill-in file** + `aeo audit import` | ⏭ Next |
| 11 | **Cost gate** on audit's AI calls | |
| 12 | **`aeo status`** — change status with a required reason (§7). The decline flow already tells people to use it | |
| 13 | **Truth document** — `aeo truth draft` (questionnaire pre-filled from the diagnosis) + `aeo truth import` (confirmed facts + story → new profile version). Replaces the v4.4 "client facts file" | |
| 13b | **`aeo questions draft`** — AI drafts candidate buyer questions from the truth document + diagnosis (§16); a person picks and freezes | |
| 14 | **`aeo guide`** — plain-language list of every command, when to use it, in what order | |
| 15 | Audit report output (scores with ranges, share of voice, reasons, wrong facts) | |
| 16 | `aeo show` (one business's history in plain sentences) + `aeo export` (context pack for Claude) | |
| 17 | `aeo backup` + Supabase move (see §16) | |
| 18 | **`aeo brief`** — citation aggregation, content brief + outreach list | |
| 19 | **`aeo recommend`** — includes the draft validator | |
| 20 | Re-audit and compare | |
| 21 | Frontend + hosting (see §16) | |

Stop after each step for review. Do not build ahead.

### CLI

```
aeo guide                                   every command, when to use it, in order
aeo diagnose <url> [--business <id>] [--checkpoint <name>]
aeo audit --business <id> [--mode manual|batch] [--checkpoint <name>]
aeo audit import <file>
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

*Changes to this plan are deliberate and dated.*
