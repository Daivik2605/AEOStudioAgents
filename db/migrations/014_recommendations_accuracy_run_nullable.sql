-- PLAN.md section 9, "Fix to recommendations": migration 008 made
-- accuracy_probe_run_id NOT NULL, but a client may be recommended-for before
-- any accuracy run exists (accuracy runs only start after profile v2).
-- visibility_probe_run_id and the other NOT NULL columns stay as they are.
ALTER TABLE recommendations ALTER COLUMN accuracy_probe_run_id DROP NOT NULL;
