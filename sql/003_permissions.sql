-- Opcional: execute depois de criar um usuário somente-leitura chamado cnpj_reader.
-- CREATE ROLE cnpj_reader LOGIN PASSWORD 'troque-esta-senha';
-- GRANT CONNECT ON DATABASE cnpj TO cnpj_reader;
-- GRANT USAGE ON SCHEMA cnpj TO cnpj_reader;
-- GRANT SELECT ON ALL TABLES IN SCHEMA cnpj TO cnpj_reader;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA cnpj GRANT SELECT ON TABLES TO cnpj_reader;

