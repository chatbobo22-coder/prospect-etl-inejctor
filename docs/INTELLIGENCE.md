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

PNCP, INPI, anúncios e provedor de pessoas aceitam uma URL configurável no formato
`*_LOOKUP_URL_TEMPLATE`. O retorno deve conter `people` e/ou `signals`. Uma fonte sem credencial
ou endpoint fica como `skipped`; ela nunca é contabilizada como consulta concluída.

## Pessoas e privacidade

São armazenados somente dados profissionais publicados pela própria empresa, dados societários
públicos ou dados recebidos de um provedor licenciado. O coletor do site lê `Person` em JSON-LD e
links profissionais associados. Não raspa LinkedIn, não tenta descobrir e-mail pessoal e não
armazena CPF, endereço residencial ou telefone privado.

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
