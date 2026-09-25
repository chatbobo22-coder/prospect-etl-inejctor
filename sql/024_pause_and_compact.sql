ALTER TABLE etl.runs DROP CONSTRAINT IF EXISTS runs_status_check;
ALTER TABLE etl.runs
  ADD CONSTRAINT runs_status_check
  CHECK (status IN ('running','success','failed','skipped','cancelled','paused'));

-- O prospect materializado possui o necessário para continuar o crawling;
-- assim o cadastro bruto pode ser removido depois da decisão.
CREATE OR REPLACE VIEW cnpj.v_marketing_enrichment_candidates AS
SELECT
  p.cnpj,p.cnpj_basico,p.email,p.telefone_1,p.nome_fantasia,p.razao_social,
  p.uf,p.municipio_descricao,
  NULL::text AS logradouro,NULL::char(8) AS cep,
  NULL::char(7) AS cnae_fiscal_principal,NULL::char(2) AS porte,
  p.opcao_mei,p.opcao_simples,p.capital_social
FROM cnpj.prospectos_qualificados p
WHERE p.qualification_status='qualified' AND p.lead_quality IN ('A','B');
