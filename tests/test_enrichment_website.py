from cnpj_etl.enrichment.email import classify_email, is_free_email_domain
from cnpj_etl.enrichment.models import EnrichSettings
from cnpj_etl.enrichment.website import (
    safe_fetch,
    site_candidates_from_email,
    validate_site_ownership,
    validate_url_safe,
)


def test_gmail_never_becomes_site():
    domain, _, tipo = classify_email("loja@gmail.com")
    assert is_free_email_domain(domain)
    assert site_candidates_from_email(domain, tipo) == []


def test_yahoo_outlook_blocked_as_site():
    for email in ("a@yahoo.com.br", "b@outlook.com.br"):
        domain, _, tipo = classify_email(email)
        assert site_candidates_from_email(domain, tipo) == []


def test_http_404_not_valid(monkeypatch):
    class Resp:
        status_code = 404
        headers = {"Content-Type": "text/html"}

        def iter_content(self, chunk_size=65536):
            yield b"<html></html>"

        def close(self):
            pass

    class Session:
        def get(self, *args, **kwargs):
            return Resp()

    settings = EnrichSettings()
    result = safe_fetch(Session(), "https://example.com", settings)
    assert result.reachable is False
    assert result.valid is False
    assert result.error == "http_404"


def test_403_reachable_not_validated_without_evidence():
    score, reasons, status = validate_site_ownership(
        html="<html><body>Login required</body></html>",
        title="Login",
        final_url="https://loja.com.br",
        nome_fantasia="Loja X",
        razao_social="Loja X LTDA",
        municipio="Curitiba",
        uf="PR",
        telefone=None,
        cnpj=None,
        email_domain="loja.com.br",
        email_tipo="corporativo",
        cnae="4751201",
    )
    assert score < 70
    assert status != "validated"


def test_shared_domain_reduces_confidence():
    score, reasons, _ = validate_site_ownership(
        html="<html><body>Loja ABC produtos</body></html>",
        title="Loja ABC",
        final_url="https://shared.com",
        nome_fantasia="Loja ABC",
        razao_social="Loja ABC LTDA",
        municipio="Curitiba",
        uf="PR",
        telefone=None,
        cnpj=None,
        email_domain="shared.com",
        email_tipo="corporativo",
        cnae="4751201",
        domain_shared_count=4,
    )
    assert "dominio_compartilhado" in reasons
    assert score <= 70


def test_accounting_title_not_auto_associated():
    score, reasons, status = validate_site_ownership(
        html="<html><body>Escritório de contabilidade</body></html>",
        title="Contabilidade Silva",
        final_url="https://contador.com.br",
        nome_fantasia="Loja de Roupas",
        razao_social="Moda LTDA",
        municipio="Curitiba",
        uf="PR",
        telefone=None,
        cnpj=None,
        email_domain="contador.com.br",
        email_tipo="corporativo",
        cnae="4751201",
    )
    assert any("title_incompativel" in r for r in reasons)
    assert status != "validated"


def test_private_redirect_blocked():
    ok, reason = validate_url_safe("http://127.0.0.1/")
    assert ok is False
    assert reason in {"localhost", "ip_bloqueado:127.0.0.1"}


def test_response_size_limit(monkeypatch):
    class Resp:
        status_code = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def iter_content(self, chunk_size=65536):
            yield b"x" * 3000

        def close(self):
            pass

    class Session:
        def get(self, *args, **kwargs):
            return Resp()

    settings = EnrichSettings(max_response_bytes=1000)
    result = safe_fetch(Session(), "https://example.com", settings)
    assert len(result.html) <= 1000


def test_non_html_not_analyzed(monkeypatch):
    class Resp:
        status_code = 200
        headers = {"Content-Type": "application/json"}

        def iter_content(self, chunk_size=65536):
            yield b'{"ok": true}'

        def close(self):
            pass

    class Session:
        def get(self, *args, **kwargs):
            return Resp()

    settings = EnrichSettings()
    result = safe_fetch(Session(), "https://example.com/data.json", settings)
    assert result.error == "conteudo_nao_html"
    assert result.html == ""
