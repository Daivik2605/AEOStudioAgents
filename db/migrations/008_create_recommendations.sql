CREATE TABLE recommendations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    business_profile_id UUID NOT NULL REFERENCES business_profiles(id),
    visibility_probe_run_id UUID NOT NULL REFERENCES probe_runs(id),
    accuracy_probe_run_id UUID NOT NULL REFERENCES probe_runs(id),
    priority_fixes JSONB,
    fact_fixes JSONB,
    off_site_sources JSONB,
    existing_jsonld JSONB,
    existing_schema_issues JSONB,
    generated_jsonld JSONB,
    llms_txt TEXT,
    validation_status TEXT,
    validation_errors JSONB,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'reviewed', 'deployed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
