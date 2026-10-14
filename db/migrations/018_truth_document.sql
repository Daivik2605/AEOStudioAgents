-- PLAN.md step 13 / section 16 (the truth document).
--
-- business_profiles already has one row per version, but its fixed fact columns (phone, address ...) cannot
-- say WHO confirmed a fact and WHEN, or whether anyone did. So a truth-document version keeps its facts in
-- two JSON columns. The old fact columns are left as they are and are no longer read by claim checking.
--
--   facts  [{label, value, status ('confirmed'|'unconfirmed'), confirmed_by, confirmed_on}]
--          Only 'confirmed' entries are ever used to judge an AI's answer.
--   story  {description, goals, customers, customer_questions}   free text, never used for scoring
--
-- Both are NULL on profiles that did not come from a truth document (the empty version 1 made by `aeo add`).
ALTER TABLE business_profiles
    ADD COLUMN facts JSONB,
    ADD COLUMN story JSONB;

-- A profile version is a record of what was known at that time, and audits are judged against it later.
-- So once saved, its facts and story can never change, and no profile can be deleted. A correction is a NEW version.
CREATE OR REPLACE FUNCTION forbid_profile_history_edits()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'business_profiles rows are never deleted (profile % version %)', OLD.id, OLD.version;
    END IF;
    IF NEW.facts IS DISTINCT FROM OLD.facts OR NEW.story IS DISTINCT FROM OLD.story THEN
        RAISE EXCEPTION 'facts and story of a saved profile version cannot be changed: save a new version instead (profile % version %)',
            OLD.id, OLD.version;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_business_profiles_no_edit
    BEFORE UPDATE ON business_profiles
    FOR EACH ROW
    EXECUTE FUNCTION forbid_profile_history_edits();

CREATE TRIGGER trg_business_profiles_no_delete
    BEFORE DELETE ON business_profiles
    FOR EACH ROW
    EXECUTE FUNCTION forbid_profile_history_edits();
