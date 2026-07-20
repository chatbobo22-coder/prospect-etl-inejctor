# ETL Dados Abertos do CNPJ

Projeto Python para descobrir a competência mais recente publicada pela Receita Federal, baixar os ZIPs oficiais e popular PostgreSQL de forma idempotente. Inclui empresas, estabelecimentos, contatos, CNAEs, Simples/MEI, sócios e tabelas auxiliares.

## Requisitos

- Docker + Docker Compose (recomendado), ou Python 3.11+ e PostgreSQL 15+.
- Espaço em disco e memória compatíveis com a base nacional. A carga completa é grande; teste primeiro com `INCLUDE_TYPES`.

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

O workflow `.github/workflows/etl-cron.yml` executa o ETL automaticamente **todo domingo às 03:00 (horário de Brasília)** e também pode ser disparado manualmente.

### Configurar secrets

No GitHub: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Obrigatório | Valor |
|--------|-------------|--------|
| `DATABASE_URL` | Sim | Pooler session mode (porta **5432**), ex.: `postgres://postgres.SEU_PROJECT_REF:SENHA@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require` |
| `INCLUDE_TYPES` | Não | Limita tipos no primeiro teste, ex.: `Municipios,Cnaes,Naturezas,Qualificacoes,Motivos,Paises` |

### Rodar manualmente

**Actions → CNPJ ETL → Run workflow**

Parâmetros opcionais: competência (`YYYY-MM`) e `force` para reprocessar arquivos.

### Confirmar que está rodando

1. **GitHub:** Actions → workflow *CNPJ ETL* → histórico verde/vermelho
2. **Supabase SQL:**
   ```sql
   SELECT id, competence, status, started_at, finished_at
   FROM etl.runs ORDER BY started_at DESC LIMIT 10;
   ```
3. **API Vercel:** `GET /api/runs`

Para mudar a frequência, edite o cron em `.github/workflows/etl-cron.yml` (ex.: `"0 * * * *"` = a cada hora).

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

## Fonte

O projeto utiliza exclusivamente os arquivos públicos do diretório oficial `arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/`. Confira o leiaute oficial antes de alterações futuras, pois a Receita pode mudar nomes ou colunas.

