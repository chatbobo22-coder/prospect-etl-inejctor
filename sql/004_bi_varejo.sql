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
WHERE v.situacao_cadastral = '02'
  AND v.cnae_fiscal_principal IN (
    '4791201', '4781400', '4782201', '4782202', '4783101', '4783102',
    '4772500', '4763601', '4763602', '4755503', '4754701', '4753900',
    '4751201', '4752100', '4789001', '4759899', '4530703', '4744099'
  );
