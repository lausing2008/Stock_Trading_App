"""E2E on production: select LLM plan -> chart -> save -> reload, with the model reply REPLAYED.

The /copilot/chat response is fulfilled from the stored real MU answer of the budgeted eval
(no new model call); everything else — plan build, chart, board save, board reload — is live.
"""
import json
import sys
from playwright.sync_api import sync_playwright

tok, eval_path, out = open(sys.argv[1]).read().strip(), sys.argv[2], sys.argv[3]
res = next(x for x in json.load(open(eval_path))["results"] if x["scenario"] == "us_entry_MU")
log, created = [], {}

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1500, "height": 1400})
    pg.on("pageerror", lambda e: log.append("PAGEERROR " + str(e)[:300]))

    def chat(route):
        body = json.loads(route.request.post_data or "{}")
        msg = {"id": 0, "role": "assistant", "request_id": body.get("request_id"), "status": res["status"],
               "content": res["answer"], "model": res["model"], "price_as_of": res["price_as_of"],
               "packet_fingerprint": body["plan_context"]["fingerprint"], "plan_context": body["plan_context"],
               "plan_setups": [], "selected_setup_id": None, "plan_proposal": res["proposal"],
               "evidence_refs": [], "limitations": [], "rejection_reasons": [], "drafts": [], "dropped_drafts": []}
        log.append("CHAT replayed for request " + str(body.get("request_id")))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"conversation_id": 0, "message": msg}))
    pg.route("**/copilot/chat", chat)

    def resp(r):
        if r.request.method == "POST" and r.url.rstrip("/").endswith("/board"):
            created.update(json.loads(r.text()))
            log.append(f"BOARD POST {r.status} id={created.get('id')}")
    pg.on("response", resp)

    pg.goto("https://lausing.com/login")
    pg.evaluate("t=>{localStorage.setItem('stockai_jwt',t);localStorage.setItem('stockai:lausing:settings',JSON.stringify({aiProvider:'claude',claudeModel:'claude-sonnet-4-6'}))}", tok)
    pg.goto("https://lausing.com/stock/MU"); pg.wait_for_selector("text=Micron", timeout=60000)
    gp = pg.get_by_role("region", name="Game Plan")
    gp.get_by_role("button", name="Build plan").click()
    gp.get_by_role("button", name="Select LLM plan").wait_for(timeout=60000)
    gp.get_by_role("button", name="Select LLM plan").click()
    gp.get_by_role("button", name="Generate LLM alternative").click()
    gp.get_by_role("article", name="LLM-proposed plan").wait_for(timeout=60000)
    shown = gp.get_by_role("article", name="LLM-proposed plan").inner_text()
    pg.wait_for_timeout(2500)
    c = pg.locator("canvas").first; c.scroll_into_view_if_needed(); bb = c.bounding_box()
    pg.screenshot(path=out + "/e2e_chart.png", clip={"x": 0, "y": max(0, bb["y"] - 40), "width": 1500, "height": bb["height"] + 80})
    gp.get_by_role("article", name="LLM-proposed plan").scroll_into_view_if_needed()
    pg.screenshot(path=out + "/e2e_card.png", full_page=False)
    save = gp.get_by_role("button", name="Save selected plan to Trade Board")
    log.append(f"save enabled={not save.is_disabled()}")
    save.click(); pg.get_by_text("Saved as a new Trade Board plan").wait_for(timeout=30000)
    # Reload: a fresh page load, then read the board through the app's own API.
    pg.reload(); pg.wait_for_selector("text=Micron", timeout=60000)
    rows = pg.evaluate("async t => (await fetch('/api/board',{headers:{Authorization:'Bearer '+t}})).json()", tok)
    row = next(r for r in rows if r["id"] == created.get("id"))
    p0 = res["proposal"]; gpl = row["game_plan"]
    checks = {
        "entry": (row["entry_price"], p0["entry"]), "stop": (row["stop_loss"], p0["stop"]),
        "take_profit": (row["take_profit"], p0["targets"][0]["price"]), "source": (row["source"], "gameplan-llm"),
        "reasoning": (gpl["reasoning"], res["answer"]), "model": (gpl["model"], res["model"]),
        "price_as_of": (gpl["price_as_of"], p0.get("price_as_of") or res["price_as_of"]),
        "derivation": (gpl["derivation"], p0["derivation"]), "setup_targets": (gpl["setup"]["targets"], p0["targets"]),
        "playbook": (gpl["playbook"], p0["playbook"]),
    }
    result = {k: v[0] == v[1] for k, v in checks.items()}
    pg.goto("https://lausing.com/board"); pg.wait_for_timeout(6000)
    pg.screenshot(path=out + "/e2e_board.png", full_page=False)
    on_board = pg.get_by_text("LLM-proposed plan").count()
    print(json.dumps({"board_id": created.get("id"), "identical_after_reload": result,
                      "shown_card_excerpt": shown[:400], "board_page_mentions": on_board,
                      "fingerprint_saved": gpl.get("packet_fingerprint"), "log": log[-8:]}, indent=1))
    # Revoke THIS test session's token (blacklists its jti). Deleting the token file does not.
    status = pg.evaluate("async t => (await fetch('/api/auth/logout',{method:'POST',headers:{Authorization:'Bearer '+t}})).status", tok)
    still = pg.evaluate("async t => (await fetch('/api/board',{headers:{Authorization:'Bearer '+t}})).status", tok)
    print(json.dumps({"logout_status": status, "board_status_after_logout": still}))
    b.close()
