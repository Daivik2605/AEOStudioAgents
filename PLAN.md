# AEOStudioAgents — Plan

**Version 2 · 27 September 2026**

Read this file before doing anything in this repository. It defines what we are building, the four prototype agents, the architecture, the database, and the rules. When a decision is not covered here, ask rather than assume.

---

## 1. What this is

AEO Studio helps businesses get named by AI assistants — ChatGPT, Perplexity, Gemini, Google AI Overviews, Claude — when a customer asks a question that business should be the answer to.

This repository is the agent platform that does the repeatable work: understanding a business, measuring how visible it is to AI, finding what needs fixing, producing the fixes, and proving the results over time.

**It must work for any business with a website.** A dentist, a real estate agent, a startup, an event company, a retail store, a digital agency. Nothing may be hardcoded to one industry, one city, or one language. Canada first, English and French, designed to work anywhere.

**People stay in charge.** Agents draft, measure, and record. They never send, publish, or deploy anything. Nothing runs without human approval.

---

## 2. The five goals

Every agent serves one of these. If a proposed agent serves none of them, do not build it.

1. Do the prerequisites before AEO work starts — understand the business, measure where it stands, record a baseline.
2. Support the actual AEO/GEO work — structured data and content that make a business understandable and citable to AI.
3. Document every step and every result for each client, so the whole journey can be reconstructed.
4. Turn results into case studies for future business.
5. Find other businesses where AEO would make a big difference.

Out of scope now: pricing, proposals, invoicing, email sending, CRM sync, outreach drafting. These come in a later phase.

---

## 3. The core idea: the Business Profile

The system does not know in advance what kind of business it is looking at, so it works it out from the website. The **Business Profile** is the first thing produced for any business and the object every other agent reads.

A profile answers:

- What does this business sell or do?
- Who buys it?
- Where does it operate, and in what language(s)?
- What type is it? One of five: `local_service`, `professional_practice`, `b2b`, `ecommerce`, `agency_startup`.
- What questions would a buyer ask an AI assistant before choosing them?
- What is it called, including alternate names and brands?
- Who are its apparent competitors?

**If the profile is wrong, everything downstream is wrong.** Build it first, test it on very different businesses, trust nothing else until it holds.

Profiles are versioned:
- **v1** — built from the public website alone. Used for prospecting and early work.
- **v2** — updated after the client signs, with information the client provides directly.

Only the latest version is used downstream. All versions are kept for the record.

---

## 4. Prototype scope

We are building four agents in this phase. The goal is to have two working end-to-end for the first real client.

| Agent | Purpose | Prototype priority |
|---|---|---|
| business-profiler | Turn a website into a Business Profile | Phase 2 (after fixtures) |
| ai-visibility-probe | Measure what AI engines say about the business | Phase 2 (after fixtures) |
| aeo-audit | Client-facing baseline report — the flagship deliverable | **Phase 1 (built first)** |
| aeo-recommendations | Specific, prioritised fixes with JSON-LD and content draft | **Phase 1 (built first)** |

Build order for the prototype:

1. Write fixture data for O'land Stations (the first test business) that represents what the profiler and probe would produce.
2. Build **aeo-audit** against those fixtures. Get it producing a real, readable PDF.
3. Build **aeo-recommendations** against those fixtures. Get it producing real JSON-LD and a content draft.
4. Only then build **business-profiler** and **ai-visibility-probe** so real data replaces the fixtures.

This order means we have something useful before we spend money on API calls.

---

## 5. Architecture map

```
Website URL
    │
    ▼
business-profiler ──────────────────────────────────▶ Business Profile (v1)
    │
    ▼
ai-visibility-probe ────────────────────────────────▶ Raw AI answers + Visibility Score
    │
    ├──▶ aeo-audit ─────────────────────────────────▶ PDF baseline report (client-facing)
    │
    └──▶ aeo-recommendations ──────────────────────▶ JSON-LD blocks + content draft (human deploys)
```

Status flow for a business:

```
prospect → gated → approved → probed → audited → client → delivering → delivered
                 ↓
              rejected (stored, not deleted)
```

---

## 6. The four agents

### business-profiler

**Purpose.** Turn a website into a structured Business Profile.
**Input.** A website URL.
**Output.** A `BusinessProfile` record: type, offering, buyer description, location, languages, buyer questions (5–8), alternate names, apparent competitors.
**Reader.** Every other agent. Never shown to a client directly.
**Model.** Sonnet — needs judgment, runs on many businesses.
**Notes.** Must handle thin sites, French sites, businesses doing several things at once. Built second (after audit and recommendations are proven against fixtures).

### ai-visibility-probe

**Purpose.** Measure what AI engines actually say when a buyer asks about this kind of business.
**Input.** A `BusinessProfile`.
**Output.** Raw probe rows (every query, engine, full answer text, every business named) stored permanently. A visibility score computed from those raws by a versioned formula.
**Modes.**
- `manual` — the default. Prints the queries so a person can run them by hand and paste the results back. No API spend until approved.
- `automatic` — calls the AI engine APIs directly. Requires explicit approval showing the cost card first.
**Reader.** Audit, recommendations. The score reaches clients; raw results never do.
**Model.** Sonnet to write queries. Haiku to extract named businesses from answers.
**Notes.** Built second. Before anything is built on top of this, run the same business three times in one day and measure score variance. That number decides how scores are reported everywhere.

### aeo-audit

**Purpose.** The client-facing baseline. Proves the problem is real and records day-one position.
**Input.** A `BusinessProfile` + probe results (from fixtures in prototype, from database in production).
**Output.** A structured PDF: visibility score and tier, the queries asked, who appeared instead, competitors ranked by gap, engine-by-engine breakdown.
**Reader.** The client.
**Model.** Opus — published under the client's name.
**Rules.**
- Output is structured fields (score, queries, named competitors, tier label) — not essays. The PDF template assembles the layout; the agent fills in the data.
- Never states anything not established by the probe data.
- Never contains our fix plan.
- Re-audit mode adds a before/after table. It records what changed; it does not claim sole causation.

### aeo-recommendations

**Purpose.** Specific, prioritised fixes that a person can act on immediately.
**Input.** `BusinessProfile` + probe results + existing site HTML (for schema analysis).
**Output.**
- JSON-LD blocks ready to deploy (structured data markup)
- An `llms.txt` file for AI crawlers
- Draft content pages targeting the buyer questions the business is invisible for
- A diff showing what existing markup is missing or wrong
**Reader.** A person reviews and deploys every piece. The agent never touches a live site.
**Model.** Sonnet for schema (rule-following, checked by validator). Opus for content (published under client's name).
**Rules.**
- Step 0: always read and assess existing JSON-LD before generating anything new.
- Every content draft is marked `human_edit_pending`. Never marked ready to publish without a human edit pass.
- JSON-LD is validated locally before it reaches the human queue.

---

## 7. The approval gate

Every business must pass through a gate before any API spend happens. The gate is a CLI step — it never auto-runs.

**Gate check (code, not AI):**
- Is the website reachable?
- Does it appear to be a real, active business? (Has reviews, a verifiable address, recent activity.)
- Does it have a CMS or editable site? (Can we actually fix it?)
- Is it the type of business where AI visibility matters?

Pass/fail is a deterministic score from plain Python functions. The LLM does not decide this.

**On pass:** A cost card is printed in the CLI. The person reads it and types `yes` to continue or `n` to reject.

**Cost card shows (facts, not estimates):**
- Exact queries that will be sent
- Which engines will be queried
- Number of API calls (arithmetic, shown explicitly)
- Model for each call
- Ceiling cost: the maximum possible spend if every call hits the configured token limit

The word "estimate" does not appear. The ceiling is the worst case. Real spend will be lower.

**On rejection:** The business is stored with `status = rejected` and a reason tag. It is not deleted. It can be reconsidered later.

**On approval:** The probe runs for that one business. Nothing runs automatically or in batch until batch mode has been proven by hand.

---

## 8. Database — 14 tables

All tables use `uuid` primary keys generated by `gen_random_uuid()`. Migrations are append-only: never edit an applied migration, add a new numbered one.

**businesses** — one row per business, prospect or client
```
id, domain, name, website_url, status, business_type, location_city,
location_province, location_country, languages[], created_at, updated_at
```
Status values: `prospect | gated | approved | probed | audited | client | delivering | delivered | rejected`

**business_profiles** — versioned, one or more rows per business
```
id, business_id, version (1, 2…), offering, buyer_description, buyer_questions (jsonb),
alternate_names[], apparent_competitors[], raw_profile_text, created_at
```
Only the highest version number is used downstream. All versions are kept.

**gate_checks** — result of the gate evaluation
```
id, business_id, passed (bool), signals (jsonb), ceiling_cost_usd, approved_by,
approved_at, rejected_reason, created_at
```

**probe_results** — one row per query per engine per run. Never deleted.
```
id, business_id, run_id, mode (manual/automatic), query_text, engine,
raw_response_text, businesses_named (jsonb), run_number, created_at
```

**probe_runs** — groups probe_results rows into a single run
```
id, business_id, mode, total_queries, total_engines, formula_version,
visibility_score, gap_score, opportunity_score, completed_at, created_at
```

**visibility_scores** — computed from probe_results by versioned formula
```
id, business_id, probe_run_id, visibility_score (0–100), gap_score,
opportunity_score, tier, formula_version, computed_at
```

**audits** — one per audit event
```
id, business_id, mode (initial/reaudit), pdf_path, baseline_probe_run_id,
comparison_probe_run_id, before_after (jsonb, reaudit only), status, created_at
```

**schema_outputs** — generated structured data for a client
```
id, business_id, existing_jsonld_audit (jsonb), generated_jsonld,
llms_txt_content, validation_status, diff_summary, created_at
```

**content_pieces** — draft content pages
```
id, business_id, target_query, competitor_cited_url, draft_markdown,
human_edit_status (pending/approved/rejected), published_at, created_at
```

**recommendations** — the full output of aeo-recommendations for one business
```
id, business_id, schema_output_id, summary, priority_order (jsonb),
status (draft/reviewed/delivered), created_at
```

**client_journal** — dated log of every action and result per client
```
id, business_id, entry_type, description, agent_name, metadata (jsonb), created_at
```

**human_queue** — items waiting for a person
```
id, item_type, business_id, payload (jsonb), reason, status (pending/done/dismissed),
created_at, resolved_at
```

**runs** — every LLM call, no exceptions
```
id, agent, model, prompt_version, input_tokens, output_tokens, cost_usd,
latency_ms, created_at
```

**fixture_data** — test inputs used during prototype development
```
id, business_id, fixture_type (profile/probe_results/probe_run), payload (jsonb),
description, created_at
```

---

## 9. Scoring formulas

All scores are computed by versioned Python functions. An LLM never computes a final score. Formula version is stored with every score so history can be recomputed if the formula changes.

**Visibility Score (0–100)** — formula v1
Measures how often the business appears when buyers ask AI assistants.
```
visibility_score = (named_count / total_queries) × 100
```
- `named_count`: number of query/engine combinations where the business was named
- `total_queries`: total query/engine combinations run

**Gap Score (0–100)** — formula v1
Measures how visible a competitor is when the business is invisible.
```
gap_score = (competitor_named_while_business_invisible / total_queries) × 100
```
A high gap score means a competitor is eating the business's lunch.

**Opportunity Score (0–100)** — formula v1
Combines gap size, fixability, and business reality signals.
```
opportunity_score = (gap_weight × gap_score) + (fix_weight × fixability_signal) + (reality_weight × business_reality_signal)
```
Default weights (in `scoring/config.yaml`, changeable without code change):
- `gap_weight`: 0.5
- `fix_weight`: 0.3
- `reality_weight`: 0.2

**Tier labels** (shared across all scores, stored in `scoring/config.yaml`):
- 0–19: Invisible
- 20–39: Barely Visible
- 40–59: Partially Visible
- 60–79: Visible
- 80–100: Dominant

---

## 10. Document output: structured fields, not essays

The aeo-audit and aeo-recommendations agents do not write free-form text. They fill in named fields. The PDF template assembles the layout.

**Audit output fields:**
- `business_name`
- `report_date`
- `visibility_score` (number)
- `visibility_tier` (label)
- `gap_score` (number)
- `queries_asked` (list: the exact questions sent to AI engines)
- `engines_used` (list)
- `competitor_table` (list of: competitor name, appearance count, engines named in)
- `engine_breakdown` (per-engine: score, who appeared)
- `summary_paragraph` (one paragraph, the only free text, written by Opus)

**Recommendations output fields:**
- `business_name`
- `report_date`
- `priority_fixes` (ordered list, each with: what to fix, why, specific action, expected impact)
- `schema_blocks` (list of JSON-LD objects, validated)
- `llms_txt` (the full file content)
- `content_drafts` (list of: target query, draft page markdown, human_edit_status)
- `existing_schema_issues` (list: what was found, what is wrong)

---

## 11. Prompt versioning

Prompts live in `prompts/<agent>/`. They are versioned files, never deleted.

```
prompts/
  business-profiler/
    v1.md
  ai-visibility-probe/
    v1.md
  aeo-audit/
    v1.md
  aeo-recommendations/
    v1.md
  golden/
    business-profiler/
      oland_stations.json
      dental_practice.json
    aeo-audit/
      oland_stations.json
    aeo-recommendations/
      oland_stations.json
```

Every LLM call logs which prompt version it used. If a prompt changes, the version number increases. A pytest harness runs all golden examples and flags regressions before a new prompt version ships.

---

## 12. Manual probe mode

The probe runs in `manual` mode by default. This means:

1. The agent generates the queries a human should ask.
2. It prints them to the terminal, formatted for copy-paste.
3. A human opens each AI engine, pastes the query, copies the answer.
4. The human pastes the answers back into the CLI.
5. The agent extracts the business names mentioned and computes the score.

No API spend for the probe engines (ChatGPT, Perplexity, Gemini, Google AI Overviews) in manual mode.

`automatic` mode calls those APIs directly and is gated behind the cost card approval. It is not built until manual mode has run successfully ten times.

---

## 13. External services

**AI engines we measure** (automatic mode only):

| Engine | Access | Note |
|---|---|---|
| ChatGPT | OpenAI API, web search enabled | Without search it answers from training data |
| Perplexity | Perplexity API (Sonar) | Returns citations natively |
| Gemini | Google Gemini API, grounding enabled | Same caveat as ChatGPT |
| Google AI Overviews | No official API — DataForSEO or SerpAPI | Most-seen AI answer, hardest to measure |
| Claude | Anthropic API, web search | Already available |
| Grok | xAI API | Optional, later |

**Website reading:** Firecrawl or Jina Reader — converts any site (including JS-heavy Squarespace/Wix) to clean text the profiler can read.

**Business signals:** Google Places API — reviews, rating, hours, locations, verified address. Used by the gate check.

**Competitor lookup:** Brave Search or Serper — finds who else does this in this city.

**Schema validation:** Local Python libraries. Google's Rich Results Test is the manual final check before a human deploys.

**Database:** Docker Postgres locally. Supabase (same SQL) in production.

**PDFs:** Jinja2 HTML templates rendered with WeasyPrint.

**Not needed now:** email sending, CRM sync, payments, scheduling.

---

## 14. Technical rules

These are not negotiable.

1. **One harness, four configs.** Build `core/` once. Each agent is a definition file — system prompt, Pydantic output schema, allowed tools, model choice. Adding an agent must be a same-day edit, not new engineering.
2. **No frameworks.** Anthropic SDK directly. A tool-use loop in `core/llm.py`. No LangChain, no CrewAI.
3. **Store raw, compute later.** The probe stores full AI answers. Scores derive from raws via a versioned formula so history can be recomputed.
4. **Scores are code, not prompts.** Any number a client might challenge comes from a deterministic Python function in `scoring/scoring.py`.
5. **Every LLM call is logged** — agent, model, prompt version, input, output, tokens, cost, latency — before returning to the caller. No exceptions.
6. **Structured output everywhere.** Every agent returns a Pydantic model. On validation failure: retry once with the error appended, then write to the human queue and fail loudly.
7. **Nothing is scheduled until it has run cleanly by hand ten times.** Agents start as CLI commands.
8. **Language follows the buyer.** Probe in the language the business's customers actually use.
9. **Migrations are append-only.** Never edit an applied migration; add a new numbered one.
10. **Manual probe is the default.** Automatic probe (API calls to AI engines) requires explicit CLI approval and never runs first.
11. **Approval gate before any spend.** Every business must pass the gate check and receive human confirmation before any API calls run. The gate check itself is free (deterministic code + Google Places).

---

## 15. Human checkpoints

| Always a person | Why |
|---|---|
| Approving a business through the gate | Controls all API spend |
| Approving automatic probe mode | Specific cost card must be read and confirmed |
| Deploying schema to a live site | A broken tag on a client site is a real incident |
| Publishing any content page | Unedited AI content under a client's name loses the client |
| Sending the audit | Their name is on it |
| Commentary in a flat or bad week | Needs judgment, not an auto-generated sentence |
| Using a case study anywhere | Written client consent first |
| Anything the agent flags as uncertain | Goes to human queue, not downstream |

---

## 16. Build order

Nothing is built before the thing that feeds it has been tested on real data.

0. **Scaffold** — folders, `pyproject.toml`, Docker Postgres, all migrations. No agent logic.
1. **`core/`** — llm wrapper with cost logging, db client, tools, runner, error handling.
2. **`scoring/`** — `shared_config.yaml` and `scoring.py` (three scoring functions as pure Python). Unit tests covering every tier boundary.
3. **Fixtures** — `fixture_data` rows for O'land Stations representing what the profiler and probe would produce. This is hand-written JSON, not generated.
4. **aeo-audit** — built against fixtures. Produces a real PDF. Tested until the output is genuinely useful.
5. **aeo-recommendations** — built against fixtures. Produces real JSON-LD and a content draft.
6. **ai-visibility-probe** — manual mode first. Automatic mode only after manual has run ten times successfully.
7. **business-profiler** — tested on all four test businesses before anything else trusts its output.

Stop after each step for review. Do not build ahead.

### Definition of done for any agent

- Pydantic output schema with named structured fields (no raw prose fields)
- Versioned prompt file under `prompts/<agent>/v1.md`
- At least 3 golden examples and a passing golden test
- Every LLM call visible in the `runs` table with cost and latency
- Failed or uncertain output lands in `human_queue`, never passed downstream
- A working CLI command that runs it on real or fixture input

---

## 17. First test businesses

Four businesses, chosen to be as different from each other as possible. The profiler must classify all four correctly before any other agent trusts its output.

1. **O'land Stations** — olandstations.com — B2B product/service, events, Montreal, English and French. First fixture. First real client.
2. A dental or medical practice — professional practice, local service.
3. An e-commerce store — online retail.
4. A digital or creative agency — agency/startup.

---

## 18. Phase 2 (not built now)

These decisions are made but not built in this phase:

- `confidence` and `evidence` fields on every agent output schema
- Human queue routing based on confidence thresholds
- Automatic probe in batch mode
- Outreach drafting
- Proposal generation
- Weekly visibility reporter
- Client journal agent
- Case study generator
- Team ops agent
- Scheduling (nothing runs on a schedule until it has run by hand)

---

*This is the base plan. Changes to it are deliberate and dated.*
