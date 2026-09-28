CREATE TABLE runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT,
    input JSONB,
    output JSONB,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd NUMERIC,
    latency_ms INTEGER,
    status TEXT NOT NULL
        CHECK (status IN ('success', 'failed')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
