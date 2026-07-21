# Enriquecimento digital pós-ETL

Pipeline separado do ETL CNPJ. Classifica prospects de `cnpj.v_bi_varejo` por presença digital.

## Comando

```bash
python -m cnpj_etl.cli enrich-digital --batch-size 300
```

GitHub Actions: workflow **Digital Enrichment** (diário + manual).

---

## O que é detectado automaticamente (grátis)

| Sinal | Método | Confiança |
|-------|--------|-----------|
| E-mail corporativo vs. gratuito | Domínio do e-mail Receita | Alta |
| Site ativo | HTTP GET no domínio do e-mail | Alta |
| Shopify, Nuvemshop, Tray, VTEX, WooCommerce, Loja Integrada | Fingerprints no HTML | Média-alta |
| Instagram / WhatsApp / LinkedIn | Links no HTML do site | Média |
| Decisor (sócio-admin) | Tabela `cnpj.socios` (já carregada no ETL) | Alta |
| Faixa de faturamento **estimada** | MEI / Porte / Simples / Capital social | Baixa (proxy) |

## Score (`digital_score` 0–100)

| Pontos | Critério |
|--------|----------|
| +35 | Site ativo |
| +25 | Plataforma e-commerce detectada |
| +15 | E-mail corporativo |
| +10 | WhatsApp no site |
| +10 | Instagram no site |
| +10 | Decisor identificado (sócios) |
| +5 | LinkedIn no site |

Maturidade: `offline` → `presenca_basica` → `ecommerce_provavel` → `ecommerce_confirmado`

---

## SQL (rodar no Supabase se ainda não migrou)

Ver `sql/006_digital_presenca.sql`.

Consulta BI:

```sql
SELECT cnpj, nome_fantasia, digital_score, plataforma, site_url,
       decisor_nome, faixa_faturamento_estimada, enrich_status
FROM cnpj.v_prospect_digital
ORDER BY digital_score DESC
LIMIT 100;
```

---

## Serviços externos — matriz de consultas

### Grátis / freemium (CNPJ cadastral)

| Serviço | URL | O que traz | Limite | Uso no projeto |
|---------|-----|------------|--------|----------------|
| **Dados locais (ETL)** | — | E-mail, tel, sócios, capital, porte | Ilimitado | Decisor + proxy faturamento |
| **Brasil API** | brasilapi.com.br | CNPJ cadastral oficial | Rate limit | Opcional (`ENRICH_BRASILAPI=true`) |
| **OpenCNPJ** | opencnpj.org | CNPJ JSON | Sem token | Alternativa gratuita |
| **MUAC** | muac.com.br | CNPJ + sócios | 10 req/min | Amostragem pontual |
| **CNPJ Aberto** | cnpjaberto.com.br | CNPJ + buscas | 1000/dia | Requer API key |

### Pagos — faturamento, vendas, decisores verificados

| Serviço | O que traz | Observação |
|---------|------------|------------|
| **Speedio** | Site, faixa faturamento, QSA, telefones validados | Integrável via `ENRICH_EXTERNAL_API_KEY` + `ENRICH_EXTERNAL_API_PROVIDER=speedio` |
| **Kipflow** | Faturamento, funcionários, site, Instagram, sócios com CPF parcial | API paga, ~48 campos |
| **Datastone** | Faixa receita, filtros B2B | Prospecção em massa |
| **LeadCNPJ** | Presença web + decisores + enriquecimento | REST pago |
| **Serpro (oficial)** | CNPJ tempo real | **Não** inclui faturamento |
| **Neoway / BigDataCorp** | Firmografia + estimativas | Enterprise |

> **Faturamento e número de vendas reais** não existem em base pública gratuita. São **estimativas** de data providers ou proxies (MEI/porte/Simples).

### Detecção técnica (grátis, implementado)

| Verificação | Como |
|-------------|------|
| Site online | HTTP status + título da página |
| Plataforma | Regex no HTML (Shopify, VTEX, etc.) |
| DNS domínio e-mail | `socket.getaddrinfo` |

---

## Variáveis de ambiente

```env
ENRICH_BATCH_SIZE=300
ENRICH_DELAY_SECONDS=0.4
ENRICH_REQUEST_TIMEOUT=15
ENRICH_BRASILAPI=false

# Opcional — Speedio ou similar
ENRICH_EXTERNAL_API_PROVIDER=speedio
ENRICH_EXTERNAL_API_KEY=sua_chave
```

No GitHub: secret `ENRICH_EXTERNAL_API_KEY`, variable `ENRICH_EXTERNAL_API_PROVIDER`.

---

## Limitações honestas

1. **Sem e-mail corporativo** → difícil descobrir site (só heurística por nome).
2. **WhatsApp comercial** → link no site é confiável; número sozinho não prova WhatsApp Business.
3. **Faturamento** → use faixa estimada local ou API paga; Receita não publica receita.
4. **Decisor com e-mail/telefone direto** → sócios na base Receita (nome); e-mail pessoal do decisor exige provider pago.
