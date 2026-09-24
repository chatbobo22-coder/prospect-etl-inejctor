"""Recomendações determinísticas baseadas somente em evidências reais."""


def recommend(profile: dict, people: list[dict]) -> dict:
    keys = {item["key"] for item in profile["score_breakdown"]}
    products: list[str] = []
    if "whatsapp_sales" in keys:
        products.extend(["ChatBô", "automação de atendimento"])
    if profile["has_sales_team"]:
        products.extend(["CRM", "MestreLead", "automação comercial"])
    if profile["has_erp"]:
        products.extend(["integrações", "BI e dashboards"])
    if profile["has_ecommerce"]:
        products.extend(["ChatBô", "CRM", "automação de e-commerce"])
    if keys & {"software_hiring", "ai_hiring"}:
        products.extend(["Tironi Tech Club", "software sob medida"])
    if "ai_hiring" in keys:
        products.extend(["consultoria de IA", "automação com IA"])
    if not products:
        products = ["diagnóstico de automação", "consultoria"]
    products = list(dict.fromkeys(products))

    complexity = sum((profile["has_crm"], profile["has_erp"], profile["has_ecommerce"]))
    score = profile["tironi_score"]
    if score >= 86 and (complexity >= 2 or profile["active_units"] >= 5):
        plan = "Enterprise"
    elif score >= 75 or "ai_hiring" in keys:
        plan = "Club Dedicated"
    elif score >= 60 or complexity >= 2:
        plan = "Club Full Access"
    elif score >= 40:
        plan = "Club Growth"
    else:
        plan = "Club Start"

    primary = next((p for p in people if p.get("is_decision_maker")), None)
    if primary:
        target = f"{primary.get('full_name')} ({primary.get('role_title') or 'decisor'})"
        action = f"Abordar {target} usando a evidência mais recente"
    elif profile["has_sales_team"]:
        action = "Identificar e abordar o Diretor ou Head Comercial"
    else:
        action = "Oferecer uma análise gratuita da operação e identificar o decisor"

    top = profile["positive_signals"][:5]
    reason = (
        f"Empresa classificada como {profile['classification'].title()} porque "
        + ", ".join(value.lower() for value in top)
        + "."
        if top else "Ainda não há sinais suficientes para priorizar esta empresa."
    )
    evidence = top[:2]
    approach = (
        f"Identificamos {evidence[0].lower()}"
        + (f" e {evidence[1].lower()}" if len(evidence) > 1 else "")
        + ". A Tironi pode ajudar a conectar vendas, atendimento, dados e automação; "
          "vale uma conversa breve para validar onde existe maior ganho operacional."
        if evidence else "Convide a empresa para um diagnóstico objetivo antes de sugerir uma solução."
    )
    return {"recommended_products": products, "recommended_plan": plan,
            "next_best_action": action, "why_this_lead": reason,
            "sales_approach": approach}
