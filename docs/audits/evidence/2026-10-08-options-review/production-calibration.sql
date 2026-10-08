BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT direction,count(*) FILTER(WHERE is_correct_10d IS NOT NULL) AS calibration_rows,count(*) FILTER(WHERE expiry<fired_date AND is_correct_10d IS NOT NULL) AS expired_before_alert_in_calibration,count(DISTINCT fired_date) FILTER(WHERE is_correct_10d IS NOT NULL) AS calibration_dates FROM options_flow_alert_outcomes GROUP BY direction;
COMMIT;
