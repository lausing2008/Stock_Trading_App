SET default_transaction_read_only = on;
SET statement_timeout = '120s';
BEGIN READ ONLY;
WITH latest AS (
  SELECT symbol, max(as_of) AS as_of FROM option_chain_history GROUP BY symbol
), rows AS (
  SELECT h.symbol, h.as_of, h.option_symbol,
         substring(upper(trim(h.option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') AS root
  FROM option_chain_history h JOIN latest l ON l.symbol = h.symbol AND l.as_of = h.as_of
), cls AS (
  SELECT symbol, as_of, option_symbol,
    CASE
      WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
      WHEN root IS NULL THEN 'unreadable_symbol'
      WHEN replace(CASE replace(root,'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT'
                     WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(root,'.','') END, '.', '')
           = replace(upper(symbol), '.', '') THEN 'standard'
      ELSE 'adjusted_nonstandard_root'
    END AS status, root
  FROM rows
)
SELECT 'TOTALS' AS k, status, count(*) AS contracts, count(DISTINCT symbol) AS symbols FROM cls GROUP BY status ORDER BY status;
