"""
N-Lateration algorithm (legacy module — not imported by positioning_server.py).
The active implementation lives in the NLateration class in positioning_server.py.
"""
import math
import numpy as np

_prev_est: dict | None = None
_raw_buf:  list        = []
ALPHA      = 0.15
RAW_HISTORY = 20


def _compute_intersections(rssi_values, emitters):
    """Geometric intersections of all C(N,2) range-circle pairs."""
    from itertools import combinations
    dists = {}
    for i, r in enumerate(rssi_values):
        if r is not None and i < len(emitters):
            dists[i] = max(10 ** ((-40 - r) / 22.0), 0.05)

    pts = []
    for i, j in combinations(dists.keys(), 2):
        e1, e2 = emitters[i], emitters[j]
        r1, r2 = dists[i], dists[j]
        dx, dy = e2["x"] - e1["x"], e2["y"] - e1["y"]
        d = math.hypot(dx, dy)
        if d < 1e-9 or d > r1+r2+1e-6 or d < abs(r1-r2)-1e-6:
            continue
        a  = (r1**2 - r2**2 + d**2) / (2*d)
        h2 = r1**2 - a**2
        if h2 < 0:
            continue
        h  = math.sqrt(h2)
        mx = e1["x"] + a*dx/d;  my = e1["y"] + a*dy/d
        pts.append({"x": round(mx + h*dy/d, 3), "y": round(my - h*dx/d, 3),
                    "pair": f"E{i}_E{j}"})
        pts.append({"x": round(mx - h*dy/d, 3), "y": round(my + h*dx/d, 3),
                    "pair": f"E{i}_E{j}"})
    return pts


def estimate_nlat(rssi_values, emitters, width=None, height=None):
    """Returns dict with raw, smoothed, intersections, uncertainty_m."""
    global _prev_est, _raw_buf
    w = width  if width  and width  > 0 else 3.0
    h = height if height and height > 0 else 3.0

    active = [(i, r) for i, r in enumerate(rssi_values)
              if r is not None and i < len(emitters)]
    if len(active) < 2:
        return {"raw": _prev_est, "smoothed": _prev_est,
                "intersections": [], "uncertainty_m": 0.0}

    # Grid search (original approach — scipy least_squares in inline class)
    _GRID_STEP = 0.05
    x_grid = np.arange(0, w + _GRID_STEP, _GRID_STEP)
    y_grid = np.arange(0, h + _GRID_STEP, _GRID_STEP)
    xx, yy = np.meshgrid(x_grid, y_grid)
    grid   = np.c_[xx.ravel(), yy.ravel()]

    errors = np.zeros(grid.shape[0])
    for i, r in active:
        d   = max(10 ** ((-40 - r) / 22.0), 0.05)
        wt  = max(0.1, 1.0 / (d + 0.5))
        p   = np.array([emitters[i]["x"], emitters[i]["y"]])
        errors += wt * (np.linalg.norm(grid - p, axis=1) - d) ** 2
    errors /= sum(max(0.1, 1.0/(max(10**((- 40 - r)/22.0),0.05)+0.5)) for _, r in active)

    best  = grid[np.argmin(errors)]
    raw_x = float(np.clip(best[0], 0, w))
    raw_y = float(np.clip(best[1], 0, h))
    raw   = {"x": round(raw_x, 3), "y": round(raw_y, 3)}

    # EMA smoothing
    if _prev_est is None:
        _prev_est = raw.copy()
    else:
        _prev_est = {
            "x": round(ALPHA*raw_x + (1-ALPHA)*_prev_est["x"], 3),
            "y": round(ALPHA*raw_y + (1-ALPHA)*_prev_est["y"], 3),
        }

    # Rolling buffer for uncertainty
    _raw_buf.append([raw_x, raw_y])
    if len(_raw_buf) > RAW_HISTORY:
        _raw_buf.pop(0)
    if len(_raw_buf) >= 2:
        xs  = [p[0] for p in _raw_buf]; ys = [p[1] for p in _raw_buf]
        mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
        sx = (sum((x-mx)**2 for x in xs)/len(xs))**.5
        sy = (sum((y-my)**2 for y in ys)/len(ys))**.5
        uncert = round(math.hypot(sx, sy), 3)
    else:
        uncert = 0.0

    return {
        "raw":           raw,
        "smoothed":      _prev_est.copy(),
        "intersections": _compute_intersections(rssi_values, emitters),
        "uncertainty_m": uncert,
    }
