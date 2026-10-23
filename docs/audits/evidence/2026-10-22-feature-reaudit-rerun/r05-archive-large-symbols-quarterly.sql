SET default_transaction_read_only = on;
SET statement_timeout = '90s';
SELECT 'AVGO', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AVGO' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AVGO', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AVGO' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AVGO', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AVGO' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AVGO', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AVGO' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AVGO', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AVGO' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AVGO', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AVGO' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AVGO', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AVGO' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AVGO', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AVGO' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AVGO', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AVGO' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'AVGO', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'AVGO' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CAT', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CAT' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CAT', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CAT' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CAT', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CAT' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CAT', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CAT' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CAT', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CAT' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CAT', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CAT' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CAT', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CAT' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CAT', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CAT' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CAT', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CAT' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CAT', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CAT' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWD', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWD' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWD', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWD' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWD', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWD' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWD', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWD' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWD', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWD' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWD', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWD' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWD', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWD' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWD', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWD' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWD', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWD' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'CRWD', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'CRWD' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GEV', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GEV' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GEV', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GEV' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GEV', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GEV' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GEV', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GEV' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GEV', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GEV' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GEV', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GEV' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GEV', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GEV' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GEV', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GEV' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GEV', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GEV' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GEV', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GEV' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLD', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLD' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLD', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLD' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLD', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLD' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLD', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLD' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLD', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLD' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLD', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLD' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLD', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLD' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLD', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLD' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLD', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLD' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GLD', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GLD' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GOOG', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GOOG' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GOOG', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GOOG' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GOOG', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GOOG' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GOOG', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GOOG' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GOOG', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GOOG' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GOOG', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GOOG' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GOOG', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GOOG' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GOOG', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GOOG' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GOOG', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GOOG' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'GOOG', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'GOOG' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'META', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'META' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'META', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'META' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'META', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'META' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'META', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'META' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'META', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'META' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'META', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'META' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'META', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'META' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'META', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'META' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'META', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'META' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'META', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'META' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MU', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MU' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MU', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MU' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MU', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MU' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MU', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MU' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MU', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MU' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MU', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MU' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MU', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MU' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MU', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MU' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MU', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MU' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'MU', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'MU' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQ', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQ' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQ', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQ' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQ', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQ' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQ', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQ' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQ', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQ' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQ', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQ' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQ', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQ' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQ', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQ' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQ', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQ' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'QQQ', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'QQQ' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RTX', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RTX' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RTX', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RTX' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RTX', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RTX' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RTX', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RTX' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RTX', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RTX' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RTX', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RTX' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RTX', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RTX' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RTX', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RTX' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RTX', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RTX' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'RTX', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'RTX' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMH', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMH' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMH', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMH' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMH', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMH' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMH', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMH' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMH', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMH' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMH', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMH' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMH', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMH' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMH', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMH' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMH', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMH' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SMH', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SMH' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXL', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXL' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXL', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXL' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXL', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXL' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXL', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXL' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXL', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXL' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXL', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXL' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXL', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXL' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXL', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXL' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXL', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXL' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXL', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXL' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXX', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXX' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXX', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXX' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXX', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXX' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXX', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXX' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXX', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXX' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXX', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXX' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXX', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXX' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXX', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXX' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXX', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXX' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SOXX', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SOXX' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPY', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPY' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPY', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPY' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPY', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPY' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPY', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPY' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPY', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPY' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPY', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPY' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPY', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPY' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPY', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPY' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPY', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPY' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'SPY', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'SPY' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSLA', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSLA' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSLA', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSLA' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSLA', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSLA' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSLA', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSLA' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSLA', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSLA' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSLA', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSLA' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSLA', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSLA' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSLA', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSLA' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSLA', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSLA' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSLA', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSLA' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSM', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSM' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSM', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSM' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSM', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSM' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSM', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSM' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSM', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSM' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSM', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSM' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSM', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSM' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSM', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSM' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSM', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSM' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'TSM', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'TSM' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLK', '2024-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLK' AND as_of >= '2024-07-01' AND as_of < '2024-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLK', '2024-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLK' AND as_of >= '2024-10-01' AND as_of < '2025-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLK', '2025-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLK' AND as_of >= '2025-01-01' AND as_of < '2025-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLK', '2025-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLK' AND as_of >= '2025-04-01' AND as_of < '2025-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLK', '2025-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLK' AND as_of >= '2025-07-01' AND as_of < '2025-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLK', '2025-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLK' AND as_of >= '2025-10-01' AND as_of < '2026-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLK', '2026-01-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLK' AND as_of >= '2026-01-01' AND as_of < '2026-04-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLK', '2026-04-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLK' AND as_of >= '2026-04-01' AND as_of < '2026-07-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLK', '2026-07-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLK' AND as_of >= '2026-07-01' AND as_of < '2026-10-01') c GROUP BY status;
SELECT pg_sleep(0.5);
SELECT 'XLK', '2026-10-01', status, count(*) FROM (SELECT symbol, option_symbol, CASE WHEN option_symbol IS NULL OR trim(option_symbol) = '' THEN 'missing_symbol'
 WHEN substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$') IS NULL THEN 'unreadable_symbol'
 WHEN (CASE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') WHEN 'SPXW' THEN 'SPX' WHEN 'NDXP' THEN 'NDX' WHEN 'RUTW' THEN 'RUT' WHEN 'VIXW' THEN 'VIX' WHEN 'XSPW' THEN 'XSP' ELSE replace(substring(upper(trim(option_symbol)) from '^([A-Z0-9.]{1,6})[0-9]{6}[CP][0-9]{8}$'),'.','') END) = replace(upper(symbol),'.','') THEN 'standard'
 ELSE 'adjusted_nonstandard_root' END AS status FROM option_chain_history WHERE symbol = 'XLK' AND as_of >= '2026-10-01' AND as_of < '2027-01-01') c GROUP BY status;
SELECT pg_sleep(0.5);
