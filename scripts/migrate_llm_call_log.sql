-- AUD-LLMUSAGE: create llm_call_log, one row per real Anthropic API call across all 9 call
-- sites in 6 services. Built to catch the NEXT usage spike after the 2026-09-05 incident
-- (six weeks of undetected news-intelligence deploy-drift reclassifying the same EDGAR
-- filings with zero dedup, 5.44M Haiku tokens burned on 2026-09-05 alone — discovered only
-- by chance, days later, because no call site logged token usage anywhere).
-- Already applied directly to production; this file formalizes that migration for the repo.
-- Safe to re-run (uses IF NOT EXISTS pattern).

CREATE TABLE IF NOT EXISTS llm_call_log (
    id BIGSERIAL PRIMARY KEY,
    service VARCHAR(32) NOT NULL,
    call_site VARCHAR(64) NOT NULL,
    model VARCHAR(64) NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    duration_ms INTEGER,
    status VARCHAR(16) NOT NULL,
    http_status INTEGER,
    error TEXT,
    context JSON,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_llm_call_log_service ON llm_call_log (service);
CREATE INDEX IF NOT EXISTS ix_llm_call_log_call_site ON llm_call_log (call_site);
CREATE INDEX IF NOT EXISTS ix_llm_call_log_created_at ON llm_call_log (created_at);
CREATE INDEX IF NOT EXISTS ix_llm_call_log_service_created ON llm_call_log (service, created_at);
