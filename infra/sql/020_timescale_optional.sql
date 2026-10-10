-- OPTIONAL: TimescaleDB for the two high-volume time-series tables. Only if the timescaledb extension is installed
-- on your PostgreSQL server. Without it the scheduler's retention job (RETENTION_HEARTBEATS_DAYS) keeps them small.
-- Hypertables need the time column in every unique index, so the surrogate primary keys are dropped first.
CREATE EXTENSION IF NOT EXISTS timescaledb;

ALTER TABLE ea_heartbeats DROP CONSTRAINT IF EXISTS ea_heartbeats_pkey;
SELECT create_hypertable('ea_heartbeats', 'ts', if_not_exists => TRUE, migrate_data => TRUE);
SELECT add_retention_policy('ea_heartbeats', INTERVAL '14 days', if_not_exists => TRUE);

ALTER TABLE candles DROP CONSTRAINT IF EXISTS candles_pkey;
SELECT create_hypertable('candles', 'ts', if_not_exists => TRUE, migrate_data => TRUE);
