# ETL Dados Abertos do CNPJ

Projeto Python para descobrir a competência mais recente publicada pela Receita Federal, baixar os ZIPs oficiais e popular PostgreSQL de forma idempotente. Inclui empresas, estabelecimentos, contatos, CNAEs, Simples/MEI, sócios e tabelas auxiliares.

Consulte [docs/INTELLIGENCE.md](docs/INTELLIGENCE.md) para a esteira de inteligência comercial,
fontes, pontuação, pessoas públicas e endpoints consumidos pelo MestreLead.

## Requisitos

- Docker + Docker Compose (recomendado), ou Python 3.11+ e PostgreSQL 15+.
- Espaço em disco e memória compatíveis com a base nacional. A carga completa é grande; teste primeiro com `INCLUDE_TYPES`.
- Por padrão (`KEEP_DOWNLOADS=false`), cada ZIP é baixado para um arquivo temporário, carregado no PostgreSQL e apagado — nada fica em `data/`.

## Supabase

Copie `.env.example` para `.env` e preencha com as credenciais do projeto. O ETL usa `DATABASE_URL`; se estiver vazio, tenta `POSTGRES_URL_NON_POOLING`, `POSTGRES_URL` ou monta a URL a partir de `POSTGRES_HOST` + `POSTGRES_PASSWORD`.

Para ETL local/cron (COPY, transações longas), use conexão direta ou pooler **session mode** (porta 5432). No **Vercel**, use pooler **transaction mode** (porta 6543).

Teste a conexão:

```bash
python -m cnpj_etl.cli check-db
```

Como você já aplicou os SQLs manualmente, pode ir direto para a carga:

```bash
python -m cnpj_etl.cli run
```

## Vercel

O deploy expõe uma API FastAPI (`app.py`) com health check e status do banco. A carga pesada (`cnpj-etl run`) **não roda no Vercel** — use local, Docker, Render Cron ou similar.

Variáveis obrigatórias no painel do Vercel:

```env
DATABASE_URL=postgres://postgres.SEU_PROJECT_REF:SEU_PASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require
```

Endpoints após o deploy:

- `/` — info da API
- `/api/health` — health check
- `/api/db` — testa conexão PostgreSQL
- `/api/runs` — últimas execuções do ETL
- `/docs` — Swagger

## GitHub Actions (agendamento)

O workflow `.github/workflows/etl-cron.yml` executa o ETL **de hora em hora** com `--auto`:

- **Base vazia** (`cnpj.empresas` e `cnpj.estabelecimentos` sem dados): primeira carga **completa**, todos os tipos de arquivo.
- **Base já populada**: sincronização incremental — processa só arquivos novos ou alterados; ignora os já concluídos em `etl.files`.

Se uma execução falhar no meio, a próxima retoma de onde parou (arquivos com `success` são pulados).

### Configurar secrets

No GitHub, abra o repositório **prospect-etl-inejctor** (não a conta pessoal):

**Settings → Secrets and variables → Actions → Repository secrets → New repository secret**

| Campo | Valor |
|-------|--------|
| **Name** | `DATABASE_URL` (exatamente assim, maiúsculas) |
| **Secret** | URL do Supabase com porta **5432** |

Exemplo (substitua pela sua senha):

```text
postgres://postgres.bpufnefrqhychqjzkgqz:43SWXaMjEkEFZGT5@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require
```

Erros comuns:

- Secret criado em **Environment secrets** em vez de **Repository secrets**
- Nome errado (`Database_URL`, `POSTGRES_URL`, etc.)
- URL com `localhost` ou sem o host do Supabase

Depois de salvar, rode **Actions → CNPJ ETL → Run workflow** de novo.

### Rodar manualmente

**Actions → CNPJ ETL → Run workflow**

Usa `--auto` por padrão. Parâmetros opcionais: competência (`YYYY-MM`) e `force`.

### Confirmar que está rodando

1. **GitHub:** Actions → workflow *CNPJ ETL* → execuções a cada hora
2. **Supabase SQL:**
   ```sql
   SELECT id, competence, status, started_at, finished_at, files_processed, rows_processed
   FROM etl.runs ORDER BY started_at DESC LIMIT 10;
   ```
3. **API Vercel:** `GET /api/runs`

### Limitações do GitHub Actions

A primeira carga completa da base nacional é **muito grande** (dezenas de GB em ZIPs). O runner do GitHub tem ~14 GB de disco e timeout de 6 h — pode falhar na carga inicial. Se isso ocorrer, rode a primeira carga localmente ou em um VPS com `python -m cnpj_etl.cli run --auto` e deixe o GitHub cuidar das sincronizações horárias depois.

## Início rápido com Docker

```bash
cp .env.example .env
docker compose up -d postgres
docker compose run --rm etl migrate
docker compose run --rm etl run
```

Para testar inicialmente somente tabelas pequenas, configure no `.env`:

```env
INCLUDE_TYPES=Municipios,Cnaes,Naturezas,Qualificacoes,Motivos,Paises
```

Depois remova a variável para importar tudo. Para uma competência específica:

```bash
docker compose run --rm etl run --competence 2026-07
```

## Execução periódica

Use o exemplo `crontab.example`, GitHub Actions, Render Cron Job, Kubernetes CronJob ou o agendador do seu servidor. Uma execução semanal é suficiente: a Receita publica snapshots por competência, não eventos em tempo real.

O ETL registra cada arquivo em `etl.files`. Arquivos com status `success` são ignorados nas próximas execuções. `pg_advisory_lock` impede concorrência. Para reprocessar deliberadamente:

```bash
docker compose run --rm etl run --force
```

## SQLs

- `000_extensions.sql`: extensão de busca textual.
- `001_schema.sql`: schemas e tabelas.
- `002_indexes_views.sql`: índices e view consolidada.
- `003_permissions.sql`: exemplo opcional de usuário somente leitura.

Todos são aplicados em ordem por `cnpj-etl migrate` e podem ser executados manualmente com `psql`.

## Consulta de exemplo

```sql
SELECT cnpj, razao_social, nome_fantasia, telefone_1, email, uf
FROM cnpj.v_empresas_completas
WHERE situacao_cadastral = '02'
  AND uf = 'PR'
  AND cnae_fiscal_principal = '6201501'
ORDER BY data_inicio_atividade DESC
LIMIT 100;
```

## Operação e recuperação

- Consulte `etl.runs` e `etl.files` para auditoria.
- Um arquivo só vira `success` depois do commit da carga.
- Em falha, execute novamente; arquivos concluídos não serão repetidos.
- Mantenha backup do PostgreSQL. Os ZIPs podem ser removidos após uma carga bem-sucedida se precisar economizar disco.
- Os arquivos da Receita usam `;`, aspas e codificação Latin-1; o leitor já trata esse formato.

## Enriquecimento e prospecção v2

Após o ETL, o pipeline enriquece candidatos (`cnpj.v_prospect_candidates`), valida sites, detecta e-commerce real e qualifica prospects.

```bash
python -m cnpj_etl.cli migrate
python -m cnpj_etl.cli prospect-pipeline
python -m cnpj_etl.cli rescore-digital --version v2
python -m cnpj_etl.cli requeue-enrichment --reason version_upgrade
```

Documentação completa: [docs/ENRICHMENT.md](docs/ENRICHMENT.md).

Consulta de prospects qualificados:

```sql
SELECT * FROM cnpj.v_prospectos_outreach_v2
WHERE qualification_status = 'qualified'
ORDER BY lead_score DESC;
```

### Qualidade A/B e retenção mínima

A qualificação v4 mantém permanentemente somente empresas com e-mail válido e qualidade
`A` ou `B`. A leitura da Receita faz primeiro uma triagem barata antes do banco: empresa
ativa, matriz, e-mail válido, endereço não contábil/fiscal/NFe, pelo menos 12 meses de
atividade e CNAE aderente. Cada execução admite no máximo 4.000 candidatos novos para
impedir que a área temporária cresça mais rápido do que o enriquecimento.
O enriquecimento digital usa oito rodadas de 500 registros e a inteligência usa quarenta
rodadas de 100, mantendo entrada e avaliação no mesmo teto de 4.000 empresas.

O lote temporário é então verificado nas fontes Receita, qualidade técnica do e-mail,
site institucional, RDAP, CVM e GDELT. Um lead só entra em
`cnpj.prospectos_qualificados` se passar simultaneamente pelo score digital e pelo perfil
de inteligência pública. A decisão compacta fica em `etl.candidate_decisions`; dados
brutos e inteligência detalhada de rejeitados são apagados ao final da execução.

Provedores gratuitos, como Gmail e Hotmail, são aceitos quando os demais sinais confirmam
a qualidade do lead. Endereços de contabilidade, fiscal, NFe, faturamento, cobrança, DP e RH
são bloqueados.

- **A:** `lead_score >= 70`, `confidence_score >= 70` e ao menos um sinal forte
  (site válido, WhatsApp confirmado ou Google Business operacional).
- **B:** `lead_score >= 60` e `confidence_score >= 70`.

Filtros recomendados para a carga:

```env
FILTER_ACTIVE_ONLY=true
FILTER_REQUIRE_NOME_FANTASIA=true
FILTER_REQUIRE_TELEFONE=true
FILTER_REQUIRE_EMAIL=true
FILTER_BLOCK_BACKOFFICE_EMAIL=true
FILTER_HEADQUARTERS_ONLY=true
FILTER_MAX_CANDIDATES_PER_RUN=4000
FILTER_MIN_ACTIVITY_MONTHS=12
FILTER_MIN_POPULATION=0
PROSPECT_MIN_CONFIDENCE_SCORE=70
PROSPECT_MIN_LEAD_SCORE=60
PROSPECT_EXCLUDE_MEI=true
STRICT_INTELLIGENCE_GATE=true
```

A view `cnpj.v_prospectos_outreach_v3` entrega somente os leads aprovados A/B.
Fontes que dependem de provedor ou chave externa não participam da aprovação enquanto não
estiverem configuradas; ausência de integração nunca é contabilizada como consulta feita.

## Fonte

O projeto usa os arquivos públicos da Receita Federal como fonte oficial. Por padrão, os ZIPs são transportados pela CDN configurada em `RFB_DOWNLOAD_MIRROR_URL`, pois o Nextcloud oficial encerra downloads longos originados de alguns runners do GitHub. O injetor só aceita um snapshot do mesmo mês solicitado e nunca retrocede silenciosamente para uma competência anterior. Para desativar a CDN, defina `RFB_DOWNLOAD_MIRROR_URL` como vazio. Confira o leiaute oficial antes de alterações futuras, pois a Receita pode mudar nomes ou colunas.

