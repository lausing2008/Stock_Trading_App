"""Synthetic local browser smoke. Start Next on localhost:3017; requires Python Playwright.
All backend/provider requests are intercepted. Never uses a real login, provider or model.
Run: python scripts/smoke_workspace_workflows.py
"""

import base64, json, sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

jwt = lambda v: base64.urlsafe_b64encode(json.dumps(v).encode()).decode().rstrip("=")
token = (
    jwt({"alg": "none"})
    + "."
    + jwt(
        {
            "sub": "workflow-preview",
            "role": "admin",
            "tier": "advanced",
            "exp": 2200000000,
        }
    )
    + ".fixture"
)
bars = [
    {
        "ts": (datetime(2026, 7, 1) + timedelta(days=i)).isoformat(),
        "open": 100 + i * 0.1,
        "high": 101 + i * 0.1,
        "low": 99 + i * 0.1,
        "close": 100 + i * 0.1,
        "volume": 1000,
    }
    for i in range(80)
]
intraday = [
    {
        "ts": (datetime(2026, 10, 16, 14) + timedelta(minutes=5 * i)).isoformat(),
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100,
        "volume": 1000,
    }
    for i in range(10)
]
stock = {
    "symbol": "TEST",
    "name": "Test Company",
    "sector": "Technology",
    "market": "US",
    "exchange": "NASDAQ",
    "currency": "USD",
}
overview = {
    "price": stock,
    "prices": bars,
    "indicators": {"ts": [p["ts"] for p in bars], "values": {}},
    "patterns": {"patterns": []},
    "levels": {"support_resistance": [], "fibonacci": {}, "trendlines": []},
    "signal": None,
    "ranking": None,
    "fundamentals": None,
}
state = {"revision": 0, "layout": None, "close": [], "posts": 0}
errors = []


def handler(route):
    p = urlparse(route.request.url)
    path = p.path.removeprefix("/api")
    data = None
    if path in [
        "/connections",
        "/connections/relationships",
        "/connections/catalog",
        "/notifications",
        "/watchlists",
        "/portfolio",
        "/alerts",
    ]:
        data = []
    if path.startswith("/rankings"):
        data = {"rankings": []}
    elif path.startswith("/aggregate/overview/"):
        data = overview
    elif path == "/stocks/latest_prices":
        data = [{"symbol": "TEST", "price": 108, "currency": "USD"}]
    elif path == "/stocks/TEST":
        data = stock
    elif path.endswith("/prices") or path.endswith("/prices_tf"):
        data = intraday if "5m" in p.query or "tf=" in p.query else bars
    elif path == "/workspace/layout":
        if route.request.method == "PUT":
            body = route.request.post_data_json
            state.update(revision=state["revision"] + 1, layout=body["layout"])
        data = {"revision": state["revision"], "layout": state["layout"]}
    elif path == "/close-alerts":
        if route.request.method == "POST":
            state["posts"] += 1
            state["close"].append(
                {"id": 1, "active": True, **route.request.post_data_json}
            )
        data = state["close"][-1] if route.request.method == "POST" else state["close"]
    elif path == "/auth/me":
        data = {
            "username": "workflow-preview",
            "role": "admin",
            "id": 1,
            "tier": "advanced",
        }
    route.fulfill(
        status=404 if data is None else 200,
        content_type="application/json",
        body=json.dumps(data),
    )


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1440, "height": 1000})

    page.on(
        "pageerror",
        lambda e: (
            errors.append(str(e)),
            print(
                "PAGE ERROR: " + str(e) + " STACK " + str(e.stack),
                file=sys.stderr,
                flush=True,
            ),
        ),
    )
    page.set_default_timeout(15000)
    page.add_init_script(f"localStorage.setItem('stockai_jwt',{json.dumps(token)});")
    page.route(
        "**/*",
        lambda route: (
            route.continue_()
            if urlparse(route.request.url).port == 3017
            and not urlparse(route.request.url).path.startswith("/api/")
            else handler(route)
        ),
    )
    page.goto(
        "http://127.0.0.1:3017/connections",
        wait_until="domcontentloaded",
        timeout=120000,
    )
    print("Connections document loaded", file=sys.stderr, flush=True)
    page.get_by_role("heading", name="Event connections", exact=True).wait_for()
    page.goto(
        "http://127.0.0.1:3017/stock/TEST",
        wait_until="domcontentloaded",
        timeout=120000,
    )
    print("Stock document loaded", file=sys.stderr, flush=True)
    page.wait_for_timeout(3000)
    command = page.get_by_placeholder("show RSI / show 1Y")
    command.wait_for(timeout=20000)
    print("Chart command available", file=sys.stderr, flush=True)
    command.fill("show RSI")
    page.get_by_role("button", name="Apply to chart", exact=True).click()
    page.get_by_text("Chart acknowledged TEST: 3M.", exact=True).wait_for(timeout=30000)
    command.fill("show 1Y")
    page.get_by_role("button", name="Apply to chart", exact=True).click()
    page.get_by_text("Chart acknowledged TEST: 1Y.", exact=True).wait_for(timeout=30000)
    page.get_by_role("button", name="Undo chart command", exact=True).click()
    page.get_by_text("Chart acknowledged TEST: 3M.", exact=True).wait_for(timeout=30000)
    command.fill("set 15m")
    page.get_by_role("button", name="Apply to chart", exact=True).click()
    page.get_by_text("Chart acknowledged TEST: 3M.", exact=True).wait_for(timeout=30000)
    command.fill("set 5m")
    page.get_by_role("button", name="Apply to chart", exact=True).click()
    page.get_by_text("Chart acknowledged TEST: 5m.", exact=True).wait_for(timeout=30000)
    page.get_by_role("button", name="1Y", exact=True).click()
    command.fill("show RSI")
    page.get_by_role("button", name="Apply to chart", exact=True).click()
    page.get_by_text("Chart acknowledged TEST: 1Y.", exact=True).wait_for(timeout=30000)
    print("Chart actions passed", file=sys.stderr, flush=True)
    page.get_by_role("checkbox", name="Sync layout across my devices").check()
    page.get_by_text("Layout synced to your account.", exact=True).wait_for(
        timeout=30000
    )
    assert state["revision"] >= 1
    page.locator("summary").filter(has_text="Daily-close monitoring").first.click()
    section = page.get_by_role("region", name="Daily-close alerts")
    section.get_by_role("spinbutton").fill("95")
    section.get_by_role("button", name="Review close alert").click()
    assert state["posts"] == 0
    section.get_by_role("button", name="Confirm daily-close alert").click()
    page.wait_for_timeout(500)
    assert state["posts"] == 1
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
    assert not errors, errors
    print(
        json.dumps(
            {
                "chart_acknowledgment": True,
                "undo": True,
                "cloud_revision": state["revision"],
                "explicit_close_save_count": state["posts"],
                "mobile_width_ok": True,
                "page_errors": errors,
            }
        )
    )
    browser.close()
