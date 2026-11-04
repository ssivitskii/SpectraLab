CREATE TABLE IF NOT EXISTS bi_records (
    created_at timestamptz NOT NULL,
    status text NOT NULL,
    config_name text NOT NULL,
    evidence text NOT NULL,
    elapsed_seconds double precision,
    completed integer NOT NULL,
    total integer NOT NULL
);

CREATE OR REPLACE VIEW bi_status AS
SELECT status, count(*) AS jobs FROM bi_records GROUP BY status;

CREATE OR REPLACE VIEW bi_elapsed AS
SELECT status, count(*) AS jobs, round(avg(elapsed_seconds)::numeric, 2) AS avg_elapsed_seconds
FROM bi_records WHERE elapsed_seconds IS NOT NULL GROUP BY status;

CREATE OR REPLACE VIEW bi_total AS
SELECT count(*) AS jobs FROM bi_records;

CREATE OR REPLACE VIEW bi_detail AS
SELECT created_at, status, config_name, evidence, elapsed_seconds, completed, total FROM bi_records;
