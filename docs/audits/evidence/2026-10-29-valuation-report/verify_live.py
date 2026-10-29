"""Production acceptance; signed test token stays in memory and is never printed or saved."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import time

import httpx

HERE = pathlib.Path(__file__).resolve().parent
BASE = "https://lausing.com"


def token():
    code = """import json,time,uuid
from jose import jwt
from common.config import get_settings
from db import SessionLocal,User
from sqlalchemy import select
with SessionLocal() as session:
 user=session.execute(select(User).where(User.is_active.is_(True)).order_by(User.id).limit(1)).scalar_one()
 now=int(time.time())
 claims={'sub':user.username,'role':user.role.value.lower(),'tier':user.tier.value.lower(),
         'iat':now,'exp':now+1800,'jti':uuid.uuid4().hex}
 print(json.dumps({'token':jwt.encode(claims,get_settings().jwt_secret,algorithm='HS256')}))
"""
    proc = subprocess.run(["ssh", "-i", "/home/lausing/Documents/Stock_AI/lausing.pem", "-o", "BatchMode=yes",
                           "-o", "ConnectTimeout=20", "ec2-user@18.205.121.71",
                           "docker exec -i stockai-market-data-1 python -"],
                          input=code, capture_output=True, text=True, timeout=60)
    if proc.returncode:
        raise RuntimeError("Could not obtain in-memory release-check authentication")
    return json.loads(proc.stdout)["token"]


def api_check(credential):
    with httpx.Client(base_url=BASE, timeout=45) as client:
        assert client.get("/api/intel/valuation/latest").status_code == 401
        client.headers["Authorization"] = "Bearer " + credential
        before = client.get("/api/intel/valuation/latest"); before.raise_for_status()
        response = client.post("/api/intel/valuation/generate"); response.raise_for_status()
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 900
        state = None
        while time.monotonic() < deadline:
            response = client.get(f"/api/intel/valuation/jobs/{job_id}"); response.raise_for_status()
            state = response.json()
            if state["state"] in ("complete", "failed"):
                break
            time.sleep(5)
        assert state and state["state"] == "complete", state
        response = client.get("/api/intel/valuation/latest"); response.raise_for_status()
        report = response.json()["report"]
        assert report["id"] == state["report_id"]
        rows = report["valuation"]["rows"]
        assert rows and any(r["metrics"]["price"]["state"] == "OK" for r in rows)
        discovered = [r["symbol"] for r in rows if not r["tracked"]]
        assert discovered, "No discovery outside the covered stock universe"
        for row in rows:
            if row["qualified"]:
                assert not row["blockers"]
            assert row["metrics"]["peg"]["value"] is None or row["metrics"]["peg"]["value"] > 0
        history = client.get("/api/intel/valuation/history"); history.raise_for_status()
        assert any(r["id"] == report["id"] for r in history.json()["reports"])
        saved = client.get(f"/api/intel/valuation/reports/{report['id']}"); saved.raise_for_status()
        assert saved.json()["valuation"] == report["valuation"]
        summary = {"report_id": report["id"], "version": report["version"],
                   "price_session": report["valuation"]["price_session"],
                   "rows": len(rows), "new_symbols": discovered,
                   "counts": report["valuation"]["counts"],
                   "provider_counts": {p: sum((r.get("provider") or "Yahoo") == p for r in rows)
                                       for p in {r.get("provider") or "Yahoo" for r in rows}},
                   "unauthenticated_status": 401, "history_verified": True, "frozen_read_verified": True}
        capture = json.dumps(report, indent=2) + "\n"
        summary["snapshot_sha256"] = hashlib.sha256(capture.encode()).hexdigest()
        summary["metric_availability"] = {key: sum(r["metrics"][key]["state"] == "OK" for r in rows)
                                          for key in ("price", "trailing_pe", "forward_pe", "peg", "rsi", "fair_value")}
        (HERE / "live-report.json").write_text(capture)
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
            page.goto(BASE + "/reports?tab=valuation", wait_until="domcontentloaded")
            page.get_by_role("heading", name="Valuation & Opportunities", exact=True).wait_for(timeout=60000)
            page.get_by_label("Search companies").wait_for(timeout=60000)
            page.get_by_role("button", name="Undervalued research", exact=True).click()
            page.get_by_role("button", name="All companies", exact=True).click()
            first = page.locator("tbody button[aria-expanded]").first
            first.click()
            page.get_by_text("Investment case to investigate", exact=True).wait_for()
            page.get_by_text("Inputs and limitations", exact=True).first.click()
            with page.expect_download() as download:
                page.get_by_role("button", name="Export full CSV", exact=True).click()
            csv = pathlib.Path(download.value.path()).read_text()
            assert "report_id" in csv and "model_limitations" in csv
            geometry = page.evaluate("({viewport:innerWidth,width:document.documentElement.scrollWidth})")
            page.screenshot(path=str(HERE / f"report-{width}.png"), full_page=True)
            results.append({"width": width, "geometry": geometry, "runtime_errors": errors,
                            "export_verified": True, "company_expansion_verified": True})
            assert not errors, errors
            assert geometry["width"] <= width + 1, geometry
            context.close()
        browser.close()
    (HERE / "browser-summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--browser-only", action="store_true")
    parser.add_argument("--api-only", action="store_true")
    args = parser.parse_args()
    credential = token()
    if not args.browser_only:
        api_check(credential)
    if not args.api_only:
        browser_check(credential)
