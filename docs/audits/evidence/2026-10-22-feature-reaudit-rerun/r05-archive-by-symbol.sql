SET default_transaction_read_only = on;
SET statement_timeout = '60s';
SELECT 'AAOI', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AAOI') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AAON', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AAON') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AAOX', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AAOX') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AAPL', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AAPL') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'ABBV', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'ABBV') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'ADBE', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'ADBE') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AI', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AI') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AKAM', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AKAM') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'ALAB', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'ALAB') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AMAT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AMAT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AMD', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AMD') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AMGN', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AMGN') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AMKR', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AMKR') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AMZA', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AMZA') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AMZN', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AMZN') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'ANET', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'ANET') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'ANF', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'ANF') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'APP', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'APP') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'ARMK', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'ARMK') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'ASTS', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'ASTS') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'ASX', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'ASX') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AVGO', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AVGO') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AXON', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AXON') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'BE', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'BE') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'BIP', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'BIP') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'BKSY', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'BKSY') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'BULL', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'BULL') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CAT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CAT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CBRS', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CBRS') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CCJ', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CCJ') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CEG', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CEG') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CGNX', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CGNX') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CIEN', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CIEN') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CLBT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CLBT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CM', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CM') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'COHR', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'COHR') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'COIN', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'COIN') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CORT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CORT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRDO', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRDO') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWD', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWD') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWV', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWV') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CWEN', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CWEN') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'DDOG', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'DDOG') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'DELL', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'DELL') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'DIA', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'DIA') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'DIS', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'DIS') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'DIVO', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'DIVO') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'DT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'DT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'EE', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'EE') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'EPD', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'EPD') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'FCEL', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'FCEL') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'FHB', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'FHB') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GDX', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GDX') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GEV', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GEV') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GFS', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GFS') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLD', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLD') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLW', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLW') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GOOG', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GOOG') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GPN', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GPN') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GRNT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GRNT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'HOOD', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'HOOD') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'HPE', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'HPE') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'HWM', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'HWM') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'IBM', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'IBM') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'IGV', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'IGV') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'IMVT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'IMVT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'INTC', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'INTC') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'IONQ', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'IONQ') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'IREN', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'IREN') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'JPM', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'JPM') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'KGS', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'KGS') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'KMT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'KMT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'KTOS', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'KTOS') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'LITE', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'LITE') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'LMT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'LMT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MDB', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MDB') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'META', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'META') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MGY', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MGY') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MNDY', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MNDY') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MRVL', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MRVL') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MSFT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MSFT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MU', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MU') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MUU', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MUU') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MXL', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MXL') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'NATL', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'NATL') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'NBIS', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'NBIS') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'NEE', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'NEE') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'NET', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'NET') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'NOC', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'NOC') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'NOK', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'NOK') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'NOW', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'NOW') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'NU', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'NU') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'NVDA', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'NVDA') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'ORCL', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'ORCL') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'OSCR', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'OSCR') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'PL', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'PL') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'PLTR', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'PLTR') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'POET', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'POET') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QBTS', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QBTS') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QLD', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QLD') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQ', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQ') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQM', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQM') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QUBT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QUBT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RGTI', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RGTI') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RKLB', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RKLB') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RTX', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RTX') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RVMD', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RVMD') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RXT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RXT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SCHD', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SCHD') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SES', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SES') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SKHY', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SKHY') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMH', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMH') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMTC', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMTC') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SNDK', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SNDK') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SNOW', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SNOW') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOFI', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOFI') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOUN', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOUN') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXL', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXL') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXQ', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXQ') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXX', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXX') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPCX', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPCX') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPY', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPY') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SYM', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SYM') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TER', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TER') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TMDX', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TMDX') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TQQQ', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TQQQ') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSEM', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSEM') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSLA', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSLA') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSM', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSM') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TTGT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TTGT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TXN', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TXN') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'UBER', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'UBER') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'UNH', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'UNH') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'UPST', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'UPST') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'USAR', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'USAR') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'UUUU', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'UUUU') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'V', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'V') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'VBK', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'VBK') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'VICR', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'VICR') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'VOO', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'VOO') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'VRTX', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'VRTX') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'VST', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'VST') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'VZ', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'VZ') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'WMT', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'WMT') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XAR', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XAR') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLE', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLE') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLK', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLK') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XPEV', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XPEV') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XSD', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XSD') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'ZS', status, count(*), count(DISTINCT option_symbol), min(as_of), max(as_of) FROM (SELECT symbol, as_of, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'ZS') c GROUP BY status;
SELECT pg_sleep(0.5);
