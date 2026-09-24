-- Arquivo mínimo e privado de contatos rejeitados pelo funil de qualidade.
-- Os dados pesados continuam sujeitos à retenção definida na migration 016.

ALTER TABLE etl.candidate_decisions
  ADD COLUMN IF NOT EXISTS lead_score smallint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS razao_social text,
  ADD COLUMN IF NOT EXISTS nome_fantasia text,
  ADD COLUMN IF NOT EXISTS telefone text,
  ADD COLUMN IF NOT EXISTS email text;

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

-- Recupera o contato de decisões antigas enquanto o cadastro bruto ainda existir.
UPDATE etl.candidate_decisions decision
SET
  razao_social = COALESCE(decision.razao_social, company.razao_social),
  nome_fantasia = COALESCE(decision.nome_fantasia, establishment.nome_fantasia),
  telefone = COALESCE(
    decision.telefone,
    NULLIF(
      regexp_replace(
        COALESCE(establishment.ddd1, '') || COALESCE(establishment.telefone1, ''),
        '[^0-9]',
        '',
        'g'
      ),
      ''
    )
  ),
  email = COALESCE(
    decision.email,
    NULLIF(lower(btrim(establishment.correio_eletronico)), '')
  )
FROM cnpj.estabelecimentos establishment
LEFT JOIN cnpj.empresas company
  ON company.cnpj_basico = establishment.cnpj_basico
WHERE decision.cnpj = establishment.cnpj
  AND decision.decision = 'rejected'
  AND (
    decision.razao_social IS NULL
    OR decision.nome_fantasia IS NULL
    OR decision.telefone IS NULL
    OR decision.email IS NULL
  );

CREATE INDEX IF NOT EXISTS idx_candidate_decisions_rejected_contact_time
  ON etl.candidate_decisions (evaluated_at DESC)
  WHERE decision = 'rejected';

CREATE INDEX IF NOT EXISTS idx_candidate_decisions_rejected_email
  ON etl.candidate_decisions (lower(email))
  WHERE decision = 'rejected' AND email IS NOT NULL;
