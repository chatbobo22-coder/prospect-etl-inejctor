import dns.resolver

from cnpj_etl.intelligence.email_quality import clear_mx_cache, verify_email


def test_invalid_email_is_rejected_without_dns():
    result = verify_email("email-invalido")

    assert result["deliverability_status"] == "invalid"
    assert result["risk_score"] == 100
    assert result["reason_codes"] == ["invalid_syntax"]


def test_free_email_with_mx_is_allowed(monkeypatch):
    class Exchange:
        def __str__(self):
            return "smtp.google.com."

    class Answer:
        exchange = Exchange()

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", lambda self, domain, kind: [Answer()])

    result = verify_email("joao@gmail.com")

    assert result["email_type"] == "gratuito"
    assert result["deliverability_status"] == "valid"
    assert result["mx_hosts"] == ["smtp.google.com"]


def test_backoffice_email_stays_invalid_even_with_mx(monkeypatch):
    class Exchange:
        def __str__(self):
            return "mx.empresa.com.br."

    class Answer:
        exchange = Exchange()

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", lambda self, domain, kind: [Answer()])

    result = verify_email("nfe@empresa.com.br")

    assert result["deliverability_status"] == "invalid"
    assert "blocked_backoffice_role" in result["reason_codes"]


def test_mx_lookup_is_cached_by_domain(monkeypatch):
    clear_mx_cache()
    calls = []

    class Exchange:
        def __str__(self):
            return "mx.empresa.com.br."

    class Answer:
        exchange = Exchange()

    def resolve(_self, domain, kind):
        calls.append((domain, kind))
        return [Answer()]

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", resolve)

    assert verify_email("maria@empresa.com.br")["mx_valid"] is True
    assert verify_email("joao@empresa.com.br")["mx_valid"] is True
    assert calls == [("empresa.com.br", "MX")]
