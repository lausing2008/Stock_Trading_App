"""Authenticated release acceptance; credentials stay in memory."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("valuation_acceptance", HERE.parent / "2026-10-29-valuation-report/verify_live.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.HERE = HERE


def check_review(credential):
    import httpx
    from playwright.sync_api import sync_playwright
    with httpx.Client(base_url=base.BASE, timeout=45, headers={"Authorization": "Bearer " + credential}) as client:
        response = client.get("/api/intel/valuation/latest")
        response.raise_for_status()
        report = response.json()["report"]
    data = report["valuation"]
    assert data["policy_version"] == "valuation-v3-candidate-review"
    reviewed = [r for r in data["rows"] if r.get("candidate_review")]
    adobe = next(r for r in reviewed if r["symbol"] == "ADBE")
    review = adobe["candidate_review"]
    assert not adobe["qualified"] and review["qualification_blockers"]
    assert review["sensitivity"]["scenarios"]["base"]["fair_value"] == 247.84
    assert review["sources"][1]["published_at"] == "2026-09-22"
    summary = {"report_id": report["id"], "version": report["version"], "policy": data["policy_version"],
               "reviewed_symbols": [r["symbol"] for r in reviewed], "qualified": data["counts"]["qualified"],
               "adobe_sensitivity": {k: v["fair_value"] for k, v in review["sensitivity"]["scenarios"].items()},
               "adobe_review_status": review["status"], "primary_sources": [s["url"] for s in review["sources"]]}
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
            page.get_by_role("button", name="Reviewed candidates", exact=True).click()
            page.get_by_role("button", name="+ ADBE", exact=True).click()
            region = page.get_by_role("region", name="Issuer qualification review")
            region.wait_for()
            assert "Research only" in region.inner_text()
            assert "$247.84" in region.inner_text()
            assert "What would invalidate the thesis" in region.inner_text()
            region.get_by_text("Primary sources", exact=True).click()
            assert region.get_by_role("link", name="Adobe FY2025 10-K · cash flows and diluted EPS").get_attribute("href") == review["sources"][0]["url"]
            with page.expect_download() as download:
                page.get_by_role("button", name="Export full CSV", exact=True).click()
            csv = Path(download.value.path()).read_text()
            assert "owner_cash_base" in csv and "247.84" in csv and "thesis_invalidation" in csv
            geometry = page.evaluate("({viewport:innerWidth,width:document.documentElement.scrollWidth})")
            assert not errors, errors
            assert geometry["width"] <= width + 1, geometry
            page.screenshot(path=str(HERE / f"review-{width}.png"), full_page=True)
            page.get_by_role("button", name="Qualified opportunities", exact=True).click()
            if data["counts"]["qualified"] == 0:
                page.get_by_text("No companies meet all opportunity requirements", exact=False).wait_for()
            results.append({"viewport": width, "geometry": geometry, "reviewed_filter": True,
                            "source_links": True, "sensitivity_and_invalidation": True, "csv": True,
                            "runtime_errors": errors})
            context.close()
        browser.close()
    summary["browser"] = results
    (HERE / "qualification-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-only", action="store_true")
    parser.add_argument("--api-only", action="store_true")
    args = parser.parse_args()
    credential = base.token()
    if not args.browser_only:
        base.api_check(credential)
    if not args.api_only:
        check_review(credential)
