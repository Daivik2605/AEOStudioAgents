-- Writes a status_change row to client_journal whenever businesses.status changes,
-- even if the change happens through raw SQL rather than the application.
CREATE OR REPLACE FUNCTION log_business_status_change()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO client_journal (business_id, entry_type, description, actor, related_table, related_id, details)
        VALUES (
            NEW.id,
            'status_change',
            'Status changed from ' || OLD.status || ' to ' || NEW.status,
            'system',
            'businesses',
            NEW.id,
            jsonb_build_object('from', OLD.status, 'to', NEW.status)
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_businesses_status_change
    AFTER UPDATE ON businesses
    FOR EACH ROW
    EXECUTE FUNCTION log_business_status_change();

-- Keeps businesses.updated_at current on every update, so it doesn't rely on
-- application code remembering to set it.
CREATE OR REPLACE FUNCTION set_businesses_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_businesses_updated_at
    BEFORE UPDATE ON businesses
    FOR EACH ROW
    EXECUTE FUNCTION set_businesses_updated_at();
