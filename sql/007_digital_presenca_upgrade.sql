-- Corrige tabelas criadas manualmente sem todas as colunas (CREATE IF NOT EXISTS não altera)
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS cnpj_basico char(8);
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS email_original text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS email_dominio text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS email_tipo text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_url text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_ativo boolean;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_http_status smallint;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_titulo text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS plataforma text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS plataformas_detectadas text[];
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS plataforma_confianca smallint;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS instagram_url text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS whatsapp_url text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS linkedin_url text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS decisor_nome text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS decisor_qualificacao text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS faixa_faturamento_estimada text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS faturamento_fonte text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS digital_score smallint;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS digital_maturity text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS sinais jsonb;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS enrich_status text DEFAULT 'pending';
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS enrich_error text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS enriched_at timestamptz;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();

UPDATE cnpj.digital_presenca
SET cnpj_basico = left(cnpj, 8)
WHERE cnpj_basico IS NULL AND cnpj IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_digital_score ON cnpj.digital_presenca (digital_score DESC);
CREATE INDEX IF NOT EXISTS idx_digital_plataforma ON cnpj.digital_presenca (plataforma);
CREATE INDEX IF NOT EXISTS idx_digital_status ON cnpj.digital_presenca (enrich_status);
CREATE INDEX IF NOT EXISTS idx_digital_enriched_at ON cnpj.digital_presenca (enriched_at);

-- Recria para permitir evolução da lista de colunas da view base.
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
