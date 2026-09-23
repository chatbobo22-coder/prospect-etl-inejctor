"""Pontuação transparente do perfil comercial."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

CATEGORY_LIMITS = {"fit": 25, "capacity": 20, "intent": 25, "pain": 20}


def calculate_profile(signals: list[dict], people: list[dict], states: list[dict]) -> dict:
    now = datetime.now(timezone.utc)
    active = [
        s
        for s in signals
        if not s.get("expires_at") or _aware(s["expires_at"]) > now
    ]
    scores: dict[str, int] = {}
    for category, limit in CATEGORY_LIMITS.items():
        weighted = sum(
            int(s.get("score") or 0) * int(s.get("confidence") or 0) / 100
            for s in active
            if s.get("category") == category
        )
        scores[category] = max(0, min(limit, round(weighted)))

    succeeded = sorted({s["source_code"] for s in states if s.get("status") == "success"})
    pending = sorted(
        {s["source_code"] for s in states if s.get("status") in {"pending", "failed"}}
    )
    confidence = min(10, len(succeeded) * 2 + (1 if active else 0) + (1 if people else 0))
    risk_penalty = min(
        20,
        round(
            sum(
                abs(int(s.get("score") or 0)) * int(s.get("confidence") or 0) / 100
                for s in active
                if s.get("category") == "risk"
            )
        ),
    )
    recent_intent = [
        s
        for s in active
        if s.get("category") == "intent"
        and _aware(s.get("observed_at") or now) >= now - timedelta(days=90)
        and int(s.get("score") or 0) > 0
    ]
    total = max(0, min(100, sum(scores.values()) + confidence - risk_penalty))
    quality = "A" if total >= 75 and confidence >= 7 and recent_intent else None
    if not quality and total >= 60 and confidence >= 6:
        quality = "B"

    decision_makers = sum(bool(p.get("is_decision_maker")) for p in people)
    reasons = [s["title"] for s in sorted(active, key=_signal_rank, reverse=True)[:5]]
    band = "alta" if scores["capacity"] >= 15 else "média" if scores["capacity"] >= 8 else "baixa"
    summary_parts = [f"perfil {quality or 'em formação'} ({total}/100)"]
    if decision_makers:
        summary_parts.append(f"{decision_makers} decisor(es) público(s)")
    if recent_intent:
        summary_parts.append("intenção recente identificada")
    return {
        "fit_score": scores["fit"],
        "capacity_score": scores["capacity"],
        "intent_score": scores["intent"],
        "pain_score": scores["pain"],
        "data_confidence_score": confidence,
        "profile_score": total,
        "profile_quality": quality,
        "estimated_capacity_band": band,
        "intent_last_seen_at": max(
            (_aware(s.get("observed_at") or now) for s in recent_intent), default=None
        ),
        "decision_makers_count": decision_makers,
        "signals_count": len(active),
        "sources_success": succeeded,
        "sources_pending": pending,
        "summary": "; ".join(summary_parts).capitalize() + ".",
        "reasons": reasons,
        "risk_penalty": risk_penalty,
    }


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _signal_rank(signal: dict) -> float:
    return abs(int(signal.get("score") or 0)) * int(signal.get("confidence") or 0) / 100
