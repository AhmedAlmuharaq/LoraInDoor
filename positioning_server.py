"""
InDoorLora Positioning Server
ZMQ SUB + WebSocket + SQLite + N-Lateration + K-NN FP + HMM Viterbi
Schema v2: campus-wide GPS mode (migration-safe, backward-compatible)

Usage:
  python positioning_server.py          # real hardware mode (GNU Radio → ZMQ)
  python positioning_server.py --sim    # simulation mode (starts lora_rx_sim.py)
"""

import argparse
import asyncio
import json
import math
import sqlite3
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.optimize import least_squares
import websockets
import zmq
import zmq.asyncio

# ── Config ────────────────────────────────────────────────────────────
ZMQ_ADDR      = "tcp://127.0.0.1:5556"
WS_PORT       = 8765
_SIM_MODE     = False   # set by --sim flag in main()
DB_PATH       = Path("indoorlora.db")
ANCHOR_NAMES  = ("E0", "E1", "E2", "E3")
CALIB_TOTAL   = 60     # packets to collect per grid point
BUFFER_WINDOW = 2.0    # seconds — RSSI validity window

DEFAULT_LOC = {
    "name":      "M2 Classroom",
    "width":     8.0,
    "height":    6.0,
    "grid_step": 0.5,
    "anchors": {
        "E0": [0.5, 0.5], "E1": [7.5, 0.5],
        "E2": [0.5, 5.5], "E3": [7.5, 5.5],
    },
}


# ── Database ──────────────────────────────────────────────────────────
def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS locations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            width      REAL    NOT NULL,
            height     REAL    NOT NULL,
            grid_step  REAL    DEFAULT 0.5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS anchors (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id  INTEGER NOT NULL,
            anchor_name  TEXT    NOT NULL,
            x            REAL    NOT NULL,
            y            REAL    NOT NULL,
            FOREIGN KEY (location_id) REFERENCES locations(id)
        );
        CREATE TABLE IF NOT EXISTS grid_points (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id  INTEGER NOT NULL,
            point_label  TEXT    NOT NULL,
            x            REAL    NOT NULL,
            y            REAL    NOT NULL,
            FOREIGN KEY (location_id) REFERENCES locations(id)
        );
        CREATE TABLE IF NOT EXISTS calibration_data (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id    INTEGER NOT NULL,
            grid_point_id  INTEGER NOT NULL,
            anchor_name    TEXT    NOT NULL,
            rssi           REAL    NOT NULL,
            snr            REAL,
            timestamp      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (location_id)   REFERENCES locations(id),
            FOREIGN KEY (grid_point_id) REFERENCES grid_points(id)
        );
        CREATE TABLE IF NOT EXISTS rssi_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id  INTEGER NOT NULL,
            anchor_name  TEXT    NOT NULL,
            rssi         REAL    NOT NULL,
            snr          REAL,
            estimated_x  REAL,
            estimated_y  REAL,
            algorithm    TEXT,
            timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (location_id) REFERENCES locations(id)
        );
    """)
    conn.commit()
    return conn


# ── Schema migration v2 (campus mode) ────────────────────────────────
# Idempotent: uses schema_version table so it runs exactly once,
# even if the server restarts mid-migration (each ALTER is try/except).
VALID_LOCATION_TYPES = {"indoor_room", "campus_outdoor", "hybrid"}

def migrate_v2_campus(conn):
    """Add campus-wide GPS columns. Safe to call on every startup."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    if conn.execute("SELECT version FROM schema_version WHERE version=2").fetchone():
        return  # already applied

    print("  [db]  Applying migration v2 (campus mode)...")
    # Each ALTER is wrapped individually — if the server crashed mid-migration
    # previously, the columns that were already added are silently skipped.
    _alters = [
        # locations — location type + campus projection fields
        "ALTER TABLE locations ADD COLUMN location_type   TEXT    NOT NULL DEFAULT 'indoor_room'",
        "ALTER TABLE locations ADD COLUMN campus_lat_ref  REAL",
        "ALTER TABLE locations ADD COLUMN campus_lng_ref  REAL",
        "ALTER TABLE locations ADD COLUMN campus_zoom     INTEGER DEFAULT 17",
        "ALTER TABLE locations ADD COLUMN campus_bbox_n   REAL",
        "ALTER TABLE locations ADD COLUMN campus_bbox_e   REAL",
        "ALTER TABLE locations ADD COLUMN campus_bbox_s   REAL",
        "ALTER TABLE locations ADD COLUMN campus_bbox_w   REAL",
        # anchors — independent GPS per anchor (NULL for indoor_room)
        "ALTER TABLE anchors ADD COLUMN lat      REAL",
        "ALTER TABLE anchors ADD COLUMN lng      REAL",
        "ALTER TABLE anchors ADD COLUMN building TEXT",
        "ALTER TABLE anchors ADD COLUMN floor    INTEGER",
        # grid_points — GPS for campus calibration points
        "ALTER TABLE grid_points ADD COLUMN lat        REAL",
        "ALTER TABLE grid_points ADD COLUMN lng        REAL",
        "ALTER TABLE grid_points ADD COLUMN point_type TEXT DEFAULT 'grid'",
    ]
    for sql in _alters:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists — silently skip

    # campus_presets: GPS shortcuts for known sites
    conn.execute("""
        CREATE TABLE IF NOT EXISTS campus_presets (
            id           INTEGER PRIMARY KEY,
            name         TEXT    NOT NULL UNIQUE,
            lat          REAL    NOT NULL,
            lng          REAL    NOT NULL,
            default_zoom INTEGER DEFAULT 17,
            notes        TEXT
        )
    """)
    conn.executemany(
        "INSERT OR IGNORE INTO campus_presets(name, lat, lng, default_zoom, notes)"
        " VALUES (?,?,?,?,?)",
        [
            ("UFR STGI — Montbéliard", 47.5108, 6.7965, 17, "Main campus, default"),
            ("Fort du Mont-Bart",       47.4929, 6.8214, 18, "Test site, elevated"),
            ("FEMTO-ST Belfort",        47.6379, 6.8636, 17, "Research lab, supervisor"),
        ],
    )

    # Advisory indexes (partial WHERE omitted for SQLite < 3.8 safety)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_anchors_gps ON anchors(lat, lng)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gp_gps      ON grid_points(lat, lng)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_locs_type   ON locations(location_type)")

    conn.execute("INSERT INTO schema_version(version) VALUES (2)")
    conn.commit()
    print("  [db]  Migration v2 applied")


# ── Campus projection ─────────────────────────────────────────────────
class CampusProjection:
    """Flat-earth tangent-plane projection centred at (lat_ref, lng_ref).

    Converts between WGS-84 GPS and local metric (x, y) in metres.
    x = east, y = north.  Accurate to ±1 cm within 50 km of reference.

    Reference: equirectangular / plate-carrée projection,
    Bowring (1983) Survey Review.
    """
    M_PER_DEG_LAT: float = 111_111.0   # metres per degree of latitude

    def __init__(self, lat_ref: float, lng_ref: float):
        self.lat_ref  = lat_ref
        self.lng_ref  = lng_ref
        self._cos_lat = math.cos(math.radians(lat_ref))

    def to_local(self, lat: float, lng: float) -> tuple[float, float]:
        """GPS (lat, lng) → local (x_m east, y_m north)."""
        x = (lng - self.lng_ref) * self.M_PER_DEG_LAT * self._cos_lat
        y = (lat - self.lat_ref) * self.M_PER_DEG_LAT
        return x, y

    def to_gps(self, x_m: float, y_m: float) -> tuple[float, float]:
        """Local (x_m east, y_m north) → GPS (lat, lng)."""
        lat = self.lat_ref + y_m / self.M_PER_DEG_LAT
        lng = self.lng_ref + x_m / (self.M_PER_DEG_LAT * self._cos_lat)
        return lat, lng


def _generate_grid_points(conn, location_id: int,
                           width: float, height: float, step: float):
    n_x = round(width  / step) + 1
    n_y = round(height / step) + 1
    pts, idx = [], 1
    for ix in range(n_x):
        x = round(min(ix * step, width), 6)
        for iy in range(n_y):
            y = round(min(iy * step, height), 6)
            pts.append((location_id, f"P{idx:03d}", x, y))
            idx += 1
    conn.executemany(
        "INSERT INTO grid_points (location_id, point_label, x, y) VALUES (?,?,?,?)", pts
    )


def db_create_location(conn, name, width, height, grid_step, anchors: dict,
                        location_type: str = "indoor_room",
                        campus_lat_ref: Optional[float] = None,
                        campus_lng_ref: Optional[float] = None,
                        campus_zoom: int = 17,
                        generate_grid: bool = True) -> dict:
    # Enforce allowed values — CHECK constraint not possible via ALTER TABLE
    if location_type not in VALID_LOCATION_TYPES:
        location_type = "indoor_room"
    conn.execute(
        "INSERT INTO locations "
        "(name, width, height, grid_step, location_type, "
        "campus_lat_ref, campus_lng_ref, campus_zoom) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (name, width, height, grid_step, location_type,
         campus_lat_ref, campus_lng_ref, campus_zoom),
    )
    conn.commit()
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for a_name, (x, y) in anchors.items():
        conn.execute(
            "INSERT INTO anchors (location_id, anchor_name, x, y) VALUES (?,?,?,?)",
            (loc_id, a_name, float(x), float(y)),
        )
    conn.commit()
    if generate_grid:
        _generate_grid_points(conn, loc_id, width, height, grid_step)
        conn.commit()
    return db_get_location_full(conn, loc_id)


def db_set_active(conn, location_id: int):
    conn.execute("UPDATE locations SET is_active=0")
    conn.commit()
    conn.execute("UPDATE locations SET is_active=1 WHERE id=?", (location_id,))
    conn.commit()


def db_get_calibrated_ids(conn, location_id: int) -> list:
    rows = conn.execute(
        "SELECT DISTINCT grid_point_id FROM calibration_data WHERE location_id=?",
        (location_id,),
    ).fetchall()
    return [r["grid_point_id"] for r in rows]


def db_get_location_full(conn, location_id: int) -> dict:
    loc = conn.execute(
        "SELECT id, name, width, height, grid_step, is_active, "
        "location_type, campus_lat_ref, campus_lng_ref, campus_zoom, "
        "campus_bbox_n, campus_bbox_e, campus_bbox_s, campus_bbox_w "
        "FROM locations WHERE id=?",
        (location_id,),
    ).fetchone()
    anchors = conn.execute(
        "SELECT anchor_name, x, y, lat, lng, building, floor "
        "FROM anchors WHERE location_id=? ORDER BY anchor_name",
        (location_id,),
    ).fetchall()
    grid_pts = conn.execute(
        "SELECT id, point_label, x, y, lat, lng, point_type "
        "FROM grid_points WHERE location_id=? ORDER BY id",
        (location_id,),
    ).fetchall()

    # Build CampusProjection if this is a campus-type location
    campus_proj = None
    lat_ref = loc["campus_lat_ref"]
    lng_ref = loc["campus_lng_ref"]
    if loc["location_type"] in ("campus_outdoor", "hybrid") and lat_ref and lng_ref:
        campus_proj = CampusProjection(lat_ref, lng_ref)

    return {
        "id":              loc["id"],
        "name":            loc["name"],
        "width":           loc["width"],
        "height":          loc["height"],
        "grid_step":       loc["grid_step"],
        "is_active":       loc["is_active"],
        "location_type":   loc["location_type"] or "indoor_room",
        "campus_lat_ref":  lat_ref,
        "campus_lng_ref":  lng_ref,
        "campus_zoom":     loc["campus_zoom"] or 17,
        "campus_bbox":     {
            "n": loc["campus_bbox_n"], "e": loc["campus_bbox_e"],
            "s": loc["campus_bbox_s"], "w": loc["campus_bbox_w"],
        } if any([loc["campus_bbox_n"], loc["campus_bbox_s"]]) else None,
        "anchors": [
            {
                "name":     r["anchor_name"],
                "x":        r["x"],        "y":        r["y"],
                "lat":      r["lat"],      "lng":      r["lng"],
                "building": r["building"], "floor":    r["floor"],
            }
            for r in anchors
        ],
        "grid_points": [
            {
                "id":         r["id"],
                "label":      r["point_label"],
                "x":          r["x"],   "y":   r["y"],
                "lat":        r["lat"], "lng": r["lng"],
                "point_type": r["point_type"] or "grid",
            }
            for r in grid_pts
        ],
        "calibrated_point_ids": db_get_calibrated_ids(conn, location_id),
    }


def db_get_all_locations(conn) -> list:
    rows = conn.execute(
        "SELECT id, name, width, height, grid_step, is_active, location_type "
        "FROM locations ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def db_get_campus_presets(conn) -> list:
    rows = conn.execute(
        "SELECT name, lat, lng, default_zoom, notes FROM campus_presets ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def db_get_active_location(conn) -> Optional[dict]:
    row = conn.execute(
        "SELECT id FROM locations WHERE is_active=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return db_get_location_full(conn, row["id"]) if row else None


def db_update_anchors(conn, location_id: int, anchors: dict):
    conn.execute("DELETE FROM anchors WHERE location_id=?", (location_id,))
    conn.commit()
    for a_name, (x, y) in anchors.items():
        conn.execute(
            "INSERT INTO anchors (location_id, anchor_name, x, y) VALUES (?,?,?,?)",
            (location_id, a_name, float(x), float(y)),
        )
    conn.commit()


def db_log_rssi(conn, location_id, anchor_name, rssi, snr, est_x, est_y, algorithm):
    conn.execute(
        "INSERT INTO rssi_log "
        "(location_id, anchor_name, rssi, snr, estimated_x, estimated_y, algorithm) "
        "VALUES (?,?,?,?,?,?,?)",
        (location_id, anchor_name, rssi, snr, est_x, est_y, algorithm),
    )
    conn.commit()


# ── Data directories ──────────────────────────────────────────────────
FP_MAP_DIR = Path("fingerprint_maps")
DATA_DIR   = Path("data")

import csv as _csv

def _safe_name(name: str, loc_id: int) -> str:
    """folder-safe name: <sanitised_name>_<id>"""
    safe = "".join(c if c.isalnum() or c in "- " else "_"
                   for c in name).strip().replace(" ", "_")
    return f"{safe}_{loc_id}"

def save_location_data(conn, loc_id: int):
    """
    Export complete location data to data/<name>_<id>/ :
      location_info.json   – room geometry + anchors
      radio_map.json       – fingerprint map (per-point mean RSSI)
      training_dataset.csv – ML-ready: x,y,label,E0,E1,E2,E3
    Called automatically after every calibration session.
    """
    DATA_DIR.mkdir(exist_ok=True)
    loc    = db_get_location_full(conn, loc_id)
    folder = DATA_DIR / _safe_name(loc["name"], loc_id)
    folder.mkdir(exist_ok=True)

    # ── 1. location_info.json ─────────────────────────────────────────
    info = {
        "id":            loc["id"],
        "name":          loc["name"],
        "location_type": loc.get("location_type", "indoor_room"),
        "width":         loc["width"],
        "height":        loc["height"],
        "grid_step":     loc["grid_step"],
        "campus_ref":    ({"lat": loc["campus_lat_ref"],
                           "lng": loc["campus_lng_ref"]}
                          if loc.get("campus_lat_ref") else None),
        "anchors":       [{"label": a["name"], "x": a["x"], "y": a["y"],
                           "lat": a.get("lat"), "lng": a.get("lng")}
                          for a in loc["anchors"]],
    }
    (folder / "location_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── 2. radio_map.json  +  training_dataset.csv ────────────────────
    rows = conn.execute("""
        SELECT gp.id, gp.point_label, gp.x, gp.y, gp.lat, gp.lng,
               gp.point_type, cd.anchor_name,
               AVG(cd.rssi) AS mean_rssi, COUNT(cd.rssi) AS cnt
        FROM grid_points gp
        JOIN calibration_data cd ON cd.grid_point_id = gp.id
        WHERE gp.location_id = ?
        GROUP BY gp.id, cd.anchor_name ORDER BY gp.id
    """, (loc_id,)).fetchall()

    pts: dict = {}
    for r in rows:
        if r["id"] not in pts:
            pts[r["id"]] = {
                "id": r["id"], "label": r["point_label"],
                "x": r["x"], "y": r["y"],
                "lat": r["lat"], "lng": r["lng"],
                "point_type": r["point_type"] or "grid",
                "rssi": {},
            }
        pts[r["id"]]["rssi"][r["anchor_name"]] = round(r["mean_rssi"], 2)

    anchor_labels = [a["name"] for a in loc["anchors"]]

    # radio_map.json
    radio_map = [
        {"id": p["id"], "label": p["label"],
         "x": p["x"], "y": p["y"],
         "lat": p["lat"], "lng": p["lng"],
         "point_type": p["point_type"],
         "rssi": {a: p["rssi"].get(a, None) for a in anchor_labels}}
        for p in pts.values()
    ]
    (folder / "radio_map.json").write_text(
        json.dumps(radio_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # training_dataset.csv  (one row per calibration point)
    csv_path = folder / "training_dataset.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        header = ["label", "x", "y", "lat", "lng", "point_type"] + anchor_labels
        w = _csv.writer(f)
        w.writerow(header)
        for p in pts.values():
            row = [p["label"], p["x"], p["y"],
                   p["lat"] or "", p["lng"] or "", p["point_type"]]
            row += [p["rssi"].get(a, "") for a in anchor_labels]
            w.writerow(row)

    n = len(pts)
    print(f"  [data] Saved {folder.name}/  ({n} pts, "
          f"radio_map.json + training_dataset.csv + location_info.json)")

def auto_save_fingerprint_map(conn, loc_id: int):
    """Persist calibration snapshot to fingerprint_maps/<name>_<id>.json."""
    FP_MAP_DIR.mkdir(exist_ok=True)
    loc = db_get_location_full(conn, loc_id)

    rows = conn.execute("""
        SELECT gp.id, gp.point_label, gp.x, gp.y, gp.lat, gp.lng,
               gp.point_type, cd.anchor_name,
               AVG(cd.rssi)                               AS mean_rssi,
               AVG(cd.rssi*cd.rssi)-AVG(cd.rssi)*AVG(cd.rssi) AS var_rssi,
               COUNT(cd.rssi)                             AS cnt
        FROM grid_points gp
        JOIN calibration_data cd ON cd.grid_point_id = gp.id
        WHERE gp.location_id = ?
        GROUP BY gp.id, cd.anchor_name ORDER BY gp.id
    """, (loc_id,)).fetchall()

    pts: dict = {}
    for r in rows:
        if r["id"] not in pts:
            pts[r["id"]] = {
                "label": r["point_label"], "x": r["x"], "y": r["y"],
                "lat": r["lat"], "lng": r["lng"],
                "point_type": r["point_type"] or "grid",
                "samples": {},
            }
        std = math.sqrt(max(r["var_rssi"] or 0.0, 0.0))
        pts[r["id"]]["samples"][r["anchor_name"]] = {
            "mean": round(r["mean_rssi"], 2),
            "std":  round(std, 2),
            "count": r["cnt"],
        }

    campus_ref = None
    if loc.get("campus_lat_ref") is not None:
        campus_ref = {"lat": loc["campus_lat_ref"], "lng": loc["campus_lng_ref"]}

    fp_data = {
        "schema_version": 1,
        "location_id":    loc["id"],
        "location_name":  loc["name"],
        "location_type":  loc.get("location_type", "indoor_room"),
        "campus_ref":     campus_ref,
        "anchors": [
            {"label": a["name"], "x": a["x"], "y": a["y"],
             "lat": a.get("lat"), "lng": a.get("lng")}
            for a in loc["anchors"]
        ],
        "calibration_points": list(pts.values()),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
    }

    safe = "".join(c if c.isalnum() or c in "- " else "_"
                   for c in loc["name"]).strip()
    fname = FP_MAP_DIR / f"{safe}_{loc_id}.json"
    fname.write_text(json.dumps(fp_data, ensure_ascii=False, indent=2),
                     encoding="utf-8")

    # Update index
    idx_path = FP_MAP_DIR / "index.json"
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        idx = []
    idx = [e for e in idx if e["location_id"] != loc_id]
    idx.append({
        "location_id":   loc_id,
        "location_name": loc["name"],
        "filename":      fname.name,
        "points":        len(pts),
        "updated_at":    fp_data["exported_at"],
    })
    idx.sort(key=lambda e: e["updated_at"], reverse=True)
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"  [fp]  Saved {fname.name}  ({len(pts)} pts)")


# ── Algorithm A: N-Lateration ─────────────────────────────────────────
class NLateration:
    A           = -42.0
    N           = 2.2
    MIN_RSSI    = -100.0
    MIN_ANCHORS = 2
    ALPHA       = 0.15        # EMA smoothing factor
    RAW_HISTORY = 20          # rolling window for uncertainty estimate

    def __init__(self):
        self.prev_est          = None
        self._raw_buf: list    = []   # last RAW_HISTORY raw solver outputs

    def _d(self, rssi: float, max_dist: float = 200.0) -> float:
        """Distance from RSSI, capped so weak signals don't dominate."""
        return min(max(10 ** ((self.A - rssi) / (10 * self.N)), 0.05), max_dist)

    BBOX_MARGIN   = 20.0
    _DBG_INTERVAL = 20

    # ── internal helpers ──────────────────────────────────────────────
    def _solve_ls(self, pts, dists, w, h) -> list:
        """Weighted least-squares: weight = 1/d (closer = more trusted)."""
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        m    = self.BBOX_MARGIN
        x_lo = max(0.0, min(p[0] for p in pts) - m)
        x_hi = min(w,   max(p[0] for p in pts) + m)
        y_lo = max(0.0, min(p[1] for p in pts) - m)
        y_hi = min(h,   max(p[1] for p in pts) + m)
        # Stronger signal = shorter distance = higher weight
        weights = [1.0 / max(d, 0.5) for d in dists]
        try:
            res = least_squares(
                lambda pos: [wi * (math.hypot(pos[0]-px, pos[1]-py) - d)
                             for (px,py), d, wi in zip(pts, dists, weights)],
                [cx, cy], method="lm", max_nfev=200,
            )
            x = float(np.clip(res.x[0], x_lo, x_hi))
            y = float(np.clip(res.x[1], y_lo, y_hi))
        except Exception:
            x, y = cx, cy
        return [round(x, 3), round(y, 3)]

    def _apply_ema(self, raw: list) -> list:
        """EMA smoothing — updates prev_est in-place."""
        if self.prev_est is None:
            self.prev_est = raw[:]
        else:
            a = self.ALPHA
            self.prev_est = [
                round(a * raw[0] + (1-a) * self.prev_est[0], 3),
                round(a * raw[1] + (1-a) * self.prev_est[1], 3),
            ]
        return self.prev_est[:]

    def _update_raw_buf(self, raw: list):
        self._raw_buf.append(raw[:])
        if len(self._raw_buf) > self.RAW_HISTORY:
            self._raw_buf.pop(0)

    def _uncertainty(self) -> float:
        """RMS spread of the last RAW_HISTORY raw solutions (metres)."""
        if len(self._raw_buf) < 2:
            return 0.0
        xs = [p[0] for p in self._raw_buf]
        ys = [p[1] for p in self._raw_buf]
        mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
        sx = (sum((x-mx)**2 for x in xs) / len(xs)) ** 0.5
        sy = (sum((y-my)**2 for y in ys) / len(ys)) ** 0.5
        return round(math.hypot(sx, sy), 3)

    def _compute_intersections(self, pts, dists) -> list:
        """Geometric intersections of C(N,2) range-circle pairs.
        Up to 2·C(N,2) points; pairs with no real intersection are skipped.
        Reference: standard two-circle intersection formula."""
        from itertools import combinations
        out = []
        for i, j in combinations(range(len(pts)), 2):
            (x1,y1), r1 = pts[i], dists[i]
            (x2,y2), r2 = pts[j], dists[j]
            d = math.hypot(x2-x1, y2-y1)
            if d < 1e-9 or d > r1+r2+1e-6 or d < abs(r1-r2)-1e-6:
                continue
            a   = (r1**2 - r2**2 + d**2) / (2*d)
            h   = math.sqrt(max(0.0, r1**2 - a**2))
            mx  = x1 + a*(x2-x1)/d
            my  = y1 + a*(y2-y1)/d
            dx_ = (x2-x1)/d;  dy_ = (y2-y1)/d
            lbl = f"E{i}_E{j}"
            out.append({"pair": lbl, "x": round(mx+h*dy_, 3), "y": round(my-h*dx_, 3)})
            out.append({"pair": lbl, "x": round(mx-h*dy_, 3), "y": round(my+h*dx_, 3)})
        return out

    # ── public interface ──────────────────────────────────────────────
    def estimate(self, rssi_dict, anchors, width, height):
        """Backward-compatible: returns smoothed [x, y] only."""
        det = self.estimate_with_details(rssi_dict, anchors, width, height)
        return det["smoothed"] if det else self.prev_est

    def estimate_with_details(self, rssi_dict, anchors, width, height):
        """Full output for dashboard transparency: raw, smoothed,
        circle intersections, and uncertainty radius."""
        # Max distance cap = room diagonal (avoids weak-signal dominance)
        max_d = math.hypot(width, height) * 1.5

        # Collect valid readings
        readings = []
        for a in anchors:
            r = rssi_dict.get(a["name"])
            if r is not None and r >= self.MIN_RSSI:
                readings.append((a["x"], a["y"], r))

        # Relative threshold: drop readings >35 dB below the strongest
        # (they are too noisy to be useful and destabilise the solver)
        if readings:
            max_rssi = max(r for _, _, r in readings)
            readings = [(x, y, r) for x, y, r in readings
                        if r >= max_rssi - 35.0]

        pts   = [(x, y) for x, y, _ in readings]
        dists = [self._d(r, max_d) for _, _, r in readings]
        if len(pts) < self.MIN_ANCHORS:
            return None
        raw = self._solve_ls(pts, dists, width, height)

        # Throttled debug: print every _DBG_INTERVAL estimates
        if not hasattr(self, "_dbg_n"):
            self._dbg_n = 0
        self._dbg_n = (self._dbg_n + 1) % self._DBG_INTERVAL
        if self._dbg_n == 1:
            m = self.BBOX_MARGIN
            x_lo = max(0.0, min(p[0] for p in pts) - m)
            x_hi = min(width,  max(p[0] for p in pts) + m)
            y_lo = max(0.0, min(p[1] for p in pts) - m)
            y_hi = min(height, max(p[1] for p in pts) + m)
            print(
                f"  [NLAT-D] anchors_xy="
                f"{[(round(p[0],1),round(p[1],1)) for p in pts]}\n"
                f"  [NLAT-D] dists_m={[round(d,1) for d in dists]}\n"
                f"  [NLAT-D] raw=({raw[0]}, {raw[1]})"
                f"  bounds=x[{x_lo:.0f},{x_hi:.0f}] y[{y_lo:.0f},{y_hi:.0f}]"
            )

        self._update_raw_buf(raw)
        return {
            "raw":           raw,
            "smoothed":      self._apply_ema(raw),
            "intersections": self._compute_intersections(pts, dists),
            "uncertainty_m": self._uncertainty(),
        }


# ── Algorithm B: K-NN Fingerprinting ─────────────────────────────────
class KNNFingerprint:
    K           = 3
    IDW_EPS     = 1e-6
    STAB_THRESH = 0.3

    def __init__(self):
        self._fps  = []
        self._prev = None

    def load_from_db(self, conn, location_id):
        rows = conn.execute("""
            SELECT gp.id, gp.x, gp.y, cd.anchor_name, AVG(cd.rssi) AS mean_rssi
            FROM calibration_data cd
            JOIN grid_points gp ON gp.id = cd.grid_point_id
            WHERE cd.location_id = ?
            GROUP BY gp.id, cd.anchor_name ORDER BY gp.id
        """, (location_id,)).fetchall()
        pts = {}
        for r in rows:
            if r["id"] not in pts:
                pts[r["id"]] = {"x": r["x"], "y": r["y"], "rssi": {}}
            pts[r["id"]]["rssi"][r["anchor_name"]] = r["mean_rssi"]
        self._fps = [
            {"x": p["x"], "y": p["y"],
             "vec": np.array([p["rssi"].get(a, -120.0) for a in ANCHOR_NAMES])}
            for p in pts.values()
        ]
        self._prev = None
        print(f"  [fp]  {len(self._fps)} fingerprint points loaded")

    def estimate(self, rssi_dict):
        if not self._fps:
            return None
        live  = np.array([rssi_dict.get(a, -120.0) for a in ANCHOR_NAMES])
        dists = sorted(
            (float(np.linalg.norm(live - fp["vec"])), fp["x"], fp["y"])
            for fp in self._fps
        )
        tw = wx = wy = 0.0
        for d, x, y in dists[:self.K]:
            w = 1.0 / (d + self.IDW_EPS);  wx += w*x;  wy += w*y;  tw += w
        est = [round(wx/tw, 3), round(wy/tw, 3)]
        if self._prev and math.hypot(est[0]-self._prev[0], est[1]-self._prev[1]) < self.STAB_THRESH:
            return self._prev
        self._prev = est
        return est


# ── Algorithm C: HMM Viterbi ──────────────────────────────────────────
class HMMViterbi:
    SIGMA_MOTION = 1.5
    DEFAULT_STD  = 3.0
    STD_FLOOR    = 0.5

    def __init__(self):
        self._states    = []
        self._means     = None
        self._stds      = None
        self._log_trans = None
        self._log_probs = None
        self._built     = False

    def load_from_db(self, conn, location_id):
        rows = conn.execute("""
            SELECT gp.id, gp.x, gp.y, cd.anchor_name,
                   AVG(cd.rssi) AS mean_rssi,
                   AVG(cd.rssi*cd.rssi) - AVG(cd.rssi)*AVG(cd.rssi) AS var_rssi,
                   COUNT(cd.rssi) AS cnt
            FROM calibration_data cd
            JOIN grid_points gp ON gp.id = cd.grid_point_id
            WHERE cd.location_id = ?
            GROUP BY gp.id, cd.anchor_name ORDER BY gp.id
        """, (location_id,)).fetchall()
        if not rows:
            self._built = False;  print("  [hmm] No calibration data");  return
        pts = {}
        for r in rows:
            if r["id"] not in pts:
                pts[r["id"]] = {"x": r["x"], "y": r["y"], "a": {}}
            var = r["var_rssi"] or 0.0
            std = max(math.sqrt(max(var, 0.0)) if r["cnt"] > 1 else self.DEFAULT_STD, self.STD_FLOOR)
            pts[r["id"]]["a"][r["anchor_name"]] = (r["mean_rssi"], std)
        self._states = [
            {"x": p["x"], "y": p["y"],
             "means": [p["a"].get(a, (-120.0, self.DEFAULT_STD))[0] for a in ANCHOR_NAMES],
             "stds":  [p["a"].get(a, (-120.0, self.DEFAULT_STD))[1] for a in ANCHOR_NAMES]}
            for p in pts.values()
        ]
        N = len(self._states)
        self._means = np.array([s["means"] for s in self._states])
        self._stds  = np.array([s["stds"]  for s in self._states])
        coords = np.array([(s["x"], s["y"]) for s in self._states])
        d_mat  = np.linalg.norm(coords[:, np.newaxis] - coords[np.newaxis, :], axis=2)
        trans  = np.exp(-d_mat / self.SIGMA_MOTION)
        np.fill_diagonal(trans, trans.diagonal() + 2.0)
        trans /= trans.sum(axis=1, keepdims=True)
        with np.errstate(divide="ignore"):
            self._log_trans = np.log(np.maximum(trans, 1e-300))
        self._log_probs = np.full(N, -math.log(N))
        self._built = True
        print(f"  [hmm] {N} states loaded")

    def estimate(self, rssi_dict):
        if not self._built:
            return None
        new_log = (self._log_probs[:, np.newaxis] + self._log_trans).max(axis=0)
        for k, a in enumerate(ANCHOR_NAMES):
            obs = rssi_dict.get(a)
            if obs is not None:
                new_log -= (obs - self._means[:, k])**2 / (2.0 * self._stds[:, k]**2)
        new_log -= new_log.max()
        self._log_probs = new_log
        s = self._states[int(np.argmax(self._log_probs))]
        return [round(s["x"], 3), round(s["y"], 3)]


# ── Algorithm engine ──────────────────────────────────────────────────
class AlgorithmEngine:
    def __init__(self):
        self._nlat  = NLateration()
        self._fp    = KNNFingerprint()
        self._hmm   = HMMViterbi()
        self._dirty = True

    def reload(self, conn, location_id):
        self._fp.load_from_db(conn, location_id)
        self._hmm.load_from_db(conn, location_id)
        self._dirty = False

    def mark_dirty(self):
        self._dirty = True

    def estimate(self, rssi_dict, anchors, width, height, conn, location_id):
        if self._dirty:
            self.reload(conn, location_id)
        det = self._nlat.estimate_with_details(rssi_dict, anchors, width, height)
        return {
            "nlat":          det["smoothed"]      if det else None,
            "nlat_raw":      det["raw"]            if det else None,
            "intersections": det["intersections"]  if det else [],
            "uncertainty_m": det["uncertainty_m"]  if det else 0.0,
            "fp":            self._fp.estimate(rssi_dict),
            "hmm":           self._hmm.estimate(rssi_dict),
        }


# ── RSSI packet buffer ────────────────────────────────────────────────
@dataclass
class _Reading:
    rssi: float
    snr:  Optional[float]
    ts:   float

class PacketBuffer:
    def __init__(self, window: float = BUFFER_WINDOW):
        self._buf: dict[str, _Reading] = {}
        self._win = window

    def add(self, name: str, rssi: float, snr=None):
        now = time.monotonic()
        self._buf = {k: v for k, v in self._buf.items() if now - v.ts < self._win}
        self._buf[name] = _Reading(rssi, snr, now)

    MIN_ACTIVE = 2   # minimum anchors needed for a position update

    def complete(self):
        now   = time.monotonic()
        valid = {k: v for k, v in self._buf.items()
                 if now - v.ts < self._win and k in ANCHOR_NAMES}
        if len(valid) >= self.MIN_ACTIVE:
            return (
                {a: valid[a].rssi for a in valid},
                {a: valid[a].snr  for a in valid},
            )
        return None, None


# ── PDU parser (matches lora_rx_sim.py build_pdu) ────────────────────
def parse_pdu(raw: bytes):
    off = 0
    meta_len  = struct.unpack(">I", raw[off:off+4])[0];  off += 4
    meta      = json.loads(raw[off:off+meta_len]);         off += meta_len
    pay_len   = struct.unpack(">I", raw[off:off+4])[0];  off += 4
    payload   = raw[off:off+pay_len].decode("utf-8", errors="replace")
    return meta, payload


# ── Server state ──────────────────────────────────────────────────────
class _CalibState:
    active:   bool           = False
    gp_id:    Optional[int]  = None
    gp_label: str            = ""
    count:    int            = 0

class State:
    clients:         set             = set()
    active_location: dict            = {}
    engine:          AlgorithmEngine = AlgorithmEngine()
    calib:           _CalibState     = _CalibState()
    last_broadcast:  float           = 0.0   # monotonic time of last position_update
    MIN_INTERVAL     = 0.200                  # max 5 broadcasts/second


# ── Broadcast helper ──────────────────────────────────────────────────
async def broadcast(msg: str):
    if State.clients:
        await asyncio.gather(*[c.send(msg) for c in State.clients],
                             return_exceptions=True)


def _anchors_update_msg(loc: dict) -> str:
    """Build anchors_update — includes lat/lng/building for campus anchors."""
    return json.dumps({
        "type":    "anchors_update",
        "anchors": [
            {"id":       a["name"],  "name":     a["name"],
             "x":        a["x"],     "y":        a["y"],
             "lat":      a.get("lat"),  "lng":   a.get("lng"),
             "building": a.get("building")}
            for a in loc.get("anchors", [])
        ],
    })


# ── Calibration helpers ───────────────────────────────────────────────
async def do_stop_calibration(conn):
    loc    = State.active_location
    loc_id = loc["id"]
    gp_id  = State.calib.gp_id
    label  = State.calib.gp_label

    # Compute per-anchor means for this grid point
    rows  = conn.execute(
        "SELECT anchor_name, AVG(rssi) AS m FROM calibration_data "
        "WHERE grid_point_id=? GROUP BY anchor_name", (gp_id,)
    ).fetchall()
    means = {r["anchor_name"]: round(r["m"], 2) for r in rows}

    State.calib.active = False
    State.calib.gp_id  = None
    State.calib.count  = 0

    State.engine.reload(conn, loc_id)
    calib_ids = db_get_calibrated_ids(conn, loc_id)

    await broadcast(json.dumps({
        "type":                 "calibration_complete",
        "point":                label,
        "grid_point_id":        gp_id,
        "means":                means,
        "calibrated_point_ids": calib_ids,
    }))
    print(f"  [cal] Done: {label}  ({State.calib.count} samples stored)")
    try:
        auto_save_fingerprint_map(conn, loc_id)
    except Exception as exc:
        print(f"  [fp]  Auto-save failed: {exc}")
    try:
        save_location_data(conn, loc_id)
    except Exception as exc:
        print(f"  [data] Save failed: {exc}")


# ── Core packet processor ─────────────────────────────────────────────
async def process_reading(rssi_dict: dict, snr_dict: dict, conn):
    loc = State.active_location
    if not loc:
        return

    loc_id  = loc["id"]
    anchors = loc["anchors"]
    w, h    = loc["width"], loc["height"]
    ts      = datetime.now().isoformat()

    # Calibration mode: save raw readings
    if State.calib.active:
        for a_name, rssi in rssi_dict.items():
            conn.execute(
                "INSERT INTO calibration_data "
                "(location_id, grid_point_id, anchor_name, rssi, snr) VALUES (?,?,?,?,?)",
                (loc_id, State.calib.gp_id, a_name, rssi, snr_dict.get(a_name)),
            )
            conn.commit()
        State.calib.count += 1
        await broadcast(json.dumps({
            "type":  "calibration_progress",
            "point": State.calib.gp_label,
            "count": State.calib.count,
            "total": CALIB_TOTAL,
        }))
        if State.calib.count >= CALIB_TOTAL:
            await do_stop_calibration(conn)

    # Run all three algorithms
    est = State.engine.estimate(rssi_dict, anchors, w, h, conn, loc_id)

    # Log only the three named algorithm estimates (not raw/meta keys)
    for algo in ("nlat", "fp", "hmm"):
        pos = est.get(algo)
        if not isinstance(pos, list):
            continue
        for a_name, rssi_val in rssi_dict.items():
            db_log_rssi(conn, loc_id, a_name, rssi_val, snr_dict.get(a_name),
                        pos[0], pos[1], algo)

    def xy(pos):
        return {"x": pos[0], "y": pos[1]} if (pos and isinstance(pos, list)) else None

    # ── Live CSV log (raw_rssi_log.csv per location) ──────────────────
    try:
        folder   = DATA_DIR / _safe_name(loc["name"], loc_id)
        folder.mkdir(parents=True, exist_ok=True)
        log_path = folder / "raw_rssi_log.csv"
        anchors_ordered = [a["name"] for a in anchors]
        write_header = not log_path.exists()
        with open(log_path, "a", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            if write_header:
                w.writerow(["timestamp"] + anchors_ordered +
                           ["nlat_x", "nlat_y", "fp_x", "fp_y",
                            "hmm_x", "hmm_y"])
            nlat = est.get("nlat") or []
            fp   = est.get("fp")   or []
            hmm  = est.get("hmm")  or []
            w.writerow(
                [ts] +
                [round(rssi_dict.get(a, float("nan")), 1)
                 for a in anchors_ordered] +
                [nlat[0] if nlat else "", nlat[1] if nlat else "",
                 fp[0]   if fp   else "", fp[1]   if fp   else "",
                 hmm[0]  if hmm  else "", hmm[1]  if hmm  else ""]
            )
    except Exception:
        pass   # never crash the server over CSV logging

    # Rate-limit: max 5 position_update broadcasts per second
    now_mono = time.monotonic()
    if now_mono - State.last_broadcast < State.MIN_INTERVAL:
        return
    State.last_broadcast = now_mono

    await broadcast(json.dumps({
        "type":          "position_update",
        "rssi":          rssi_dict,
        "nlat_raw":      xy(est.get("nlat_raw")),
        "nlat":          xy(est["nlat"]),
        "intersections": est.get("intersections", []),
        "uncertainty_m": est.get("uncertainty_m", 0.0),
        "fp":            xy(est["fp"]),
        "hmm":           xy(est["hmm"]),
        "timestamp":     ts,
    }))


# ── ZMQ subscriber task ───────────────────────────────────────────────
async def zmq_task(conn):
    ctx = zmq.asyncio.Context()
    sub = ctx.socket(zmq.SUB)
    # Server BINDS — publishers (GNU Radio or sim) CONNECT to us.
    # This prevents port conflicts when switching between real/sim mode.
    sub.bind(ZMQ_ADDR)
    sub.setsockopt_string(zmq.SUBSCRIBE, "")
    buf        = PacketBuffer()
    rssi_ema: dict[str, float] = {}   # per-anchor smoothed RSSI
    RSSI_ALPHA = 0.25   # EMA factor: lower = more smoothing (0.1=heavy, 0.5=light)

    src = "SIMULATION" if _SIM_MODE else "REAL HARDWARE (GNU Radio)"
    print(f"  [zmq] Listening on {ZMQ_ADDR}  <- {src}")
    while True:
        try:
            raw = await sub.recv()
            meta, payload = parse_pdu(raw)
            parts = payload.split(",")
            if len(parts) < 3:
                continue
            raw_id = parts[2].strip()
            # Real hardware sends numeric "0","1","2","3" → map to "E0"-"E3"
            a_name = f"E{raw_id}" if raw_id.isdigit() else raw_id
            if a_name not in ANCHOR_NAMES:
                continue
            raw_rssi = float(meta["rssi"])
            snr      = meta.get("snr")
            # EMA smoothing: reduces RSSI noise (USRP fluctuates ±10 dB)
            if a_name in rssi_ema:
                rssi_ema[a_name] = (RSSI_ALPHA * raw_rssi
                                    + (1.0 - RSSI_ALPHA) * rssi_ema[a_name])
            else:
                rssi_ema[a_name] = raw_rssi
            rssi = round(rssi_ema[a_name], 1)
            buf.add(a_name, rssi, snr)
            rssi_d, snr_d = buf.complete()
            if rssi_d:
                await process_reading(rssi_d, snr_d, conn)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"  [zmq] {exc}")
            await asyncio.sleep(0.05)


# ── WebSocket handler ─────────────────────────────────────────────────
async def ws_handler(ws, conn):
    State.clients.add(ws)
    print(f"  [ws]  Connected ({len(State.clients)} clients)")

    # Push full state bundle on connect
    loc = State.active_location
    await ws.send(json.dumps({
        "type":        "active_location",
        "location":    loc,
        "anchors":     loc.get("anchors", []),
        "grid_points": loc.get("grid_points", []),
    }))
    await ws.send(json.dumps({"type": "locations_list",
                              "locations": db_get_all_locations(conn)}))
    await ws.send(json.dumps({"type": "campus_presets",
                              "presets": db_get_campus_presets(conn)}))
    await ws.send(json.dumps({
        "type":       "server_mode",
        "sim":        _SIM_MODE,
        "zmq_addr":   ZMQ_ADDR,
    }))
    await ws.send(_anchors_update_msg(loc))   # for lora_rx_sim.py

    try:
        async for raw in ws:
            try:
                data   = json.loads(raw)
                action = data.get("action")

                if action == "get_locations":
                    await ws.send(json.dumps({
                        "type":      "locations_list",
                        "locations": db_get_all_locations(conn),
                    }))

                elif action == "create_location":
                    name     = str(data["name"]).strip()
                    loc_type = data.get("location_type", "indoor_room")

                    if loc_type == "campus_outdoor":
                        # ── Campus: project GPS → local (x,y) ──────────────
                        lat_ref  = float(data["campus_lat_ref"])
                        lng_ref  = float(data["campus_lng_ref"])
                        zoom     = int(data.get("campus_zoom", 17))
                        raw_ancs = data["anchors"]   # {name: {lat,lng,building}}

                        # Choose SW corner as projection origin so all x,y > 0
                        lats = [float(v["lat"]) for v in raw_ancs.values()]
                        lngs = [float(v["lng"]) for v in raw_ancs.values()]
                        MARGIN_M = 60.0
                        sw_lat = min(lats) - MARGIN_M / CampusProjection.M_PER_DEG_LAT
                        sw_lng = min(lngs) - MARGIN_M / (
                            CampusProjection.M_PER_DEG_LAT *
                            math.cos(math.radians(min(lats)))
                        )
                        proj = CampusProjection(sw_lat, sw_lng)

                        anc_xy = {}
                        for a_name, pos in raw_ancs.items():
                            x, y = proj.to_local(float(pos["lat"]), float(pos["lng"]))
                            anc_xy[a_name] = [round(x, 3), round(y, 3)]

                        xs = [v[0] for v in anc_xy.values()]
                        ys = [v[1] for v in anc_xy.values()]
                        width  = round(max(xs) + MARGIN_M, 1)
                        height = round(max(ys) + MARGIN_M, 1)

                        loc = db_create_location(
                            conn, name, width, height, 10.0, anc_xy,
                            location_type="campus_outdoor",
                            campus_lat_ref=sw_lat,
                            campus_lng_ref=sw_lng,
                            campus_zoom=zoom,
                            generate_grid=False,
                        )
                        loc_id = loc["id"]
                        # Persist lat/lng/building for each anchor
                        for a_name, pos in raw_ancs.items():
                            conn.execute(
                                "UPDATE anchors SET lat=?, lng=?, building=? "
                                "WHERE location_id=? AND anchor_name=?",
                                (float(pos["lat"]), float(pos["lng"]),
                                 pos.get("building"), loc_id, a_name),
                            )
                        conn.commit()
                        loc = db_get_location_full(conn, loc_id)
                        print(f"  [ws]  Campus '{name}': {len(anc_xy)} anchors, "
                              f"{width:.0f}x{height:.0f} m bounding box")

                    else:
                        # ── Indoor (existing flow — unchanged) ──────────────
                        width     = float(data["width"])
                        height    = float(data["height"])
                        grid_step = float(data.get("grid_step", 0.5))
                        anchors   = {k: list(v) for k, v in data["anchors"].items()}
                        loc = db_create_location(conn, name, width, height,
                                                 grid_step, anchors)

                    # ── Common: activate + broadcast ────────────────────────
                    db_set_active(conn, loc["id"])
                    loc = db_get_location_full(conn, loc["id"])
                    State.active_location = loc
                    State.engine.mark_dirty()
                    await broadcast(json.dumps({
                        "type":        "location_created",
                        "location":    loc,
                        "grid_count":  len(loc["grid_points"]),
                    }))
                    await broadcast(json.dumps({
                        "type":        "active_location",
                        "location":    loc,
                        "anchors":     loc["anchors"],
                        "grid_points": loc["grid_points"],
                    }))
                    await broadcast(json.dumps({
                        "type":      "locations_list",
                        "locations": db_get_all_locations(conn),
                    }))
                    await broadcast(_anchors_update_msg(loc))

                elif action == "set_active":
                    loc_id = int(data["location_id"])
                    db_set_active(conn, loc_id)
                    loc = db_get_location_full(conn, loc_id)
                    State.active_location  = loc
                    State.calib.active     = False
                    State.calib.gp_id      = None
                    State.calib.count      = 0
                    State.engine.reload(conn, loc_id)
                    await broadcast(json.dumps({
                        "type":        "active_location",
                        "location":    loc,
                        "anchors":     loc["anchors"],
                        "grid_points": loc["grid_points"],
                    }))
                    await broadcast(_anchors_update_msg(loc))

                elif action == "update_anchors":
                    loc_id   = int(data["location_id"])
                    loc_type = State.active_location.get("location_type", "indoor_room")
                    if loc_type == "campus_outdoor":
                        loc  = State.active_location
                        proj = CampusProjection(loc["campus_lat_ref"],
                                                loc["campus_lng_ref"])
                        conn.execute("DELETE FROM anchors WHERE location_id=?",
                                     (loc_id,))
                        conn.commit()
                        for a_name, pos in data["anchors"].items():
                            x_m, y_m = proj.to_local(float(pos["lat"]),
                                                       float(pos["lng"]))
                            conn.execute(
                                "INSERT INTO anchors"
                                "(location_id,anchor_name,x,y,lat,lng,building)"
                                " VALUES(?,?,?,?,?,?,?)",
                                (loc_id, a_name,
                                 round(x_m, 3), round(y_m, 3),
                                 float(pos["lat"]), float(pos["lng"]),
                                 pos.get("building")),
                            )
                        conn.commit()
                    else:
                        anchors = {k: list(v) for k, v in data["anchors"].items()}
                        db_update_anchors(conn, loc_id, anchors)
                    loc = db_get_location_full(conn, loc_id)
                    if loc_id == State.active_location.get("id"):
                        State.active_location = loc
                    await broadcast(_anchors_update_msg(loc))
                    await ws.send(json.dumps({"type": "anchors_saved"}))

                elif action == "update_room":
                    loc_id   = int(data["location_id"])
                    new_w    = float(data["width"])
                    new_h    = float(data["height"])
                    new_step = float(data["grid_step"])
                    # Update dimensions
                    conn.execute(
                        "UPDATE locations SET width=?, height=?, grid_step=?"
                        " WHERE id=?",
                        (new_w, new_h, new_step, loc_id)
                    )
                    conn.commit()
                    # Delete old calibration data + grid points
                    conn.execute("""
                        DELETE FROM calibration_data
                        WHERE grid_point_id IN
                              (SELECT id FROM grid_points WHERE location_id=?)
                    """, (loc_id,))
                    conn.execute(
                        "DELETE FROM grid_points WHERE location_id=?",
                        (loc_id,)
                    )
                    conn.commit()
                    # Regenerate grid
                    _generate_grid_points(conn, loc_id, new_w, new_h, new_step)
                    conn.commit()
                    # Reload engine + state
                    State.engine.reload(conn, loc_id)
                    loc = db_get_location_full(conn, loc_id)
                    if loc_id == State.active_location.get("id"):
                        State.active_location = loc
                    n_pts = len(loc["grid_points"])
                    await broadcast(json.dumps({
                        "type":       "room_updated",
                        "width":      new_w,
                        "height":     new_h,
                        "grid_step":  new_step,
                        "grid_count": n_pts,
                    }))
                    await broadcast(json.dumps({
                        "type":        "active_location",
                        "location":    loc,
                        "anchors":     loc["anchors"],
                        "grid_points": loc["grid_points"],
                    }))
                    print(f"  [ws]  Room updated: {new_w}x{new_h} m, "
                          f"step={new_step}, {n_pts} pts")

                elif action == "remove_anchor":
                    loc_id  = int(data["location_id"])
                    a_label = str(data["anchor_label"])
                    conn.execute(
                        "DELETE FROM anchors WHERE location_id=? AND anchor_name=?",
                        (loc_id, a_label)
                    )
                    conn.commit()
                    loc = db_get_location_full(conn, loc_id)
                    if loc_id == State.active_location.get("id"):
                        State.active_location = loc
                    await broadcast(_anchors_update_msg(loc))
                    await broadcast(json.dumps({
                        "type":    "anchor_removed",
                        "label":   a_label,
                        "anchors": loc["anchors"],
                    }))
                    print(f"  [ws]  Removed anchor {a_label}")

                elif action == "update_single_anchor":
                    loc_id  = int(data["location_id"])
                    a_label = str(data["anchor_label"])
                    loc     = State.active_location
                    if loc.get("location_type") == "campus_outdoor":
                        proj  = CampusProjection(loc["campus_lat_ref"],
                                                 loc["campus_lng_ref"])
                        x_m, y_m = proj.to_local(float(data["lat"]),
                                                   float(data["lng"]))
                        conn.execute(
                            "UPDATE anchors SET x=?,y=?,lat=?,lng=?,building=?"
                            " WHERE location_id=? AND anchor_name=?",
                            (round(x_m,3), round(y_m,3),
                             float(data["lat"]), float(data["lng"]),
                             data.get("building"), loc_id, a_label),
                        )
                    else:
                        conn.execute(
                            "UPDATE anchors SET x=?,y=?"
                            " WHERE location_id=? AND anchor_name=?",
                            (float(data["x"]), float(data["y"]),
                             loc_id, a_label),
                        )
                    conn.commit()
                    loc = db_get_location_full(conn, loc_id)
                    if loc_id == State.active_location.get("id"):
                        State.active_location = loc
                    await broadcast(_anchors_update_msg(loc))
                    await ws.send(json.dumps({
                        "type": "anchor_updated", "label": a_label,
                    }))

                elif action == "start_calibration":
                    gp_id = int(data["grid_point_id"])
                    loc   = State.active_location
                    gp    = next((p for p in loc["grid_points"] if p["id"] == gp_id), None)
                    # Delete previous calibration for this point
                    conn.execute(
                        "DELETE FROM calibration_data WHERE grid_point_id=?", (gp_id,)
                    )
                    conn.commit()
                    State.calib.active   = True
                    State.calib.gp_id    = gp_id
                    State.calib.gp_label = gp["label"] if gp else "?"
                    State.calib.count    = 0
                    await ws.send(json.dumps({
                        "type":          "calibration_started",
                        "grid_point_id": gp_id,
                        "point":         State.calib.gp_label,
                    }))

                elif action == "stop_calibration":
                    if State.calib.active:
                        await do_stop_calibration(conn)

                elif action == "remove_calibration_point":
                    gp_id  = int(data["grid_point_id"])
                    loc_id = State.active_location["id"]
                    conn.execute(
                        "DELETE FROM calibration_data WHERE grid_point_id=?",
                        (gp_id,))
                    conn.execute("DELETE FROM grid_points WHERE id=?", (gp_id,))
                    conn.commit()
                    State.engine.reload(conn, loc_id)
                    State.active_location = db_get_location_full(conn, loc_id)
                    calib_ids = db_get_calibrated_ids(conn, loc_id)
                    await broadcast(json.dumps({
                        "type":                 "calibration_point_removed",
                        "grid_point_id":        gp_id,
                        "calibrated_point_ids": calib_ids,
                    }))
                    print(f"  [cal] Removed grid point {gp_id}")

                elif action == "move_calibration_point":
                    gp_id = int(data["grid_point_id"])
                    loc   = State.active_location
                    if loc.get("location_type") == "campus_outdoor":
                        proj  = CampusProjection(loc["campus_lat_ref"],
                                                 loc["campus_lng_ref"])
                        x_m, y_m = proj.to_local(float(data["lat"]),
                                                   float(data["lng"]))
                        conn.execute(
                            "UPDATE grid_points SET x=?,y=?,lat=?,lng=?"
                            " WHERE id=?",
                            (round(x_m,3), round(y_m,3),
                             float(data["lat"]), float(data["lng"]), gp_id),
                        )
                    else:
                        conn.execute(
                            "UPDATE grid_points SET x=?,y=? WHERE id=?",
                            (float(data["x"]), float(data["y"]), gp_id),
                        )
                    conn.commit()
                    State.active_location = db_get_location_full(conn,
                                                                   loc["id"])
                    await ws.send(json.dumps({
                        "type":         "calibration_point_moved",
                        "grid_point_id": gp_id,
                        "warning": "Re-calibrate this point for accurate results",
                    }))

                elif action == "add_campus_calibration_point":
                    loc    = State.active_location
                    loc_id = loc.get("id")
                    if loc.get("location_type") != "campus_outdoor":
                        await ws.send(json.dumps({
                            "type":    "error",
                            "message": "add_campus_calibration_point: not a campus location",
                        }))
                    else:
                        gps_lat = float(data["lat"])
                        gps_lng = float(data["lng"])
                        # Project GPS → local (x, y) using stored SW-corner ref
                        proj  = CampusProjection(loc["campus_lat_ref"],
                                                 loc["campus_lng_ref"])
                        x_m, y_m = proj.to_local(gps_lat, gps_lng)
                        # Auto-label C001, C002, …
                        count = conn.execute(
                            "SELECT COUNT(*) FROM grid_points "
                            "WHERE location_id=? AND point_type='campus_calib'",
                            (loc_id,)
                        ).fetchone()[0]
                        label = f"C{count + 1:03d}"
                        cur = conn.execute(
                            "INSERT INTO grid_points"
                            " (location_id, point_label, x, y, lat, lng, point_type)"
                            " VALUES (?,?,?,?,?,?,'campus_calib')",
                            (loc_id, label,
                             round(x_m, 3), round(y_m, 3),
                             gps_lat, gps_lng)
                        )
                        conn.commit()
                        gp_id = cur.lastrowid
                        # Refresh State so start_calibration can find the new point
                        State.active_location = db_get_location_full(conn, loc_id)
                        await broadcast(json.dumps({
                            "type":          "campus_calibration_point_added",
                            "grid_point_id": gp_id,
                            "label":         label,
                            "lat":           gps_lat,
                            "lng":           gps_lng,
                            "x":             round(x_m, 3),
                            "y":             round(y_m, 3),
                            "source":        data.get("source", "manual_click"),
                            "accuracy_m":    data.get("accuracy_m"),
                        }))
                        print(f"  [cal] Campus point {label}: "
                              f"({x_m:.1f}, {y_m:.1f}) m  "
                              f"GPS({gps_lat:.6f}, {gps_lng:.6f})")

                elif action == "generate_synthetic":
                    loc_id = State.active_location["id"]
                    loc = db_get_location_full(conn, loc_id)
                    conn.execute("DELETE FROM calibration_data WHERE location_id=?", (loc_id,))
                    synth_data = []
                    for gp in loc["grid_points"]:
                        for a in loc["anchors"]:
                            d = max(math.hypot(gp["x"] - a["x"], gp["y"] - a["y"]), 0.1)
                            r = NLateration.A - 10 * NLateration.N * math.log10(d)
                            synth_data.append((loc_id, gp["id"], a["name"], r))
                    conn.executemany(
                        "INSERT INTO calibration_data (location_id, grid_point_id, anchor_name, rssi) VALUES (?,?,?,?)",
                        synth_data
                    )
                    conn.commit()
                    State.engine.reload(conn, loc_id)
                    loc = db_get_location_full(conn, loc_id)
                    State.active_location = loc
                    await broadcast(json.dumps({
                        "type": "active_location", "location": loc,
                        "anchors": loc["anchors"], "grid_points": loc["grid_points"]
                    }))

                elif action == "save_location_data":
                    loc_id = int(data.get("location_id",
                                          State.active_location.get("id", 0)))
                    try:
                        save_location_data(conn, loc_id)
                        folder = DATA_DIR / _safe_name(
                            conn.execute("SELECT name FROM locations WHERE id=?",
                                         (loc_id,)).fetchone()["name"], loc_id)
                        await ws.send(json.dumps({
                            "type":    "data_saved",
                            "folder":  str(folder),
                            "loc_id":  loc_id,
                        }))
                    except Exception as exc:
                        await ws.send(json.dumps({
                            "type": "error", "message": str(exc)
                        }))

                elif action == "get_fp_index":
                    # Return index of all saved fingerprint maps
                    idx_path = FP_MAP_DIR / "index.json"
                    try:
                        idx = json.loads(idx_path.read_text(encoding="utf-8"))
                    except Exception:
                        idx = []
                    # Enrich with current calibration counts from DB
                    all_locs = {l["id"]: l for l in db_get_all_locations(conn)}
                    for entry in idx:
                        lid = entry.get("location_id")
                        if lid in all_locs:
                            entry["location_type"] = all_locs[lid].get(
                                "location_type", "indoor_room")
                    await ws.send(json.dumps({"type": "fp_index", "maps": idx}))

                elif action == "export_fp_map":
                    # Export fingerprint map for any location (not just active)
                    loc_id = int(data.get("location_id",
                                          State.active_location.get("id", 0)))
                    try:
                        auto_save_fingerprint_map(conn, loc_id)
                        fp_path = None
                        for f in FP_MAP_DIR.glob(f"*_{loc_id}.json"):
                            fp_path = f; break
                        if fp_path:
                            content = json.loads(fp_path.read_text(encoding="utf-8"))
                            await ws.send(json.dumps({
                                "type":     "fp_export",
                                "location_id": loc_id,
                                "filename": fp_path.name,
                                "data":     content,
                            }))
                        else:
                            await ws.send(json.dumps({
                                "type": "error",
                                "message": "No fingerprint map saved yet for this location",
                            }))
                    except Exception as exc:
                        await ws.send(json.dumps({"type": "error",
                                                   "message": str(exc)}))

                elif action == "download_map":
                    loc_id = State.active_location["id"]
                    rows = conn.execute("""
                        SELECT gp.id, gp.x, gp.y, cd.anchor_name, AVG(cd.rssi) as mean_rssi
                        FROM calibration_data cd
                        JOIN grid_points gp ON gp.id = cd.grid_point_id
                        WHERE cd.location_id = ?
                        GROUP BY gp.id, cd.anchor_name ORDER BY gp.id
                    """, (loc_id,)).fetchall()
                    pts = {}
                    for r in rows:
                        if r["id"] not in pts:
                            pts[r["id"]] = {"id": r["id"], "x": r["x"], "y": r["y"], "rssi": {}}
                        pts[r["id"]]["rssi"][r["anchor_name"]] = r["mean_rssi"]
                    map_data = []
                    for p in pts.values():
                        rssi_arr = [
                            round(p["rssi"].get("E0", -100), 2),
                            round(p["rssi"].get("E1", -100), 2),
                            round(p["rssi"].get("E2", -100), 2),
                            round(p["rssi"].get("E3", -100), 2)
                        ]
                        map_data.append({
                            "id": p["id"], "x": p["x"], "y": p["y"], "rssi": rssi_arr
                        })
                    await ws.send(json.dumps({"type": "map_download", "data": map_data}))

                elif action == "upload_map":
                    try:
                        loc_id = State.active_location["id"]
                        map_data = data.get("map_data", [])
                        print(f"  [ws] Uploading map: {len(map_data)} points received...")

                        conn.execute("DELETE FROM calibration_data WHERE location_id=?", (loc_id,))

                        loc = db_get_location_full(conn, loc_id)
                        loc_grid = loc["grid_points"]
                        inserts = []

                        for p in map_data:
                            if "x" not in p or "y" not in p or "rssi" not in p:
                                continue

                            min_dist = float('inf')
                            best_gp = None
                            for gp in loc_grid:
                                d = math.hypot(gp["x"] - p["x"], gp["y"] - p["y"])
                                if d < min_dist:
                                    min_dist = d
                                    best_gp = gp["id"]

                            if best_gp is not None:
                                for i, a_name in enumerate(ANCHOR_NAMES):
                                    if i < len(p["rssi"]) and p["rssi"][i] is not None:
                                        inserts.append((loc_id, best_gp, a_name, float(p["rssi"][i]), None))

                        print(f"  [ws] Preparing to insert {len(inserts)} RSSI readings...")

                        conn.executemany(
                            "INSERT INTO calibration_data (location_id, grid_point_id, anchor_name, rssi, snr) VALUES (?,?,?,?,?)",
                            inserts
                        )
                        conn.commit()

                        State.engine.reload(conn, loc_id)
                        loc = db_get_location_full(conn, loc_id)
                        State.active_location = loc

                        await broadcast(json.dumps({
                            "type": "active_location",
                            "location": loc,
                            "anchors": loc["anchors"],
                            "grid_points": loc["grid_points"]
                        }))
                        await ws.send(json.dumps({"type": "upload_success"}))
                        print("  [ws] Upload complete and broadcasted!")

                    except Exception as e:
                        print(f"  [ws] CRITICAL UPLOAD ERROR: {e}")
                        import traceback
                        traceback.print_exc()
                        await ws.send(json.dumps({"type": "upload_error", "msg": str(e)}))

            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                print(f"  [ws]  msg error: {exc}")
    finally:
        State.clients.discard(ws)
        print(f"  [ws]  Disconnected ({len(State.clients)} clients)")


# ── Bootstrap ─────────────────────────────────────────────────────────
def bootstrap(conn):
    count = conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    if count > 0:
        return
    print("  [db]  Seeding default: M2 Classroom")
    loc = db_create_location(
        conn,
        DEFAULT_LOC["name"], DEFAULT_LOC["width"],
        DEFAULT_LOC["height"], DEFAULT_LOC["grid_step"],
        DEFAULT_LOC["anchors"],
    )
    db_set_active(conn, loc["id"])


# ── Main ──────────────────────────────────────────────────────────────
async def main():
    global _SIM_MODE
    parser = argparse.ArgumentParser(description="InDoorLora Positioning Server")
    parser.add_argument("--sim", action="store_true",
                        help="Run in simulation mode (starts lora_rx_sim.py)")
    args = parser.parse_args()
    _SIM_MODE = args.sim

    print("=" * 56)
    print("  InDoorLora Positioning Server")
    print("  N-Lat | K-NN Fingerprint | HMM Viterbi")
    print("=" * 56)
    if _SIM_MODE:
        print("  [!]  MODE: SIMULATION  (lora_rx_sim.py)")
        print("  [!]  Data is SYNTHETIC -- not from real hardware")
        sim_path = Path(__file__).parent / "lora_rx_sim.py"
        if sim_path.exists():
            subprocess.Popen([sys.executable, str(sim_path)],
                             creationflags=subprocess.CREATE_NEW_CONSOLE
                             if sys.platform == "win32" else 0)
            print("  [sim] Started lora_rx_sim.py in separate window")
        else:
            print("  [sim] WARNING: lora_rx_sim.py not found!")
    else:
        print("  [OK]  MODE: REAL HARDWARE")
        print(f"  [OK]  Waiting for GNU Radio on {ZMQ_ADDR}")
        print("  [OK]  Make sure GNU Radio flowgraph is running")
    print("=" * 56)

    conn = open_db()
    migrate_v2_campus(conn)   # idempotent — safe on every startup
    bootstrap(conn)

    loc = db_get_active_location(conn)
    if not loc:
        print("  [db]  ERROR: no active location")
        return

    State.active_location = loc
    n_pts = len(loc["grid_points"])
    n_cal = len(loc["calibrated_point_ids"])
    print(f"  [db]  Active: '{loc['name']}'  "
          f"({loc['width']}x{loc['height']} m, {n_pts} pts, {n_cal} calibrated)")
    State.engine.reload(conn, loc["id"])

    async def _handler(ws):
        await ws_handler(ws, conn)

    async with websockets.serve(
        _handler, "127.0.0.1", WS_PORT,
        ping_interval=None, close_timeout=10, reuse_address=True,
    ):
        print(f"  [ws]  ws://127.0.0.1:{WS_PORT}")
        print(f"  [db]  {DB_PATH.resolve()}")
        await asyncio.gather(zmq_task(conn), asyncio.Future())


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
