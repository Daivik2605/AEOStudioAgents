# AEOStudioAgents

Read PLAN.md first. **PLAN.md (v3.2) is the source of truth.** If this file and PLAN.md disagree, PLAN.md wins — say so, and ask. This file is about how to work in this repo.

*Last updated 29 Sep 2026 — brought in line with PLAN.md v3.2 (statuses, gate, commands, build order, database rules, lessons learned).*

## How to work with me

- Build one thing at a time. Stop after each and wait for review. Do not build ahead of what was asked.
- Before you start, say briefly what you are about to do. After you finish, say what you created and any decision you made that PLAN.md did not specify.
- If something is ambiguous or missing from PLAN.md, ask. Do not guess and continue.
- I am learning this codebase as it is built. Prefer clear, obvious code over clever code, and comment anything non-obvious.
- When I ask a question about the code, answer the question. Do not start editing unless I ask you to.
- Always speak in simple language. No jargon unless it is necessary.
- Git is mine. Never commit or push. Tell me the commands and I run them.

## Prototype scope

We are building four agents: `business-profiler`, `ai-visibility-probe`, `aeo-audit`, `aeo-recommendations`. The audit is the top priority.

Do not build anything outside these four until they are done and tested.

## Build order (PLAN.md section 19)

| # | Step | Status |
|---|---|---|
| 0 | Scaffold: folders, pyproject, Docker Postgres, migrations 001–013, triggers | Done |
| 1 | `core/`: db, llm (logs every call), journal, runner, model prices | Done |
| 2 | `scoring/`: five formulas, tier tests, separate `aeo_test` DB | Done (fixes pending) |
| 3 | Seed O'land: `aeo add` + hand-written `seed/oland_stations/profile_v1.json` | Next |
| 4 | ai-visibility-probe: manual mode, visibility run | |
| 5 | aeo-audit: PDF | |
| 6 | Real O'land audit (baseline), then a dental practice through the same steps | |
| 7 | aeo-recommendations | |
| 8 | Accuracy run type (after O'land's questionnaire) | |
| 9 | business-profiler: replaces hand-written profiles | |
| 10 | Re-audit and compare | |

## Architecture conventions

- One shared harness in `core/`. Each agent is a definition — prompt, Pydantic schema, tool list, model — not a standalone program.
- No agent frameworks (LangChain, CrewAI). Use the Anthropic SDK with a simple tool-use loop in `core/llm.py`.
- Every LLM call goes through `core/llm.py` and is logged in `runs` with prompt version, tokens, cost and latency — including failures, before the call returns. No exceptions.
- Every agent returns a Pydantic model with structured named fields. On validation failure: retry once with the error, then write to `human_queue` and stop.
- The probe stores full raw AI answers and the sources they cite. Names and claim checks are derived from them and can be re-run.
- All journal writes go through the one function in `core/`.
- Nothing is hardcoded to an industry, city, or language. Everything reads from the Business Profile. O'land exists only in seed data — never in code, prompts or templates. No mention of O'land, events, water or Montreal outside `seed/`.

## Scoring

- Scores and tiers are plain Python in `scoring/scoring.py`; weights and tier bands in `scoring/config.yaml`. An LLM never computes a final score.
- Every score stores its `formula_version`.
- Tiers are defined by their **lower edge**, because scores are decimals: below 20 Invisible · below 40 Barely Visible · below 60 Partially Visible · below 80 Visible · 80–100 Dominant. (19.4 is Invisible.)

## Statuses (on `businesses`)

`prospect`, `client`, `delivered`, `rejected`, `declined`.

- `rejected` = **they** said no to us.
- `declined` = **we** said no to them (at the gate or any time later).
- **Every move to `declined` or `rejected` needs a reason.** `aeo status ... declined|rejected` refuses to run without `--reason "..."`. The reason is written to `client_journal` as a `note` linked to the automatic `status_change` row (PLAN.md section 5). Gate declines also store it in `gate_checks.decision_reason`.
- Progress within a stage (profiled, gated, probed, audited) is not a status.

## Approval gate and cost card

- **Business checks** (plain Python, not AI) run on the **first visibility run** only: site reachable, real and active business, site editable.
- **Cost card + typed `yes`** is required before **every probe run**, both run types, manual or automatic. No auto-approve.
- The cost card shows facts, not estimates: exact questions, engines, call count with the arithmetic shown, model per call, and ceiling cost (worst case). In manual mode it lists only our own model calls.
- On decline: `gate_checks.decision = declined` with a reason, business status becomes `declined`, reason journaled. Nothing is deleted.

## Probe

- Two run types: `visibility` (category questions) and `accuracy` (brand questions, only after profile v2 exists).
- Two modes. **Manual is the default**: print the questions, a person asks each engine by hand and pastes the answers back.
- `automatic` is not built until manual has run cleanly ten times, and always needs a cost card approval.
- Every result records its location context.

## Database

- Postgres 16 in Docker on **port 5434** (5432 and 5433 are taken on this Mac). Dev database `aeo`, test database `aeo_test`. Supabase later; same SQL.
- 12 tables, defined in PLAN.md section 9. uuid primary keys, fixed value sets enforced by the database, `created_at` everywhere.
- **Nothing is ever deleted.**
- Migrations are append-only and protected by SHA-256 checksums. Never edit an applied migration — add a new numbered one.
- Triggers: one logs every `businesses.status` change to `client_journal`; one keeps `updated_at` current.
- **Tests only ever touch `aeo_test`.** Tests refuse to run without `TEST_DATABASE_URL`.

## Lessons from the build (do not repeat these)

- `migrate.py` once silently rolled back every migration (fixed with `autocommit = True`). After any migration, **verify in Postgres directly** — not from the script's output — and show me the query you used.
- Tests once wrote fake rows into the dev database. That is why `aeo_test` exists. Never point tests at `aeo`.
- Never invent numbers. Model prices come only from `core/model_prices.json`, checked against Anthropic's pricing page. If a real value is unknown, stop and ask.

## Prompts

- Prompts live in `prompts/<agent>/v1.md`, `v2.md`, etc.
- Never edit a prompt version after it has run. Add a new version.
- Every LLM call logs which prompt version it used.
- Golden test examples live in `prompts/golden/<agent>/`.

## Never

- Never commit `.env` or any API key. Before every commit, check that `.env` is not staged.
- Never commit or push — git is the human's job.
- Never write to a client's live website. Schema and content are generated for a human to deploy.
- Never send, publish, or auto-approve anything. Agents draft; people decide.
- Never schedule or automate a step that has not run successfully by hand ten times.
- Never run the probe in automatic mode without a cost card approval first.
- Never delete a row.
- Never build outside the four prototype agents until they are all done and reviewed.

## Later phases (do not build now)

- `confidence` and `evidence` fields on agent outputs, and routing by confidence
- Automatic probe mode and batch runs, budgets
- Outreach, proposals, weekly reporter, case study generator, team ops agent
- Any scheduling

## Commands

```bash
source .venv/bin/activate          # activate the Python environment
docker compose up -d               # start local Postgres (port 5434)
pytest                             # run tests (needs TEST_DATABASE_URL)

aeo add <url>                      # add a business (prospect)
aeo seed <folder>                  # load a hand-written profile, e.g. seed/oland_stations
aeo profile --business <id>        # business-profiler
aeo gate --business <id>           # business checks + cost card
aeo probe --business <id> --type visibility|accuracy [--mode manual|automatic]
aeo audit --business <id> [--reaudit]
aeo recommend --business <id>
aeo status --business <id> <status> [--reason "text"]   # --reason required for declined/rejected
aeo mark-sent | mark-deployed | mark-published --business <id>
aeo note --business <id> "text"
aeo queue                          # show human_queue items
```
