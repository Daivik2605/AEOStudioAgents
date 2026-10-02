# AEOStudioAgents

Read PLAN.md first. **PLAN.md (v4.0) is the source of truth.** If this file and PLAN.md disagree, PLAN.md wins — say so, and ask. This file is about how to work in this repo.

*Last updated 2 Oct 2026 — rewritten for PLAN v4.0: three scripts, not four agents.*

Two research documents sit behind the plan. Read the relevant one before building the thing it covers:
- `research/AEO_PLAYBOOK.md` — what actually improves AI visibility, with evidence tiers
- `research/PLATFORM_DELIVERY.md` — what can be delivered on each website platform

## How to work with me

- Build one thing at a time. Stop after each and wait for review. Do not build ahead of what was asked.
- Before you start, say briefly what you are about to do. After you finish, say what you created and any decision you made that PLAN.md did not specify.
- If something is ambiguous or missing from PLAN.md, ask. Do not guess and continue.
- I am learning this codebase as it is built. Prefer clear, obvious code over clever code, and comment anything non-obvious.
- When I ask a question about the code, answer the question. Do not start editing unless I ask you to.
- Always speak in simple language. No jargon unless it is necessary.
- Git is mine. Never commit or push. Tell me the commands and I run them.

## Scripts, not agents

This is the central design rule, and v4.0 exists because of it.

**An agent is only justified when the task needs judgement.** Platform detection is pattern matching. Crawler testing is HTTP status codes. Scoring is arithmetic. Building a robots.txt is a template. Wrapping those in AI adds cost, latency and non-determinism for nothing.

Three scripts:

| Script | What it does |
|---|---|
| `aeo diagnose <url>` | Site check. Free, seconds, code only. Verdict: serve / conditional / decline |
| `aeo audit --business <id>` | Ask the engines (manual by default), store raw answers, score |
| `aeo recommend --business <id>` | On-page AEO files + a markdown document |

**AI is used for exactly three things.** Everything else is plain Python:
1. Extracting which businesses were named in an AI answer
2. Checking a claim against the client's confirmed facts
3. Writing prose a person will read (the verdict explanation, the audit summary, content drafts)

**Rules decide, AI explains.** A verdict comes from a lookup table with a cited source. A model turns it into a sendable paragraph. Never the reverse — if the model decides, the answer changes between runs.

## Build order (PLAN.md §13)

Steps 0–2 done. **Step 3 next:** migration 014 (nullable `accuracy_probe_run_id`), lower-edge tier fix, `.gitignore`. Then `aeo diagnose`.

## Architecture conventions

- No agent frameworks. Anthropic SDK directly, with a simple loop in `core/llm.py`.
- Every AI call goes through `core/llm.py` and is logged in `runs` with prompt version, tokens, cost and latency — including failures, before returning.
- Structured output everywhere. On validation failure: retry once with the error, then write to `human_queue` and stop.
- **Store raw, compute later.** Raw AI answers, raw robots.txt, raw JSON-LD are kept permanently. Names, claim checks and findings are derived and can be re-run against old raw data.
- All journal writes go through the one function in `core/`.
- Nothing hardcoded to an industry, city or language. O'land exists only in seed data — never in code, prompts or templates.

## Diagnosis

- Plain Python. One AI call, for the verdict explanation only.
- Detect platform from headers and raw HTML. **Never report a platform on a Tier 3 signal alone** (PLAN.md §4).
- **We write our own detection rules.** The public fingerprint database is GPL-3.0 — a licensing problem for commercial software — and has gaps for our platforms. The AI bot list (`ai-robots-txt/ai.robots.txt`) is MIT and safe to bundle.
- Fetch robots.txt **as a bot**, not as a browser. A 5xx or challenged robots.txt means total disallow under RFC 9309 — a silent, invisible block.
- Use `protego` for robots.txt. Not `urllib.robotparser` — it has no `$` anchor support and no longest-match precedence, so it returns wrong answers on real files.
- Always fetch with a browser control UA alongside the bot UAs and compare. `403` for both means our IP, not an AI block — do not report it as one.
- Any `200` with under ~80 words goes through the challenge check before being called "allowed."
- **Findings carry stable codes** so checkpoints can be diffed automatically. Each has `fixable_on_platform`, `where_to_fix` and `source`.
- **Decision rules are data with sources, not prompt text** (PLAN.md §5).
- **Whatever the platform is, if the crawler test fails, that is the finding.** Measure, never look up.
- **On a `decline`: document it, set the status with a reason, stop.** Print what a migration pitch would cover. Do not generate the pitch — a person types `yes` first.

## Audit

- Two run types: `visibility` (category questions) and `accuracy` (brand questions, only after profile v2 exists).
- **Manual is the default and stays the primary instrument permanently.** Batch is a separate, cheaper, clearly labelled series — never merged into the same number. API answers overlap the real interface by only 15–32% on which brands get named.
- Batch is not built until manual has run cleanly ten times, and always needs a cost card.
- Every result records its location context, engine version **and reasoning mode**, whether retrieval activated, whether any business was named, and whether ours was recommended rather than merely mentioned.
- The frozen question set is versioned and never edited. Every checkpoint reuses it.

## Measurement honesty

These rules exist to stop us overstating what we measured.

- **Every score ships with a range. Never report per-question numbers as findings.**
- Report both denominators — all answers, and answers naming at least one business.
- The before/after design detects roughly a 20-point change and not less. Say so up front.
- A single day's per-brand rate carries a ±63 percentage point confidence interval.
- **Optimise narrow, measure broad.** Never report only the prompts we optimised for.
- The deterministic proof is the findings diff — same input, same answer, no sampling.

## Recommendations

- **Scope: on-page AEO only.** Off-site work is named in the document but not executed by the script.
- Priority order comes from the audit, not from our instincts.
- JSON-LD and llms.txt ship **last**, as cheap hygiene, labelled unproven. They are the weakest levers in the evidence base.
- Platform routing comes from `research/PLATFORM_DELIVERY.md` as data, not prompt text.
- **A recommendation may be a refusal** — "this cannot be done on your platform, revert to client" is a valid, useful output.
- Every content draft starts as `pending` and needs a human edit.

## Scoring

- Plain Python in `scoring/scoring.py`; weights and tiers in `scoring/config.yaml`. An LLM never computes a final score.
- Every score stores its `formula_version`.
- Tiers are defined by their **lower edge**: below 20 Invisible · below 40 Barely Visible · below 60 Partially Visible · below 80 Visible · 80–100 Dominant. (19.4 is Invisible.)

## Statuses

`prospect` · `client` · `delivered` · `rejected` (**they** said no) · `declined` (**we** said no).

**Every move to `declined` or `rejected` needs a reason.** `aeo status ... declined|rejected` refuses to run without `--reason "..."`. The reason is written to `client_journal` as a `note` linked to the automatic `status_change` row.

## Database

- Postgres 16 in Docker on **port 5434** (5432 and 5433 are taken on this Mac). Dev `aeo`, test `aeo_test`.
- 13 tables (PLAN.md §9). uuid primary keys, fixed value sets enforced by the database, `created_at` everywhere.
- **Nothing is ever deleted.**
- Migrations are append-only, protected by SHA-256 checksums. Never edit an applied migration.
- Triggers: one logs every `businesses.status` change to `client_journal`; one keeps `updated_at` current.
- **Tests only ever touch `aeo_test`.** Tests refuse to run without `TEST_DATABASE_URL`.

## Storage

Every report kept forever, per checkpoint, under `reports/<business-slug>/<checkpoint>/`. Files are for humans; the database is the record. Both are written.

## Lessons from the build (do not repeat these)

- `migrate.py` once silently rolled back every migration (fixed with `autocommit = True`). After any migration, **verify in Postgres directly** — not from the script's output — and show me the query you used.
- Tests once wrote fake rows into the dev database. That is why `aeo_test` exists.
- Never invent numbers. Model prices come only from `core/model_prices.json`. If a real value is unknown, stop and ask.

## Never

- Never commit `.env` or any API key. Before every commit, check that `.env` is not staged.
- Never commit or push — git is the human's job.
- Never write to a client's live website.
- Never send, publish, or auto-approve anything.
- Never promise an outcome we cannot deliver. We cannot guarantee an AI will name a business — nobody can (PLAN.md §6).
- Never schedule or automate a step that has not run successfully by hand ten times.
- Never delete a row.

## Commands

```bash
source .venv/bin/activate
docker compose up -d                 # Postgres on 5434
pytest                               # needs TEST_DATABASE_URL

aeo diagnose <url> [--business <id>] [--checkpoint <name>]
aeo audit --business <id> [--mode manual|batch] [--checkpoint <name>]
aeo recommend --business <id>
aeo add <url>
aeo seed <folder>
aeo status --business <id> <status> [--reason "text"]
aeo mark-sent | mark-deployed | mark-published --business <id>
aeo note --business <id> "text"
aeo queue
```
