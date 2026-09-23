import cnpj_etl.intelligence.pipeline as pipeline


def test_until_empty_publishes_after_each_non_empty_round(monkeypatch):
    rounds = iter(
        [
            {"processed": 100, "success": 80, "no_data": 20, "failed": 0, "skipped": 0},
            {"processed": 50, "success": 40, "no_data": 8, "failed": 2, "skipped": 0},
            {"processed": 0, "success": 0, "no_data": 0, "failed": 0, "skipped": 0},
        ]
    )
    monkeypatch.setattr(pipeline, "run_intelligence", lambda *_args, **_kwargs: next(rounds))
    published = []

    stats = pipeline.run_intelligence_until_empty(
        object(),
        after_round=lambda number, result: published.append((number, result["processed"])),
    )

    assert published == [(1, 100), (2, 50)]
    assert stats["processed"] == 150
    assert stats["rounds"] == 3
