# IF-03: Earnings Call NLP

## Problem Statement

Missing qualitative signals from earnings calls:
- Management tone and confidence level
- Forward guidance sentiment
- Risk language and hedging
- Analyst question sentiment

---

## Solution Overview

Use Claude to analyze earnings call transcripts, extract sentiment, and generate actionable signals.

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Transcript     │────▶│  Claude API      │────▶│  earnings_call  │
│  Source (API)   │     │  (analysis)      │     │  _analysis      │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │  Signal boost/   │
                        │  penalty         │
                        └──────────────────┘
```

---

## Database Schema

```python
class EarningsCallAnalysis(Base):
    """Claude-analyzed earnings call transcript."""
    __tablename__ = "earnings_call_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    earnings_date: Mapped[date] = mapped_column(Date, index=True)
    fiscal_quarter: Mapped[str] = mapped_column(String(8))  # "Q1", "Q2", etc.
    fiscal_year: Mapped[int] = mapped_column(Integer)
    
    # Overall scores (0-100)
    overall_sentiment: Mapped[float] = mapped_column(Float)  # 50 = neutral
    management_confidence: Mapped[float] = mapped_column(Float)
    guidance_sentiment: Mapped[float] = mapped_column(Float)
    analyst_sentiment: Mapped[float] = mapped_column(Float)
    
    # Specific signals
    raised_guidance: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lowered_guidance: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mentioned_headwinds: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["supply chain", "inflation"]
    mentioned_tailwinds: Mapped[list | None] = mapped_column(JSON, nullable=True)
    key_metrics_mentioned: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    
    # Risk language
    hedging_language_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # Higher = more hedging
    uncertainty_phrases_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    # Key quotes
    bullish_quotes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    bearish_quotes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    
    # Summary
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    trading_implication: Mapped[str | None] = mapped_column(String(16), nullable=True)  # BULLISH/BEARISH/NEUTRAL
    
    # Metadata
    transcript_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transcript_word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("stock_id", "earnings_date", name="uq_earnings_call_analysis"),
    )
```

---

## Claude Analysis Prompt

```python
EARNINGS_CALL_ANALYSIS_PROMPT = """
Analyze this earnings call transcript for {symbol} ({company_name}).

TRANSCRIPT:
{transcript}

Provide analysis in the following JSON format:
{
  "overall_sentiment": <0-100, 50=neutral>,
  "management_confidence": <0-100>,
  "guidance_sentiment": <0-100>,
  "analyst_sentiment": <0-100>,
  "raised_guidance": <true/false/null>,
  "lowered_guidance": <true/false/null>,
  "hedging_language_score": <0-100, higher=more hedging/uncertainty>,
  "uncertainty_phrases_count": <int>,
  "mentioned_headwinds": ["list", "of", "challenges"],
  "mentioned_tailwinds": ["list", "of", "positives"],
  "key_metrics_mentioned": {
    "revenue_growth": "mentioned X% growth",
    "margin_expansion": "mentioned Y bps improvement"
  },
  "bullish_quotes": ["exact quote 1", "exact quote 2"],
  "bearish_quotes": ["exact quote 1", "exact quote 2"],
  "summary": "2-3 sentence summary of key takeaways",
  "trading_implication": "BULLISH" | "BEARISH" | "NEUTRAL"
}

Focus on:
1. Management tone - confident vs defensive
2. Forward guidance - raised, maintained, or lowered
3. Analyst questions - skeptical vs supportive
4. Risk language - hedging words like "may", "could", "uncertain"
5. Specific metrics and their trajectory
"""
```

---

## Core Analysis Logic

```python
# services/event-intelligence/src/services/earnings_nlp.py

import anthropic
from typing import Optional

async def analyze_earnings_call(
    symbol: str,
    transcript: str,
    company_name: str
) -> dict:
    """Analyze earnings call transcript using Claude."""
    
    client = anthropic.Anthropic()
    
    prompt = EARNINGS_CALL_ANALYSIS_PROMPT.format(
        symbol=symbol,
        company_name=company_name,
        transcript=transcript[:50000]  # Limit to ~50k chars
    )
    
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    # Parse JSON response
    analysis = json.loads(response.content[0].text)
    
    return analysis


def compute_earnings_signal_modifier(analysis: dict) -> float:
    """
    Convert earnings analysis to signal confidence modifier.
    Returns multiplier: 0.8 (bearish) to 1.2 (bullish)
    """
    sentiment = analysis.get("overall_sentiment", 50)
    confidence = analysis.get("management_confidence", 50)
    guidance = analysis.get("guidance_sentiment", 50)
    hedging = analysis.get("hedging_language_score", 50)
    
    # Weighted score
    score = (
        sentiment * 0.3 +
        confidence * 0.25 +
        guidance * 0.3 +
        (100 - hedging) * 0.15  # Invert hedging (less hedging = better)
    )
    
    # Convert to multiplier (50 = 1.0, 0 = 0.8, 100 = 1.2)
    multiplier = 0.8 + (score / 50) * 0.2
    
    return round(multiplier, 2)
```

---

## Transcript Sources

```python
# services/event-intelligence/src/services/transcript_fetcher.py

async def fetch_transcript(symbol: str, earnings_date: date) -> Optional[str]:
    """Fetch earnings call transcript from available sources."""
    
    # Try sources in order of preference
    sources = [
        _fetch_from_seeking_alpha,
        _fetch_from_motley_fool,
        _fetch_from_sec_8k,  # 8-K filings sometimes include transcripts
    ]
    
    for source_fn in sources:
        try:
            transcript = await source_fn(symbol, earnings_date)
            if transcript and len(transcript) > 1000:
                return transcript
        except Exception as e:
            log.warning(f"Transcript source failed: {e}")
            continue
    
    return None
```

---

## API Endpoints

```python
@router.get("/earnings/{symbol}/analysis")
async def get_earnings_analysis(symbol: str) -> dict:
    """Get latest earnings call analysis."""
    return {
        "symbol": "AAPL",
        "earnings_date": "2026-08-01",
        "overall_sentiment": 72,
        "management_confidence": 78,
        "guidance_sentiment": 68,
        "trading_implication": "BULLISH",
        "summary": "Strong iPhone demand, services growth accelerating. Management confident in holiday quarter.",
        "signal_modifier": 1.12
    }


@router.post("/earnings/{symbol}/analyze")
async def trigger_earnings_analysis(symbol: str) -> dict:
    """Manually trigger earnings call analysis."""
    pass
```

---

## Integration with Signals

```python
# In signal-engine, modify confidence calculation:

def _apply_earnings_modifier(symbol: str, base_confidence: float) -> float:
    """Adjust signal confidence based on recent earnings analysis."""
    
    # Get most recent earnings analysis (within 30 days)
    analysis = get_recent_earnings_analysis(symbol, days=30)
    if not analysis:
        return base_confidence
    
    modifier = compute_earnings_signal_modifier(analysis)
    
    # Apply modifier
    adjusted = base_confidence * modifier
    
    return min(100, max(0, adjusted))
```

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Transcript coverage | > 80% of watchlist earnings |
| Analysis accuracy | Sentiment matches 3-day price direction > 60% |
| Signal improvement | +1-2% win rate on earnings-modified signals |

---

*Created: 2026-08-16*
