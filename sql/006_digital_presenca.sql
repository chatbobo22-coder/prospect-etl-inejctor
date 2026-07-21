CREATE TABLE IF NOT EXISTS cnpj.digital_presenca (
  cnpj char(14) PRIMARY KEY,
  cnpj_basico char(8) NOT NULL,
  email_original text,
  email_dominio text,
  email_tipo text,
  site_url text,
  site_ativo boolean,
  site_http_status smallint,
  site_titulo text,
  plataforma text,
  plataformas_detectadas text[],
  plataforma_confianca smallint,
  instagram_url text,
  whatsapp_url text,
  linkedin_url text,
  decisor_nome text,
  decisor_qualificacao text,
  faixa_faturamento_estimada text,
  faturamento_fonte text,
  digital_score smallint,
  digital_maturity text,
  sinais jsonb,
  enrich_status text NOT NULL DEFAULT 'pending'
    CHECK (enrich_status IN ('pending', 'done', 'partial', 'failed', 'no_site')),
  enrich_error text,
  enriched_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_digital_score ON cnpj.digital_presenca (digital_score DESC);
CREATE INDEX IF NOT EXISTS idx_digital_plataforma ON cnpj.digital_presenca (plataforma);
CREATE INDEX IF NOT EXISTS idx_digital_status ON cnpj.digital_presenca (enrich_status);
CREATE INDEX IF NOT EXISTS idx_digital_enriched_at ON cnpj.digital_presenca (enriched_at);

-- A lista de colunas da view pode mudar quando v_bi_varejo evolui.
-- PostgreSQL não permite renomear/reordenar colunas via CREATE OR REPLACE.
DROP VIEW IF EXISTS cnpj.v_prospect_digital;
CREATE VIEW cnpj.v_prospect_digital AS
SELECT
  v.*,
  d.email_dominio,
  d.email_tipo,
  d.site_url,
  d.site_ativo,
  d.site_http_status,
  d.plataforma,
  d.plataformas_detectadas,
  d.instagram_url,
  d.whatsapp_url,
  d.linkedin_url,
  d.decisor_nome,
  d.decisor_qualificacao,
  d.faixa_faturamento_estimada,
  d.faturamento_fonte,
  d.digital_score,
  d.digital_maturity,
  d.sinais,
  d.enrich_status,
  d.enriched_at AS digital_enriched_at
FROM cnpj.v_bi_varejo v
LEFT JOIN cnpj.digital_presenca d ON d.cnpj = v.cnpj;
