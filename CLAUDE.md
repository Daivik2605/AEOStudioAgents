
# AEOStudioAgents

Read PLAN.md first. It defines what we are building, the four prototype agents, the architecture, the database, and the build order. This file is about how to work in this repo.

## How to work with me

- Build one thing at a time. Stop after each and wait for review. Do not build ahead of what was asked.

- Before you start, say briefly what you are about to do. After you finish, say what you created and any decision you made that PLAN.md did not specify.

- If something is ambiguous or missing from PLAN.md, ask. Do not guess and continue.

- I am learning this codebase as it is built. Prefer clear, obvious code over clever code, and comment anything non-obvious.

- When I ask a question about the code, answer the question. Do not start editing unless I ask you to.

- Always speak in simple language. No jargon unless it is necessary.

## Prototype scope

We are building four agents: `business-profiler`, `ai-visibility-probe`, `aeo-audit`, `aeo-recommendations`.

Build order: scaffold → core → scoring → seed O'land profile → ai-visibility-probe (manual) → aeo-audit → aeo-recommendations → business-profiler. The audit is the top priority.

Do not build anything outside this list until the four are done and tested.

## Architecture conventions

- One shared harness in `core/`. Each agent is a definition file — prompt, Pydantic schema, tool list, model — not a standalone program.

- No agent frameworks (LangChain, CrewAI). Use the Anthropic SDK with a simple tool-use loop in `core/llm.py`.

- Every LLM call goes through `core/llm.py` and is logged with prompt version, tokens, cost, and latency. No exceptions.

- Scores and thresholds are plain Python functions in `scoring/scoring.py`. An LLM never computes a final score.

- Every agent returns a Pydantic model with structured named fields. No raw prose output fields.

- The probe stores full AI answers. Scores are derived from them by a versioned formula.

- Nothing is hardcoded to an industry, city, or language. Everything reads from the Business Profile. O'land exists only in seed data — never in code, prompts or templates.

## Approval gate

Every business must pass a gate check before any API spend. The gate is a CLI step — it never runs automatically.

On pass: print a cost card showing exact queries, engines, call count, model per call, and ceiling cost (worst-case max, not an estimate). Wait for `yes` before continuing. No auto-approve.

On rejection: store the business with `status = rejected` and a reason tag. Do not delete it.

## Probe modes

The probe has two modes. **Manual is the default.**

- `manual`: generate queries, print them for the human to run by hand, wait for pasted answers.
- `automatic`: call AI engine APIs directly. Requires cost card approval. Do not build automatic mode until manual has run successfully ten times.

## Database

- Local development runs Postgres in Docker. Supabase comes later; the SQL is the same.
- Migrations are append-only. Never edit an applied migration — add a new numbered one.
- 12 tables are defined in PLAN.md section 9. Write migrations for all of them, including the status-change trigger, in the scaffold step.

## Prompts

- Prompts live in `prompts/<agent>/v1.md`, `v2.md`, etc.
- Never edit a versioned prompt file after it has run in production. Add a new version instead.
- Every LLM call logs which prompt version it used.
- Golden test examples live in `prompts/golden/<agent>/`.

## Never

- Never commit `.env` or any API key.
- Never write to a client's live website. Schema and content are generated for a human to deploy.
- Never send, publish, or auto-approve anything. Agents draft; people decide.
- Never schedule or automate a step that has not run successfully by hand.
- Never run the probe in automatic mode without a cost card approval first.
- Never build outside the four prototype agents until they are all done and reviewed.

## Phase 2 items (do not build now)

- `confidence` and `evidence` fields on agent outputs
- Human queue routing based on confidence thresholds
- Automatic probe batch mode
- Outreach, proposals, weekly reports, client journal, case studies, team ops agent
- Any scheduling

## Commands

```bash
source .venv/bin/activate    # activate the Python environment
docker compose up            # start local Postgres
pytest                       # run tests
aeo probe --mode manual --business <id>    # run probe, manual mode
aeo audit --business <id>                  # run aeo-audit
aeo recommend --business <id>              # run aeo-recommendations
aeo queue                                  # show human_queue items
```
