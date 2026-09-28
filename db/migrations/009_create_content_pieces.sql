CREATE TABLE content_pieces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id UUID NOT NULL REFERENCES recommendations(id),
    target_query TEXT,
    cited_url TEXT,
    draft_markdown TEXT,
    edit_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (edit_status IN ('pending', 'approved', 'rejected')),
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
