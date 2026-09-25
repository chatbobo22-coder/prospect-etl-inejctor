-- Score temporário da decisão. Contatos rejeitados não são arquivados: apenas
-- os contadores agregados da migration 025 permanecem.

ALTER TABLE etl.candidate_decisions
  ADD COLUMN IF NOT EXISTS lead_score smallint NOT NULL DEFAULT 0;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'etl.candidate_decisions'::regclass
      AND conname = 'candidate_decisions_lead_score_check'
  ) THEN
    ALTER TABLE etl.candidate_decisions
      ADD CONSTRAINT candidate_decisions_lead_score_check
      CHECK (lead_score BETWEEN 0 AND 100);
  END IF;
END
$$;
