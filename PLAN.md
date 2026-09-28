# AEOStudioAgents — Plan

**Version 3 · 27 September 2026**

Read this file before doing anything in this repository. It defines what we are building, the four prototype agents, the lifecycle, the database, and the rules. When a decision is not covered here, ask rather than assume.

---

## 1. What this is

AEO Studio helps businesses get named — and described correctly — by AI assistants: ChatGPT, Perplexity, Gemini, Google AI Overviews, Claude.

This repository is the agent platform that does the repeatable work: understanding a business, measuring what AI says about it, finding what needs fixing, producing the fixes, and proving the results over time.

**It must work for any business with a website.** A dentist, a real estate agent, a startup, an event company, a retail store, a digital agency. Nothing may be hardcoded to one industry, one city, or one language. Canada first, English and French, designed to work anywhere.

**People stay in charge.** Agents draft, measure, and record. They never send, publish, or deploy anything. Nothing that costs money runs without human approval.

---

## 2. The five goals

Every agent serves one of these. If a proposed agent serves none of them, do not build it.

1. Do the prerequisites before AEO work starts — understand the business, measure where it stands, record a baseline.
2. Support the actual AEO/GEO work — structured data and content that make a business understandable and citable to AI.
3. Document every step and every result for each client, so the whole journey can be reconstructed.
4. Turn results into case studies for future business.
5. Find other businesses where AEO would make a big difference.

Out of scope now: pricing, proposals, invoicing, email sending, CRM sync, outreach drafting.

---

## 3. The two things AI must get right

| | Visibility | Accuracy |
|---|---|---|
| Buyer asks | About the category, not naming anyone | About the business by name |
| Example | "Best water station providers in Canada?" / "Best clinic for a root canal?" / "Best web developer near me" | "Where does O'land Stations operate?" |
| Question we answer | Does AI **name** them? | Is what AI says about them **true**? |
| Question type | `category` | `brand` |
| When it runs | Prospect stage, before the audit | Client stage, after the questionnaire |
| Compared against | Nothing — we count who gets named | The client-confirmed facts (profile v2) |
| Scores | Visibility Score, Gap Score | Accuracy Score, Recognition Rate |

Real example: ChatGPT says O'land Stations only works with Montreal clients. They actually serve the US and Canada. That is an accuracy error.

**Accuracy only runs against client-confirmed facts.** A claim is only marked wrong if the correct fact comes from the client (profile v2). We never tell a client "AI is wrong about you" based on our own assumptions.

**Where did the wrong fact come from?** Every AI answer stores the pages it cited. Often the wrong fact comes from the business's own website (e.g. the site never says it serves the US). The fix then is on their site, not a mistake by the AI.

**Location matters for category questions.** Typing "near me" into ChatGPT by hand uses your own location. The API has no location at all. So every result records which location it assumed, and automatic mode always puts the place in the question ("…in Toronto").

---

## 4. The Business Profile — the fact sheet

The system does not know in advance what kind of business it is looking at, so it works it out from the website. The **Business Profile** is the first thing produced for any business and the object every other agent reads. It is also the answer key for the accuracy check.

**There is no fixed list of business types.** Instead, every business is described by four traits:

| Trait | What it is | Values | Used for |
|---|---|---|---|
| `industry` | Plain description of what they do | Free text: "Dentistry", "Event water refill stations" | Reading, agent context |
| `schema_type` | The official schema.org category | Any schema.org type: `Dentist`, `Plumber`, `OnlineStore`, `ProfessionalService`… | Which JSON-LD to generate; consistent grouping in SQL |
| `customer_type` | Who buys | `consumers` / `businesses` / `both` | How buyers phrase questions |
| `reach` | How far they serve | `local` / `regional` / `national` / `online` | Whether a place goes in the questions |

Profiles are versioned:
- **v1** — built from the public website by the profiler. Used for the prospect stage.
- **v2** — built from the client questionnaire after signing. Client-confirmed. Used for the accuracy check and recommendations.

Only the latest version is used downstream. All versions are kept.

**If the profile is wrong, everything downstream is wrong.**

---

## 5. The lifecycle

```
PROSPECT STAGE
 1. Add business                     → businesses (status: prospect)
 2. business-profiler reads website  → business_profiles v1
 3. Gate check + cost card + "yes"   → gate_checks (approved, or declined → status: declined)
 4. Visibility probe (category Qs)   → probe_runs (run_type: visibility) + probe_results
 5. Scoring code                     → visibility_scores (visibility, gap, opportunity)
 6. aeo-audit                        → audits (PDF) → you send it (aeo mark-sent)
 7. They say no                      → status: rejected
    They sign                        → status: client

CLIENT STAGE
 8. Questionnaire answered           → business_profiles v2 (client-confirmed facts)
 9. Accuracy probe (brand Qs)        → probe_runs (run_type: accuracy) + probe_results with claims checked
10. Scoring code                     → visibility_scores (accuracy, recognition)
11. aeo-recommendations              → recommendations + content_pieces
12. You deploy the fixes             → aeo mark-deployed
13. Re-audit later (both run types)  → audits (mode: reaudit)
14. Work complete                    → status: delivered

EVERY STEP ALSO WRITES TO
   runs           → every AI call and its cost
   client_journal → what happened, when
   human_queue    → anything that failed or needs a person
```

**Statuses** (on `businesses`):

| Status | Meaning |
|---|---|
| `prospect` | Might become a client |
| `client` | Work is ongoing |
| `delivered` | Work is completed |
| `rejected` | **They** said no to us |
| `declined` | **We** said no to them (at the gate or any time later) |

Progress within a stage (profiled, gated, probed, audited) is not a status — it is already recorded in its own table.

---

## 6. Prototype scope

Four agents in this phase. Two are built first, against hand-written test data.

| Agent | Purpose | Built |
|---|---|---|
| aeo-audit | Client-facing visibility report | **First** |
| aeo-recommendations | Fixes for wrong facts and visibility gaps: JSON-LD, llms.txt, content drafts | **First** |
| ai-visibility-probe | Asks AI engines, records answers, checks claims | After the two above |
| business-profiler | Turns a website into a profile v1 | Last |

**Test data (fixtures).** A seed script fills the real tables with hand-written O'land Stations data: the business, profile v1, a visibility probe run, profile v2 (questionnaire answers), and an accuracy probe run that includes the real "Montreal only" error. The agents read the same tables either way — they cannot tell test data from real data, so there is no special fixture code to remove later. Fixture files live in `fixtures/oland_stations/`.

This order gives us something useful before any money is spent on API calls.

---

## 7. The four agents

### business-profiler

**Purpose.** Turn a website into profile v1.
**Input.** A website URL.
**Output.** A `BusinessProfile`: the four traits, offering, services, buyers, location, service area, contact details, social profiles, languages, category questions, alternate names, apparent competitors, proof points.
**Model.** Sonnet.
**Notes.** Must handle thin sites, French sites, and businesses that do several things.

### ai-visibility-probe

**Purpose.** Ask AI engines what buyers ask, and record exactly what they say.
**Run types.**
- `visibility` — category questions. Extracts every business named.
- `accuracy` — brand questions, only after profile v2 exists. Breaks each answer into claims and marks each one against the v2 facts: `correct` / `incorrect` / `unverifiable`. Also records whether the AI recognised the business at all.
**Modes.**
- `manual` — the default. Prints the questions; a person runs them by hand and pastes the answers back.
- `automatic` — calls the engine APIs. Built only after manual has run cleanly ten times.
**Models.** Sonnet writes questions and checks claims (judgment). Haiku extracts named businesses (high volume).
**Notes.** Before anything relies on it, run one business three times in one day and measure how much scores move.

### aeo-audit

**Purpose.** The client-facing visibility report. Proves the problem is real and records the starting point.
**Input.** Profile + visibility probe run + scores.
**Output.** Structured fields (section 11) stored in `audits.audit_data`, rendered to a PDF by a template.
**Model.** Opus for the one summary paragraph. Everything else is data.
**Rules.** Never states anything the probe data does not show. Never contains our fix plan. Re-audit mode compares against the baseline run and includes accuracy once it exists; it records what changed without claiming sole cause.

### aeo-recommendations

**Purpose.** Specific, prioritised fixes a person can act on.
**Input.** Profile v2 + visibility run + accuracy run + the live site's HTML.
**Output.**
- Priority fixes, ordered
- For each wrong fact: where to state it on the site, the JSON-LD property (e.g. `areaServed`), an FAQ entry, and — if the wrong fact came from another website — that source, flagged for a person to correct
- For each visibility gap: a content page draft, and the sites AI cited instead (places to get listed)
- JSON-LD blocks (validated) and an `llms.txt` file
- What existing markup is missing or wrong
**Models.** Sonnet for schema. Opus for content.
**Rules.** Always read existing JSON-LD before generating anything. Every content draft starts as `pending` and needs a human edit. Never touches a live site.

---

## 8. The approval gate and cost card

Nothing runs a probe without a person typing `yes` in the CLI. No auto-approve.

**Business checks** (first visibility run only; plain Python, not AI):
- Is the website reachable?
- Is it a real, active business? (Reviews, verifiable address, recent activity.)
- Is the site editable? (Can we actually fix it?)

**Cost card** (every probe run, manual or automatic). Facts, not estimates:
- The exact questions that will be sent
- Which engines
- Number of AI calls, with the arithmetic shown
- The model for each call
- Ceiling cost: the most it could possibly cost if every call hits its token limit

In manual mode the card only lists our own model calls (extraction, claim checking), since the engines are queried by hand.

**On decline:** `gate_checks.decision = declined` with a reason, and the business status becomes `declined`. Nothing is deleted.

---

## 9. Database — 12 tables

All tables use `uuid` primary keys (`gen_random_uuid()`). Every table links to `businesses` directly or through its parent, and has a `created_at`. Status-like fields use a fixed set of values (enforced by the database). Nothing is deleted. Simple lists use Postgres `text[]`; lists where each item has several parts use `jsonb`.

Migrations are append-only: never edit an applied migration, add a new numbered one.

### Core records

**1. businesses**
```
id, name, domain (unique, normalised: lowercase, no https://, no www.),
website_url, status (prospect/client/delivered/rejected/declined),
created_at (the date we found them), updated_at
```

**2. business_profiles** — versioned; unique on (business_id, version)
```
id, business_id, version, source (website/client),
industry, schema_type, customer_type (consumers/businesses/both),
reach (local/regional/national/online),
offering, services text[], buyer_description,
location_city, location_region, location_country, service_area text[],
address, phone, email, hours jsonb, social_profiles text[],
languages text[], alternate_names text[], apparent_competitors text[],
proof_points text[],
questions jsonb       -- [{question, type: brand|category, language, location}]
questionnaire_answers jsonb   -- v2 only, exactly as the client answered
created_at
```

**3. gate_checks** — one row per approval before a probe run
```
id, business_id, run_type (visibility/accuracy), passed, signals jsonb,
cost_card jsonb, ceiling_cost_usd, decision (pending/approved/declined),
decision_reason, decided_at, created_at
```

### Measuring

**4. probe_runs** — one session of asking the engines
```
id, business_id, business_profile_id, gate_check_id,
run_type (visibility/accuracy), mode (manual/automatic),
status (in_progress/complete/failed), started_at, completed_at
```

**5. probe_results** — one row per question × engine × repeat. Never deleted.
```
id, probe_run_id, query_text, query_language, location_context, engine,
repeat_number, raw_response_text, sources_cited jsonb,
businesses_named jsonb,       -- visibility runs
recognized boolean,           -- accuracy runs: did the AI know the business?
claims_checked jsonb,         -- accuracy runs: [{claim, verdict, correct_fact, profile_field}]
created_at
```
The raw answer is the source of truth. Extracted names and claim checks are derived from it and can be re-run later without asking the engines again.

**6. visibility_scores** — computed by code; unique on (probe_run_id, formula_version)
```
id, probe_run_id, formula_version,
visibility_score, gap_score, opportunity_score,   -- visibility runs
accuracy_score, recognition_rate,                 -- accuracy runs
tier, computed_at
```

### Outputs

**7. audits**
```
id, business_id, business_profile_id, probe_run_id,
mode (initial/reaudit), comparison_probe_run_id,
audit_data jsonb (all structured fields — the PDF is drawn from these),
pdf_path, status (draft/approved/sent), created_at
```

**8. recommendations**
```
id, business_id, business_profile_id,
visibility_probe_run_id, accuracy_probe_run_id,
priority_fixes jsonb, fact_fixes jsonb, off_site_sources jsonb,
existing_jsonld jsonb, existing_schema_issues jsonb,
generated_jsonld jsonb, llms_txt, validation_status, validation_errors jsonb,
status (draft/reviewed/deployed), created_at
```

**9. content_pieces** — several per recommendation
```
id, recommendation_id, target_query, cited_url, draft_markdown,
edit_status (pending/approved/rejected), published_at, created_at
```

### Record-keeping

**10. runs** — every AI call, no exceptions
```
id, business_id, agent, model, prompt_version, input jsonb, output jsonb,
input_tokens, output_tokens, cost_usd, latency_ms,
status (success/failed), error, created_at
```

**11. client_journal** — the story of each business. Append-only.
```
id, business_id, entry_type, description, actor (agent name or "human"),
related_table, related_id, details jsonb, created_at
```

**12. human_queue** — things waiting for a person
```
id, business_id, item_type, reason, payload jsonb,
status (pending/done/dismissed), resolution_note, created_at, resolved_at
```

### Which tables the prototype agents touch

| Agent | Reads | Writes |
|---|---|---|
| aeo-audit | businesses, business_profiles, probe_runs, probe_results, visibility_scores | audits, runs, client_journal, human_queue |
| aeo-recommendations | the same five + live site HTML | recommendations, content_pieces, runs, client_journal, human_queue |

---

## 10. Logging the whole process

Three layers, each answering a different question:

| Layer | Answers |
|---|---|
| `runs` | What did the AI do, and what did it cost? |
| `client_journal` | What happened to this business, and when? |
| Output tables | What exactly was produced? |

**Journal entry types:** `business_added`, `status_change`, `profile_created`, `questionnaire_received`, `gate_checked`, `gate_decision`, `probe_started`, `probe_completed`, `score_computed`, `audit_generated`, `audit_sent`, `recommendations_generated`, `schema_deployed`, `content_published`, `note`, `error`.

**Two rules make it complete:**
1. **Status changes log themselves.** A Postgres trigger writes a `status_change` row (with from/to in `details`) whenever `businesses.status` changes — even if changed with raw SQL.
2. **Things done outside the system get a CLI command.** `aeo mark-sent`, `aeo mark-deployed`, `aeo mark-published`, `aeo note`. If it is not recorded, the case study cannot use it.

All journal writes go through one function in `core/`.

---

## 11. Scoring formulas

Plain Python in `scoring/scoring.py`. An AI never computes a final score. Every score stores its `formula_version`, so history can be recalculated. Weights and tier bands live in `scoring/config.yaml`.

**Visibility Score (0–100)** — category questions
```
visibility_score = answers naming the business / total answers × 100
```

**Gap Score (0–100)** — category questions
```
gap_score = answers naming a competitor but not the business / total answers × 100
```

**Opportunity Score (0–100)**
```
opportunity_score = 0.5 × gap_score + 0.3 × fixability_signal + 0.2 × business_reality_signal
```
Fixability and reality signals come from the gate check.

**Accuracy Score (0–100)** — brand questions
```
accuracy_score = correct claims / (correct + incorrect claims) × 100
```
Unverifiable claims are not counted.

**Recognition Rate (0–100)** — brand questions
```
recognition_rate = answers where the AI knew the business / total brand answers × 100
```

**Tiers** (visibility): 0–19 Invisible · 20–39 Barely Visible · 40–59 Partially Visible · 60–79 Visible · 80–100 Dominant

A business is spotted in an answer by matching its `name` and `alternate_names`.

---

## 12. Documents: structured fields, not essays

Agents fill in named fields. A template draws the PDF. Layout is code, not AI.

**Audit fields:** business_name, report_date, visibility_score, visibility_tier, gap_score, questions_asked, engines_used, location_context, competitor_table (name, times named, engines), engine_breakdown (per engine: score, who appeared), summary_paragraph (the only free text). Re-audits add a before/after table, and accuracy_score, recognition_rate and wrong_facts once they exist.

**Recommendations fields:** business_name, report_date, priority_fixes (what, why, action, expected impact), fact_fixes (AI says, truth, engine, source cited, fix), off_site_sources, schema_blocks, llms_txt, content_drafts (target question, draft, edit status), existing_schema_issues.

---

## 13. The client questionnaire

Sent after signing. The answers become profile v2 — the facts the accuracy check is measured against. Answers are stored exactly as given in `questionnaire_answers`.

It asks the client to confirm or provide: official name and other names they go by, everything they sell, who buys, where they are based, **everywhere they serve**, address / phone / email / hours, social profiles, languages their customers use, competitors, proof points (years, notable clients, awards, certifications), and — most valuable — **the questions customers ask them before buying.**

---

## 14. Manual probe mode (the default)

1. The agent writes the questions.
2. It prints them for copy-paste, with the location to use.
3. A person asks each AI engine by hand and copies the answer.
4. The person pastes the answers back into the CLI, noting the engine.
5. The agent extracts names / checks claims, and the scoring code computes the scores.

No engine API spend. `automatic` mode is built only after manual has run cleanly ten times.

---

## 15. Prompts

```
prompts/
  business-profiler/v1.md
  ai-visibility-probe/v1.md
  aeo-audit/v1.md
  aeo-recommendations/v1.md
  golden/<agent>/<business>.json   -- input + hand-checked ideal output
```

Never edit a prompt version after it has run; add a new version. Every AI call logs its prompt version. A pytest harness runs the golden examples and flags regressions.

---

## 16. External services

**AI engines** (automatic mode only):

| Engine | Access | Note |
|---|---|---|
| ChatGPT | OpenAI API, web search on | Without search it answers from memory |
| Perplexity | Perplexity API (Sonar) | Returns sources directly |
| Gemini | Gemini API, grounding on | Same note as ChatGPT |
| Google AI Overviews | DataForSEO or SerpAPI | No official API |
| Claude | Anthropic API, web search | Already available |
| Grok | xAI API | Optional, later |

**Other:** Firecrawl or Jina Reader (reading websites) · Google Places API (business checks) · Brave Search or Serper (competitor lookup) · local Python validation for JSON-LD, Google Rich Results Test as a manual final check · Docker Postgres locally, Supabase later (same SQL) · Jinja2 + WeasyPrint for PDFs.

**Not needed now:** email sending, CRM, payments, scheduling.

---

## 17. Technical rules

1. **One harness, four definitions.** `core/` is built once. Each agent is a definition: prompt, Pydantic output schema, tools, model.
2. **No frameworks.** Anthropic SDK directly, with a simple tool-use loop in `core/llm.py`.
3. **Store raw, compute later.** Full AI answers are kept; everything else is derived from them.
4. **Scores are code, not prompts.**
5. **Every AI call is logged** in `runs` before returning — no exceptions.
6. **Structured output everywhere.** On validation failure: retry once with the error, then write to `human_queue` and stop.
7. **Manual probe is the default.** Nothing costs money without a cost card and a typed `yes`.
8. **Nothing is scheduled** until it has run cleanly by hand ten times.
9. **Language follows the buyer.**
10. **Migrations are append-only.**
11. **Nothing is hardcoded to an industry, city, or language.** Everything reads from the profile.
12. **Everything is analysable with SQL.** Fixed value sets, numbers as numbers, dates on everything, nothing deleted.

---

## 18. Human checkpoints

| Always a person | Why |
|---|---|
| Approving any probe run (cost card) | Controls all spend |
| Declining a business | Our decision, recorded with a reason |
| Sending the audit | Their name is on it |
| Confirming facts (questionnaire) | Accuracy is only measured against client-confirmed facts |
| Deploying schema / llms.txt | A broken tag on a live site is a real incident |
| Publishing any content page | Unedited AI content under a client's name loses the client |
| Correcting wrong facts on other websites | Needs the client's accounts and judgment |
| Using a case study anywhere | Written client consent first |

---

## 19. Build order

0. **Scaffold** — folders, `pyproject.toml`, Docker Postgres, migrations for all 12 tables including the status trigger. No agent logic.
1. **`core/`** — AI call wrapper with logging, database access, journal function, runner, error handling.
2. **`scoring/`** — the five formulas as pure functions, with tests at every tier boundary.
3. **Fixtures** — O'land seed data (see section 6).
4. **aeo-audit** — against fixtures, until the PDF is genuinely useful.
5. **aeo-recommendations** — against fixtures, until the JSON-LD validates and the fixes are specific.
6. **ai-visibility-probe** — manual mode, both run types.
7. **business-profiler** — tested on all four test businesses.

Stop after each step for review. Do not build ahead.

### Definition of done for any agent

- Pydantic output schema with named fields
- Versioned prompt file
- At least 3 golden examples and a passing golden test
- Every AI call visible in `runs` with cost and latency
- Journal entries written for what it did
- Failures land in `human_queue`
- A working CLI command

### CLI

```
aeo add <url>                        # add a business (prospect)
aeo profile --business <id>          # business-profiler
aeo gate --business <id>             # business checks + cost card
aeo probe --business <id> --type visibility|accuracy [--mode manual|automatic]
aeo audit --business <id> [--reaudit]
aeo recommend --business <id>
aeo status --business <id> <status>
aeo mark-sent | mark-deployed | mark-published --business <id>
aeo note --business <id> "text"
aeo queue
aeo seed oland                       # load O'land fixtures
```

---

## 20. First test businesses

Chosen to be as different as possible:

| Business | industry | schema_type | customer_type | reach |
|---|---|---|---|---|
| **O'land Stations** (olandstations.com) — first fixture, first client | Event water refill stations | LocalBusiness | businesses | national (US + Canada) |
| A dental practice | Dentistry | Dentist | consumers | local |
| An online store | e.g. outdoor footwear | OnlineStore | consumers | online |
| A digital agency | Branding and design | ProfessionalService | businesses | national |

---

## 21. Later phases (decided, not built now)

- `confidence` and `evidence` fields on agent outputs, and routing by confidence
- Automatic probe mode, batch runs, budgets, auto-runs (the design must allow these later)
- Outreach, proposals, weekly reporter, case study generator, team ops agent
- Scheduling

---

*This is the base plan. Changes to it are deliberate and dated.*
