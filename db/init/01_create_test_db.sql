-- Runs automatically the first time the postgres container initializes an
-- empty data directory (see docker-compose.yml's volume mount for this
-- folder). aeo_test holds the exact same schema as aeo (via db/migrate.py)
-- but is only ever touched by the test suite, so tests never leave rows
-- behind in the real dev database.
CREATE DATABASE aeo_test;
