-- PLAN.md step 25h: a business can have several names.
-- businesses.name stays the current canonical name (every report, dossier and comparison already reads it). This table keeps
-- every name the business has had or been known by, who set it and when. Rows are never edited or deleted: changing the
-- canonical name, or adding an alias, is a new row.
--   role 'canonical'  the name we use for the business. The newest one is the current one (it matches businesses.name).
--   role 'alias'      another name the business is written under (the website's, or a shorter form).
-- The domain is always an alias too, but it is not stored here: it is read from businesses.domain, so it can never be missing.
-- (business_profiles.alternate_names, an older column nothing fills in, is left exactly as it was.)

CREATE TABLE business_names (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seq BIGINT GENERATED ALWAYS AS IDENTITY,   -- orders rows made in the same instant
    business_id UUID NOT NULL REFERENCES businesses(id),
    name TEXT NOT NULL CHECK (btrim(name) <> ''),
    role TEXT NOT NULL CHECK (role IN ('canonical', 'alias')),
    set_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_business_names_business ON business_names (business_id, seq);

CREATE OR REPLACE FUNCTION forbid_business_names_edits()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'business_names rows are never edited or deleted (row %): add a new name instead', OLD.id;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_business_names_no_edit
    BEFORE UPDATE OR DELETE ON business_names
    FOR EACH ROW
    EXECUTE FUNCTION forbid_business_names_edits();

-- A canonical row for every business the moment it is added with a name, and one for every later change of businesses.name,
-- whoever makes it (aeo names, aeo add, or raw SQL). `aeo names` passes the person through a transaction-local setting;
-- with none, it is 'system'.
CREATE OR REPLACE FUNCTION log_business_name()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.name IS NOT NULL AND btrim(NEW.name) <> ''
       AND (TG_OP = 'INSERT' OR NEW.name IS DISTINCT FROM OLD.name) THEN
        INSERT INTO business_names (business_id, name, role, set_by)
        VALUES (NEW.id, NEW.name, 'canonical', COALESCE(NULLIF(current_setting('aeo.name_set_by', true), ''), 'system'));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_businesses_name_insert
    AFTER INSERT ON businesses
    FOR EACH ROW
    EXECUTE FUNCTION log_business_name();

CREATE TRIGGER trg_businesses_name_update
    AFTER UPDATE ON businesses
    FOR EACH ROW
    EXECUTE FUNCTION log_business_name();

-- Businesses that already exist: their current name is the first canonical row. Nothing before today is lost, because the
-- name was never changed before today.
INSERT INTO business_names (business_id, name, role, set_by, created_at)
SELECT b.id, b.name, 'canonical', 'system (backfilled)', b.created_at
FROM businesses b
WHERE b.name IS NOT NULL AND btrim(b.name) <> ''
ORDER BY b.created_at, b.id;
