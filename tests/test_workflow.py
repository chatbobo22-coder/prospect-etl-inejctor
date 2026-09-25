from pathlib import Path


WORKFLOW = Path(".github/workflows/prospect-pipeline.yml")


def test_pipeline_chains_only_after_full_batch_processing():
    text = WORKFLOW.read_text(encoding="utf-8")

    finish = text.index("- name: Finish remaining enrichment + qualification")
    summary = text.index("- name: Summary counts")
    check = text.index("- name: Check whether another sequential batch exists")
    queue = text.index("- name: Queue next sequential batch after this one is complete")

    assert finish < summary < check < queue
    assert "cancel-in-progress: false" in text
    assert "-f continuous=true" in text
    assert "-f force_etl=true" in text
    assert "SUM(last_run_rows)" in text


def test_pipeline_separates_cheap_entry_and_final_quality_thresholds():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "PROSPECT_MIN_PRE_SCORE=${{ inputs.min_lead_score || '70' }}" in text
    assert "INTELLIGENCE_ENTRY_MIN_SCORE=${{ inputs.min_lead_score || '70' }}" in text
    assert "PROSPECT_MIN_LEAD_SCORE=${{ inputs.min_lead_score || '70' }}" in text
    assert "ENRICH_WORKERS=24" in text
    assert "INTELLIGENCE_WORKERS=12" in text


def test_pipeline_publishes_email_first_and_bounds_deep_enrichment():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "MARKETING_BATCH_SIZE=${{ inputs.load_batch_size || '100000' }}" in text
    assert "MARKETING_EMAIL_WORKERS=64" in text
    assert "ENRICH_CANDIDATE_VIEW=cnpj.v_marketing_enrichment_candidates" in text
    assert "ENRICH_MAX_ROUNDS=1" in text
