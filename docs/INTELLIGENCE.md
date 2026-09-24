# Inteligência comercial progressiva

O Injector enriquece cada empresa fonte por fonte. Cada consulta registra status, última
checagem, próxima checagem, erro, quantidade de registros, URL de origem, confiança e prazo de
validade. Assim, o perfil comercial mostra tanto o que foi encontrado quanto o que ainda não foi
consultado.

## Fontes

| Fonte | Situação | Informação produzida |
| --- | --- | --- |
| Receita Federal | pronta | cadastro, capital, filiais, sócios e administradores |
| Site institucional | pronta | equipe publicada em dados estruturados, cargos, contatos corporativos e vagas |
| RDAP | pronta | idade e existência do domínio empresarial |
| GDELT | pronta | notícias recentes de expansão, investimento, contratação e lançamento |
| Google Business | usa a coleta existente | operação, avaliações e endereço confirmados |
| PageSpeed | requer `PAGESPEED_API_KEY` | problemas de desempenho do site |
| CVM | pronta, opcional | registro público e setor de companhia aberta |
| PNCP | conector configurável | contratos públicos; requer índice/provedor que consulte por fornecedor |
| INPI | conector configurável | marcas e patentes; a API pública por CNPJ ainda não é estável |
| Meta/Google Ads | conector configurável | anúncios ativos por provedor autorizado |
| Pessoas | conector licenciado | perfis profissionais e contatos corporativos licenciados |
| Qualidade do e-mail | pronta | sintaxe, domínio descartável, MX, função da caixa e risco de entrega |

PNCP, INPI, anúncios e provedor de pessoas aceitam uma URL configurável no formato
`*_LOOKUP_URL_TEMPLATE`. O retorno deve conter `people` e/ou `signals`. Uma fonte sem credencial
ou endpoint fica como `skipped`; ela nunca é contabilizada como consulta concluída.

## Pessoas e privacidade

São armazenados somente dados profissionais publicados pela própria empresa, dados societários
públicos ou dados recebidos de um provedor licenciado. O coletor do site lê `Person` em JSON-LD e
links profissionais associados. Não raspa LinkedIn, não tenta descobrir e-mail pessoal e não
armazena CPF, endereço residencial ou telefone privado.

A validação de e-mail é passiva: consulta DNS/MX, mas não realiza tentativa SMTP. Gmail, Hotmail e
outros provedores gratuitos continuam válidos quando o endereço atende aos demais critérios.

## Aprendizado comercial e deduplicação

O MestreLead pode registrar resultados em `POST /api/intelligence/feedback`. Resposta positiva,
reunião, oportunidade e venda aquecem o perfil; bounce, descadastro, contato errado e resposta
negativa geram penalidade temporária. A escrita exige `X-API-Key` mesmo quando as rotas de leitura
estão públicas.

Empresas são agrupadas pelo domínio corporativo ou pela raiz do CNPJ. E-mails gratuitos nunca são
usados para unir empresas diferentes. Uma empresa principal é escolhida pelo maior score e as
demais ficam disponíveis para revisão, evitando abordagens duplicadas.

Pessoas recebem prioridade explícita: fundador, administrador, executivo, sócio, contato e
funcionário. Plataformas de comércio e atendimento detectadas passam a ser armazenadas como
tecnologias estruturadas no perfil.

## Pessoas e presença profissional

As integrações opcionais `apollo`, `prospeo` e `hunter` usam somente APIs oficiais. Elas não
raspam páginas do LinkedIn. As URLs públicas retornadas pelos provedores são guardadas como
evidência, junto com nome, cargo, senioridade, e-mail profissional verificado e telefone quando
essa opção for explicitamente habilitada.

Configure uma ou mais chaves:

```ini
APOLLO_API_KEY=
PROSPEO_API_KEY=
HUNTER_API_KEY=
PEOPLE_PROVIDER_BATCH_SIZE=10
PEOPLE_PROVIDER_LIMIT=3
PEOPLE_PROVIDER_REVEAL_EMAILS=true
PEOPLE_PROVIDER_REVEAL_PHONES=false
```

Quando `INTELLIGENCE_SOURCES` não é informado, cada provedor é ativado automaticamente somente
se sua chave existir. No GitHub Actions, as três chaves devem ser cadastradas como Repository
Secrets com os nomes acima.

Os sinais profissionais reforçam o perfil de forma auditável:

- página corporativa e presença multicanal: até 10 pontos de `presence_score`;
- quantidade estimada de funcionários e captação: reforço de capacidade;
- vagas abertas e crescimento do quadro: reforço de intenção recente;
- decisores encontrados: aumenta cobertura e confiança dos dados.

Na publicação, esses sinais geram um bônus auditável de até 20 pontos sobre o score digital:
presença entra integralmente; capacidade e intenção entram com peso de 25%; e decisores somam até
4 pontos. O resultado final nunca ultrapassa 100. O score digital original, o bônus e o resultado
final ficam registrados em `sinais.intelligence_profile`.

Ausência de resultado ou indisponibilidade de um fornecedor não reduz o score. Telefones ficam
desabilitados por padrão porque consomem muito mais créditos que e-mails.

## Pontuação

O perfil soma aderência (25), capacidade (20), intenção (25), dor/oportunidade (20), presença
profissional (10) e confiança dos dados (10), com resultado final limitado a 100. Qualidade A
exige pelo menos 65 pontos, boa cobertura de fontes e um sinal de intenção recente. Qualidade B
exige pelo menos 35 pontos e confiança mínima. A publicação comercial continua obedecendo ao
gate digital configurado (70 por padrão). Sinais expirados deixam de pontuar automaticamente.

## Execução

`python -m cnpj_etl.cli intelligence-pipeline` processa um lote de cada fonte, sempre na ordem
configurada em `INTELLIGENCE_SOURCES`. Use `--until-empty` em uma execução dedicada para percorrer
toda a fila dentro de `INTELLIGENCE_MAX_ROUNDS`. O `prospect-pipeline` executa um lote após o
enriquecimento digital, portanto o processo avança progressivamente sem bloquear toda a carga.

As telas podem consumir:

- `GET /api/intelligence/sources` para disponibilidade e andamento por fonte;
- `GET /api/intelligence/companies/{cnpj}` para perfil, pessoas, sinais e histórico de consultas.
- `POST /api/intelligence/feedback` para devolver resultados do comercial ao score.
