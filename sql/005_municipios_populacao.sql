-- População municipal (IBGE) para cruzamento com código Receita (UF + municipio 4 dígitos)
CREATE TABLE IF NOT EXISTS cnpj.municipios_populacao (
  codigo_ibge char(7) PRIMARY KEY,
  uf char(2) NOT NULL,
  codigo char(4) NOT NULL,
  nome text NOT NULL,
  populacao integer NOT NULL CHECK (populacao >= 0),
  ano_referencia smallint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (uf, codigo)
);

CREATE INDEX IF NOT EXISTS idx_munpop_uf_codigo ON cnpj.municipios_populacao (uf, codigo);
CREATE INDEX IF NOT EXISTS idx_munpop_populacao ON cnpj.municipios_populacao (populacao);
