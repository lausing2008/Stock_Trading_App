"""Verify dated technical enrichment through production API, UI and CSV."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("valuation_acceptance", HERE.parent / "2026-10-29-valuation-report/verify_live.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.HERE = HERE

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-only", action="store_true")
    parser.add_argument("--api-only", action="store_true")
    args = parser.parse_args()
    credential = base.token()
    if not args.browser_only:
        base.api_check(credential)
        report = json.loads((HERE / "live-report.json").read_text())
        rows = report["valuation"]["rows"]
        current = [r["metrics"]["rsi"] for r in rows if r["metrics"]["rsi"]["state"] == "OK"]
        assert current
        for value in current:
            assert 0 <= value["value"] <= 100
            assert value["as_of"] == report["valuation"]["price_session"]
            assert value["source"] in ("Unusual Whales daily screener", "Calculated Wilder RSI")
        summary = {"report_id": report["id"], "policy": report["valuation"]["policy_version"],
                   "price_session": report["valuation"]["price_session"], "rsi_count": len(current),
                   "eps_yoy_count": sum(r["metrics"]["eps_yoy_growth_pct"]["state"] == "OK" for r in rows),
                   "rsi_sources": sorted({v["source"] for v in current})}
        (HERE / "technical-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary), flush=True)
    if not args.api_only:
        base.browser_check(credential, require_technical=True)
