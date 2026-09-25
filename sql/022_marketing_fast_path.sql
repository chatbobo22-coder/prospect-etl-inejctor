-- O crawling profundo opera somente sobre leads que já passaram pelo caminho
-- barato de score local + MX. Assim ele nunca bloqueia a publicação no Outreach.
-- A migration 024 compacta esta view. Como todas as migrations são reaplicadas,
-- remova a versão compacta antes de restaurar a versão larga desta etapa.
DROP VIEW IF EXISTS cnpj.v_marketing_enrichment_candidates;
CREATE VIEW cnpj.v_marketing_enrichment_candidates AS
SELECT v.*
FROM cnpj.v_prospect_candidates v
JOIN etl.candidate_decisions d ON d.cnpj=v.cnpj
WHERE d.decision IN ('qualified_a','qualified_b');

CREATE INDEX IF NOT EXISTS idx_candidate_decisions_marketing_enrichment
  ON etl.candidate_decisions (cnpj)
  WHERE decision IN ('qualified_a','qualified_b');

CREATE INDEX IF NOT EXISTS idx_email_verifications_due
  ON intelligence.email_verifications (expires_at,cnpj);
