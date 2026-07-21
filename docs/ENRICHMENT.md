# Enriquecimento digital v2

Pipeline pós-ETL com scores separados, validação de site, crawler transacional e qualificação auditável.

## Comandos

```bash
python -m cnpj_etl.cli migrate
python -m cnpj_etl.cli enrich-digital --until-empty
python -m cnpj_etl.cli qualify-prospects
python -m cnpj_etl.cli rescore-digital --version v2
python -m cnpj_etl.cli requeue-enrichment --reason version_upgrade
python -m cnpj_etl.cli prospect-pipeline
```

GitHub Actions: **Prospect Pipeline** (ETL + enrich + qualify) e **Digital Enrichment** (somente enrich).

---

## Scores v2 (`score_version=v2`)

| Score | Significado |
|-------|-------------|
| `presence_score` | Presença digital (site validado, redes, WhatsApp confirmado) |
| `commerce_score` | Capacidade transacional (produto, preço, carrinho, checkout) |
| `fit_score` | Adequação comercial (CNAE, porte, e-commerce confirmado) |
| `pain_score` | Dor/oportunidade de automação (ex.: sem chatbot) |
| `confidence_score` | Confiança dos dados (match site, CNPJ no site, Google Places) |
| `lead_score` | `fit*0.40 + pain*0.35 + confidence*0.25` |

### Campos deprecated (compatibilidade)

- `digital_score` → espelha `lead_score`
- `digital_maturity` → espelha `commerce_maturity`
- `whatsapp_url` → só preenchido quando `whatsapp_valid=true`
- `faixa_faturamento_estimada` → proxy cadastral; use `faixa_porte_receita`

---

## Regras importantes

1. **Site encontrado ≠ site oficial** — exige `site_valid` (`site_match_score >= 70`).
2. **Site oficial ≠ e-commerce** — `ecommerce_confirmado` exige sinais transacionais fortes.
3. **Plataforma detectada ≠ loja ativa** — WooCommerce sozinho não confirma.
4. **Telefone CNPJ ≠ WhatsApp** — vira apenas `telefone_candidato_whatsapp`.
5. **Faixa de porte ≠ faturamento** — somente provider externo preenche `faturamento_estimado`.
6. **Decisor cadastral ≠ opt-in LGPD** — não contate com `confidence_score` baixo.
7. **Instagram** — presença, não canal automático de outreach.

---

## Qualificação v2

Tabela: `cnpj.prospectos_qualificados`
View: `cnpj.v_prospectos_outreach_v2`

Status: `qualified` | `rejected` | `review_required` | `blocked`

Padrão:
- `confidence_score >= 60`
- `lead_score >= 60`
- Canal válido: e-mail corporativo comercial, WhatsApp confirmado ou telefone comercial
- Prospects **não são apagados** — status é atualizado a cada reavaliação

```sql
SELECT
  cnpj,
  razao_social,
  nome_fantasia,
  site_final_url,
  presence_score,
  commerce_score,
  fit_score,
  pain_score,
  confidence_score,
  lead_score,
  commerce_maturity,
  lead_classification,
  contact_channel,
  qualification_status
FROM cnpj.v_prospectos_outreach_v2
WHERE qualification_status = 'qualified'
ORDER BY lead_score DESC, confidence_score DESC;
```

---

## Migrations

- `sql/009_enrichment_quality.sql` — colunas v2 em `digital_presenca`
- `sql/010_prospect_qualification_v2.sql` — qualificação v2

---

## Variáveis de ambiente

Ver `.env.example`. Principais:

- `ENRICHMENT_VERSION=v2`
- `PROSPECT_MIN_CONFIDENCE_SCORE=60`
- `PROSPECT_MIN_LEAD_SCORE=60`
- `GOOGLE_PLACES_ENABLED=false` (ativar só com chave)
- `ENRICH_NO_SITE_RETRY_DAYS=30`

---

## Google Places (opcional)

Text Search + Place Details com match determinístico. Site do Place tem precedência sobre domínio de e-mail quando `google_place_match_score >= 70`. TTL cache: 90 dias.

---

## Segurança

- Crawler com proteção SSRF (DNS, IPs privados, metadata cloud, redirects)
- Resposta limitada a 2 MB, somente HTML
- SQL dinâmico: whitelist de views (`v_prospect_candidates`, `v_bi_varejo`)
- Advisory lock impede enriquecimento concorrente
