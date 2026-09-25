-- Telemetria e controle de execucoes iniciadas pelo MestreLead.
ALTER TABLE etl.runs
  ADD COLUMN IF NOT EXISTS workflow_run_id bigint,
  ADD COLUMN IF NOT EXISTS cancel_requested_at timestamptz;

-- Heartbeats leves para que o painel acompanhe arquivos grandes sem depender
-- da liberação tardia dos logs brutos do GitHub Actions.
ALTER TABLE etl.files
  ADD COLUMN IF NOT EXISTS downloaded_bytes bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS scanned_rows bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS skipped_rows bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS activity_at timestamptz;

ALTER TABLE etl.enrichment_runs
  ADD COLUMN IF NOT EXISTS activity_at timestamptz;

ALTER TABLE intelligence.source_runs
  ADD COLUMN IF NOT EXISTS activity_at timestamptz;

ALTER TABLE etl.runs DROP CONSTRAINT IF EXISTS runs_status_check;
ALTER TABLE etl.runs
  ADD CONSTRAINT runs_status_check
  -- Migrations are replayed on every pipeline start. Keep states introduced by
  -- later migrations here too, otherwise replaying 015 rejects paused runs
  -- before 024 has a chance to recreate the constraint.
  CHECK (status IN ('running','success','failed','skipped','cancelled','paused'));

CREATE INDEX IF NOT EXISTS idx_etl_runs_workflow_run_id
  ON etl.runs (workflow_run_id)
  WHERE workflow_run_id IS NOT NULL;
