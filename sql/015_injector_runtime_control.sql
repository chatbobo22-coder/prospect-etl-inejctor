-- Telemetria e controle de execucoes iniciadas pelo MestreLead.
ALTER TABLE etl.runs
  ADD COLUMN IF NOT EXISTS workflow_run_id bigint,
  ADD COLUMN IF NOT EXISTS cancel_requested_at timestamptz;

ALTER TABLE etl.runs DROP CONSTRAINT IF EXISTS runs_status_check;
ALTER TABLE etl.runs
  ADD CONSTRAINT runs_status_check
  CHECK (status IN ('running','success','failed','skipped','cancelled'));

CREATE INDEX IF NOT EXISTS idx_etl_runs_workflow_run_id
  ON etl.runs (workflow_run_id)
  WHERE workflow_run_id IS NOT NULL;

