-- PLAN.md step 25: what the diagnosis found about the site's setup, beyond the platform.
-- detected_cdn / detected_host / detected_framework come from the response headers of the main page
-- (diagnose/infrastructure_rules.yaml). NULL means unknown. They are information, not findings.
-- site_checks holds the raw result of the sitemap, www / http address and (optional) old-address checks, and a
-- `checks_ran` list. Compare needs that list: a finding that is absent only counts as fixed if its check ran.
-- Diagnoses made before this migration keep NULL in all four columns: nothing is guessed about them.
ALTER TABLE diagnoses
    ADD COLUMN detected_cdn TEXT,
    ADD COLUMN detected_host TEXT,
    ADD COLUMN detected_framework TEXT,
    ADD COLUMN site_checks JSONB;
