"""Tests for T258-SECTOR-ROTATION-TRAJECTORY's rank_sectors()/classify_trajectory()/
build_trajectories() — pure functions, no DB/network dependency, tested directly.
"""
from src.services.sector_trajectory import (
    SectorRank, build_trajectories, classify_trajectory, rank_sectors,
)


# ── rank_sectors() ────────────────────────────────────────────────────────────

def test_ranks_sectors_by_recent_kscore_descending():
    rotation = {
        "Technology": {"recent_kscore": 75.0},
        "Energy": {"recent_kscore": 60.0},
        "Healthcare": {"recent_kscore": 55.0},
    }
    ranks = {r.sector: r.rank for r in rank_sectors(rotation)}
    assert ranks["Technology"] == 1
    assert ranks["Energy"] == 2
    assert ranks["Healthcare"] == 3


def test_sector_with_no_recent_kscore_gets_rank_none_not_last_place():
    rotation = {
        "Technology": {"recent_kscore": 75.0},
        "ThinData": {"recent_kscore": None},
    }
    ranks = {r.sector: r.rank for r in rank_sectors(rotation)}
    assert ranks["Technology"] == 1
    assert ranks["ThinData"] is None


def test_missing_recent_kscore_key_entirely_treated_same_as_none():
    rotation = {
        "Technology": {"recent_kscore": 75.0},
        "NoKey": {"momentum": 0},  # no recent_kscore key at all
    }
    ranks = {r.sector: r.rank for r in rank_sectors(rotation)}
    assert ranks["NoKey"] is None


def test_empty_rotation_returns_empty_list():
    assert rank_sectors({}) == []


# ── classify_trajectory() ──────────────────────────────────────────────────────

def test_top_half_rank_improved_is_emerging_leader():
    # rank 1 of 4 (top half), improved from rank 3
    assert classify_trajectory(current_rank=1, prior_rank=3, total_sectors=4) == "Emerging Leader"


def test_top_half_rank_unchanged_is_established_leader():
    assert classify_trajectory(current_rank=1, prior_rank=1, total_sectors=4) == "Established Leader"


def test_top_half_rank_worsened_but_still_top_half_is_fading_leader():
    # still top half (rank 3 of 8, half=4.5) but worsened by 2+ from rank 1 -> "down"
    assert classify_trajectory(current_rank=3, prior_rank=1, total_sectors=8) == "Fading Leader"


def test_bottom_half_rank_improved_is_emerging_laggard():
    assert classify_trajectory(current_rank=6, prior_rank=8, total_sectors=8) == "Emerging Laggard"


def test_bottom_half_rank_unchanged_is_established_laggard():
    assert classify_trajectory(current_rank=8, prior_rank=8, total_sectors=8) == "Established Laggard"


def test_bottom_half_rank_worsened_is_fading_laggard():
    assert classify_trajectory(current_rank=8, prior_rank=5, total_sectors=8) == "Fading Laggard"


def test_missing_current_rank_returns_none():
    assert classify_trajectory(current_rank=None, prior_rank=3, total_sectors=8) is None


def test_missing_prior_rank_returns_none():
    """A sector newly entering the rankable set this snapshot has no real prior rank to
    compare against — must not fabricate a trajectory."""
    assert classify_trajectory(current_rank=1, prior_rank=None, total_sectors=8) is None


def test_zero_total_sectors_returns_none_not_a_divide_by_zero():
    assert classify_trajectory(current_rank=1, prior_rank=1, total_sectors=0) is None


def test_small_rank_wobble_within_flat_threshold_counts_as_flat():
    # a 1-place move (default flat_threshold=1) should NOT count as up/down
    assert classify_trajectory(current_rank=2, prior_rank=1, total_sectors=8) == "Established Leader"
    assert classify_trajectory(current_rank=1, prior_rank=2, total_sectors=8) == "Established Leader"


def test_two_place_move_exceeds_default_flat_threshold():
    assert classify_trajectory(current_rank=1, prior_rank=3, total_sectors=8) == "Emerging Leader"


def test_custom_flat_threshold_widens_the_flat_band():
    # with flat_threshold=3, a 2-place move should still read as flat
    assert classify_trajectory(current_rank=1, prior_rank=3, total_sectors=8, flat_threshold=3) == "Established Leader"


def test_odd_total_sectors_top_half_includes_the_middle_rank():
    # 5 sectors: half = 3.0, so rank 3 is top half (ties resolve toward top per the module's
    # own docstring)
    assert classify_trajectory(current_rank=3, prior_rank=3, total_sectors=5) == "Established Leader"
    assert classify_trajectory(current_rank=4, prior_rank=4, total_sectors=5) == "Established Laggard"


# ── build_trajectories() ────────────────────────────────────────────────────────

def test_build_trajectories_combines_current_and_prior_ranks():
    current = [SectorRank("Tech", 75.0, 1), SectorRank("Energy", 60.0, 2)]
    prior = [SectorRank("Tech", 55.0, 2), SectorRank("Energy", 65.0, 1)]
    result = build_trajectories(current, prior)
    assert result["Tech"]["rank"] == 1
    assert result["Tech"]["prior_rank"] == 2
    assert result["Tech"]["trajectory"] == "Established Leader"  # 1-place move, within default flat threshold
    assert result["Energy"]["rank"] == 2
    assert result["Energy"]["prior_rank"] == 1


def test_build_trajectories_handles_a_sector_missing_from_prior_snapshot():
    """A sector that didn't have enough ranked stocks 4 weeks ago (excluded from that
    snapshot's rank_sectors() output) must get trajectory=None, not crash or guess."""
    current = [SectorRank("NewSector", 60.0, 1)]
    prior: list[SectorRank] = []  # NewSector wasn't rankable then
    result = build_trajectories(current, prior)
    assert result["NewSector"]["prior_rank"] is None
    assert result["NewSector"]["trajectory"] is None


def test_build_trajectories_total_sectors_excludes_unrankable_ones():
    """total_sectors (used for the top/bottom-half cutoff) must count only sectors with a
    real rank this snapshot — an unrankable sector must not skew the half-cutoff for others."""
    current = [
        SectorRank("Tech", 75.0, 1),
        SectorRank("Energy", 60.0, 2),
        SectorRank("ThinData", None, None),
    ]
    prior = [SectorRank("Tech", 55.0, 2), SectorRank("Energy", 65.0, 1)]
    result = build_trajectories(current, prior)
    # total_sectors should be 2 (Tech, Energy), not 3 — half=1.5, so rank 1 is top, rank 2 is bottom
    assert result["Energy"]["trajectory"] == "Established Laggard"
    assert result["ThinData"]["trajectory"] is None


# ── AUD-T258-RANKNORM: rank normalized across different-sized fields ───────────

def test_dead_last_in_a_small_field_that_grows_reads_as_emerging_not_established():
    """The exact reported failure case: a sector at rank 4 of 4 (dead last, worst possible
    standing) a month ago, now at rank 4 of 8 (top half) — a raw-rank delta of 0 would
    incorrectly read 'flat'/Established; normalized by field size, this is a genuine
    improvement in standing (percentile 1.0 -> 0.43) and must read as Emerging Leader."""
    result = classify_trajectory(current_rank=4, prior_rank=4, total_sectors=8, prior_total_sectors=4)
    assert result == "Emerging Leader"


def test_top_of_a_small_field_that_shrinks_to_the_same_rank_reads_as_fading():
    """The mirror case: rank 2 of 2 (dead last in a tiny field) improving to rank 2 of 8 is
    NOT what this tests — instead: rank 1 of 4 (best) staying at rank 1 numerically while the
    field grows to 8 is still 'best', but rank 4 of 4 shrinking to rank 4 of 4 with the SAME
    field size must be unaffected (regression check that same-size fields still work)."""
    result = classify_trajectory(current_rank=4, prior_rank=4, total_sectors=4, prior_total_sectors=4)
    assert result == "Established Laggard"


def test_omitting_prior_total_sectors_defaults_to_current_total_unchanged_behavior():
    """Backward compatibility: a caller that only has one field size to compare (the pre-fix
    behavior) must get identical results to before — omitting prior_total_sectors defaults it
    to total_sectors, which is a same-size comparison, so percentile normalization is a no-op
    relative to the original raw-rank comparison."""
    assert classify_trajectory(current_rank=1, prior_rank=3, total_sectors=8) == "Emerging Leader"
    assert classify_trajectory(current_rank=8, prior_rank=5, total_sectors=8) == "Fading Laggard"


def test_zero_or_negative_prior_total_sectors_returns_none_not_a_crash():
    assert classify_trajectory(current_rank=1, prior_rank=1, total_sectors=8, prior_total_sectors=0) is None


def test_build_trajectories_passes_the_real_prior_field_size_not_current():
    """build_trajectories() must compute and pass the PRIOR snapshot's own rankable count,
    not silently reuse the current snapshot's count for both sides — that's exactly the bug
    this whole fix closes."""
    # Prior snapshot: 4 rankable sectors, this one at rank 4 (dead last).
    # Current snapshot: 8 rankable sectors, this one still at rank 4 (now top half).
    current = [SectorRank(f"S{i}", 100.0 - i, i + 1) for i in range(8)]
    prior = [SectorRank(f"P{i}", 100.0 - i, i + 1) for i in range(4)]
    # Rename the 4th current sector and 4th prior sector to the same name to compare directly.
    current[3] = SectorRank("Target", 90.0, 4)
    prior[3] = SectorRank("Target", 90.0, 4)
    result = build_trajectories(current, prior)
    assert result["Target"]["trajectory"] == "Emerging Leader"
