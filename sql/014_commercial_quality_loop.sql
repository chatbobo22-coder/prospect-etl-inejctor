-- Qualidade de contato, deduplicação e aprendizado com o resultado comercial.

INSERT INTO intelligence.source_registry (
  source_code, display_name, category, availability, enabled_default, ttl_days, description
) VALUES
  ('email_quality', 'Qualidade técnica do e-mail', 'contact', 'ready', true, 14,
   'Sintaxe, domínio descartável, MX e risco de entrega sem realizar envio SMTP.'),
  ('commercial_feedback', 'Feedback do comercial', 'intent', 'manual', true, 365,
   'Aberturas, respostas, reuniões, oportunidades, vendas, bounces e descadastros.')
ON CONFLICT (source_code) DO UPDATE SET
  display_name=EXCLUDED.display_name, category=EXCLUDED.category,
  availability=EXCLUDED.availability, enabled_default=EXCLUDED.enabled_default,
  ttl_days=EXCLUDED.ttl_days, description=EXCLUDED.description, updated_at=now();

CREATE TABLE IF NOT EXISTS intelligence.email_verifications (
  cnpj char(14) PRIMARY KEY,
  email text NOT NULL,
  domain text,
  syntax_valid boolean NOT NULL DEFAULT false,
  mx_valid boolean,
  mx_hosts text[] NOT NULL DEFAULT '{}',
  disposable boolean NOT NULL DEFAULT false,
  email_role text,
  deliverability_status text NOT NULL CHECK (
    deliverability_status IN ('valid', 'risky', 'invalid', 'unknown')
  ),
  risk_score smallint NOT NULL DEFAULT 0 CHECK (risk_score BETWEEN 0 AND 100),
  reason_codes text[] NOT NULL DEFAULT '{}',
  checked_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  last_error text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_verifications_status
  ON intelligence.email_verifications (deliverability_status, risk_score, expires_at);
CREATE INDEX IF NOT EXISTS idx_email_verifications_domain
  ON intelligence.email_verifications (domain) WHERE domain IS NOT NULL;

CREATE TABLE IF NOT EXISTS intelligence.commercial_feedback (
  id bigserial PRIMARY KEY,
  cnpj char(14) NOT NULL,
  person_id bigint REFERENCES intelligence.company_people(id) ON DELETE SET NULL,
  outcome text NOT NULL CHECK (outcome IN (
    'attempted', 'delivered', 'opened', 'clicked', 'replied_positive',
    'replied_negative', 'meeting_scheduled', 'opportunity_created', 'won',
    'lost', 'bounced', 'unsubscribed', 'wrong_contact'
  )),
  channel text,
  campaign_id text,
  source text NOT NULL DEFAULT 'mestrelead',
  external_id text,
  notes text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_commercial_feedback_external
  ON intelligence.commercial_feedback (source, external_id)
  WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_commercial_feedback_company_time
  ON intelligence.commercial_feedback (cnpj, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_commercial_feedback_outcome_time
  ON intelligence.commercial_feedback (outcome, occurred_at DESC);

CREATE TABLE IF NOT EXISTS intelligence.company_groups (
  group_key text PRIMARY KEY,
  root_domain text,
  cnpj_basico char(8),
  primary_cnpj char(14),
  members_count integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intelligence.company_group_members (
  cnpj char(14) PRIMARY KEY,
  group_key text NOT NULL REFERENCES intelligence.company_groups(group_key) ON DELETE CASCADE,
  is_primary boolean NOT NULL DEFAULT false,
  confidence smallint NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
  reason text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_company_group_members_group
  ON intelligence.company_group_members (group_key, is_primary DESC);

CREATE TABLE IF NOT EXISTS intelligence.company_technologies (
  cnpj char(14) NOT NULL,
  technology text NOT NULL,
  category text NOT NULL,
  confidence smallint NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
  source_code text NOT NULL REFERENCES intelligence.source_registry(source_code),
  source_url text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  active boolean NOT NULL DEFAULT true,
  raw_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (cnpj, technology, source_code)
);

CREATE INDEX IF NOT EXISTS idx_company_technologies_lookup
  ON intelligence.company_technologies (technology, category, cnpj) WHERE active=true;

ALTER TABLE intelligence.company_people
  ADD COLUMN IF NOT EXISTS priority_score smallint NOT NULL DEFAULT 0;
ALTER TABLE intelligence.company_people
  ADD COLUMN IF NOT EXISTS priority_reason text;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_company_people_priority'
      AND conrelid='intelligence.company_people'::regclass
  ) THEN
    ALTER TABLE intelligence.company_people ADD CONSTRAINT chk_company_people_priority
      CHECK (priority_score BETWEEN 0 AND 100) NOT VALID;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_company_people_priority
  ON intelligence.company_people (cnpj, priority_score DESC, confidence DESC)
  WHERE active=true;

UPDATE intelligence.company_people SET
  priority_score=CASE relationship_type
    WHEN 'founder' THEN 100 WHEN 'administrator' THEN 95 WHEN 'executive' THEN 90
    WHEN 'partner' THEN 85 WHEN 'contact' THEN 55 WHEN 'employee' THEN 40 ELSE 30 END,
  priority_reason='relationship:' || relationship_type
WHERE priority_score=0;

ALTER TABLE intelligence.company_profiles
  ADD COLUMN IF NOT EXISTS commercial_temperature text;
ALTER TABLE intelligence.company_profiles
  ADD COLUMN IF NOT EXISTS last_commercial_event_at timestamptz;
ALTER TABLE intelligence.company_profiles
  ADD COLUMN IF NOT EXISTS feedback_events_count integer NOT NULL DEFAULT 0;

CREATE OR REPLACE VIEW intelligence.v_commercial_profiles AS
SELECT
  v.cnpj,
  v.cnpj_basico,
  v.razao_social,
  v.nome_fantasia,
  v.cnae_fiscal_principal,
  v.uf,
  v.municipio_descricao,
  v.telefone_1,
  v.email,
  d.site_final_url,
  d.plataforma,
  d.google_business_status,
  p.fit_score,
  p.capacity_score,
  p.intent_score,
  p.pain_score,
  p.data_confidence_score,
  p.profile_score,
  p.profile_quality,
  p.estimated_capacity_band,
  p.intent_last_seen_at,
  p.decision_makers_count,
  p.signals_count,
  p.sources_success,
  p.sources_pending,
  p.summary,
  p.reasons,
  p.calculated_at,
  ev.deliverability_status,
  ev.risk_score AS email_risk_score,
  gm.group_key,
  gm.is_primary AS group_primary,
  p.commercial_temperature,
  p.last_commercial_event_at,
  p.feedback_events_count
FROM cnpj.v_prospect_candidates v
LEFT JOIN cnpj.digital_presenca d ON d.cnpj=v.cnpj
LEFT JOIN intelligence.company_profiles p ON p.cnpj=v.cnpj
LEFT JOIN intelligence.email_verifications ev ON ev.cnpj=v.cnpj
LEFT JOIN intelligence.company_group_members gm ON gm.cnpj=v.cnpj;
