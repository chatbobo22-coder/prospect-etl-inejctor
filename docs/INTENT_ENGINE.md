# MestreLead Intent-Based B2B Lead Engine

## Diagnóstico da arquitetura encontrada

O sistema é composto por três aplicações que compartilham o mesmo PostgreSQL/Supabase:

- `prospect-etl-inejctor`: ETL Python/FastAPI, carga da Receita Federal, enriquecimento digital, providers públicos/licenciados, score inicial e telemetria dos jobs do GitHub Actions.
- `tironi-outreach`: FastAPI da operação de e-mail e CRM, fila transacional e worker com bloqueio `SKIP LOCKED`.
- `mestrelead`: Next.js 16/React 19. O navegador usa Route Handlers como BFF; chaves do Injector e Outreach permanecem somente no servidor.

A busca anterior era orientada a cadastro/qualificação: empresas ativas com e-mail válido passavam pelo enriquecimento digital, inteligência por fonte e promoção A/B para `cnpj.prospectos_qualificados`. Os contatos eram sincronizados em `outreach.leads`. Já existiam fontes para Receita, site, RDAP, e-mail, GDELT, CVM, Google Places/PageSpeed e adapters configuráveis/licenciados (Apollo, Prospeo, Hunter e providers genéricos).

As estruturas `intelligence.company_signals`, `company_people`, `company_technologies`, `company_profiles` e `company_source_state` já resolviam sinais, decisores, stack, perfil genérico e cache/TTL. Elas foram reaproveitadas. O gap principal era uma projeção específica para o ICP da Tironi, com score explicável, recência, confiança, recomendação, histórico e uma UX orientada à decisão.

## MVP implementado

Fluxo:

`Discovery → Enrichment → Technology Detection → Intent Signals → People → Tironi Score → Recommendation → Sales Intelligence → CRM`

- `intelligence.tironi_profiles`: projeção materializada do score e recomendações.
- `intelligence.tironi_score_history`: histórico somente quando o score muda.
- `intelligence.intent_alert_events`: domínio de alertas pronto, sem infraestrutura de notificação acoplada.
- `cnpj_etl.intent`: serviços separados de scoring, freshness, segmentação, recomendação, busca e contratos de providers.
- `/api/opportunities`: busca paginada e filtros server-side.
- `/api/opportunities/{cnpj}`: visão executiva com sinais, fontes, decisores, tecnologias e histórico.
- Busca Inteligente e Prioridade Comercial no MestreLead.

O score usa regras determinísticas. Cada parcela guarda peso-base, confiança, multiplicador de recência, impacto final e origem. Vagas só geram sinais específicos se o provider devolver título e evidência pública real. Nenhum contato, cargo ou evento é inventado.

## Próximas fases

1. Conectar JobProvider e AdsProvider licenciados para ampliar sinais de contratação e mídia.
2. Processar `intent_alert_events` em notificações configuráveis.
3. Adicionar acompanhamento contínuo de mudanças de páginas e novas filiais.
4. Usar IA somente para classificar textos ambíguos de notícias/vagas e redigir mensagens, sempre vinculada às evidências persistidas.
5. Medir custo, latência e taxa de conversão por provider para recalibrar os pesos com feedback comercial.
