-- Candidatos à prospecção: CNAE varejo + ativas (sem filtro de fantasia/telefone/cidade)
CREATE OR REPLACE VIEW cnpj.v_prospect_candidates AS
SELECT v.*
FROM cnpj.v_empresas_completas v
WHERE v.situacao_cadastral = '02'
  AND v.cnae_fiscal_principal IN (
    '4791201', '4781400', '4782201', '4782202', '4783101', '4783102',
    '4772500', '4763601', '4763602', '4755503', '4754701', '4753900',
    '4751201', '4752100', '4789001', '4759899', '4530703', '4744099'
  );

-- Base final: só prospects qualificados para outreach automatizado
CREATE TABLE IF NOT EXISTS cnpj.prospectos_qualificados (
  cnpj char(14) PRIMARY KEY,
  cnpj_basico char(8) NOT NULL,
  razao_social text,
  nome_fantasia text,
  uf char(2),
  municipio_descricao text,
  telefone_1 text,
  email text,
  site_url text,
  site_ativo boolean,
  plataforma text,
  whatsapp_url text,
  instagram_url text,
  linkedin_url text,
  digital_score smallint,
  digital_maturity text,
  decisor_nome text,
  decisor_qualificacao text,
  faixa_faturamento_estimada text,
  capital_social numeric(18,2),
  opcao_mei char(1),
  opcao_simples char(1),
  qualification_reasons text[],
  sinais jsonb,
  qualified_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_prospect_qual_score ON cnpj.prospectos_qualificados (digital_score DESC);
CREATE INDEX IF NOT EXISTS idx_prospect_qual_uf ON cnpj.prospectos_qualificados (uf);

CREATE OR REPLACE VIEW cnpj.v_prospectos_outreach AS
SELECT *
FROM cnpj.prospectos_qualificados
ORDER BY digital_score DESC, qualified_at DESC;
