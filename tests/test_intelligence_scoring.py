from datetime import datetime, timedelta, timezone

from cnpj_etl.intelligence.scoring import calculate_profile


def test_profile_a_requires_recent_intent_and_good_coverage():
    now = datetime.now(timezone.utc)
    signals = [
        {"category": "fit", "score": 25, "confidence": 100, "title": "Bom fit", "source_code": "receita", "observed_at": now},
        {"category": "capacity", "score": 20, "confidence": 100, "title": "Capacidade", "source_code": "cvm", "observed_at": now},
        {"category": "intent", "score": 25, "confidence": 100, "title": "Expansão", "source_code": "gdelt", "observed_at": now, "expires_at": now + timedelta(days=30)},
        {"category": "pain", "score": 15, "confidence": 100, "title": "Dor", "source_code": "pagespeed", "observed_at": now},
    ]
    people = [{"is_decision_maker": True}]
    states = [
        {"source_code": code, "status": "success"}
        for code in ("receita", "cvm", "gdelt", "website")
    ]

    profile = calculate_profile(signals, people, states)

    assert profile["profile_quality"] == "A"
    assert profile["profile_score"] == 95
    assert profile["decision_makers_count"] == 1


def test_expired_signal_is_not_scored():
    now = datetime.now(timezone.utc)
    profile = calculate_profile(
        [
            {
                "category": "intent",
                "score": 25,
                "confidence": 100,
                "title": "Antigo",
                "source_code": "gdelt",
                "observed_at": now - timedelta(days=300),
                "expires_at": now - timedelta(days=1),
            }
        ],
        [],
        [],
    )

    assert profile["intent_score"] == 0
    assert profile["signals_count"] == 0
    assert profile["profile_quality"] is None


def test_delivery_risk_reduces_profile_score():
    now = datetime.now(timezone.utc)
    base = [
        {"category": "fit", "score": 25, "confidence": 100, "title": "Fit", "source_code": "receita", "observed_at": now},
        {"category": "capacity", "score": 20, "confidence": 100, "title": "Capacidade", "source_code": "cvm", "observed_at": now},
        {"category": "intent", "score": 25, "confidence": 100, "title": "Intenção", "source_code": "gdelt", "observed_at": now},
        {"category": "pain", "score": 20, "confidence": 100, "title": "Dor", "source_code": "website", "observed_at": now},
    ]
    states = [{"source_code": code, "status": "success"} for code in ("receita", "cvm", "gdelt", "website")]
    without_risk = calculate_profile(base, [], states)
    with_risk = calculate_profile(
        base + [{"category": "risk", "score": 20, "confidence": 100, "title": "Bounce", "source_code": "commercial_feedback", "observed_at": now}],
        [],
        states,
    )

    assert with_risk["profile_score"] == without_risk["profile_score"] - 20
    assert with_risk["risk_penalty"] == 20
