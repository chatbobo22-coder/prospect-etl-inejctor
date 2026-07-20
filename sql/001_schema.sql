CREATE SCHEMA IF NOT EXISTS cnpj;
CREATE SCHEMA IF NOT EXISTS etl;

CREATE TABLE IF NOT EXISTS etl.runs (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  competence text NOT NULL,
  status text NOT NULL CHECK (status IN ('running','success','failed','skipped')),
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  files_total integer NOT NULL DEFAULT 0,
  files_processed integer NOT NULL DEFAULT 0,
  rows_processed bigint NOT NULL DEFAULT 0,
  error_message text
);

CREATE TABLE IF NOT EXISTS etl.files (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  competence text NOT NULL,
  file_name text NOT NULL,
  file_type text NOT NULL,
  source_url text NOT NULL,
  source_size bigint,
  source_last_modified text,
  sha256 text,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','downloading','processing','success','failed')),
  rows_processed bigint NOT NULL DEFAULT 0,
  downloaded_at timestamptz,
  processed_at timestamptz,
  error_message text,
  UNIQUE (competence, file_name)
);

CREATE TABLE IF NOT EXISTS cnpj.empresas (
  cnpj_basico char(8) PRIMARY KEY,
  razao_social text NOT NULL,
  natureza_juridica char(4),
  qualificacao_responsavel char(2),
  capital_social numeric(18,2),
  porte char(2),
  ente_federativo_responsavel text,
  source_competence text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cnpj.estabelecimentos (
  cnpj char(14) PRIMARY KEY,
  cnpj_basico char(8) NOT NULL,
  cnpj_ordem char(4) NOT NULL,
  cnpj_dv char(2) NOT NULL,
  identificador_matriz_filial char(1),
  nome_fantasia text,
  situacao_cadastral char(2),
  data_situacao_cadastral date,
  motivo_situacao_cadastral char(2),
  nome_cidade_exterior text,
  pais char(3),
  data_inicio_atividade date,
  cnae_fiscal_principal char(7),
  cnaes_fiscais_secundarios text,
  tipo_logradouro text,
  logradouro text,
  numero text,
  complemento text,
  bairro text,
  cep char(8),
  uf char(2),
  municipio char(4),
  ddd1 varchar(4),
  telefone1 varchar(12),
  ddd2 varchar(4),
  telefone2 varchar(12),
  ddd_fax varchar(4),
  fax varchar(12),
  correio_eletronico text,
  situacao_especial text,
  data_situacao_especial date,
  source_competence text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cnpj.simples (
  cnpj_basico char(8) PRIMARY KEY,
  opcao_simples char(1),
  data_opcao_simples date,
  data_exclusao_simples date,
  opcao_mei char(1),
  data_opcao_mei date,
  data_exclusao_mei date,
  source_competence text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cnpj.socios (
  id text PRIMARY KEY,
  cnpj_basico char(8) NOT NULL,
  identificador_socio char(1),
  nome_socio_razao_social text,
  cpf_cnpj_socio text,
  qualificacao_socio char(2),
  data_entrada_sociedade date,
  pais char(3),
  representante_legal text,
  nome_representante text,
  qualificacao_representante char(2),
  faixa_etaria char(1),
  source_competence text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cnpj.cnaes (codigo char(7) PRIMARY KEY, descricao text NOT NULL);
CREATE TABLE IF NOT EXISTS cnpj.municipios (codigo char(4) PRIMARY KEY, descricao text NOT NULL);
CREATE TABLE IF NOT EXISTS cnpj.paises (codigo char(3) PRIMARY KEY, descricao text NOT NULL);
CREATE TABLE IF NOT EXISTS cnpj.naturezas_juridicas (codigo char(4) PRIMARY KEY, descricao text NOT NULL);
CREATE TABLE IF NOT EXISTS cnpj.qualificacoes_socios (codigo char(2) PRIMARY KEY, descricao text NOT NULL);
CREATE TABLE IF NOT EXISTS cnpj.motivos_situacao (codigo char(2) PRIMARY KEY, descricao text NOT NULL);

