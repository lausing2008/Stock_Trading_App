"""Live phased-report acceptance. Short-lived authentication stays in memory."""
import argparse
import importlib.util
import json
from pathlib import Path
import time

import httpx

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("valuation_acceptance", HERE.parent / "2026-10-29-valuation-report/verify_live.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def api_check(credential):
    with httpx.Client(base_url=base.BASE, timeout=45) as client:
        assert client.get("/api/intel/valuation/latest").status_code == 401
        assert client.get("/api/intel/valuation/status").status_code == 401
        client.headers["Authorization"] = "Bearer " + credential
        before = client.get("/api/intel/valuation/latest").json().get("report")
        response = client.get("/api/intel/valuation/status")
        response.raise_for_status()
        assert response.json()["automatic_refresh"] == "active"
        response = client.post("/api/intel/valuation/generate")
        response.raise_for_status()
        queued = response.json()
        assert queued["durable"] is True
        print(json.dumps({"job": queued["job_id"], "durable": True}), flush=True)
        deadline, last_stage = time.monotonic() + 1200, None
        while time.monotonic() < deadline:
            response = client.get("/api/intel/valuation/jobs/" + queued["job_id"])
            response.raise_for_status()
            job = response.json()
            if job.get("stage") != last_stage:
                last_stage = job.get("stage")
                print(json.dumps({"state": job["state"], "stage": last_stage}), flush=True)
            if job["state"] in {"complete", "failed"}:
                break
            time.sleep(5)
        assert job["state"] == "complete", job
        report = client.get("/api/intel/valuation/latest").json()["report"]
        assert report["id"] == job["report_id"]
        data = report["valuation"]
        assert data["policy_version"] == "valuation-v5-phased-research"
        assert data["sampled"] <= 60
        assert data["catalog"]["state"] == "OK" and data["catalog"]["eligible_count"] > 3000
        assert any(not r["tracked"] for r in data["rows"])
        adobe = next(r for r in data["rows"] if r["symbol"] == "ADBE")
        review = adobe["candidate_review"]
        assert review["version"] == 2 and not adobe["qualified"]
        assert review["filing_verification"]["state"] == "OK"
        assert len(review["filing_verification"]["filings"]) >= 20
        assert review["financial_verification"]["state"] == "OK"
        assert review["financial_verification"]["latest_annual"]["stock_compensation"]["value"] == 1942e6
        assert client.get(f"/api/intel/valuation/reports/{report['id']}").json() == report
        if before:
            assert client.get(f"/api/intel/valuation/reports/{before['id']}").json() == before
        history = client.get("/api/intel/valuation/history").json()["reports"]
        assert report["id"] in {r["id"] for r in history}
        deadline = time.monotonic() + 90
        evaluation = None
        while time.monotonic() < deadline:
            response = client.get(f"/api/intel/valuation/performance/{report['id']}")
            response.raise_for_status()
            evaluation = response.json()["evaluation"]
            if evaluation:
                break
            time.sleep(5)
        assert evaluation and {h["sessions"] for h in evaluation["horizons"]} == {5, 20, 60}
        assert all(g["state"] in {"PENDING", "NO_COHORT"} for h in evaluation["horizons"] for g in h["groups"].values())
        invalid = client.post("/api/stocks/valuation-outcome-prices", params={"symbols": "../private"})
        assert invalid.status_code == 400
        (HERE / "live-report.json").write_text(json.dumps(report, indent=2) + "\n")
        summary = {"report_id": report["id"], "version": report["version"], "price_session": data["price_session"],
            "policy": data["policy_version"], "catalog_candidates": data["catalog"]["eligible_count"],
            "sampled": data["sampled"], "outside_tracked": sum(not r["tracked"] for r in data["rows"]),
            "counts": data["counts"], "job_durable": job["durable"], "attempts": job["attempts"],
            "worker": "active", "sec_filings": len(review["filing_verification"]["filings"]),
            "sec_annual_period": review["financial_verification"]["latest_period_end"],
            "frozen_history_verified": True, "outcomes": [{"sessions": h["sessions"],
                "entry": h["entry_session"], "end": h["end_session"],
                "states": {name: g["state"] for name, g in h["groups"].items()}} for h in evaluation["horizons"]],
            "outcome_input_validation": invalid.status_code}
        (HERE / "api-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary), flush=True)


def browser_check(credential):
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True,
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        for width in (1440, 390):
            context = browser.new_context(viewport={"width": width, "height": 1000})
            context.add_init_script("localStorage.setItem('stockai_jwt', " + json.dumps(credential) + ");")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base.BASE + "/reports?tab=valuation", wait_until="domcontentloaded")
            page.get_by_role("button", name="Reviewed candidates", exact=True).wait_for(timeout=60000)
            page.get_by_text("Automatic refresh: active", exact=False).wait_for(timeout=60000)
            page.get_by_text("US listing catalog:", exact=False).wait_for()
            page.get_by_role("heading", name="Frozen cohort outcomes", exact=True).wait_for()
            page.get_by_text("Awaiting sessions", exact=True).first.wait_for(timeout=60000)
            with page.expect_download() as download:
                page.get_by_role("button", name="Export outcomes CSV", exact=True).click()
            csv = Path(download.value.path()).read_text()
            assert "PENDING" in csv and "excess_percentage_points" in csv
            page.get_by_role("button", name="Reviewed candidates", exact=True).click()
            page.get_by_role("button", name="+ ADBE", exact=True).click()
            region = page.get_by_role("region", name="Issuer qualification review")
            region.get_by_text("SEC filing verification · OK", exact=True).click()
            region.get_by_text("SEC annual financial verification · OK", exact=True).click()
            region.get_by_role("link", name="10-Q · 2026-09-22", exact=True).wait_for()
            page.get_by_text("stock compensation:", exact=False).wait_for()
            with page.expect_download() as download:
                page.get_by_role("button", name="Export full CSV", exact=True).click()
            csv = Path(download.value.path()).read_text()
            assert "sec_inventory_checked_at" in csv and "sec_annual_period" in csv
            geometry = page.evaluate("({viewport:innerWidth,width:document.documentElement.scrollWidth})")
            assert geometry["width"] <= width + 1 and not errors, (geometry, errors)
            page.screenshot(path=str(HERE / f"phases-{width}.png"), full_page=True)
            results.append({"viewport": width, "geometry": geometry, "runtime_errors": errors,
                "automation_and_catalog": True, "sec_panels": True, "outcome_csv": True, "report_csv": True})
            context.close()
        browser.close()
    (HERE / "browser-summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--browser-only", action="store_true")
    args = parser.parse_args()
    credential = base.token()
    if not args.browser_only:
        api_check(credential)
    if not args.api_only:
        browser_check(credential)
