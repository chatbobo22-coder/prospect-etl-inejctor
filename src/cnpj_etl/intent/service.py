"""Persistência, busca e visão executiva do motor de intenção."""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .recommendations import recommend
from .scoring import calculate_tironi_score
from .segmentation import segment_company


def refresh_tironi_profile(conn, cnpj: str) -> dict:
    company, signals, people, technologies = _profile_inputs(conn, cnpj)
    if not company:
        return {}
    score = calculate_tironi_score(company, signals, people, technologies)
    score["segment_fit"] = segment_company(company, technologies, signals)
    score.update(recommend(score, people))
    score["risks"] = _risks(company, signals)
    score["signals_count"] = len(_deduplicate_signals(signals))
    score["latest_signal_at"] = max(
        (signal.get("observed_at") for signal in signals if signal.get("observed_at")),
        default=None,
    )
    previous = conn.execute(
        "SELECT tironi_score,classification FROM intelligence.tironi_profiles WHERE cnpj=%s",
        (cnpj,),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO intelligence.tironi_profiles
          (cnpj,segment_fit,tironi_score,classification,employee_count,
           estimated_sellers_count,active_units,has_whatsapp,has_crm,has_erp,
           has_ecommerce,has_sales_team,why_this_lead,positive_signals,risks,
           recommended_products,recommended_plan,next_best_action,sales_approach,
           score_breakdown,signals_count,latest_signal_at,calculated_at,updated_at)
        VALUES (%(cnpj)s,%(segment_fit)s,%(tironi_score)s,%(classification)s,
          %(employee_count)s,%(estimated_sellers_count)s,%(active_units)s,
          %(has_whatsapp)s,%(has_crm)s,%(has_erp)s,%(has_ecommerce)s,%(has_sales_team)s,
          %(why_this_lead)s,%(positive_signals)s,%(risks)s,%(recommended_products)s,
          %(recommended_plan)s,%(next_best_action)s,%(sales_approach)s,
          %(score_breakdown)s,%(signals_count)s,%(latest_signal_at)s,now(),now())
        ON CONFLICT (cnpj) DO UPDATE SET
          segment_fit=EXCLUDED.segment_fit,tironi_score=EXCLUDED.tironi_score,
          classification=EXCLUDED.classification,employee_count=EXCLUDED.employee_count,
          estimated_sellers_count=EXCLUDED.estimated_sellers_count,
          active_units=EXCLUDED.active_units,has_whatsapp=EXCLUDED.has_whatsapp,
          has_crm=EXCLUDED.has_crm,has_erp=EXCLUDED.has_erp,
          has_ecommerce=EXCLUDED.has_ecommerce,has_sales_team=EXCLUDED.has_sales_team,
          why_this_lead=EXCLUDED.why_this_lead,positive_signals=EXCLUDED.positive_signals,
          risks=EXCLUDED.risks,recommended_products=EXCLUDED.recommended_products,
          recommended_plan=EXCLUDED.recommended_plan,next_best_action=EXCLUDED.next_best_action,
          sales_approach=EXCLUDED.sales_approach,score_breakdown=EXCLUDED.score_breakdown,
          signals_count=EXCLUDED.signals_count,latest_signal_at=EXCLUDED.latest_signal_at,
          calculated_at=now(),updated_at=now()
        """,
        {"cnpj": cnpj, **score, "score_breakdown": Jsonb(score["score_breakdown"])},
    )
    if previous is None or previous[0] != score["tironi_score"]:
        conn.execute(
            """INSERT INTO intelligence.tironi_score_history
               (cnpj,tironi_score,classification,score_breakdown,reasons)
               VALUES (%s,%s,%s,%s,%s)""",
            (cnpj, score["tironi_score"], score["classification"],
             Jsonb(score["score_breakdown"]), score["positive_signals"][:5]),
        )
        _record_alerts(conn, cnpj, previous, score)
    return score


def rebuild_profiles(conn, limit: int = 1000) -> int:
    rows = conn.execute(
        """SELECT p.cnpj FROM cnpj.prospectos_qualificados p
           WHERE p.qualification_status='qualified' AND p.lead_quality IN ('A','B')
           ORDER BY p.updated_at DESC LIMIT %s""",
        (limit,),
    ).fetchall()
    for row in rows:
        refresh_tironi_profile(conn, row[0])
    conn.commit()
    return len(rows)


def list_opportunities(conn, filters: dict[str, Any]) -> dict:
    page = max(1, int(filters.get("page") or 1))
    page_size = max(1, min(100, int(filters.get("page_size") or 25)))
    conditions = ["p.qualification_status='qualified'", "p.lead_quality IN ('A','B')"]
    params: list[Any] = []
    _apply_preset(filters)

    def add(condition: str, value: Any):
        conditions.append(condition)
        params.append(value)

    if filters.get("q"):
        add("(p.cnpj LIKE %s OR p.razao_social ILIKE %s OR p.nome_fantasia ILIKE %s OR p.email ILIKE %s)",
            f"%{filters['q']}%")
        params.extend([params[-1], params[-1], params[-1]])
    if filters.get("cnpj"):
        add("p.cnpj = %s", filters["cnpj"])
    for key, column in (("state", "p.uf"), ("city", "p.municipio_descricao"),
                        ("segment_fit", "t.segment_fit"),
                        ("classification", "t.classification")):
        if filters.get(key):
            add(f"{column} = %s", filters[key])
    for key, column in (("min_score", "t.tironi_score"),
                        ("min_employees", "t.employee_count"),
                        ("min_units", "t.active_units")):
        if filters.get(key) is not None:
            add(f"COALESCE({column},0) >= %s", int(filters[key]))
    for key, column in (("has_whatsapp", "t.has_whatsapp"), ("has_crm", "t.has_crm"),
                        ("has_erp", "t.has_erp"), ("has_ecommerce", "t.has_ecommerce"),
                        ("has_sales_team", "t.has_sales_team")):
        if filters.get(key) is not None:
            add(f"{column} = %s", bool(filters[key]))
    if filters.get("technology"):
        add("EXISTS (SELECT 1 FROM intelligence.company_technologies ct WHERE ct.cnpj=p.cnpj AND ct.active=true AND ct.technology ILIKE %s)",
            f"%{filters['technology']}%")
    if filters.get("signal_type"):
        types = [item.strip() for item in str(filters["signal_type"]).split(",") if item.strip()]
        add("EXISTS (SELECT 1 FROM intelligence.company_signals cs WHERE cs.cnpj=p.cnpj AND cs.signal_type = ANY(%s) AND (cs.expires_at IS NULL OR cs.expires_at>now()))", types)
    if filters.get("signal_since_days") is not None:
        add("t.latest_signal_at >= now() - (%s * interval '1 day')", int(filters["signal_since_days"]))

    where = " AND ".join(conditions)
    params.extend([page_size, (page - 1) * page_size])
    query = f"""
      SELECT p.cnpj,p.razao_social,p.nome_fantasia,p.uf,p.municipio_descricao,p.email,
        p.telefone_1,COALESCE(p.site_final_url,p.site_url) AS website,p.whatsapp_url,
        p.faixa_faturamento_estimada,t.*,ol.id AS lead_id,
        COUNT(*) OVER() AS total_count,
        (SELECT jsonb_agg(x ORDER BY (x->>'impact')::int DESC) FROM
          (SELECT value AS x FROM jsonb_array_elements(t.score_breakdown) value LIMIT 3) s
        ) AS top_signals,
        (SELECT jsonb_agg(jsonb_build_object('technology',ct.technology,'category',ct.category))
           FROM intelligence.company_technologies ct WHERE ct.cnpj=p.cnpj AND ct.active=true) technologies,
        (SELECT jsonb_build_object('name',cp.full_name,'role',cp.role_title,
                  'linkedin_url',cp.linkedin_url,'email',cp.business_email,'phone',cp.business_phone,
                  'confidence',cp.confidence,'source',cp.source_code)
           FROM intelligence.company_people cp WHERE cp.cnpj=p.cnpj AND cp.active=true
           ORDER BY cp.is_decision_maker DESC,cp.priority_score DESC,cp.confidence DESC LIMIT 1
        ) AS primary_decision_maker
      FROM cnpj.prospectos_qualificados p
      JOIN intelligence.tironi_profiles t ON t.cnpj=p.cnpj
      LEFT JOIN outreach.leads ol ON ol.cnpj=p.cnpj
      WHERE {where}
      ORDER BY t.tironi_score DESC,t.latest_signal_at DESC NULLS LAST,t.signals_count DESC
      LIMIT %s OFFSET %s
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        rows = list(cur.fetchall())
    total = int(rows[0]["total_count"]) if rows else 0
    for row in rows:
        row.pop("total_count", None)
    return {"items": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size}


def get_opportunity(conn, cnpj: str) -> dict | None:
    result = list_opportunities(conn, {"cnpj": cnpj, "page_size": 1})
    item = next((value for value in result["items"] if value["cnpj"].strip() == cnpj), None)
    if not item:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT id,signal_type,category,title,description,score,
                    confidence/100.0 AS confidence,observed_at,expires_at,source_code AS source_name,
                    source_url,raw_data FROM intelligence.company_signals
                    WHERE cnpj=%s AND (expires_at IS NULL OR expires_at>now())
                    ORDER BY observed_at DESC,score DESC""", (cnpj,))
        item["signals"] = _deduplicate_signals(list(cur.fetchall()))
        cur.execute("""SELECT full_name AS name,role_title AS role,linkedin_url,business_email AS email,
                    business_phone AS phone,confidence/100.0 AS confidence,source_code AS source,
                    source_url FROM intelligence.company_people
                    WHERE cnpj=%s AND active=true ORDER BY is_decision_maker DESC,
                    priority_score DESC,confidence DESC""", (cnpj,))
        item["decision_makers"] = list(cur.fetchall())
        cur.execute("""SELECT tironi_score,classification,reasons,calculated_at
                    FROM intelligence.tironi_score_history WHERE cnpj=%s
                    ORDER BY calculated_at DESC LIMIT 30""", (cnpj,))
        item["score_history"] = list(cur.fetchall())
    return item


def _profile_inputs(conn, cnpj: str):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT v.*,d.* FROM cnpj.v_prospect_candidates v
                    LEFT JOIN cnpj.digital_presenca d ON d.cnpj=v.cnpj WHERE v.cnpj=%s""", (cnpj,))
        company = cur.fetchone()
        cur.execute("SELECT * FROM intelligence.company_signals WHERE cnpj=%s AND (expires_at IS NULL OR expires_at>now())", (cnpj,))
        signals = list(cur.fetchall())
        cur.execute("SELECT * FROM intelligence.company_people WHERE cnpj=%s AND active=true ORDER BY is_decision_maker DESC,priority_score DESC", (cnpj,))
        people = list(cur.fetchall())
        cur.execute("SELECT * FROM intelligence.company_technologies WHERE cnpj=%s AND active=true", (cnpj,))
        technologies = list(cur.fetchall())
    return company, signals, people, technologies


def _deduplicate_signals(signals: list[dict]) -> list[dict]:
    unique = {}
    for signal in signals:
        raw = signal.get("raw_data") or {}
        evidence_key = raw.get("job_id") or raw.get("canonical_url") or signal.get("source_url") or signal.get("title")
        key = (signal.get("signal_type"), str(evidence_key).strip().lower())
        current = unique.get(key)
        if current is None or int(signal.get("confidence") or 0) > int(current.get("confidence") or 0):
            unique[key] = signal
    return list(unique.values())


def _risks(company: dict, signals: list[dict]) -> list[str]:
    risks = [signal["title"] for signal in signals if signal.get("category") == "risk"]
    if not company.get("whatsapp_valid"):
        risks.append("WhatsApp comercial não confirmado")
    if not company.get("site_valid"):
        risks.append("Site institucional não confirmado")
    return list(dict.fromkeys(risks))[:5]


def _record_alerts(conn, cnpj: str, previous, score: dict) -> None:
    old = int(previous[0]) if previous else 0
    events = []
    if old < 70 <= score["tironi_score"]:
        events.append("lead_became_hot")
    if score["tironi_score"] - old >= 15:
        events.append("relevant_score_increase")
    for event in events:
        fingerprint = hashlib.sha256(f"{event}:{old}:{score['tironi_score']}".encode()).hexdigest()
        conn.execute("""INSERT INTO intelligence.intent_alert_events
                     (cnpj,event_type,previous_score,current_score,event_fingerprint,payload)
                     VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                     (cnpj,event,old,score["tironi_score"],fingerprint,
                      Jsonb({"classification": score["classification"]})))


def _apply_preset(filters: dict) -> None:
    preset = filters.get("preset")
    values = {
        "ecommerce_hot": {"has_ecommerce": True, "has_whatsapp": True, "min_score": 60},
        "automotive": {"segment_fit": "automotive", "has_whatsapp": True},
        "distributors": {"segment_fit": "distributor_wholesale", "has_erp": True},
        "ai_hiring": {"signal_type": "ai_hiring,tech_hiring", "signal_since_days": 90},
        "dev_hiring": {"signal_type": "software_hiring", "signal_since_days": 90},
        "expanding_networks": {"signal_type": "multiunit,new_branch,expansion", "signal_since_days": 90},
    }.get(preset, {})
    for key, value in values.items():
        filters.setdefault(key, value)
