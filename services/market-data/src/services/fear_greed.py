"""CNN's published sentiment snapshot; never synthesize a replacement score."""
from datetime import datetime, timezone
import math

import httpx

CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"


def parse_snapshot(payload: dict) -> dict:
    snapshot = payload["fear_and_greed"]

    def score(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Invalid CNN score")
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("CNN score outside 0–100")
        return float(value)

    rating = snapshot["rating"].strip().title()
    if rating not in {"Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"}:
        raise ValueError("Invalid CNN rating")
    timestamp = datetime.fromisoformat(snapshot["timestamp"].replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("CNN timestamp must include timezone")
    result = {
        "score": score(snapshot["score"]),
        "rating": rating,
        "source": "CNN",
        "updated_at": timestamp.astimezone(timezone.utc).isoformat(),
    }
    for field in ("previous_close", "previous_1_week", "previous_1_month", "previous_1_year"):
        value = snapshot.get(field)
        result[field] = None if value is None else score(value)
    return result


def fetch_snapshot() -> dict:
    response = httpx.get(CNN_URL, timeout=4.0, headers={
        "Accept": "application/json, text/plain, */*",
        # The public feed requires the same headers as its browser client.
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Referer": "https://www.cnn.com/",
        "Origin": "https://www.cnn.com",
    })
    response.raise_for_status()
    return parse_snapshot(response.json())
