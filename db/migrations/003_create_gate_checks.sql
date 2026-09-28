CREATE TABLE gate_checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    run_type TEXT NOT NULL
        CHECK (run_type IN ('visibility', 'accuracy')),
    passed BOOLEAN NOT NULL,
    signals JSONB,
    cost_card JSONB,
    ceiling_cost_usd NUMERIC,
    decision TEXT NOT NULL DEFAULT 'pending'
        CHECK (decision IN ('pending', 'approved', 'declined')),
    decision_reason TEXT,
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
