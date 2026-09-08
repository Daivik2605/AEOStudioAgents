
# AEOStudioAgents

Read PLAN.md first. It defines what we are building, the ten agents, the architecture, and the build order. This file is about how to work in this repo.

## How to work with me

- Build one thing at a time. Stop after each and wait for review. Do not build ahead of what was asked.

- Before you start, say briefly what you are about to do. After you finish, say what you created and any decision you made that PLAN.md did not specify.

- If something is ambiguous or missing from PLAN.md, ask. Do not guess and continue.

- I am learning this codebase as it is built. Prefer clear, obvious code over clever code, and comment anything non-obvious.

- When I ask a question about the code, answer the question. Do not start editing unless I ask you to.

## Architecture conventions

- One shared harness in `core/`. Each agent is a definition file — prompt, Pydantic schema, tool list, model — not a standalone program.

- No agent frameworks (LangChain, CrewAI). Our agents are fixed pipelines, not autonomous crews, and we need direct control over prompts, logging, and cost. Use the Anthropic SDK with a simple tool-use loop in `core/llm.py`.

- Every LLM call goes through `core/llm.py` and is logged with prompt version, tokens, cost, and latency.

- Scores and thresholds are plain Python functions. An LLM never computes a final score.

- Every agent returns a Pydantic model with `confidence` and `evidence`. Low-confidence output goes to the human queue, never downstream.

- The probe stores raw AI answers; scores are derived from them by a versioned formula.

- Nothing is hardcoded to an industry, city, or language. Everything reads from the Business Profile.

## Database

- Local development runs Postgres in Docker. Supabase comes later; the SQL is the same.

- Migrations are append-only. Never edit an applied migration — add a new numbered one.

## Never

- Never commit `.env` or any API key.

- Never write to a client's live website. Schema and content are generated for a human to deploy.

- Never send, publish, or auto-approve anything. Agents draft; people decide.

- Never schedule or automate a step that has not been run successfully by hand.

## Commands

```bash

source .venv/bin/activate    # activate the Python environment

docker compose up            # start local Postgres

pytest                       # run tests

```

