-- Provedores profissionais licenciados e reforço auditável de presença no score.

INSERT INTO intelligence.source_registry (
  source_code, display_name, category, availability, enabled_default, ttl_days, description
) VALUES
  ('apollo', 'Apollo - pessoas e empresa', 'people', 'requires_key', false, 30,
   'Decisores, cargos, perfil profissional e sinais empresariais via API oficial.'),
  ('prospeo', 'Prospeo - empresa e contatos', 'people', 'requires_key', false, 30,
   'LinkedIn corporativo, força da empresa, vagas, tecnologias e contatos licenciados.'),
  ('hunter', 'Hunter - contatos por domínio', 'contact', 'requires_key', false, 30,
   'E-mails profissionais validados, decisores, cargos e presença corporativa por domínio.')
ON CONFLICT (source_code) DO UPDATE SET
  display_name=EXCLUDED.display_name,
  category=EXCLUDED.category,
  availability=EXCLUDED.availability,
  ttl_days=EXCLUDED.ttl_days,
  description=EXCLUDED.description,
  updated_at=now();

ALTER TABLE intelligence.company_profiles
  ADD COLUMN IF NOT EXISTS presence_score smallint NOT NULL DEFAULT 0;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='company_profiles_presence_score_check'
      AND conrelid='intelligence.company_profiles'::regclass
  ) THEN
    ALTER TABLE intelligence.company_profiles
      ADD CONSTRAINT company_profiles_presence_score_check
      CHECK (presence_score BETWEEN 0 AND 10) NOT VALID;
  END IF;
END $$;

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
  p.feedback_events_count,
  p.presence_score,
  d.linkedin_url
FROM cnpj.v_prospect_candidates v
LEFT JOIN cnpj.digital_presenca d ON d.cnpj=v.cnpj
LEFT JOIN intelligence.company_profiles p ON p.cnpj=v.cnpj
LEFT JOIN intelligence.email_verifications ev ON ev.cnpj=v.cnpj
LEFT JOIN intelligence.company_group_members gm ON gm.cnpj=v.cnpj;

