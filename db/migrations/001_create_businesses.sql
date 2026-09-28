CREATE TABLE businesses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT,
    domain TEXT NOT NULL UNIQUE,
    website_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'prospect'
        CHECK (status IN ('prospect', 'client', 'delivered', 'rejected', 'declined')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
