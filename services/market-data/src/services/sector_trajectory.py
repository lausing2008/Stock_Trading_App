"""T258-SECTOR-ROTATION-TRAJECTORY: classifies whether a sector's K-Score leadership is rising
or fading, by comparing its current rank against a snapshot from ~4 weeks ago.

Before this module, _compute_sector_rotation() (scheduler.py) only ever answered "what is this
week's sector K-Score snapshot" — a single Redis key, overwritten every week, no history. That
tells you a sector's CURRENT momentum sign but nothing about whether its RANK among sectors is
improving or declining over time, which is the actionable half of rotation analysis: an
Emerging Leader (rank climbing into the top half) is a buy-context; an Established Leader
printing the same top-half rank week after week is just status quo, not a fresh signal.

Uses the vocabulary from the original "Combined Agent Catalog" design doc this tracker item
cites: Emerging Leader / Established Leader / Fading Leader / Emerging Laggard / Established
Laggard / Fading Laggard — six classes from the cross of {top half, bottom half} x
{rank improved, rank unchanged/small move, rank worsened}.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SectorRank:
    sector: str
    recent_kscore: float | None
    rank: int | None  # 1 = highest recent_kscore this snapshot; None if unrankable (no kscore)


def rank_sectors(rotation: dict[str, dict]) -> list[SectorRank]:
    """Assign a 1-indexed rank (1 = highest recent_kscore) to every sector that has a real
    recent_kscore this snapshot. Sectors with no recent_kscore (insufficient ranking data that
    week) get rank=None — they're excluded from the ranking, not assigned a fake last place.
    """
    rankable = [
        (sector, data.get("recent_kscore"))
        for sector, data in rotation.items()
        if data.get("recent_kscore") is not None
    ]
    rankable.sort(key=lambda pair: pair[1], reverse=True)
    ranks: dict[str, int] = {sector: i + 1 for i, (sector, _) in enumerate(rankable)}
    return [
        SectorRank(sector=sector, recent_kscore=data.get("recent_kscore"), rank=ranks.get(sector))
        for sector, data in rotation.items()
    ]


_TRAJECTORY_LABELS = {
    ("top", "up"): "Emerging Leader",
    ("top", "flat"): "Established Leader",
    ("top", "down"): "Fading Leader",
    ("bottom", "up"): "Emerging Laggard",
    ("bottom", "flat"): "Established Laggard",
    ("bottom", "down"): "Fading Laggard",
}


def classify_trajectory(
    current_rank: int | None, prior_rank: int | None, total_sectors: int,
    flat_threshold: int = 1, prior_total_sectors: int | None = None,
) -> str | None:
    """Classify a sector's trajectory from its rank this snapshot vs. ~4 weeks prior.

    `total_sectors` is the count of RANKABLE sectors THIS snapshot (used to determine the
    top/bottom-half cutoff for `current_rank`) — not a hardcoded constant, since the real
    sector universe can genuinely vary between snapshots (a sector can drop out entirely if it
    has too few ranked stocks that week, per rank_sectors()'s own exclusion of unrankable
    sectors).

    `prior_total_sectors` is the count of rankable sectors the PRIOR snapshot had. AUD-T258-
    RANKNORM: the rankable field size can genuinely differ week to week (more/fewer sectors
    clearing the >=3-ranked-stocks floor), so comparing raw rank NUMBERS across two different
    field sizes is wrong — rank 4 of 4 (dead last) and rank 4 of 8 (top half) are not the same
    standing, but a raw-rank delta of 0 would call that "flat," mislabeling a sector that was
    last place a month ago as a steady leader purely because the field grew. Both ranks are
    normalized to a 0-1 percentile (0 = best) using their OWN respective field size before
    computing delta/half, so the comparison is standing-relative-to-peers, not raw position.
    Defaults to `total_sectors` when omitted (the pre-normalization behavior, for any caller
    that genuinely has only one field size to compare — e.g. same-snapshot comparisons).

    `flat_threshold`: expressed in the SAME units as `total_sectors` (rank-equivalent places,
    not raw percentile) — a percentile delta of `flat_threshold / total_sectors` or less counts
    as "flat" (Established) rather than "up"/"down". Default 1: roughly a 1-place move in a
    field the size of `total_sectors` counts as flat.

    Returns None when either rank or the current total is unavailable (a sector newly entering
    the rankable set, or one that dropped out 4 weeks ago) — there's no real trajectory to
    report without both endpoints, and a caller must not guess one.
    """
    if current_rank is None or prior_rank is None or total_sectors <= 0:
        return None
    prior_total = prior_total_sectors if prior_total_sectors is not None else total_sectors
    if prior_total <= 0:
        return None

    half = (total_sectors + 1) / 2  # rank <= half is "top half" (ties resolved toward top)
    current_half = "top" if current_rank <= half else "bottom"

    # Percentile position within each snapshot's own field (0 = best/rank-1, 1 = worst/last).
    # Using (rank - 1) / (total - 1) so rank 1 is always exactly 0 and the last rank is always
    # exactly 1, regardless of field size — the two endpoints are then directly comparable.
    current_pctl = (current_rank - 1) / (total_sectors - 1) if total_sectors > 1 else 0.0
    prior_pctl = (prior_rank - 1) / (prior_total - 1) if prior_total > 1 else 0.0

    delta_pctl = prior_pctl - current_pctl  # positive = rank improved (lower percentile is better)
    flat_band = flat_threshold / max(total_sectors - 1, 1)
    if delta_pctl > flat_band:
        direction = "up"
    elif delta_pctl < -flat_band:
        direction = "down"
    else:
        direction = "flat"

    return _TRAJECTORY_LABELS[(current_half, direction)]


def build_trajectories(
    current_ranks: list[SectorRank], prior_ranks: list[SectorRank],
) -> dict[str, dict]:
    """Combine this snapshot's ranks with a prior snapshot's ranks into a per-sector trajectory
    payload: {sector: {rank, prior_rank, trajectory, recent_kscore}}.
    """
    prior_by_sector = {r.sector: r.rank for r in prior_ranks}
    total = sum(1 for r in current_ranks if r.rank is not None)
    prior_total = sum(1 for r in prior_ranks if r.rank is not None)

    result: dict[str, dict] = {}
    for r in current_ranks:
        prior_rank = prior_by_sector.get(r.sector)
        result[r.sector] = {
            "rank": r.rank,
            "prior_rank": prior_rank,
            "recent_kscore": r.recent_kscore,
            "trajectory": classify_trajectory(r.rank, prior_rank, total, prior_total_sectors=prior_total),
        }
    return result
