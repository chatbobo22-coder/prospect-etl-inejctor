-- Índices da esteira rápida de leads para marketing.
-- A contagem de domínio é feita durante o enriquecimento de cada candidato;
-- o índice parcial evita varreduras sucessivas conforme a base cresce.
CREATE INDEX IF NOT EXISTS idx_digital_email_domain_cnpj
  ON cnpj.digital_presenca (email_dominio, cnpj)
  WHERE email_dominio IS NOT NULL;

-- A fila de fontes consulta repetidamente itens vencidos de uma fonte.
CREATE INDEX IF NOT EXISTS idx_company_source_due
  ON intelligence.company_source_state (source_code, next_check_at, cnpj)
  WHERE status <> 'running';

-- A promoção incremental compara a última avaliação com dados que mudaram.
CREATE INDEX IF NOT EXISTS idx_candidate_decisions_evaluated
  ON etl.candidate_decisions (evaluated_at, cnpj);

-- Um ZIP grande pode parar ao completar o lote. O cursor fica em scanned_rows
-- e a execução seguinte continua do ponto salvo.
ALTER TABLE etl.files DROP CONSTRAINT IF EXISTS files_status_check;
ALTER TABLE etl.files
  ADD CONSTRAINT files_status_check
  CHECK (status IN ('pending','downloading','processing','partial','success','failed'));

ALTER TABLE etl.files
  ADD COLUMN IF NOT EXISTS last_run_rows bigint NOT NULL DEFAULT 0;
