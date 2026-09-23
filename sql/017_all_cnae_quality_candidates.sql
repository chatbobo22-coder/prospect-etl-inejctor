-- Qualidade nacional sem whitelist de CNAEs.
-- A triagem continua exigindo empresa ativa, madura, não-MEI e e-mail útil.

CREATE OR REPLACE VIEW cnpj.v_prospect_candidates AS
SELECT v.*
FROM cnpj.v_empresas_completas v
WHERE v.situacao_cadastral = '02'
  AND v.email IS NOT NULL
  AND btrim(v.email) ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
  AND split_part(lower(v.email), '@', 1) !~
    '^(administrativo|boleto|boletos|cobranca|contabilidade|contador|departamentopessoal|dp|faturamento|financeiro|fiscal|nfe|pagamento|pagamentos|rh|tributario)'
  AND v.data_inicio_atividade <= current_date - interval '12 months'
  AND COALESCE(v.opcao_mei, 'N') <> 'S';
