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

from intelligence.report_contract import (
    FieldState, Field, StatementClass, interpreted, unknown)


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
    #: Inputs the report would need. NOT market observations — an estimate being ingested is
    #: something WE do, and listing it beside a price level confuses what to monitor with what
    #: to obtain. Market watching is for releases, guidance, price behaviour and other external
    #: developments.
    data_needed: list = dc_field(default_factory=list)

    def lead(self) -> dict:
        """Three short lines, readable before any table."""
        # EVERY REAL CLAUSE, not just the first. Taking one dropped the peer lag — the most
        # substantive counterevidence the report has — after the latest bar's return.
        if self.findings:
            clauses = [c.strip() for c in self.findings[0].contradicts.split(";")]
            real = [c for c in clauses if "as a method limit" not in c]
            counter = "; ".join(real) or clauses[0]
        else:
            counter = "no counterevidence is identified"
        nxt = (f"{self.watch_next[0].observation} — {self.watch_next[0].trigger}"
               if self.watch_next else "no specific next observation is defined")
        return {"assessment": self.verdict,
                "main_counterevidence": counter[:400],
                "next_observation": nxt,
                "evidence_needed": self.data_needed[:3]}

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


def _money(v, units) -> str:
    """One scaling rule for figures named in an assessment."""
    if not isinstance(v, (int, float)):
        return str(v)
    u = (units or "").strip().lower()
    if u in ("usd", "$"):
        for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if abs(v) >= scale:
                return f"${v / scale:,.2f}{suffix}"
        return f"${v:,.2f}"
    if u == "usd/share":
        return f"${v:,.2f} per share"
    return f"{v:,.2f} {units}".strip()


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


def _evidence_needed(fields, limits) -> list:
    """Inputs to OBTAIN. Never market observations — ingesting a forecast is something we do.

    A prior forecast sat under "Watch next" beside a price level, which confuses what to
    monitor with what to acquire. It belongs here.
    """
    needed = [l.split(" — ")[0] for l in limits]
    if _ok(fields, "guidance_current") and not _ok(fields, "guidance_change"):
        needed.insert(0, "a comparable prior forecast for the same fiscal period, without "
                         "which current guidance cannot be called raised, maintained or "
                         "lowered")
    return needed


def _results_context(fields) -> bool:
    return _ok(fields, "revenue_actual") or _ok(fields, "eps_actual")


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
    # ACTUAL counterevidence before the methodological caveat. A negative latest bar and a
    # peer lag are things that HAPPENED; "averages describe past bars" is a limitation of the
    # method, and leading with it buried the evidence.
    r1_only = w["returns"].get(1)
    if not w["recent_negative"] and r1_only is not None and r1_only < 0:
        against.append(f"the latest bar is {r1_only:+.2f}%")
    rel = _relative_gap(fields, subject=subject)
    if rel is not None and rel["gap"] < 0:
        against.append(f"over the same {rel['sessions']} sessions the stock trails its "
                       f"{rel['industry']} peers by {abs(rel['gap']):.2f}pp "
                       f"({rel['own']:+.2f}% against {rel['peer']:+.2f}%)")
    against.append("separately, as a method limit: a moving-average structure describes past "
                   "bars, carries no horizon, and does not establish what the next bars do")

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
                           prior_covered: int | None = None,
                           prior_population: str | None = None) -> Finding | None:
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
    elif (prior_covered is not None and covered is not None and prior_covered != covered) or (
            prior_population is not None and b.get("population_fingerprint") is not None
            and prior_population != b.get("population_fingerprint")):
        # NOT LIKE FOR LIKE. Equal counts are not the same symbols: one company dropping out as
        # another gains its 21st bar leaves the denominator unchanged while the population
        # changes, and the difference is then partly composition rather than market.
        same_count = prior_covered == covered
        contradicts = (
            (f"an earlier reading exists ({prior_pct}%) over the SAME NUMBER of symbols "
             f"({covered}) but not the same ones, "
             if same_count else
             f"an earlier reading exists ({prior_pct}%) but covered {prior_covered} symbols "
             f"against {covered} now, ")
            + "so the change is not like-for-like and no direction is claimed from it")
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


def _peer_relative_finding(fields, *, subject: str) -> Finding | None:
    """The stock against its own industry peers — the company-specific part of the move."""
    r = _relative_gap(fields, subject=subject)
    if r is None:
        return None
    direction = "trails" if r["gap"] < 0 else "leads"
    return Finding(
        headline=(f"{subject} {direction} the {r['members']} covered {r['industry']} peers "
                  f"by {abs(r['gap']):.2f}pp over {r['sessions']} sessions"),
        supports=(f"{subject} returned {r['own']:+.2f}% against {r['peer']:+.2f}% for an "
                  f"{r['weighting']} basket of {r['members']} covered {r['industry']} symbols, "
                  f"{'including' if r['subject_included'] else 'excluding'} {subject} itself, "
                  f"over an identical window on {r['convention']}s"),
        contradicts=(f"this is the {r['members']} {r['industry']} symbols THIS PLATFORM "
                     f"covers, not a complete industry benchmark, and it is not "
                     f"capitalisation-weighted. A gap locates the company-specific part of the "
                     f"move; it does not say what produced it"),
        invalidated_by=(f"the gap closing to zero over the same {r['sessions']}-session window"),
        evidence_ids=tuple((fields.get("peer_basket").evidence_ids or [])[:2]
                           if fields.get("peer_basket") else ()))


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
                       prior_covered: int | None = None,
                       prior_population: str | None = None) -> Field:
    """The opening read for a market or stock outlook."""
    findings = [f for f in (_structure_finding(fields, subject),
                            _peer_relative_finding(fields, subject=subject),
                            _participation_finding(fields, prior_participation, prior_covered,
                                                   prior_population),
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
        _pos = ", ".join(f"{n}-session" for n in (20, 63)
                         if (w["returns"].get(n) or 0) > 0)
        verdict = (f"{subject}: remains above both moving averages with positive {_pos} "
                   f"returns.")
    elif both and w["has_longer"]:
        # NAME THE WINDOW. "Without a positive longer-window return" is too broad when one of
        # the two longer windows IS positive — here 20 bars is negative and 63 is +2.44%.
        neg = [n for n in (20, 63) if (w["returns"].get(n) or 0) < 0]
        pos = [n for n in (20, 63) if (w["returns"].get(n) or 0) > 0]
        parts = []
        if neg:
            parts.append("the " + " and ".join(f"{n}-bar" for n in neg) +
                         f" return is negative")
        if pos:
            parts.append("the " + " and ".join(f"{n}-bar" for n in pos) +
                         f" return remains positive")
        verdict = (f"{subject}: above both averages, but " + "; ".join(parts) +
                   ". Direction unresolved.")
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
        # A RULE, NOT A LEVEL. Printing "a daily close below 764.83" made a recomputed average
        # look like a fixed threshold that stays put while the market moves.
        watch.append(Watch(
            observation="the next completed daily close versus the RECALCULATED 20-bar average",
            trigger=f"a completed close below the 20-bar average as it stands that session "
                    f"(snapshot reference: {sma20})",
            would_change="it would end the above-20-bar-average condition this assessment "
                         "rests on"))
    # EACH FINDING GETS ITS OWN. A close below the average changes the technical condition and
    # resolves nothing about peer performance or guidance; one trigger cannot stand for three.
    _r = _relative_gap(fields, subject=subject)
    if _r is not None:
        watch.append(Watch(
            observation=(f"the {_r['sessions']}-session return gap against the "
                         f"{_r['industry']} peer basket (currently {_r['gap']:+.2f}pp)"),
            trigger="the gap narrowing towards zero, or widening further",
            would_change="it is the company-specific part of the move, and it is independent "
                         "of where the close sits relative to its averages"))
    b = _v(fields, "breadth")
    if isinstance(b, dict):
        watch.append(Watch(
            observation="participation among covered symbols",
            trigger=(f"a reading materially below {b.get('participation_pct')}% on the next "
                     f"snapshot" if prior_participation is None else
                     "a second consecutive fall in participation"),
            would_change="a falling participation reading alongside a holding index would mean "
                         "the advance is narrowing"))
    # RELATIVE WEAKNESS BELONGS IN THE OPENING. "Structure and returns agree" is true and hides
    # the newest thing the report knows — that the stock is trailing its own peers.
    _rel = _relative_gap(fields, subject=subject)
    if _rel is not None and t:
        _dir = "trails" if _rel["gap"] < 0 else "leads"
        verdict = verdict.rstrip(".") + (
            f", but {_dir} its {_rel['industry']} peers by {abs(_rel['gap']):.2f}pp over the "
            f"same {_rel['sessions']} sessions.")
    if _results_context(fields):
        verdict += (" Official results and current guidance provide company context; they do "
                    "not establish what caused the price move.")

    a = Assessment(verdict=verdict,
                   horizon=("no horizon is claimed: these are daily-bar observations, and no "
                            "horizon-specific evidence is joined to this report"),
                   findings=findings, counterargument=counter, watch_next=watch[:4],
                   material_limits=limits, other_limits_count=others,
                   data_needed=_evidence_needed(fields, limits))
    return interpreted(a.as_dict(), label="Read this first")


def post_earnings_assessment(fields, *, subject: str) -> Field:
    """What actually changed, how it compares, how price responded, and whether those agree."""
    findings, watch = [], []
    figs = _v(fields, "official_figures")
    rev, eps = _v(fields, "revenue_actual"), _v(fields, "eps_actual")
    if isinstance(figs, dict) and (rev or eps):
        named = []
        # SCALED THE SAME WAY EVERYWHERE. A raw 54230000000 beside a "$54.23B" elsewhere in
        # the same report reads as two different figures.
        if isinstance(rev, dict):
            named.append(f"revenue {_money(rev.get('value'), rev.get('units'))} "
                         f"({rev.get('basis')})")
        if isinstance(eps, dict):
            named.append(f"EPS {_money(eps.get('value'), eps.get('units'))} "
                         f"({eps.get('basis')})")
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
        supports=("a frozen baseline is what makes 'were we right' answerable afterwards. "
                  + (("On file: " + ", ".join(_on_file) + ".") if (_on_file := [
                      n for n, ok in (("an EPS expectation", have_eps),
                                      ("a revenue expectation", have_rev),
                                      ("prior company guidance", have_prior)) if ok])
                     else "Nothing in the comparison set is on file.")),
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


# =====================================================================================
# DRIVERS — why the structure may have formed, as opposed to what it looks like.
#
# EVERY DRIVER CARRIES THE SAME FIVE PARTS: what changed, what it is compared against, the
# MECHANISM by which it could matter, the evidence against it, and what would change the
# reading. A driver without a mechanism is a coincidence with a date on it, and a mechanism is
# not a cause — it is a route by which something COULD matter, which is a different claim and
# is labelled as one.
# =====================================================================================

def _driver(name, what_changed, compared_with, mechanism, against, watch, evidence=()) -> dict:
    return {
        "driver": name,
        "what_changed": what_changed,
        "compared_with": compared_with,
        "why_it_may_matter": mechanism,
        "evidence_against": against,
        "what_to_watch": watch,
        "claim_type": "plausible mechanism, NOT an established cause",
        "evidence_ids": list(evidence),
    }


def earnings_driver(fields) -> dict | None:
    """MU's own results and guidance, joined under the same cutoff and provenance rules."""
    rev, eps = _v(fields, "revenue_actual"), _v(fields, "eps_actual")
    g_now = _v(fields, "guidance_current")
    period = _v(fields, "fiscal_period")
    if not isinstance(rev, dict) and not isinstance(eps, dict):
        return None

    named = []
    if isinstance(rev, dict):
        named.append(f"revenue {_money(rev.get('value'), rev.get('units'))} "
                     f"({rev.get('basis')}, {rev.get('period')})")
    if isinstance(eps, dict):
        named.append(f"EPS {_money(eps.get('value'), eps.get('units'))} "
                     f"({eps.get('basis')}, {eps.get('period')})")
    what = "the issuer reported " + "; ".join(named)
    if isinstance(period, dict):
        what += f", for {period.get('label')} ending {period.get('period_end')}"

    compared = ("no frozen pre-release expectation exists for this event, and the stored "
                "estimate carries neither units nor an accounting basis — so these figures are "
                "reported, not measured against anything")
    if _ok(fields, "pre_report_link"):
        compared = "a frozen pre-release baseline exists and these are scored against it"

    mech = ("reported revenue and earnings change what a share is a claim on, which is the "
            "route by which results could matter to price. That route is NOT demonstrated "
            "here: no link between these figures and the observed structure is established")
    against = ("the price structure above was formed over 20 and 63 sessions, while these "
               "figures were published on a single date — overlap in time is not evidence that "
               "one produced the other")
    watch = ("the next release, and any revision to these figures by the issuer")

    out = _driver("Company results", what, compared, mech, against, watch,
                  evidence=(fields.get("revenue_actual").evidence_ids
                            if fields.get("revenue_actual") else ()))
    if isinstance(g_now, dict):
        out["guidance"] = {
            "issued": True,
            "detail": "; ".join(
                f"{k.replace('guidance_', '').replace('_', ' ')} "
                f"{_money(v.get('value'), v.get('units'))} ({v.get('basis')}, {v.get('period')})"
                for k, v in g_now.items() if isinstance(v, dict)),
            # NEVER "raised" without the comparable prior.
            "change": ("NOT ESTABLISHED — a raise requires the PRIOR guidance for the SAME "
                       "target period on the SAME accounting basis, which is not stored"),
        }
    return out


def _relative_gap(fields, *, subject: str) -> dict | None:
    """MU against its own INDUSTRY peers, and separately against the broader sector.

    TWO BASKETS, NAMED SEPARATELY. The industry basket is the planned comparison and excludes
    the subject; the sector mean is broader context and (as published) includes it. Reporting
    one as the other is how "trails its peers" became a claim about 59 unrelated companies.
    """
    peers = _v(fields, "peer_basket")
    if not isinstance(peers, dict):
        return None
    sessions = peers.get("sessions")
    own = _pct(fields, f"return_{sessions}_bars")
    if own is None:
        return None
    gap = own - peers["mean_return_pct"]
    return {"sessions": sessions, "own": own, "peer": peers["mean_return_pct"], "gap": gap,
            "industry": peers.get("industry"), "members": peers.get("member_count"),
            "weighting": peers.get("weighting"), "subject_included": peers.get("subject_included"),
            "convention": peers.get("price_convention")}


def sector_relative_driver(fields, *, subject: str) -> dict | None:
    """The stock against its industry peers, over identical sessions and price conventions."""
    r = _relative_gap(fields, subject=subject)
    if r is None:
        return None
    direction = "ahead of" if r["gap"] > 0 else "behind"
    what = (f"over the same {r['sessions']} sessions, {subject} returned {r['own']:+.2f}% "
            f"against {r['peer']:+.2f}% for its {r['industry']} peers — {r['gap']:+.2f}pp "
            f"{direction} the basket")
    compared = (f"an {r['weighting']} basket of {r['members']} covered {r['industry']} symbols, "
                f"{'INCLUDING' if r['subject_included'] else 'EXCLUDING'} {subject} itself, over "
                f"an identical {r['sessions']}-session window on {r['convention']}s")

    sect = _v(fields, "sector_context") or _v(fields, "sector_leadership")
    issuer = _v(fields, "issuer") or {}
    broader = ""
    if isinstance(sect, dict) and sect.get("ranked"):
        row = next((x for x in sect["ranked"] if x.get("sector") == issuer.get("sector")), None)
        if row:
            # THE TWO COMPARISONS ANSWER DIFFERENT QUESTIONS. The sector mean did not
            # mismeasure semiconductor performance — it measured a broader basket. Both are
            # reported rather than one replacing the other.
            broader = (f". The {row.get('sector')} sector mean over the same window is "
                       f"{row.get('mean_return_pct'):+.2f}% across {row.get('symbols')} "
                       f"covered symbols, published including the subject. That is a "
                       f"DIFFERENT QUESTION, not a worse answer: it measures a broader basket "
                       f"than {r['industry']}, and both comparisons are reported")
    mech = ("the part of a move not shared with its peers is where company-specific "
            "explanations would have to act. This locates where to look; it does not identify "
            "what acted" + broader)
    against = (f"this basket is {r['members']} covered {r['industry']} symbols, "
               f"{r['weighting']} — not an industry index and not capitalisation-weighted. "
               f"Relative return is also not an independent driver: it is the same price series "
               f"measured against a different baseline")
    watch = (f"whether the {r['gap']:+.2f}pp gap against the {r['industry']} basket narrows or "
             f"widens over the next {r['sessions']} sessions, measured over the same window")
    return _driver(f"{r['members']} covered {r['industry']} peers, relative performance",
                   what, compared, mech, against, watch)


def drivers_for_stock(fields, *, subject: str) -> Field:
    """The drivers a stock report can support, each with its mechanism and its counterevidence."""
    found = [d for d in (earnings_driver(fields),
                         sector_relative_driver(fields, subject=subject)) if d]
    missing = []
    if not _ok(fields, "news"):
        missing.append("material news with per-item source times and first availability")
    if not _ok(fields, "estimate_revisions"):
        missing.append("estimate revisions, which would show the direction of expectations")
    if not found:
        return unknown(
            "no driver is supported by the evidence joined to this report. "
            + ("Needed: " + "; ".join(missing) if missing else ""),
            label="What may be driving this")
    return interpreted(
        {"drivers": found,
         "not_yet_joined": missing,
         "note": ("each driver states a MECHANISM — a route by which it could matter — and "
                  "never a cause. Nothing here establishes that any of these produced the "
                  "observed price structure.")},
        label="What may be driving this")


# =====================================================================================
# MARKET DRIVERS. Each states its MINIMUM EVIDENCE REQUIREMENT and refuses when it is unmet,
# because a driver assembled from inadequate provenance is worse than an absent one: it reads
# as corroboration while resting on nothing.
# =====================================================================================

def rates_driver(fields) -> dict | None:
    """Market-priced rates. Requires a named instrument, tenor, observation time and change."""
    r = _v(fields, "rates")
    if not isinstance(r, dict) or not r.get("readings"):
        return None
    read = r["readings"]
    moved, absent = [], []
    # EVERY series is checked for absence, not only the ones that get reported. Scanning a
    # subset meant an unobserved series could go unnamed — which is the absence-as-unchanged
    # error this field exists to avoid.
    for key, e in read.items():
        if (e or {}).get("status") == "NOT OBSERVED":
            absent.append(e.get("series", key))
    for key in ("yield_2y", "yield_10y", "yield_curve_2s10s", "hy_spread"):
        e = read.get(key) or {}
        if e.get("change") is None:
            continue
        bp = e.get("change_bp")
        moved.append(
            f"{e['series']} ({e['tenor']}) {e['latest']}% on {e['observation_date']}, "
            f"{bp:+.1f}bp from {e['previous']}% on {e['previous_observation_date']}")
    if not moved:
        return None

    what = "; ".join(moved)
    compared = ("each series against its OWN previous non-null observation, with both dates "
                "named — the daily row is not uniformly populated, so one date does not "
                "describe every series")
    recon = r.get("curve_reconciliation") or {}
    if recon.get("status") == "RECONCILED":
        compared += (f". On {recon['on_date']} the reported spread "
                     f"{recon['reported_spread']}% equals 10y minus 2y "
                     f"({recon['implied_10y_minus_2y']}%) on that same date")
    elif recon.get("status") == "NOT COMPARABLE":
        compared += f". {recon['reason']}"
    mech = ("a discount rate applied to future cash flows, and a financing cost for leveraged "
            "borrowers; a higher long yield lowers the present value of distant earnings, all "
            "else equal. WHICH WAY THAT POINTS DEPENDS ON WHY THE YIELD MOVED, and these "
            "readings do not contain the why. Two alternatives fit falling yields equally "
            "well here: (a) EASING INFLATION — a lower discount rate with earnings "
            "expectations intact, which is supportive; (b) WEAKER GROWTH — a lower discount "
            "rate alongside deteriorating earnings expectations, which is not. To tell them "
            "apart this report would need inflation releases against expectations captured "
            "before publication, and the direction of earnings estimate revisions. Neither is "
            "stored, so neither mechanism is asserted")
    against = ("these are MARKET-PRICED yields, not policy decisions, and they move on policy "
               "expectations, term premium and flows together. What is supported is the "
               "OBSERVATION over the windows named — yields fell, the high-yield spread "
               "widened — and not any account of why")
    if absent:
        against += f". Not observed in this window: {', '.join(absent)}"
    if r.get("dates_diverge"):
        _ages = ", ".join(
            f"{e['series']} {e['observation_date']}"
            + (f" ({e['observation_age_days']}d old)" if e.get("observation_age_days")
               is not None else "")
            for e in read.values() if e.get("observation_date"))
        against += (f". These series are NOT all observed on the same date — {_ages} — so they "
                    f"describe different moments and are not one market reading")
    against += (". Availability is evidenced by retrieval time only; the source's publication "
                "timestamp and revision vintage are not stored, so no value here can be shown "
                "to have been available earlier than it was retrieved")
    watch = ("the next observation of each named series, and whether the 2s10s spread and the "
             "high-yield spread move together or apart")
    return _driver("Rates and credit (market-priced)", what, compared, mech, against, watch)


def _unmet(name: str, requirement: str, found: str) -> dict:
    """A driver that cannot be formed, stating its requirement and what is actually there."""
    return {"driver": name, "status": "NOT FORMED",
            "minimum_requirement": requirement,
            "what_is_stored": found,
            "why_it_is_not_estimated": (
                "a driver assembled from inadequate provenance reads as corroboration while "
                "resting on nothing, which is worse than its absence")}


def earnings_revisions_driver(fields) -> dict:
    """Requires an earlier AND a later estimate for the same fiscal period."""
    return _unmet(
        "Earnings revisions",
        "an earlier and a later estimate for the SAME fiscal period, over a comparable universe "
        "and accounting basis, each with the time it was captured",
        "a single mutable estimate column per event, overwritten in place on each provider "
        "refresh. No earlier value is retained anywhere, so a revision cannot be measured — "
        "and the direction of expectations cannot be inferred from one snapshot without "
        "claiming a change that was never observed")


def macro_surprise_driver(fields) -> dict:
    """Requires a released actual against an expectation captured BEFORE publication."""
    return _unmet(
        "Macro surprises",
        "a released actual against an expectation captured BEFORE publication, with later "
        "revisions to the actual identified separately from the original vintage",
        "an economic calendar with released actuals but NO expectation on any row, so a "
        "surprise has nothing to be a surprise against. Revision vintages are also not "
        "distinguished, so a revised actual cannot be told from its first print")


def drivers_for_market(fields) -> Field:
    """Market drivers, in the order their evidence requirements are met."""
    formed = [d for d in (rates_driver(fields),) if d]
    unmet = [earnings_revisions_driver(fields), macro_surprise_driver(fields)]
    if not formed:
        return unknown(
            "no market driver meets its evidence requirement. "
            + "; ".join(f"{d['driver']}: {d['what_is_stored']}" for d in unmet),
            label="What may be driving this")
    return interpreted(
        {"drivers": formed,
         "not_formed": unmet,
         "note": ("each driver states a MECHANISM — a route by which it could matter — and "
                  "never a cause. Drivers whose minimum evidence is not stored are listed "
                  "with that requirement rather than estimated from what is available.")},
        label="What may be driving this")
