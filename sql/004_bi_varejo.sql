CREATE OR REPLACE VIEW cnpj.v_bi_varejo AS
SELECT
  v.cnpj,
  v.cnpj_basico,
  v.razao_social,
  v.nome_fantasia,
  v.capital_social,
  v.porte,
  v.natureza_juridica,
  v.natureza_juridica_descricao,
  v.identificador_matriz_filial,
  v.situacao_cadastral,
  v.data_inicio_atividade,
  v.cnae_fiscal_principal,
  v.cnae_principal_descricao,
  v.cnaes_fiscais_secundarios,
  v.uf,
  v.municipio,
  v.municipio_descricao,
  v.telefone_1,
  v.telefone_2,
  v.email,
  v.opcao_simples,
  v.opcao_mei,
  v.source_competence,
  v.updated_at
FROM cnpj.v_empresas_completas v
WHERE v.situacao_cadastral = '02';
