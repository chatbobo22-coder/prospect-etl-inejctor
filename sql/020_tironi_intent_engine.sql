-- Camada privada de intenção comercial da Tironi Tech.
-- Reutiliza company_signals, company_people e company_technologies; não expõe
-- novas tabelas pela Data API do Supabase.

CREATE TABLE IF NOT EXISTS intelligence.tironi_profiles (
  cnpj char(14) PRIMARY KEY,
  segment_fit text NOT NULL DEFAULT 'other' CHECK (segment_fit IN (
    'ecommerce_retail', 'automotive', 'distributor_wholesale',
    'franchise_multiunit', 'industry_b2b', 'high_ticket_services',
    'digital_company', 'other'
  )),
  tironi_score smallint NOT NULL DEFAULT 0 CHECK (tironi_score BETWEEN 0 AND 100),
  classification text NOT NULL DEFAULT 'FRIO' CHECK (classification IN (
    'FRIO', 'POTENCIAL', 'QUENTE', 'MUITO QUENTE', 'PRIORIDADE COMERCIAL'
  )),
  employee_count integer,
  estimated_sellers_count integer,
  active_units integer NOT NULL DEFAULT 1,
  has_whatsapp boolean NOT NULL DEFAULT false,
  has_crm boolean NOT NULL DEFAULT false,
  has_erp boolean NOT NULL DEFAULT false,
  has_ecommerce boolean NOT NULL DEFAULT false,
  has_sales_team boolean NOT NULL DEFAULT false,
  why_this_lead text NOT NULL DEFAULT '',
  positive_signals text[] NOT NULL DEFAULT '{}',
  risks text[] NOT NULL DEFAULT '{}',
  recommended_products text[] NOT NULL DEFAULT '{}',
  recommended_plan text NOT NULL DEFAULT 'Club Start' CHECK (recommended_plan IN (
    'Club Start', 'Club Growth', 'Club Full Access', 'Club Dedicated', 'Enterprise'
  )),
  next_best_action text NOT NULL DEFAULT '',
  sales_approach text NOT NULL DEFAULT '',
  score_breakdown jsonb NOT NULL DEFAULT '[]'::jsonb,
  signals_count integer NOT NULL DEFAULT 0,
  latest_signal_at timestamptz,
  score_version text NOT NULL DEFAULT 'tironi-v1',
  calculated_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tironi_profiles_priority
  ON intelligence.tironi_profiles (tironi_score DESC, latest_signal_at DESC);
CREATE INDEX IF NOT EXISTS idx_tironi_profiles_segment_priority
  ON intelligence.tironi_profiles (segment_fit, tironi_score DESC);
CREATE INDEX IF NOT EXISTS idx_tironi_profiles_hot
  ON intelligence.tironi_profiles (tironi_score DESC, latest_signal_at DESC)
  WHERE tironi_score >= 70;

CREATE TABLE IF NOT EXISTS intelligence.tironi_score_history (
  id bigserial PRIMARY KEY,
  cnpj char(14) NOT NULL,
  tironi_score smallint NOT NULL CHECK (tironi_score BETWEEN 0 AND 100),
  classification text NOT NULL,
  score_breakdown jsonb NOT NULL DEFAULT '[]'::jsonb,
  reasons text[] NOT NULL DEFAULT '{}',
  calculated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tironi_score_history_company_time
  ON intelligence.tironi_score_history (cnpj, calculated_at DESC);

CREATE TABLE IF NOT EXISTS intelligence.intent_alert_events (
  id bigserial PRIMARY KEY,
  cnpj char(14) NOT NULL,
  event_type text NOT NULL,
  previous_score smallint,
  current_score smallint,
  signal_id bigint REFERENCES intelligence.company_signals(id) ON DELETE SET NULL,
  event_fingerprint text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  delivered_at timestamptz,
  UNIQUE (cnpj, event_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_intent_alert_events_pending
  ON intelligence.intent_alert_events (occurred_at, event_type)
  WHERE delivered_at IS NULL;
