SET default_transaction_read_only = on;
SET statement_timeout = '300s';
BEGIN READ ONLY;
WITH c AS (SELECT stage, symbol, CASE
      WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
      WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
      WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','')
              WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP'
              ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END)
           = replace(upper(symbol), '.', '') THEN 'standard'
      ELSE 'adjusted_nonstandard_root'
    END AS status FROM options_income_positions)
SELECT stage, status, count(*) AS positions, count(DISTINCT symbol) AS symbols,
       string_agg(DISTINCT symbol, ',') FILTER (WHERE status <> 'standard') AS affected_symbols
FROM c GROUP BY stage, status ORDER BY stage, status;
ROLLBACK;
