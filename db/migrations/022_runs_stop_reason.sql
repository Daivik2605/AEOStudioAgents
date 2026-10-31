-- PLAN.md step 25f: why the model stopped. The API's own word: end_turn, max_tokens, tool_use, ...
-- A reply that stopped at the output cap (max_tokens) is cut off: core/llm.py logs it with status 'failed'
-- and this value, so a cut-off can be told apart from a bad reply and from a real success.
-- Runs made before this migration keep NULL: nothing is guessed about how they ended.
ALTER TABLE runs ADD COLUMN stop_reason TEXT;
