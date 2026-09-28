CREATE TABLE audits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    business_profile_id UUID NOT NULL REFERENCES business_profiles(id),
    probe_run_id UUID NOT NULL REFERENCES probe_runs(id),
    mode TEXT NOT NULL DEFAULT 'initial'
        CHECK (mode IN ('initial', 'reaudit')),
    comparison_probe_run_id UUID REFERENCES probe_runs(id),
    audit_data JSONB NOT NULL,
    pdf_path TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'sent')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
