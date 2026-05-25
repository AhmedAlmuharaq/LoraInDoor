"""
K-NN Fingerprinting algorithm (legacy module — not imported by positioning_server.py).
The active implementation lives in the KNNFingerprint class in positioning_server.py.
"""
import math
import numpy as np

RADIO_MAP = None
_GRID_STEP = 0.2


def build_synthetic_map(emitters, width=3.0, height=3.0):
    """Generate a synthetic radio map from path-loss model.
    Use ONLY for preview / visualisation — never as a calibration substitute."""
    rmap = []
    for x in np.arange(0, width + _GRID_STEP, _GRID_STEP):
        x = min(x, width)
        for y in np.arange(0, height + _GRID_STEP, _GRID_STEP):
            y = min(y, height)
            rssi = []
            for e in emitters:
                d = np.hypot(x - e["x"], y - e["y"])
                r = -40 - 22.0 * np.log10(max(d, 0.1))
                rssi.append(float(r))
            rmap.append({"x": float(round(x, 2)), "y": float(round(y, 2)), "rssi": rssi})
    return rmap


def load_radio_map(data):
    global RADIO_MAP
    RADIO_MAP = data if data else None


def estimate_fp(rssi_values, emitters, width=None, height=None):
    """Weighted K-NN fingerprinting on the real radio map.
    Returns None if no calibration data exists — never falls back to synthetic."""
    global RADIO_MAP
    if RADIO_MAP is None or len(RADIO_MAP) == 0:
        return None   # honest: no calibration → no estimate

    if all(r is None for r in rssi_values):
        return None

    distances = []
    for point in RADIO_MAP:
        sig_dist_sq = 0.0
        valid = 0
        for i, r in enumerate(rssi_values):
            if r is not None and i < len(point["rssi"]):
                sig_dist_sq += (r - point["rssi"][i]) ** 2
                valid += 1
        if valid >= 2:
            distances.append({"point": point, "sig_dist": math.sqrt(sig_dist_sq)})

    if not distances:
        return None

    distances.sort(key=lambda d: d["sig_dist"])
    k       = min(3, len(distances))
    top_k   = distances[:k]
    eps     = 1e-6
    weights = [1.0 / (d["sig_dist"] + eps) for d in top_k]
    total_w = sum(weights)
    x = sum(w * d["point"]["x"] for w, d in zip(weights, top_k)) / total_w
    y = sum(w * d["point"]["y"] for w, d in zip(weights, top_k)) / total_w
    return {"x": round(x, 3), "y": round(y, 3)}
