CREATE INDEX IF NOT EXISTS idx_estab_basico ON cnpj.estabelecimentos (cnpj_basico);
CREATE INDEX IF NOT EXISTS idx_estab_situacao ON cnpj.estabelecimentos (situacao_cadastral);
CREATE INDEX IF NOT EXISTS idx_estab_cnae ON cnpj.estabelecimentos (cnae_fiscal_principal);
CREATE INDEX IF NOT EXISTS idx_estab_uf_municipio ON cnpj.estabelecimentos (uf, municipio);
CREATE INDEX IF NOT EXISTS idx_estab_inicio ON cnpj.estabelecimentos (data_inicio_atividade);
CREATE INDEX IF NOT EXISTS idx_estab_email_lower ON cnpj.estabelecimentos (lower(correio_eletronico)) WHERE correio_eletronico IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_socios_basico ON cnpj.socios (cnpj_basico);
CREATE INDEX IF NOT EXISTS idx_empresas_razao_trgm ON cnpj.empresas USING gin (razao_social gin_trgm_ops);

CREATE OR REPLACE VIEW cnpj.v_empresas_completas AS
SELECT
  es.cnpj, e.cnpj_basico, e.razao_social, es.nome_fantasia,
  e.natureza_juridica, nj.descricao AS natureza_juridica_descricao,
  e.capital_social, e.porte, es.identificador_matriz_filial,
  es.situacao_cadastral, es.data_inicio_atividade,
  es.cnae_fiscal_principal, c.descricao AS cnae_principal_descricao,
  es.cnaes_fiscais_secundarios, es.tipo_logradouro, es.logradouro,
  es.numero, es.complemento, es.bairro, es.cep, es.uf,
  es.municipio, m.descricao AS municipio_descricao,
  concat_ws('', es.ddd1, es.telefone1) AS telefone_1,
  concat_ws('', es.ddd2, es.telefone2) AS telefone_2,
  lower(es.correio_eletronico) AS email,
  s.opcao_simples, s.opcao_mei, es.source_competence, es.updated_at
FROM cnpj.estabelecimentos es
JOIN cnpj.empresas e ON e.cnpj_basico = es.cnpj_basico
LEFT JOIN cnpj.cnaes c ON c.codigo = es.cnae_fiscal_principal
LEFT JOIN cnpj.municipios m ON m.codigo = es.municipio
LEFT JOIN cnpj.naturezas_juridicas nj ON nj.codigo = e.natureza_juridica
LEFT JOIN cnpj.simples s ON s.cnpj_basico = es.cnpj_basico;

