"""R08: versioned same-session chain intensity, independent of live gamma."""
import ast
from pathlib import Path
import pytest

source = Path(__file__).resolve().parents[1] / 'src/api/routes.py'
node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'compute_options_pressure_score')
ns = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), ns)
score = ns['compute_options_pressure_score']


def measure(c, p, whales=None, gex=None, ratio=1):
    return score(ratio, 'neutral', whales, c, p, gex)


@pytest.mark.parametrize('c,p', [(2,1), (5,1), (100,0), (13,10), (0,100)])
def test_swapping_sides_preserves_imbalance(c, p):
    assert measure(c,p)['components']['cp_ratio_pts'] == measure(p,c)['components']['cp_ratio_pts']


def test_known_ratio_witness_is_symmetric_and_hand_calculated():
    assert measure(2000,1000)['components']['cp_ratio_pts'] == 13.3
    assert measure(1000,2000)['components']['cp_ratio_pts'] == 13.3
    assert measure(1000,1000)['components']['cp_ratio_pts'] == 0
    assert measure(1000,0)['components']['cp_ratio_pts'] == 40


@pytest.mark.parametrize('bad', [None, -1, float('nan'), float('inf')])
def test_invalid_or_missing_volume_is_not_scored(bad):
    assert measure(bad,100) is None
    assert measure(100,bad) is None


def test_no_activity_is_unmeasured():
    assert measure(0,0) is None
    assert measure(100,100,ratio=None) is None


@pytest.mark.parametrize('whales,points,maximum', [(None,0,50),(0,0,80),(1,10,80),(3,30,80),(5,30,80)])
def test_whale_coverage_stays_explicit(whales,points,maximum):
    p = measure(1000,1000,whales)
    assert p['components']['whale_pts'] == points
    assert p['components']['max_possible'] == maximum
    assert ('whale' in p['components']['measured']) is (whales is not None)


def test_volume_is_combined_and_capped():
    assert measure(1000,1000)['components']['volume_pts'] == 4
    assert measure(10_000,10_000)['components']['volume_pts'] == 10


@pytest.mark.parametrize('gex', [None, {}, {'distance_to_flip_pct':0}, {'distance_to_flip_pct':20}])
def test_live_gamma_cannot_change_settled_intensity(gex):
    assert measure(2000,1000,gex=gex) == measure(2000,1000)
    assert 'gex' not in measure(2000,1000)['components']['measured']


def test_exact_volumes_override_legacy_rounded_ratio():
    assert measure(1296,1000,ratio=1.3) == measure(1296,1000,ratio=1.296)
    assert measure(10000,0,3)['score'] == 80
    assert measure(10000,0,3)['method_version'] == 'chain_intensity_v2'
