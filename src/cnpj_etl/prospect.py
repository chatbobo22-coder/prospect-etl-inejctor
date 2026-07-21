"""Qualificação de prospects v2 para outreach automatizado."""

from __future__ import annotations

import logging
import os

from psycopg.types.json import Jsonb

from .enrichment.email import classify_email_role

log = logging.getLogger(__name__)
QUALIFICATION_VERSION = "v2"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def select_contact_channel(row: dict) -> tuple[str | None, str | None, int, str]:
    """Retorna (channel, value, confidence, role)."""
    email = (row.get("email") or row.get("email_original") or "").strip()
    email_tipo = row.get("email_tipo") or ""
    email_role = classify_email_role(email)

    if email_tipo == "corporativo" and email and email_role in {"sales", "support", "general"}:
        confidence = 85 if email_role == "sales" else 75
        return "email_corporativo", email, confidence, email_role

    if row.get("whatsapp_valid") and row.get("whatsapp_number_normalized"):
        url = row.get("whatsapp_url") or f"https://wa.me/{row['whatsapp_number_normalized']}"
        return "whatsapp_confirmado", url, 90, "sales"

    telefone = (row.get("telefone_1") or "").strip()
    if telefone:
        return "telefone_comercial", telefone, 60, "general"

    if email_tipo == "corporativo" and email and email_role in {"finance", "accounting"}:
        return "email_corporativo", email, 40, email_role

    if row.get("telefone_candidato_whatsapp"):
        return "whatsapp_candidato", row["telefone_candidato_whatsapp"], 25, "unknown"

    if row.get("instagram_url"):
        return "instagram", row["instagram_url"], 20, "unknown"

    return None, None, 0, "unknown"


def evaluate_qualification(row: dict) -> tuple[str, list[str], list[str]]:
    """
    Retorna (status, rejection_reasons, qualification_reasons).
    status: qualified | rejected | review_required | blocked
    """
    rejection: list[str] = []
    reasons: list[str] = []

    min_confidence = _env_int("PROSPECT_MIN_CONFIDENCE_SCORE", 60)
    min_lead = _env_int("PROSPECT_MIN_LEAD_SCORE", 60)

    confidence = int(row.get("confidence_score") or row.get("digital_score") or 0)
    lead = int(row.get("lead_score") or row.get("digital_score") or 0)
    commerce = row.get("commerce_maturity") or row.get("digital_maturity") or ""
    site_valid = bool(row.get("site_valid"))
    site_status = row.get("site_match_status") or ""

    if site_status == "rejected_webmail":
        return "blocked", ["site_webmail"], reasons
    if site_status and "title_incompativel" in " ".join(row.get("site_validation_reasons") or []):
        rejection.append("site_incompativel_cnae")

    channel, contact_value, contact_conf, contact_role = select_contact_channel(row)
    if not channel or contact_conf < 50:
        rejection.append("sem_canal_outreach_valido")
    elif channel == "whatsapp_candidato":
        rejection.append("whatsapp_nao_confirmado")
    elif channel == "instagram":
        rejection.append("instagram_nao_automatico")

    if confidence < min_confidence:
        rejection.append(f"confidence_baixa:{confidence}<{min_confidence}")
    if lead < min_lead:
        rejection.append(f"lead_baixo:{lead}<{min_lead}")

    if (
        not site_valid
        and row.get("site_reachable")
        and commerce
        not in (
            "ecommerce_confirmado",
            "ecommerce_provavel",
        )
    ):
        if "site_incompativel_cnae" not in rejection:
            rejection.append("site_nao_validado")

    if row.get("decisor_nome"):
        reasons.append("decisor_cadastral")
        if lead >= min_lead:
            lead = min(100, lead + 5)

    if channel == "whatsapp_confirmado":
        reasons.append("whatsapp_confirmado")
    if channel == "email_corporativo":
        reasons.append("email_corporativo")
    if channel == "telefone_comercial":
        reasons.append("telefone_comercial")
    if channel == "instagram":
        rejection.append("instagram_nao_automatico")
    if commerce == "ecommerce_confirmado":
        reasons.append("ecommerce_confirmado")

    blocking = {
        "site_webmail",
        "sem_canal_outreach_valido",
        "whatsapp_nao_confirmado",
        "instagram_nao_automatico",
    }
    hard_fail = [
        r
        for r in rejection
        if r.split(":")[0] in blocking
        or r.startswith("confidence_baixa")
        or r.startswith("lead_baixo")
    ]

    if "instagram_nao_automatico" in rejection and len(hard_fail) == 1:
        return "review_required", rejection, reasons
    if "site_nao_validado" in rejection and not hard_fail:
        return "review_required", rejection, reasons
    if hard_fail:
        return "rejected", rejection, reasons
    if rejection:
        return "review_required", rejection, reasons
    return "qualified", rejection, reasons


def promote_qualified(conn) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT
          v.cnpj, v.cnpj_basico, v.razao_social, v.nome_fantasia, v.uf, v.municipio_descricao,
          v.telefone_1, v.email,
          d.site_url, d.site_final_url, d.site_ativo, d.site_valid, d.site_reachable,
          d.site_match_status, d.site_validation_reasons,
          d.plataforma, d.whatsapp_url, d.whatsapp_valid, d.whatsapp_number_normalized,
          d.telefone_candidato_whatsapp, d.instagram_url, d.linkedin_url,
          d.digital_score, d.digital_maturity,
          d.presence_score, d.commerce_score, d.fit_score, d.pain_score,
          d.confidence_score, d.lead_score,
          d.presence_maturity, d.commerce_maturity, d.lead_classification,
          d.decisor_nome, d.decisor_qualificacao,
          d.faixa_faturamento_estimada, d.faixa_porte_receita,
          v.capital_social, v.opcao_mei, v.opcao_simples,
          d.email_tipo, d.email_original, d.sinais
        FROM cnpj.v_prospect_candidates v
        JOIN cnpj.digital_presenca d ON d.cnpj = v.cnpj
        WHERE d.enrich_status IN ('done', 'partial', 'no_site', 'failed')
        """
    ).fetchall()

    columns = [
        "cnpj",
        "cnpj_basico",
        "razao_social",
        "nome_fantasia",
        "uf",
        "municipio_descricao",
        "telefone_1",
        "email",
        "site_url",
        "site_final_url",
        "site_ativo",
        "site_valid",
        "site_reachable",
        "site_match_status",
        "site_validation_reasons",
        "plataforma",
        "whatsapp_url",
        "whatsapp_valid",
        "whatsapp_number_normalized",
        "telefone_candidato_whatsapp",
        "instagram_url",
        "linkedin_url",
        "digital_score",
        "digital_maturity",
        "presence_score",
        "commerce_score",
        "fit_score",
        "pain_score",
        "confidence_score",
        "lead_score",
        "presence_maturity",
        "commerce_maturity",
        "lead_classification",
        "decisor_nome",
        "decisor_qualificacao",
        "faixa_faturamento_estimada",
        "faixa_porte_receita",
        "capital_social",
        "opcao_mei",
        "opcao_simples",
        "email_tipo",
        "email_original",
        "sinais",
    ]

    stats = {"qualified": 0, "rejected": 0, "review_required": 0, "blocked": 0, "updated": 0}

    for raw in rows:
        item = dict(zip(columns, raw))
        status, rejection, reasons = evaluate_qualification(item)
        channel, contact_value, contact_conf, contact_role = select_contact_channel(item)
        stats[status] = stats.get(status, 0) + 1

        conn.execute(
            """
            INSERT INTO cnpj.prospectos_qualificados (
                cnpj, cnpj_basico, razao_social, nome_fantasia, uf, municipio_descricao,
                telefone_1, email, site_url, site_final_url, site_ativo, plataforma,
                whatsapp_url, instagram_url, linkedin_url,
                digital_score, digital_maturity,
                presence_score, commerce_score, fit_score, pain_score, confidence_score, lead_score,
                presence_maturity, commerce_maturity, lead_classification,
                decisor_nome, decisor_qualificacao, faixa_faturamento_estimada,
                capital_social, opcao_mei, opcao_simples,
                qualification_status, rejection_reasons, qualification_reasons,
                contact_channel, contact_value, contact_confidence, contact_role,
                qualification_version, sinais, qualified_at, last_qualified_at, updated_at
            ) VALUES (
                %(cnpj)s, %(cnpj_basico)s, %(razao_social)s, %(nome_fantasia)s, %(uf)s,
                %(municipio_descricao)s, %(telefone_1)s, %(email)s, %(site_url)s,
                %(site_final_url)s, %(site_ativo)s, %(plataforma)s,
                %(whatsapp_url)s, %(instagram_url)s, %(linkedin_url)s,
                %(digital_score)s, %(digital_maturity)s,
                %(presence_score)s, %(commerce_score)s, %(fit_score)s, %(pain_score)s,
                %(confidence_score)s, %(lead_score)s,
                %(presence_maturity)s, %(commerce_maturity)s, %(lead_classification)s,
                %(decisor_nome)s, %(decisor_qualificacao)s, %(faixa_faturamento_estimada)s,
                %(capital_social)s, %(opcao_mei)s, %(opcao_simples)s,
                %(qualification_status)s, %(rejection_reasons)s, %(qualification_reasons)s,
                %(contact_channel)s, %(contact_value)s, %(contact_confidence)s, %(contact_role)s,
                %(qualification_version)s, %(sinais)s, now(), now(), now()
            )
            ON CONFLICT (cnpj) DO UPDATE SET
                razao_social = EXCLUDED.razao_social,
                nome_fantasia = EXCLUDED.nome_fantasia,
                uf = EXCLUDED.uf,
                municipio_descricao = EXCLUDED.municipio_descricao,
                telefone_1 = EXCLUDED.telefone_1,
                email = EXCLUDED.email,
                site_url = EXCLUDED.site_url,
                site_final_url = EXCLUDED.site_final_url,
                site_ativo = EXCLUDED.site_ativo,
                plataforma = EXCLUDED.plataforma,
                whatsapp_url = EXCLUDED.whatsapp_url,
                instagram_url = EXCLUDED.instagram_url,
                linkedin_url = EXCLUDED.linkedin_url,
                digital_score = EXCLUDED.digital_score,
                digital_maturity = EXCLUDED.digital_maturity,
                presence_score = EXCLUDED.presence_score,
                commerce_score = EXCLUDED.commerce_score,
                fit_score = EXCLUDED.fit_score,
                pain_score = EXCLUDED.pain_score,
                confidence_score = EXCLUDED.confidence_score,
                lead_score = EXCLUDED.lead_score,
                presence_maturity = EXCLUDED.presence_maturity,
                commerce_maturity = EXCLUDED.commerce_maturity,
                lead_classification = EXCLUDED.lead_classification,
                decisor_nome = EXCLUDED.decisor_nome,
                decisor_qualificacao = EXCLUDED.decisor_qualificacao,
                faixa_faturamento_estimada = EXCLUDED.faixa_faturamento_estimada,
                capital_social = EXCLUDED.capital_social,
                opcao_mei = EXCLUDED.opcao_mei,
                opcao_simples = EXCLUDED.opcao_simples,
                qualification_status = EXCLUDED.qualification_status,
                rejection_reasons = EXCLUDED.rejection_reasons,
                qualification_reasons = EXCLUDED.qualification_reasons,
                contact_channel = EXCLUDED.contact_channel,
                contact_value = EXCLUDED.contact_value,
                contact_confidence = EXCLUDED.contact_confidence,
                contact_role = EXCLUDED.contact_role,
                qualification_version = EXCLUDED.qualification_version,
                sinais = EXCLUDED.sinais,
                last_qualified_at = now(),
                updated_at = now(),
                qualified_at = CASE
                  WHEN EXCLUDED.qualification_status = 'qualified' THEN now()
                  ELSE cnpj.prospectos_qualificados.qualified_at
                END
            """,
            {
                "cnpj": item["cnpj"],
                "cnpj_basico": item["cnpj_basico"],
                "razao_social": item["razao_social"],
                "nome_fantasia": item["nome_fantasia"],
                "uf": item["uf"],
                "municipio_descricao": item["municipio_descricao"],
                "telefone_1": item["telefone_1"],
                "email": item["email"],
                "site_url": item["site_url"],
                "site_final_url": item["site_final_url"],
                "site_ativo": item["site_ativo"],
                "plataforma": item["plataforma"],
                "whatsapp_url": item["whatsapp_url"] if item.get("whatsapp_valid") else None,
                "instagram_url": item["instagram_url"],
                "linkedin_url": item["linkedin_url"],
                "digital_score": item["lead_score"] or item["digital_score"],
                "digital_maturity": item["commerce_maturity"] or item["digital_maturity"],
                "presence_score": item["presence_score"],
                "commerce_score": item["commerce_score"],
                "fit_score": item["fit_score"],
                "pain_score": item["pain_score"],
                "confidence_score": item["confidence_score"],
                "lead_score": item["lead_score"],
                "presence_maturity": item["presence_maturity"],
                "commerce_maturity": item["commerce_maturity"],
                "lead_classification": item["lead_classification"],
                "decisor_nome": item["decisor_nome"],
                "decisor_qualificacao": item["decisor_qualificacao"],
                "faixa_faturamento_estimada": item["faixa_porte_receita"]
                or item["faixa_faturamento_estimada"],
                "capital_social": item["capital_social"],
                "opcao_mei": item["opcao_mei"],
                "opcao_simples": item["opcao_simples"],
                "qualification_status": status,
                "rejection_reasons": rejection,
                "qualification_reasons": reasons,
                "contact_channel": channel,
                "contact_value": contact_value,
                "contact_confidence": contact_conf,
                "contact_role": contact_role,
                "qualification_version": QUALIFICATION_VERSION,
                "sinais": Jsonb(item["sinais"] if item.get("sinais") else {}),
            },
        )
        stats["updated"] += 1

    conn.commit()
    total_q = conn.execute(
        "SELECT COUNT(*) FROM cnpj.prospectos_qualificados WHERE qualification_status = 'qualified'"
    ).fetchone()[0]
    stats["total_qualified"] = total_q
    log.info("Qualificação v2: %s", stats)
    return stats
