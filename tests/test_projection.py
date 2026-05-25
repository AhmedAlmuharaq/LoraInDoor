"""
Unit tests for CampusProjection — flat-earth tangent-plane projection.

Checks:
  - Origin maps to (0, 0)
  - Known metric offsets (1 m north, 1 m east)
  - Round-trip GPS → local → GPS is lossless to floating-point precision
  - Plausible inter-campus distances (UFR STGI ↔ FEMTO-ST)
  - Parametric spot-checks against hand-computed values
"""
import math
import sys
from pathlib import Path

# Allow running from both project root and tests/ subdirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from positioning_server import CampusProjection

import pytest


@pytest.fixture
def proj():
    """Projection centred on UFR STGI — Montbéliard."""
    return CampusProjection(47.5108, 6.7965)


# ── 1. Origin ──────────────────────────────────────────────────────────
def test_origin_is_zero(proj):
    x, y = proj.to_local(47.5108, 6.7965)
    assert abs(x) < 1e-6, f"x should be 0 at reference, got {x}"
    assert abs(y) < 1e-6, f"y should be 0 at reference, got {y}"


# ── 2. 1 m north ───────────────────────────────────────────────────────
def test_one_metre_north(proj):
    """Moving 1/111111 degrees north should give y ≈ 1 m."""
    lat_plus_1m = 47.5108 + 1.0 / CampusProjection.M_PER_DEG_LAT
    _, y = proj.to_local(lat_plus_1m, 6.7965)
    assert abs(y - 1.0) < 0.01, f"Expected y≈1.0 m, got {y:.6f}"


# ── 3. 1 m east ────────────────────────────────────────────────────────
def test_one_metre_east(proj):
    """Moving 1/(111111*cos(lat)) degrees east should give x ≈ 1 m."""
    cos_lat = math.cos(math.radians(47.5108))
    lng_plus_1m = 6.7965 + 1.0 / (CampusProjection.M_PER_DEG_LAT * cos_lat)
    x, _ = proj.to_local(47.5108, lng_plus_1m)
    assert abs(x - 1.0) < 0.01, f"Expected x≈1.0 m, got {x:.6f}"


# ── 4. Round-trip GPS → local → GPS ────────────────────────────────────
@pytest.mark.parametrize("lat,lng", [
    (47.5108, 6.7965),   # origin itself
    (47.5118, 6.7975),   # ~100 m NE
    (47.4929, 6.8214),   # Fort du Mont-Bart (~17 km away)
    (47.6379, 6.8636),   # FEMTO-ST Belfort  (~18 km away)
])
def test_roundtrip(proj, lat, lng):
    x, y   = proj.to_local(lat, lng)
    lat2, lng2 = proj.to_gps(x, y)
    assert abs(lat2 - lat) < 1e-9, f"lat round-trip error: {abs(lat2-lat)}"
    assert abs(lng2 - lng) < 1e-9, f"lng round-trip error: {abs(lng2-lng)}"


# ── 5. 100 m east spot check ────────────────────────────────────────────
def test_100m_east(proj):
    lat, lng = proj.to_gps(100.0, 0.0)
    assert abs(lat - 47.5108) < 1e-6, "latitude should not change for pure east move"
    assert lng > 6.7965,              "longitude must increase going east"
    # inverse check
    x, y = proj.to_local(lat, lng)
    assert abs(x - 100.0) < 0.01
    assert abs(y)          < 0.01


# ── 6. Inter-campus distance sanity check ──────────────────────────────
def test_femto_st_distance(proj):
    """UFR STGI (47.5108,6.7965) to FEMTO-ST (47.6379,6.8636) ≈ 16–20 km."""
    x, y  = proj.to_local(47.6379, 6.8636)
    dist_m = math.hypot(x, y)
    assert 14_000 < dist_m < 22_000, (
        f"UFR STGI ↔ FEMTO-ST distance = {dist_m/1000:.1f} km, expected 14–22 km"
    )


# ── 7. Parametric spot-checks (hand-computed) ──────────────────────────
@pytest.mark.parametrize("lat,lng,exp_x,exp_y,tol_m", [
    # origin
    (47.5108, 6.7965,   0.0,   0.0,  0.01),
    # 10 m north only
    (47.5108 + 10/111111, 6.7965,  0.0,  10.0,  0.1),
    # 10 m east only — x = 10/cos(47.5108°)×cos(47.5108°) = 10 m
    (47.5108, 6.7965 + 10/(111111*math.cos(math.radians(47.5108))),
     10.0, 0.0, 0.1),
])
def test_spot_checks(proj, lat, lng, exp_x, exp_y, tol_m):
    x, y = proj.to_local(lat, lng)
    assert abs(x - exp_x) <= tol_m, f"x={x:.4f}, expected {exp_x} ±{tol_m}"
    assert abs(y - exp_y) <= tol_m, f"y={y:.4f}, expected {exp_y} ±{tol_m}"


# ── 8. Different reference point behaves independently ─────────────────
def test_different_reference():
    p1 = CampusProjection(47.5108, 6.7965)
    p2 = CampusProjection(47.4929, 6.8214)
    # The same GPS point should give DIFFERENT local coords in each projection
    gps = (47.5108, 6.8000)
    x1, y1 = p1.to_local(*gps)
    x2, y2 = p2.to_local(*gps)
    assert abs(x1 - x2) > 1.0 or abs(y1 - y2) > 1.0, \
        "Two projections with different origins must give different local coords"
