-- Funil de retenção: dados brutos são triagem; somente A/B permanecem como leads.

CREATE TABLE IF NOT EXISTS etl.candidate_decisions (
  cnpj char(14) PRIMARY KEY,
  cnpj_basico char(8) NOT NULL,
  decision text NOT NULL CHECK (decision IN ('qualified_a', 'qualified_b', 'rejected')),
  profile_score smallint NOT NULL DEFAULT 0 CHECK (profile_score BETWEEN 0 AND 100),
  lead_score smallint NOT NULL DEFAULT 0 CHECK (lead_score BETWEEN 0 AND 100),
  data_confidence_score smallint NOT NULL DEFAULT 0 CHECK (data_confidence_score BETWEEN 0 AND 10),
  razao_social text,
  nome_fantasia text,
  telefone text,
  email text,
  reason_codes text[] NOT NULL DEFAULT '{}',
  source_competence text,
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  next_review_at timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_candidate_decisions_review
  ON etl.candidate_decisions (next_review_at)
  WHERE next_review_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_candidate_decisions_result
  ON etl.candidate_decisions (decision, profile_score DESC);

-- A interface comercial nunca deve enxergar rejeitados ou registros em revisão.
DROP VIEW IF EXISTS cnpj.v_prospectos_outreach_v3;
CREATE VIEW cnpj.v_prospectos_outreach_v3 AS
SELECT p.*
FROM cnpj.prospectos_qualificados p
WHERE p.qualification_status = 'qualified'
  AND p.lead_quality IN ('A', 'B')
ORDER BY p.lead_quality, p.lead_score DESC NULLS LAST,
         p.confidence_score DESC NULLS LAST;
