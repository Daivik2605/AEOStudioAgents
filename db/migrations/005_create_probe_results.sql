CREATE TABLE probe_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    probe_run_id UUID NOT NULL REFERENCES probe_runs(id),
    query_text TEXT NOT NULL,
    query_language TEXT,
    location_context TEXT,
    engine TEXT NOT NULL,
    repeat_number INTEGER NOT NULL DEFAULT 1,
    raw_response_text TEXT,
    sources_cited JSONB,
    businesses_named JSONB,
    recognized BOOLEAN,
    claims_checked JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
