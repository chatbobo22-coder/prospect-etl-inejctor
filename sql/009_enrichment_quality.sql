-- Enrichment quality v2: colunas adicionais em digital_presenca (idempotente)

-- Identidade do site
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_source text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_match_score smallint DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_match_status text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_reachable boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_valid boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_content_type text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_final_url text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_redirect_count smallint DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_last_checked_at timestamptz;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS site_validation_reasons text[];
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS domain_shared_count integer DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS domain_is_shared boolean DEFAULT false;

-- Google Places
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS google_place_id text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS google_place_match_score smallint DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS google_place_name text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS google_place_address text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS google_place_phone text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS google_place_website text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS google_business_status text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS google_rating numeric(3,2);
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS google_rating_count integer;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS google_maps_url text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS google_places_checked_at timestamptz;

-- WhatsApp
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS whatsapp_detected boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS whatsapp_number text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS whatsapp_number_normalized text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS whatsapp_valid boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS whatsapp_source text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS whatsapp_confidence smallint DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS telefone_candidato_whatsapp text;

-- Sinais transacionais
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS has_product_page boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS has_product_schema boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS has_price boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS has_cart boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS has_checkout boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS has_add_to_cart boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS has_catalog boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS has_search boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS has_customer_login boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS has_chat boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS chat_provider text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS has_contact_form boolean DEFAULT false;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS transactional_signals jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Scores separados
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS presence_score smallint DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS commerce_score smallint DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS fit_score smallint DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS pain_score smallint DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS confidence_score smallint DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS lead_score smallint DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS presence_maturity text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS commerce_maturity text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS lead_classification text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS score_version text;

-- Controle de execução
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS enrich_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS next_retry_at timestamptz;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS retry_reason text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS enrichment_version text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS processing_run_id bigint;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS processing_started_at timestamptz;

-- Faixa cadastral (novos nomes; colunas antigas mantidas)
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS faixa_porte_receita text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS porte_receita_fonte text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS faturamento_estimado text;
ALTER TABLE cnpj.digital_presenca ADD COLUMN IF NOT EXISTS faturamento_estimado_fonte text;

-- Checks de score (NOT VALID para não quebrar dados legados)
DO $$ BEGIN
  ALTER TABLE cnpj.digital_presenca
    ADD CONSTRAINT chk_presence_score_range CHECK (presence_score BETWEEN 0 AND 100) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  ALTER TABLE cnpj.digital_presenca
    ADD CONSTRAINT chk_commerce_score_range CHECK (commerce_score BETWEEN 0 AND 100) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  ALTER TABLE cnpj.digital_presenca
    ADD CONSTRAINT chk_lead_score_range CHECK (lead_score BETWEEN 0 AND 100) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_digital_google_place_id ON cnpj.digital_presenca (google_place_id);
CREATE INDEX IF NOT EXISTS idx_digital_lead_score ON cnpj.digital_presenca (lead_score DESC);
CREATE INDEX IF NOT EXISTS idx_digital_confidence_score ON cnpj.digital_presenca (confidence_score DESC);
CREATE INDEX IF NOT EXISTS idx_digital_commerce_maturity ON cnpj.digital_presenca (commerce_maturity);
CREATE INDEX IF NOT EXISTS idx_digital_presence_maturity ON cnpj.digital_presenca (presence_maturity);
CREATE INDEX IF NOT EXISTS idx_digital_next_retry ON cnpj.digital_presenca (next_retry_at);
CREATE INDEX IF NOT EXISTS idx_digital_status_retry ON cnpj.digital_presenca (enrich_status, next_retry_at);

CREATE TABLE IF NOT EXISTS etl.enrichment_runs (
  id bigserial PRIMARY KEY,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  enrichment_version text,
  processed integer DEFAULT 0,
  done integer DEFAULT 0,
  partial integer DEFAULT 0,
  no_site integer DEFAULT 0,
  failed integer DEFAULT 0,
  google_places_requests integer DEFAULT 0,
  status text NOT NULL DEFAULT 'running'
);
