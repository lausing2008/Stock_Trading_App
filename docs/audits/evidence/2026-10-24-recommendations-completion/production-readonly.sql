BEGIN READ ONLY;
SET LOCAL statement_timeout = '10s';
SELECT json_build_object('kind','cohort_by_session','rows',coalesce(json_agg(t),'[]'::json)) FROM (
 SELECT observed_to,delivery_mode,count(*) AS events,count(*) FILTER (WHERE rule='unmeasurable') AS exclusions,
 count(*) FILTER (WHERE evidence_status='verified') AS verified,
 count(*) FILTER (WHERE evidence->>'price_source'='live_quote') AS live_quote_events
 FROM risk_events WHERE detected_at >= '2026-10-16' AND detected_at < '2026-10-23 04:00:00' AND observed_to BETWEEN '2026-10-16' AND '2026-10-22'
 GROUP BY observed_to,delivery_mode ORDER BY observed_to,delivery_mode
) t;
SELECT json_build_object('kind','risk_receipts','rows',coalesce(json_agg(t),'[]'::json)) FROM (
 SELECT state,count(*) AS receipts FROM risk_event_receipts r JOIN risk_events e ON e.id=r.event_id
 WHERE e.detected_at >= '2026-10-16' AND e.detected_at < '2026-10-23 04:00:00' AND e.observed_to BETWEEN '2026-10-16' AND '2026-10-22' GROUP BY state ORDER BY state
) t;
SELECT json_build_object('kind','risk_outbox','rows',coalesce(json_agg(t),'[]'::json)) FROM (
 SELECT alert_type,state,count(*) AS messages FROM notification_outbox WHERE alert_type='price_risk' GROUP BY alert_type,state
) t;
SELECT json_build_object('kind','company_events','rows',coalesce(json_agg(t),'[]'::json)) FROM (
 SELECT evidence_status,count(*) AS events FROM news_impact_events GROUP BY evidence_status
) t;
ROLLBACK;
