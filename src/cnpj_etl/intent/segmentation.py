"""Segmentação determinística baseada em CNAE e evidências públicas."""

import re


def segment_company(company: dict, technologies: list[dict], signals: list[dict]) -> str:
    cnae = str(company.get("cnae_fiscal_principal") or "")
    name = f"{company.get('razao_social') or ''} {company.get('nome_fantasia') or ''}".lower()
    tech = " ".join(str(item.get("technology") or "") for item in technologies).lower()
    types = {str(item.get("signal_type") or "") for item in signals}
    if cnae.startswith(("45",)) or re.search(r"concession.r|ve.culos|automot", name):
        return "automotive"
    if cnae.startswith(("46",)) or re.search(r"distribuid|atacad", name):
        return "distributor_wholesale"
    if cnae.startswith(("47",)) or "ecommerce_detected" in types or re.search(
        r"shopify|woocommerce|nuvemshop|tray|vtex|magento", tech
    ):
        return "ecommerce_retail"
    if "multiunit" in types or re.search(r"franqui|rede de lojas", name):
        return "franchise_multiunit"
    if cnae.startswith(("10", "11", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31", "32", "33")):
        return "industry_b2b"
    if cnae.startswith(("62", "63")) or re.search(r"software|digital|tecnologia", name):
        return "digital_company"
    if cnae.startswith(("64", "65", "66", "68", "69", "70", "71", "73", "74", "75", "85", "86")):
        return "high_ticket_services"
    return "other"
