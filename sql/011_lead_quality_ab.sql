-- Qualidade de leads v3: somente A/B e e-mails úteis para outreach.

ALTER TABLE cnpj.prospectos_qualificados
  ADD COLUMN IF NOT EXISTS lead_quality char(1);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chk_prospect_lead_quality'
      AND conrelid = 'cnpj.prospectos_qualificados'::regclass
  ) THEN
    ALTER TABLE cnpj.prospectos_qualificados
      ADD CONSTRAINT chk_prospect_lead_quality
      CHECK (lead_quality IS NULL OR lead_quality IN ('A', 'B')) NOT VALID;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_prospect_outreach_quality
  ON cnpj.prospectos_qualificados (lead_quality, lead_score DESC, confidence_score DESC)
  WHERE qualification_status = 'qualified' AND lead_quality IN ('A', 'B');

-- A fila de enriquecimento recebe empresas maduras, com contato utilizável e
-- atuação nos municípios-alvo. Gmail/Hotmail são aceitos; o bloqueio considera
-- o propósito do endereço, não o provedor.
CREATE OR REPLACE VIEW cnpj.v_prospect_candidates AS
SELECT v.*
FROM cnpj.v_empresas_completas v
JOIN cnpj.municipios_populacao mp
  ON mp.uf = v.uf
 AND mp.codigo = v.municipio
WHERE v.situacao_cadastral = '02'
  AND v.nome_fantasia IS NOT NULL
  AND btrim(v.nome_fantasia) <> ''
  AND v.telefone_1 IS NOT NULL
  AND btrim(v.telefone_1) <> ''
  AND v.email IS NOT NULL
  AND btrim(v.email) ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
  AND split_part(lower(v.email), '@', 1) !~
    '^(administrativo|boleto|boletos|cobranca|contabilidade|contador|departamentopessoal|dp|faturamento|financeiro|fiscal|nfe|pagamento|pagamentos|rh|tributario)'
  AND v.data_inicio_atividade <= current_date - interval '12 months'
  AND mp.populacao >= 50000
  AND COALESCE(v.opcao_mei, 'N') <> 'S'
  AND v.cnae_fiscal_principal IN (
    '4791201', '4781400', '4782201', '4782202', '4783101', '4783102',
    '4772500', '4763601', '4763602', '4755503', '4754701', '4753900',
    '4751201', '4752100', '4789001', '4759899', '4530703', '4744099'
  );

-- Mantém a ordem das colunas da view anterior e acrescenta a qualidade ao fim.
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
  p.updated_at,
  p.lead_quality
FROM cnpj.prospectos_qualificados p
WHERE p.qualification_status = 'qualified'
  AND p.lead_quality IN ('A', 'B')
ORDER BY p.lead_quality, p.lead_score DESC NULLS LAST,
  p.confidence_score DESC NULLS LAST;

CREATE OR REPLACE VIEW cnpj.v_prospectos_outreach_v3 AS
SELECT * FROM cnpj.v_prospectos_outreach_v2;
