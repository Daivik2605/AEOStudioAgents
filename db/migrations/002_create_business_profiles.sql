CREATE TABLE business_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    version INTEGER NOT NULL,
    source TEXT NOT NULL
        CHECK (source IN ('website', 'client')),
    industry TEXT,
    schema_type TEXT,
    customer_type TEXT
        CHECK (customer_type IN ('consumers', 'businesses', 'both')),
    reach TEXT
        CHECK (reach IN ('local', 'regional', 'national', 'online')),
    offering TEXT,
    services TEXT[],
    buyer_description TEXT,
    location_city TEXT,
    location_region TEXT,
    location_country TEXT,
    service_area TEXT[],
    address TEXT,
    phone TEXT,
    email TEXT,
    hours JSONB,
    social_profiles TEXT[],
    languages TEXT[],
    alternate_names TEXT[],
    apparent_competitors TEXT[],
    proof_points TEXT[],
    questions JSONB,
    questionnaire_answers JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (business_id, version)
);
