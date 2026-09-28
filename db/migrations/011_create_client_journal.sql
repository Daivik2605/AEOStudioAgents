CREATE TABLE client_journal (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    entry_type TEXT NOT NULL
        CHECK (entry_type IN (
            'business_added', 'status_change', 'profile_created', 'questionnaire_received',
            'gate_checked', 'gate_decision', 'probe_started', 'probe_completed',
            'score_computed', 'audit_generated', 'audit_sent', 'recommendations_generated',
            'schema_deployed', 'content_published', 'note', 'error'
        )),
    description TEXT NOT NULL,
    actor TEXT NOT NULL,
    related_table TEXT,
    related_id UUID,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
