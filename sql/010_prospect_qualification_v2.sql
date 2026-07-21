-- Qualificação de prospects v2

ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS qualification_status text DEFAULT 'qualified';
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS rejection_reasons text[];
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS presence_score smallint DEFAULT 0;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS commerce_score smallint DEFAULT 0;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS fit_score smallint DEFAULT 0;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS pain_score smallint DEFAULT 0;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS confidence_score smallint DEFAULT 0;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS lead_score smallint DEFAULT 0;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS presence_maturity text;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS commerce_maturity text;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS lead_classification text;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS contact_channel text;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS contact_value text;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS contact_confidence smallint DEFAULT 0;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS contact_role text;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS last_qualified_at timestamptz;
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS qualification_version text DEFAULT 'v2';
ALTER TABLE cnpj.prospectos_qualificados ADD COLUMN IF NOT EXISTS site_final_url text;

CREATE INDEX IF NOT EXISTS idx_prospect_qual_status ON cnpj.prospectos_qualificados (qualification_status);
CREATE INDEX IF NOT EXISTS idx_prospect_lead_score ON cnpj.prospectos_qualificados (lead_score DESC);

CREATE OR REPLACE VIEW cnpj.v_prospectos_outreach_v2 AS
SELECT
  p.cnpj,
  p.razao_social,
  p.nome_fantasia,
  COALESCE(p.site_final_url, p.site_url) AS site_final_url,
  p.presence_score,
  p.commerce_score,
  p.fit_score,
  p.pain_score,
  p.confidence_score,
  p.lead_score,
  p.presence_maturity,
  p.commerce_maturity,
  p.lead_classification,
  p.contact_channel,
  p.contact_value,
  p.contact_confidence,
  p.contact_role,
  p.qualification_status,
  p.rejection_reasons,
  p.qualification_reasons,
  p.decisor_nome,
  p.uf,
  p.municipio_descricao,
  p.plataforma,
  p.whatsapp_url,
  p.instagram_url,
  p.last_qualified_at,
  p.updated_at
FROM cnpj.prospectos_qualificados p
ORDER BY p.lead_score DESC NULLS LAST, p.confidence_score DESC NULLS LAST;
