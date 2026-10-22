"""Run over SSH on the existing host: read-only, aggregate-only audit capture.

Uses allowlisted Redis keys and PostgreSQL READ ONLY transactions. Does not invoke
application endpoints, calibration jobs, model loading, email or deployment actions.
"""
import json
import subprocess
from datetime import datetime, timezone


def run(args):
    return subprocess.check_output(args, text=True, timeout=45).strip()


def query(sql):
    statement = (
        "BEGIN READ ONLY; SET LOCAL statement_timeout='20s'; "
        "SELECT coalesce(json_agg(t), '[]'::json) FROM (" + sql + ") t; ROLLBACK;"
    )
    return json.loads(run([
        "docker", "exec", "stockai-postgres-1", "psql", "-X", "-qAt",
        "-v", "ON_ERROR_STOP=1", "-U", "stockai", "-d", "stockai", "-c", statement,
    ]))


def main():
    styles = ("SHORT", "SWING", "LONG", "GROWTH")
    params = ("ml_weight_cap", "adx_min", "adx_compression", "high_vol_compression",
              "breadth_compression", "weekly_boost", "weekly_compress", "rs_compression",
              "max_compress_ratio", "min_pillars_for_sell")
    keys = ["stockai:ml_weight_cap", "stockai:ta_weights", "stockai:conviction_weights"]
    for style in styles:
        keys.extend([f"stockai:watchdog:{style}:threshold",
                     f"stockai:signal_thresholds:{style}",
                     f"stockai:signal_thresholds:SELL:{style}"])
        keys.extend(f"stockai:style_tune:{style}:{p}" for p in params)
    values = json.loads(run(["docker", "exec", "stockai-redis-1", "redis-cli", "--json", "MGET", *keys]))
    output = {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
              "redis_allowlisted_values": dict(zip(keys, values))}
    output["database_time"] = query("SELECT now() AS database_time, current_date AS database_date")
    output["outcome_cohorts_180d"] = query("""
      SELECT o.horizon, o.signal_direction, s.market,
             coalesce(o.evidence_status, 'unknown') AS evidence_status,
             count(*) AS resolved_n, count(distinct o.signal_date) AS distinct_dates,
             min(o.signal_date) AS first_signal_date, max(o.signal_date) AS last_signal_date,
             count(*) FILTER (WHERE o.entry_date <= o.signal_date OR o.entry_date IS NULL) AS invalid_or_missing_entry,
             round((100.0*avg(o.is_correct::int))::numeric,3) AS stored_hurdle_hit_pct,
             round((100.0*avg(CASE WHEN o.signal_direction='SELL' THEN -o.pct_return ELSE o.pct_return END))::numeric,3) AS direction_adjusted_mean_return_pct
      FROM signal_outcomes o JOIN stocks s ON s.id=o.stock_id
      WHERE o.signal_date >= current_date - 180 AND o.is_correct IS NOT NULL
      GROUP BY o.horizon, o.signal_direction, s.market, coalesce(o.evidence_status,'unknown')
      ORDER BY o.horizon, o.signal_direction, s.market, evidence_status
    """)
    output["signal_population_30d"] = query("""
      SELECT horizon, signal, count(*) AS rows, count(distinct stock_id) AS symbols,
             min(ts) AS earliest, max(ts) AS latest
      FROM signals WHERE ts >= now() - interval '30 days'
      GROUP BY horizon,signal ORDER BY horizon,signal
    """)
    output["observed_regimes_180d"] = query("""
      SELECT horizon, signal_direction, market_regime, count(*) AS resolved_n
      FROM signal_outcomes
      WHERE signal_date >= current_date - 180 AND is_correct IS NOT NULL
      GROUP BY horizon,signal_direction,market_regime ORDER BY horizon,signal_direction,market_regime
    """)
    output["recent_tuning"] = query("""
      SELECT ts, parameter_class, parameter_name, style, market, old_value, new_value,
             validation_n, validation_ev_pct, baseline_validation_ev_pct, promoted, gate_failures
      FROM tune_history
      WHERE parameter_name IN ('buy_threshold','sell_threshold','watchdog_buy_threshold','ml_weight_cap','adx_min')
      ORDER BY ts DESC LIMIT 40
    """)
    output["latest_signal_ml_weights"] = query("""
      SELECT horizon, count(*) AS n, min(ts) AS earliest, max(ts) AS latest,
             min((reasons->>'ml_weight')::numeric) AS min_ml_weight,
             max((reasons->>'ml_weight')::numeric) AS max_ml_weight,
             count(*) FILTER (WHERE (reasons->>'ml_weight')::numeric > 0) AS positive_ml_weight_n
      FROM (SELECT DISTINCT ON(stock_id,horizon) stock_id,horizon,ts,reasons
            FROM signals WHERE ts >= now()-interval '3 days'
            ORDER BY stock_id,horizon,ts DESC) latest
      GROUP BY horizon ORDER BY horizon
    """)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
