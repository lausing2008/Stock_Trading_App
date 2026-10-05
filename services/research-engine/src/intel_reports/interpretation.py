"""Layer 1: deciding what the evidence SUPPORTS, deterministically.

WHAT THIS IS FOR. The reports were accurate and did not interpret. "Above both moving averages"
is a true statement that leaves the reader to work out what it supports, what argues against it,
and what would change it — which is the job the product exists to do. This layer does that
arithmetic in code, before any writing, so a narrator later has findings to express rather than
fields to describe.

THREE RULES IT KEEPS.

1. A reading states what it supports AND what contradicts it. A one-sided reading of a
   multi-horizon price structure is how "above the 20-bar average" becomes a bullish call.
2. A direction is never claimed from a level. 54.2% participation is a level; "broadening" is a
   comparison, and without the earlier value the honest answer is that the direction is unknown.
3. Missing inputs are RANKED by whether they constrain THIS conclusion. A list of eight
   unavailable fields tells a reader nothing; naming the two that matter tells them what the
   report cannot settle and why.

NO MODEL, NO PROSE GENERATION. Every sentence here is assembled from measured values by fixed
rules, which is what makes it checkable.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from intelligence.report_contract import FieldState, Field, StatementClass, interpreted


@dataclass
class Finding:
    """One thing that matters, with the case against it attached."""
    headline: str
    supports: str
    contradicts: str
    invalidated_by: str
    evidence_ids: tuple = ()

    def as_dict(self) -> dict:
        return {"finding": self.headline, "supports": self.supports,
                "contradicts": self.contradicts, "invalidated_by": self.invalidated_by,
                "evidence_ids": list(self.evidence_ids)}


@dataclass
class Watch:
    observation: str
    trigger: str
    would_change: str

    def as_dict(self) -> dict:
        return {"watch": self.observation, "trigger": self.trigger,
                "would_change": self.would_change}


@dataclass
class Assessment:
    verdict: str
    horizon: str
    findings: list = dc_field(default_factory=list)
    counterargument: str = ""
    watch_next: list = dc_field(default_factory=list)
    material_limits: list = dc_field(default_factory=list)
    other_limits_count: int = 0
    #: Inputs the report would need. NOT market triggers — obtaining a volatility feed is not
    #: something the market does, and listing it beside a price level confuses what to monitor
    #: with what to build.
    data_needed: list = dc_field(default_factory=list)

    def lead(self) -> dict:
        """Three short lines, readable before any table."""
        counter = (self.findings[0].contradicts.split(";")[0].strip()
                   if self.findings else "no counterevidence is identified")
        nxt = (f"{self.watch_next[0].observation} — {self.watch_next[0].trigger}"
               if self.watch_next else "no specific next observation is defined")
        return {"assessment": self.verdict,
                "main_counterevidence": counter[:400],
                "next_observation": nxt}

    def as_dict(self) -> dict:
        return {
            **self.lead(),
            "horizon": self.horizon,
            "why_it_matters": [f.as_dict() for f in self.findings],
            "counterargument": self.counterargument,
            "watch_next": [w.as_dict() for w in self.watch_next],
            "what_limits_this": self.material_limits,
            "data_needed": self.data_needed,
            "other_unavailable_inputs": self.other_limits_count,
        }


def _ok(fields, key):
    f = fields.get(key)
    return f is not None and f.state is FieldState.OK


def _v(fields, key, default=None):
    f = fields.get(key)
    return f.value if f is not None and f.state is FieldState.OK else default


def _pct(fields, key):
    v = _v(fields, key)
    if isinstance(v, dict):
        v = v.get("pct")
    return v if isinstance(v, (int, float)) else None


#: Which missing inputs actually constrain a conclusion, most limiting first. Ranked per report
#: type because the same gap matters differently: no volatility series blocks a market-wide
#: direction claim, while for one issuer the absent comparable expectation matters more.
_LIMIT_RANK = {
    "market_outlook": ["volatility", "rates_credit_fx", "macro", "liquidity", "positioning"],
    "stock_outlook": ["company_condition", "valuation", "estimate_revisions", "news",
                      "options_positioning"],
    "pre_earnings": ["consensus_eps", "consensus_revenue", "prior_guidance", "fiscal_period",
                     "accounting_basis", "options_expected_move"],
    "post_earnings": ["pre_report_link", "guidance_change", "accounting_basis",
                      "management_commentary", "revenue_surprise_pct", "options_reaction"],
}

#: Why each one matters, so a named gap explains itself instead of repeating its field reason.
_LIMIT_WHY = {
    "volatility": "no volatility series, so a calm advance cannot be told from a fragile one",
    "rates_credit_fx": "no rates or credit series, so the financing backdrop is unobserved",
    "macro": "no macro releases with their prior expectations, so surprises cannot be placed",
    "liquidity": "no financial-conditions measure",
    "positioning": "no aggregated positioning, so crowding is unobserved",
    "company_condition": "no fundamentals series, so the price structure has no earnings context",
    "valuation": "no multiples with peer or historical context, so the move cannot be placed "
                 "against what was already priced",
    "estimate_revisions": "only one consensus snapshot, so the direction of expectations is "
                          "unobserved",
    "news": "headlines are not joined with their availability times",
    "options_positioning": "no per-leg quotes, so positioning is unobserved",
    "consensus_eps": "no EPS expectation to freeze, so a surprise cannot be defined",
    "consensus_revenue": "no revenue expectation to freeze",
    "prior_guidance": "no prior company guidance, so a raise cannot be distinguished from a "
                      "first issue",
    "fiscal_period": "the period is not source-confirmed, so WHICH results these are is unsettled",
    "accounting_basis": "the estimate's basis is unknown, so a difference is not a verified beat",
    "options_expected_move": "no expected move, so the reaction has no interval to be judged "
                             "against",
    "pre_report_link": "no frozen pre-release baseline, so nothing records what was expected "
                       "beforehand",
    "guidance_change": "no comparable prior forecast, so raised/maintained/lowered is unsettled",
    "management_commentary": "no management commentary, so the company's own account is absent",
    "revenue_surprise_pct": "revenue cannot be compared against an expectation",
    "options_reaction": "no fresh per-leg quotes, so an option outcome cannot be stated",
}


def _limits(fields, report_type: str, keep: int = 3) -> tuple[list[str], int]:
    """Name the few gaps that constrain THIS conclusion; count the rest."""
    ranked = _LIMIT_RANK.get(report_type, [])
    missing = [k for k in ranked if k in fields and not _ok(fields, k)]
    others = sum(1 for k, f in fields.items()
                 if f.state in (FieldState.UNAVAILABLE, FieldState.UNKNOWN)
                 and k not in missing[:keep])
    named = [f"{k.replace('_', ' ')} — {_LIMIT_WHY.get(k, 'not joined to this report')}"
             for k in missing[:keep]]
    return named, others


def _window_reading(fields) -> dict:
    """What the RETURN WINDOWS say, separately from where the close sits.

    THE DEFECT THIS CLOSES. The headline was derived from the moving-average structure alone, so
    the US benchmark — above both averages with a 20-bar return of -0.46% — was described as
    "longer-window strength". Being above an average and having risen over the window are
    different facts, and when they disagree that disagreement IS the finding.
    """
    r = {n: _pct(fields, f"return_{n}_bars") for n in (1, 5, 20, 63)}
    longer = [r[n] for n in (20, 63) if r[n] is not None]
    recent = [r[n] for n in (1, 5) if r[n] is not None]
    return {
        "returns": r,
        "longer_positive": bool(longer) and all(x > 0 for x in longer),
        "longer_negative": bool(longer) and all(x < 0 for x in longer),
        "longer_mixed": len(longer) > 1 and not (all(x > 0 for x in longer)
                                                 or all(x < 0 for x in longer)),
        "recent_negative": bool(recent) and all(x < 0 for x in recent),
        "recent_positive": bool(recent) and all(x > 0 for x in recent),
        "has_longer": bool(longer), "has_recent": bool(recent),
    }


def _windows_text(r: dict, names) -> str:
    bits = [f"{r['returns'][n]:+.2f}% over {n} bar{'s' if n != 1 else ''}"
            for n in names if r["returns"].get(n) is not None]
    return ", ".join(bits)


def _structure_finding(fields, subject: str) -> Finding | None:
    """Where the close sits, what the windows say, and where the two disagree."""
    t = _v(fields, "trend_structure")
    if not isinstance(t, dict):
        return None
    above20, above50 = t.get("above_sma20"), t.get("above_sma50")
    sma20 = t.get("sma20")
    both = above20 and above50
    w = _window_reading(fields)

    # SUPPORTS ALWAYS NAMES ITS WINDOWS. A claim about a longer window that does not show the
    # window's return is unsupported on its face.
    where = ("the close is above both the 20- and 50-bar averages" if both else
             "the close is above one average and below the other" if (above20 or above50) else
             "the close is below both the 20- and 50-bar averages")
    longer_txt = _windows_text(w, (20, 63))
    supports = where + (f". Over the longer windows: {longer_txt}" if longer_txt else "")

    against = []
    recent_txt = _windows_text(w, (1, 5))
    if w["recent_negative"] and recent_txt:
        against.append(f"the most recent bars move the other way ({recent_txt}), which is "
                       f"recent weakness and does not establish whether the pullback reverses "
                       f"or deepens")
    if both and (w["longer_negative"] or w["longer_mixed"]):
        against.append("being ABOVE an average and having RISEN over that window are different "
                       "facts, and here they disagree — the structure is intact while the "
                       "longer-window return is not positive")
    if not against:
        against.append("a moving-average structure describes past bars. It carries no horizon "
                       "and does not establish what the next bars do")

    # THE INVALIDATION IS SCOPED. "Invalidated below 1025" read as though it ended every
    # bullish case; it ends ONE condition.
    inval = (f"a completed daily close below the 20-bar average (currently {sma20}) would end "
             f"the above-20-bar-average condition this finding rests on — NOT every longer-term "
             f"reading. The average is recomputed each session, so monitor the live average "
             f"rather than treating {sma20} as a fixed level"
             if sma20 is not None else
             "a completed daily close below the 20-bar average, which is recomputed each session")
    return Finding(
        headline=f"{subject}: {t.get('structure', 'structure unavailable')}",
        supports=supports, contradicts="; ".join(against), invalidated_by=inval,
        evidence_ids=tuple((fields.get("trend_structure").evidence_ids or [])[:4]))


def _participation_finding(fields, prior_pct: float | None,
                           prior_covered: int | None = None) -> Finding | None:
    """Participation is a LEVEL. Direction needs a comparison, and says so when it lacks one."""
    b = _v(fields, "breadth")
    if not isinstance(b, dict):
        return None
    pct, covered, universe = (b.get("participation_pct"), b.get("covered"), b.get("universe"))
    supports = (f"{pct}% of the {covered} covered symbols are above their own 20-bar average. "
                f"This describes the ingested universe ({covered} of {universe} symbols), not "
                f"index constituents")
    if prior_pct is None:
        contradicts = ("no earlier participation reading is on file, so whether this is "
                       "broadening or narrowing is UNKNOWN. A level alone cannot support a "
                       "claim that breadth is strengthening")
        inval = "an earlier reading, once available, that shows participation falling"
    elif prior_covered is not None and covered is not None and prior_covered != covered:
        # NOT LIKE FOR LIKE. A different number of covered symbols means the two percentages
        # have different denominators, and their difference is partly a coverage change.
        contradicts = (f"an earlier reading exists ({prior_pct}%) but covered {prior_covered} "
                       f"symbols against {covered} now, so the change is not like-for-like and "
                       f"no direction is claimed from it")
        inval = "a comparable reading over the same symbol population"
        return Finding(headline=f"Participation {pct}% of covered symbols",
                       supports=supports, contradicts=contradicts, invalidated_by=inval)
    else:
        delta = pct - prior_pct
        direction = "broadening" if delta > 0 else ("narrowing" if delta < 0 else "flat")
        supports += f", {direction} from {prior_pct}% five sessions earlier ({delta:+.1f}pp)"
        contradicts = ("participation measures how many symbols are above their own average, "
                       "not how far — a broad, shallow advance and a narrow, strong one can "
                       "produce the same number")
        inval = f"participation falling back below {min(pct, prior_pct):.1f}%"
    return Finding(headline=f"Participation {pct}% of covered symbols",
                   supports=supports, contradicts=contradicts, invalidated_by=inval)


def _leadership_finding(fields) -> Finding | None:
    s = _v(fields, "sector_leadership") or _v(fields, "sector_context")
    if not isinstance(s, dict) or not s.get("ranked"):
        return None
    ranked = list(s["ranked"])
    top, bottom = ranked[0], ranked[-1]
    thin = [r for r in ranked if (r.get("symbols") or 0) < 5]
    supports = (f"leadership is concentrated in {top.get('sector')} "
                f"({top.get('mean_return_pct'):+.2f}% over {s.get('sessions')} sessions, "
                f"{top.get('symbols')} symbols); {bottom.get('sector')} is weakest at "
                f"{bottom.get('mean_return_pct'):+.2f}%")
    contradicts = ("these are equal-weighted means of covered symbols, not sector indices, so "
                   "they do not measure where capital went")
    if thin:
        contradicts += (f". {len(thin)} sector(s) rest on fewer than five symbols "
                        f"({', '.join(r.get('sector', '?') for r in thin[:3])}) and should not "
                        f"be read as sector readings at all")
    return Finding(headline=f"{top.get('sector')} leads, {bottom.get('sector')} lags",
                   supports=supports, contradicts=contradicts,
                   invalidated_by=f"{top.get('sector')} falling out of the top rank over the "
                                  f"next {s.get('sessions')} sessions")


def outlook_assessment(fields, *, subject: str, report_type: str,
                       prior_participation: float | None = None,
                       prior_covered: int | None = None) -> Field:
    """The opening read for a market or stock outlook."""
    findings = [f for f in (_structure_finding(fields, subject),
                            _participation_finding(fields, prior_participation, prior_covered),
                            _leadership_finding(fields)) if f is not None][:3]
    limits, others = _limits(fields, report_type)

    t = _v(fields, "trend_structure") or {}
    w = _window_reading(fields)
    both = t.get("above_sma20") and t.get("above_sma50")
    if not t:
        verdict = f"{subject}: insufficient evidence — no price structure is on file."
    elif both and w["longer_positive"] and w["recent_negative"]:
        verdict = (f"{subject}: longer-window strength, recent weakness; near-term direction "
                   f"unresolved.")
    elif both and w["longer_positive"]:
        verdict = (f"{subject}: structure and longer-window returns agree; no horizon is "
                   f"implied.")
    elif both and w["has_longer"]:
        # The case that was being mislabelled.
        verdict = (f"{subject}: above both averages WITHOUT a positive longer-window return — "
                   f"structure and returns disagree; direction unresolved.")
    elif both:
        verdict = f"{subject}: above both averages; no return windows are on file to corroborate."
    elif t.get("above_sma20") or t.get("above_sma50"):
        verdict = f"{subject}: structure in transition between its two averages."
    else:
        verdict = f"{subject}: below both averages."

    counter = ("The strongest argument against this reading is that it rests entirely on price "
               "relative to its own averages. " +
               (limits[0].split(" — ", 1)[1].capitalize() + ". " if limits else "") +
               "Nothing here establishes why the structure formed, so it cannot distinguish a "
               "continuation from a reversal already underway.")

    watch = []
    sma20 = t.get("sma20")
    if sma20 is not None:
        watch.append(Watch(
            observation=f"the daily close against {sma20} (the 20-bar average)",
            trigger=f"a daily close below {sma20}",
            would_change="it would remove the structure this assessment rests on"))
    b = _v(fields, "breadth")
    if isinstance(b, dict):
        watch.append(Watch(
            observation="participation among covered symbols",
            trigger=(f"a reading materially below {b.get('participation_pct')}% on the next "
                     f"snapshot" if prior_participation is None else
                     "a second consecutive fall in participation"),
            would_change="a falling participation reading alongside a holding index would mean "
                         "the advance is narrowing"))
    a = Assessment(verdict=verdict,
                   horizon=("no horizon is claimed: these are daily-bar observations, and no "
                            "horizon-specific evidence is joined to this report"),
                   findings=findings, counterargument=counter, watch_next=watch[:3],
                   material_limits=limits, other_limits_count=others,
                   data_needed=[l.split(" — ")[0] for l in limits])
    return interpreted(a.as_dict(), label="Read this first")


def post_earnings_assessment(fields, *, subject: str) -> Field:
    """What actually changed, how it compares, how price responded, and whether those agree."""
    findings, watch = [], []
    figs = _v(fields, "official_figures")
    rev, eps = _v(fields, "revenue_actual"), _v(fields, "eps_actual")
    if isinstance(figs, dict) and (rev or eps):
        named = []
        if isinstance(rev, dict):
            named.append(f"revenue {rev.get('value'):,.0f} {rev.get('units')} "
                         f"({rev.get('basis')})")
        if isinstance(eps, dict):
            named.append(f"EPS {eps.get('value')} {eps.get('units')} ({eps.get('basis')})")
        findings.append(Finding(
            headline="Results are sourced from the issuer's own release",
            supports="; ".join(named) + ", each carrying the basis the issuer stated",
            contradicts=("the stored ESTIMATE carries neither units nor an accounting basis, so "
                         "the difference against it is a factual difference and NOT a verified "
                         "beat or miss"),
            invalidated_by="a corrected release from the issuer, which would supersede these",
            evidence_ids=tuple((fields.get("official_figures").evidence_ids or [])[:2])))

    g_now, g_chg = _v(fields, "guidance_current"), fields.get("guidance_change")
    if isinstance(g_now, dict):
        findings.append(Finding(
            headline="Guidance was issued with these results",
            supports=("the company's own forward numbers for the next period are on file, with "
                      "their basis and target period"),
            contradicts=("whether this is a RAISE is NOT established. That needs the prior "
                         "guidance for the SAME target period on the SAME basis, which is not "
                         "stored — the previous quarter's guidance for a different quarter is "
                         "not that comparison"),
            invalidated_by="the prior guidance arriving and showing a maintained or lower range"))
        watch.append(Watch(
            observation="prior guidance for the same target period",
            trigger="the earlier forecast being ingested",
            would_change="it is the single input that converts 'guidance issued' into "
                         "'guidance raised, maintained or lowered'"))

    r1 = _v(fields, "return_1d")
    if isinstance(r1, dict) and r1.get("pct") is not None:
        findings.append(Finding(
            headline=f"The shares returned {r1['pct']:+.2f}% across the release",
            supports=f"measured {r1.get('window')}",
            contradicts=("this is a close-to-close return spanning " +
                         (r1.get("basis") or "the stated window") +
                         ". It includes pre-announcement trading and does not isolate the "
                         "announcement's effect. No cause is attributed to it here"),
            invalidated_by="a five-session return that resolves in the opposite direction",
            evidence_ids=tuple((fields.get("return_1d").evidence_ids or [])[:2])))
        watch.append(Watch(
            observation="the five-session return across the release",
            trigger="the endpoint maturing",
            would_change="a reversal would argue the first reaction was not durable"))

    limits, others = _limits(fields, "post_earnings")
    have_figs = isinstance(figs, dict)
    have_baseline = _ok(fields, "pre_report_link")
    if have_figs and not have_baseline:
        verdict = (f"{subject}: official results and current guidance are available; the "
                   f"comparison with pre-release expectations is unverified.")
    elif have_figs:
        verdict = (f"{subject}: official results available and scored against a frozen "
                   f"pre-release baseline.")
    else:
        verdict = (f"{subject}: insufficient evidence — no issuer release is joined to this "
                   f"event.")

    counter = ("What most limits this reading: " +
               (limits[0] if limits else "no material gap is identified") +
               ". Without it the report cannot explain the price response or call the release a "
               "favourable or unfavourable outcome — only state what was reported and how the "
               "shares moved over a named window.")

    a = Assessment(
        verdict=verdict,
        horizon="at the event; the price observation spans the window named with it",
        findings=findings[:3], counterargument=counter, watch_next=watch[:3],
        material_limits=limits, other_limits_count=others,
        data_needed=[l.split(" — ")[0] for l in limits])
    return interpreted(a.as_dict(), label="Read this first")


def pre_earnings_assessment(fields, *, subject: str, event_date: str | None = None,
                            days_out: int | None = None) -> Field:
    """The early-preparation read: can this report evaluate the setup yet, and if not, what is
    missing?

    WHY IT EXISTS. The pre-earnings report had no opening at all, so a reader met a wall of
    UNKNOWN fields with no statement of what the report could or could not yet conclude. "The
    expectations are not on file" is a conclusion, and a useful one — it says the setup cannot
    be evaluated and names the input that would change that.
    """
    have_eps = _ok(fields, "consensus_eps")
    have_rev = _ok(fields, "consensus_revenue")
    have_prior = _ok(fields, "prior_guidance")
    have_period = _ok(fields, "fiscal_period")
    findings, watch = [], []

    if not (have_eps or have_rev):
        verdict = (f"{subject}: the earnings setup CANNOT be evaluated yet — no expectation is "
                   f"on file to freeze, so there is nothing a result could surprise against.")
    elif not have_prior:
        verdict = (f"{subject}: expectations are on file and can be frozen; company guidance is "
                   f"not, so a guidance change will not be measurable afterwards.")
    else:
        verdict = (f"{subject}: expectations and prior guidance are on file; the setup can be "
                   f"frozen and evaluated after the release.")

    findings.append(Finding(
        headline="What this report can freeze",
        supports=("a frozen baseline is what makes 'were we right' answerable afterwards; "
                  + ", ".join(filter(None, [
                      "an EPS expectation is on file" if have_eps else None,
                      "a revenue expectation is on file" if have_rev else None,
                      "prior company guidance is on file" if have_prior else None]))
                  or "nothing in the comparison set is on file"),
        contradicts=("a frozen expectation is not a forecast and carries no probability; it "
                     "records what was expected, nothing about what will happen"),
        invalidated_by="the provider revising the estimate before the release, which the "
                       "frozen copy deliberately does not follow"))

    snap = _v(fields, "snapshot_timing")
    if isinstance(snap, dict) and snap.get("stage") == "early_preparation":
        findings.append(Finding(
            headline=f"This is an EARLY snapshot, {snap.get('calendar_days_before_scheduled_release', days_out)} calendar days before the scheduled release",
            supports="prices and structure here describe today, not the session before the "
                     "release; later snapshots are captured as their own versions",
            contradicts="an early snapshot is NOT the immediate pre-release reference, and a "
                        "reaction measured against it would include weeks of unrelated trading",
            invalidated_by="the immediate pre-release snapshot, which supersedes this one for "
                           "reaction measurement"))

    if not (have_eps or have_rev):
        watch.append(Watch(
            observation="the consensus estimate for this event",
            trigger="an estimate being published and ingested before the release",
            would_change="it is the single input that makes a surprise definable"))
    if event_date:
        watch.append(Watch(
            observation=f"the release itself on {event_date}",
            trigger="the issuer publishing results",
            would_change="it converts every conditional here into a measured outcome"))

    limits, others = _limits(fields, "pre_earnings")
    counter = ("What most limits this reading: " + (limits[0] if limits else "no material gap") +
               ". Until that is resolved the report can describe the setup but cannot say "
               "whether a result would be a surprise.")
    a = Assessment(verdict=verdict,
                   horizon=(f"until the scheduled release"
                            + (f" on {event_date}" if event_date else "")),
                   findings=findings[:3], counterargument=counter, watch_next=watch[:3],
                   material_limits=limits, other_limits_count=others,
                   data_needed=[l.split(" — ")[0] for l in limits])
    return interpreted(a.as_dict(), label="Read this first")
