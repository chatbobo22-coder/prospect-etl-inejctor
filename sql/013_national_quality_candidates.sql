-- Cobertura nacional: qualidade do lead decide, não estado ou tamanho do município.

CREATE OR REPLACE VIEW cnpj.v_prospect_candidates AS
SELECT v.*
FROM cnpj.v_empresas_completas v
WHERE v.situacao_cadastral = '02'
  AND v.email IS NOT NULL
  AND btrim(v.email) ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
  AND split_part(lower(v.email), '@', 1) !~
    '^(administrativo|boleto|boletos|cobranca|contabilidade|contador|departamentopessoal|dp|faturamento|financeiro|fiscal|nfe|pagamento|pagamentos|rh|tributario)'
  AND v.data_inicio_atividade <= current_date - interval '12 months'
  AND COALESCE(v.opcao_mei, 'N') <> 'S'
  AND (
    v.cnae_fiscal_principal IN (
      '4791201', '4781400', '4782201', '4782202', '4783101', '4783102',
      '4772500', '4763601', '4763602', '4755503', '4754701', '4753900',
      '4751201', '4752100', '4789001', '4759899', '4530703', '4744099'
    )
    OR EXISTS (
      SELECT 1
      FROM unnest(string_to_array(COALESCE(v.cnaes_fiscais_secundarios, ''), ',')) AS cnae(codigo)
      WHERE btrim(cnae.codigo) = ANY (ARRAY[
        '4791201', '4781400', '4782201', '4782202', '4783101', '4783102',
        '4772500', '4763601', '4763602', '4755503', '4754701', '4753900',
        '4751201', '4752100', '4789001', '4759899', '4530703', '4744099'
      ])
    )
  );
