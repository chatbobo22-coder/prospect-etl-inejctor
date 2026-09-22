-- Inteligência comercial progressiva por empresa e por fonte.

CREATE SCHEMA IF NOT EXISTS intelligence;

CREATE TABLE IF NOT EXISTS intelligence.source_registry (
  source_code text PRIMARY KEY,
  display_name text NOT NULL,
  category text NOT NULL,
  availability text NOT NULL CHECK (
    availability IN ('ready', 'requires_key', 'requires_license', 'manual')
  ),
  enabled_default boolean NOT NULL DEFAULT false,
  ttl_days integer NOT NULL DEFAULT 30 CHECK (ttl_days > 0),
  description text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO intelligence.source_registry (
  source_code, display_name, category, availability, enabled_default, ttl_days, description
) VALUES
  ('receita', 'Receita Federal e quadro societário', 'identity', 'ready', true, 30,
   'Cadastro, porte, capital, filiais, sócios e administradores.'),
  ('website', 'Site institucional e equipe publicada', 'digital', 'ready', true, 14,
   'Equipe, cargos, contatos corporativos, vagas e sinais do site.'),
  ('rdap', 'Registro público do domínio', 'digital', 'ready', true, 90,
   'Idade, atualização e situação pública do domínio.'),
  ('gdelt', 'Notícias públicas (GDELT)', 'intent', 'ready', true, 7,
   'Notícias recentes de expansão, investimento, contratação e lançamento.'),
  ('pncp', 'Portal Nacional de Contratações Públicas', 'intent', 'ready', false, 7,
   'Contratações, editais e planos públicos associados ao CNPJ.'),
  ('cvm', 'CVM - companhias abertas', 'capacity', 'ready', false, 90,
   'Cadastro e demonstrações financeiras de companhias abertas.'),
  ('inpi', 'INPI - marcas e patentes', 'intent', 'ready', false, 30,
   'Pedidos e registros públicos vinculados à empresa.'),
  ('google_places', 'Google Business / Places', 'presence', 'requires_key', false, 30,
   'Operação, avaliações, endereço, telefone e site confirmados.'),
  ('pagespeed', 'Google PageSpeed Insights', 'pain', 'requires_key', false, 30,
   'Desempenho, experiência e problemas técnicos do site.'),
  ('meta_ads', 'Meta Ad Library', 'intent', 'requires_key', false, 7,
   'Anúncios ativos e investimento comercial aparente.'),
  ('google_ads', 'Google Ads Transparency', 'intent', 'requires_license', false, 7,
   'Presença de anúncios recentes; depende de provedor autorizado.'),
  ('people_provider', 'Provedor licenciado de pessoas', 'people', 'requires_license', false, 30,
   'Perfis profissionais e contatos corporativos licenciados.')
ON CONFLICT (source_code) DO UPDATE SET
  display_name = EXCLUDED.display_name,
  category = EXCLUDED.category,
  availability = EXCLUDED.availability,
  ttl_days = EXCLUDED.ttl_days,
  description = EXCLUDED.description,
  updated_at = now();

CREATE TABLE IF NOT EXISTS intelligence.company_source_state (
  cnpj char(14) NOT NULL,
  source_code text NOT NULL REFERENCES intelligence.source_registry(source_code),
  status text NOT NULL DEFAULT 'pending' CHECK (
    status IN ('pending', 'running', 'success', 'no_data', 'failed', 'skipped')
  ),
  attempts integer NOT NULL DEFAULT 0,
  records_found integer NOT NULL DEFAULT 0,
  last_checked_at timestamptz,
  next_check_at timestamptz,
  last_error text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (cnpj, source_code)
);

CREATE TABLE IF NOT EXISTS intelligence.company_people (
  id bigserial PRIMARY KEY,
  cnpj char(14) NOT NULL,
  full_name text NOT NULL,
  role_title text,
  relationship_type text NOT NULL CHECK (
    relationship_type IN ('partner', 'administrator', 'founder', 'executive', 'employee', 'contact')
  ),
  linkedin_url text,
  business_email text,
  business_phone text,
  is_decision_maker boolean NOT NULL DEFAULT false,
  confidence smallint NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
  source_code text NOT NULL REFERENCES intelligence.source_registry(source_code),
  source_url text,
  source_observed_at timestamptz NOT NULL DEFAULT now(),
  raw_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_company_people_source_identity
  ON intelligence.company_people (
    cnpj, source_code, lower(full_name), lower(COALESCE(role_title, ''))
  );
CREATE INDEX IF NOT EXISTS idx_company_people_decision_maker
  ON intelligence.company_people (cnpj, confidence DESC)
  WHERE active = true AND is_decision_maker = true;

CREATE TABLE IF NOT EXISTS intelligence.company_signals (
  id bigserial PRIMARY KEY,
  cnpj char(14) NOT NULL,
  source_code text NOT NULL REFERENCES intelligence.source_registry(source_code),
  signal_type text NOT NULL,
  category text NOT NULL CHECK (
    category IN ('fit', 'capacity', 'intent', 'pain', 'confidence', 'presence', 'risk')
  ),
  title text NOT NULL,
  description text,
  score smallint NOT NULL DEFAULT 0 CHECK (score BETWEEN -100 AND 100),
  confidence smallint NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
  observed_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
  source_url text,
  fingerprint text NOT NULL,
  raw_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (cnpj, source_code, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_company_signals_active
  ON intelligence.company_signals (cnpj, category, expires_at, observed_at DESC);

CREATE TABLE IF NOT EXISTS intelligence.company_profiles (
  cnpj char(14) PRIMARY KEY,
  fit_score smallint NOT NULL DEFAULT 0 CHECK (fit_score BETWEEN 0 AND 25),
  capacity_score smallint NOT NULL DEFAULT 0 CHECK (capacity_score BETWEEN 0 AND 20),
  intent_score smallint NOT NULL DEFAULT 0 CHECK (intent_score BETWEEN 0 AND 25),
  pain_score smallint NOT NULL DEFAULT 0 CHECK (pain_score BETWEEN 0 AND 20),
  data_confidence_score smallint NOT NULL DEFAULT 0 CHECK (data_confidence_score BETWEEN 0 AND 10),
  profile_score smallint NOT NULL DEFAULT 0 CHECK (profile_score BETWEEN 0 AND 100),
  profile_quality char(1) CHECK (profile_quality IS NULL OR profile_quality IN ('A', 'B')),
  estimated_capacity_band text,
  intent_last_seen_at timestamptz,
  decision_makers_count integer NOT NULL DEFAULT 0,
  signals_count integer NOT NULL DEFAULT 0,
  sources_success text[] NOT NULL DEFAULT '{}',
  sources_pending text[] NOT NULL DEFAULT '{}',
  summary text,
  reasons text[] NOT NULL DEFAULT '{}',
  calculated_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_company_profiles_quality
  ON intelligence.company_profiles (profile_quality, profile_score DESC)
  WHERE profile_quality IN ('A', 'B');

CREATE TABLE IF NOT EXISTS intelligence.source_runs (
  id bigserial PRIMARY KEY,
  source_code text REFERENCES intelligence.source_registry(source_code),
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'success', 'failed')),
  processed integer NOT NULL DEFAULT 0,
  success integer NOT NULL DEFAULT 0,
  no_data integer NOT NULL DEFAULT 0,
  failed integer NOT NULL DEFAULT 0,
  error_message text
);

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
  p.calculated_at
FROM cnpj.v_prospect_candidates v
LEFT JOIN cnpj.digital_presenca d ON d.cnpj = v.cnpj
LEFT JOIN intelligence.company_profiles p ON p.cnpj = v.cnpj;
