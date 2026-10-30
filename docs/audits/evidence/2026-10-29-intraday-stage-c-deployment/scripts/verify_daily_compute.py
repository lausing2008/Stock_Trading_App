"""Daily-path smoke after the a3107b47 release: compute (persist=false) daily signals through the
gateway for each daily style, then revoke the minted token. Prints statuses only."""
import json

import httpx
from sqlalchemy import text

from db.session import engine
from src.api.auth import _make_token

with engine.connect() as conn:
    admin = conn.execute(text("SELECT username, role, tier FROM users WHERE role = 'ADMIN' "
                              "AND is_active ORDER BY id LIMIT 1")).first()
tok = _make_token(admin.username, admin.role, admin.tier)
H = {"Authorization": f"Bearer {tok}"}
out = {}
with httpx.Client(base_url="http://api-gateway:8000", timeout=120) as c:
    for sym in ("AAPL", "MSFT"):
        for style in ("SHORT", "SWING", "LONG", "GROWTH"):
            r = c.get(f"/signals/{sym}", params={"style": style, "persist": "false", "live": "false"}, headers=H)
            b = r.json() if r.status_code == 200 else r.text[:200]
            out[f"{sym}/{style}"] = {"status": r.status_code,
                                      "signal": b.get("signal") if isinstance(b, dict) else b,
                                      "style": b.get("style") if isinstance(b, dict) else None}
    lo = c.post("/auth/logout", headers=H)
    out["token_revoked"] = {"logout": lo.status_code,
                            "reuse_after_logout": c.get("/signals?style=SWING", headers=H).status_code}
out["ok"] = all(v["status"] == 200 for k, v in out.items() if "/" in k) and out["token_revoked"]["reuse_after_logout"] == 401
print(json.dumps(out, indent=1, sort_keys=True))
