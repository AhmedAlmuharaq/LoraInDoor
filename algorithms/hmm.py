"""
HMM-Viterbi tracking algorithm (legacy module — not imported by positioning_server.py).
The active implementation lives in the HMMViterbi class in positioning_server.py.

Ref: Hoang et al. 2014 — physics-based transition matrix for indoor tracking.
"""
import math
import numpy as np
from algorithms import fp

N_STATES         = 0
prob_vector      = None
state_points     = []
state_rssi       = []
last_confirmed   = None
TRANSITION_MATRIX = None

_SIGMA_MOTION = 1.5   # metres — Gaussian kernel half-width
_SELF_BOOST   = 2.0   # additive boost on diagonal before row-normalisation
_OBS_SIGMA_SQ = 9.0   # emission variance (dB²)


def _build_transition_matrix(pts, sigma=_SIGMA_MOTION, self_boost=_SELF_BOOST):
    """Physics-based transition: A[i,j] ∝ exp(-||p_i - p_j|| / σ).
    Self-loop bias added before row-normalisation (Hoang et al. 2014)."""
    n = len(pts)
    A = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
            A[i, j] = math.exp(-d / sigma)
        A[i, i] += self_boost
        A[i] /= A[i].sum()
    return A


def reset():
    global prob_vector, N_STATES, state_points, state_rssi
    global last_confirmed, TRANSITION_MATRIX
    N_STATES         = 0
    prob_vector      = None
    state_points     = []
    state_rssi       = []
    last_confirmed   = None
    TRANSITION_MATRIX = None

    rmap = fp.RADIO_MAP
    if not rmap:
        return

    state_points = [(p["x"], p["y"]) for p in rmap]
    state_rssi   = [p["rssi"] for p in rmap]
    N_STATES     = len(state_points)
    prob_vector  = np.ones(N_STATES) / N_STATES
    TRANSITION_MATRIX = _build_transition_matrix(state_points)


def _default_pos(width=3.0, height=3.0):
    return {"x": round(width / 2, 2), "y": round(height / 2, 2)}


def estimate_hmm(rssi_values, emitters, width=None, height=None):
    global prob_vector, N_STATES, state_points, state_rssi
    global last_confirmed, TRANSITION_MATRIX
    w  = width  if width  and width  > 0 else 3.0
    h  = height if height and height > 0 else 3.0
    dp = _default_pos(w, h)

    if N_STATES == 0 or TRANSITION_MATRIX is None:
        reset()
    if N_STATES == 0 or prob_vector is None:
        return {"current": dp, "previous": dp, "prediction": dp}

    if last_confirmed is None or last_confirmed >= N_STATES:
        last_confirmed = int(np.argmax(prob_vector))

    prev_pos = {"x": state_points[last_confirmed][0],
                "y": state_points[last_confirmed][1]}

    if all(r is None for r in rssi_values):
        return {"current": prev_pos, "previous": prev_pos, "prediction": prev_pos}

    # Prediction step: proper Markov transition (physics-based geometry)
    prob_vector = TRANSITION_MATRIX.T @ prob_vector
    prob_vector /= prob_vector.sum()
    pred_idx = int(np.argmax(prob_vector))
    pred_pos = {"x": state_points[pred_idx][0], "y": state_points[pred_idx][1]}

    # Update step: Gaussian emission likelihood
    for i in range(N_STATES):
        lhood = 1.0
        for k, r in enumerate(rssi_values):
            if r is not None and k < len(state_rssi[i]):
                diff = r - state_rssi[i][k]
                lhood *= math.exp(-(diff ** 2) / (2 * _OBS_SIGMA_SQ))
        prob_vector[i] *= lhood

    total = prob_vector.sum()
    if total > 0:
        prob_vector /= total
    else:
        prob_vector = np.ones(N_STATES) / N_STATES

    last_confirmed = int(np.argmax(prob_vector))
    curr_pos = {"x": state_points[last_confirmed][0],
                "y": state_points[last_confirmed][1]}

    return {"current": curr_pos, "previous": prev_pos, "prediction": pred_pos}
