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

## Pontuação

O perfil soma cinco dimensões: aderência (25), capacidade (20), intenção (25), dor/oportunidade
(20) e confiança dos dados (10). Qualidade A exige pelo menos 75 pontos, boa cobertura de fontes e
um sinal de intenção recente. Qualidade B exige pelo menos 60 pontos e confiança mínima. Sinais
expirados deixam de pontuar automaticamente.

## Execução

`python -m cnpj_etl.cli intelligence-pipeline` processa um lote de cada fonte, sempre na ordem
configurada em `INTELLIGENCE_SOURCES`. Use `--until-empty` em uma execução dedicada para percorrer
toda a fila dentro de `INTELLIGENCE_MAX_ROUNDS`. O `prospect-pipeline` executa um lote após o
enriquecimento digital, portanto o processo avança progressivamente sem bloquear toda a carga.

As telas podem consumir:

- `GET /api/intelligence/sources` para disponibilidade e andamento por fonte;
- `GET /api/intelligence/companies/{cnpj}` para perfil, pessoas, sinais e histórico de consultas.
- `POST /api/intelligence/feedback` para devolver resultados do comercial ao score.
