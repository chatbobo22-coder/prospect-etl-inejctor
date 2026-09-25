-- Registro mínimo dos CNPJs já avaliados. Substitui decisões detalhadas por
-- uma chave compacta, evitando reler e regravar rejeitados em ciclos futuros.
CREATE TABLE IF NOT EXISTS etl.processed_candidates (
  cnpj char(14) PRIMARY KEY,
  cnpj_basico char(8) NOT NULL,
  decision text NOT NULL,
  next_review_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_processed_candidates_review
  ON etl.processed_candidates (next_review_at);
