-- Contadores agregados substituem milhões de decisões individuais já concluídas.
CREATE TABLE IF NOT EXISTS etl.funnel_metrics (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  rejected bigint NOT NULL DEFAULT 0,
  rejected_below_score bigint NOT NULL DEFAULT 0,
  rejected_pre_enrichment bigint NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO etl.funnel_metrics (singleton)
VALUES (true)
ON CONFLICT (singleton) DO NOTHING;

DROP INDEX IF EXISTS etl.idx_candidate_decisions_rejected_contact_time;
DROP INDEX IF EXISTS etl.idx_candidate_decisions_rejected_email;

ALTER TABLE etl.candidate_decisions
  DROP COLUMN IF EXISTS razao_social,
  DROP COLUMN IF EXISTS nome_fantasia,
  DROP COLUMN IF EXISTS telefone,
  DROP COLUMN IF EXISTS email;
