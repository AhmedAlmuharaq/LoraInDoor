#!/usr/bin/env python3
"""
Dynamic LoRa RX Simulator — InDoorLora
Syncs anchor positions and room dimensions from server via WebSocket,
walks a rectangular path adapted to the active room, publishes PDUs via ZMQ.

ZMQ PUB  → tcp://127.0.0.1:5556  (server subscribes as SUB)
WS client→ ws://127.0.0.1:8765   (receives anchors_update + active_location)

PDU format (matches positioning_server.py parse_pdu):
  [4-byte BE meta_len][meta JSON][4-byte BE payload_len][payload string]
  meta    = {"rssi": -42.1, "samp_rate": 250000}
  payload = "UE07,13,{anchor_id},{sequence}"
"""

import json
import math
import random
import struct
import time
import threading

import zmq
import websocket   # pip install websocket-client

# ── Config ────────────────────────────────────────────────────────────
ZMQ_ADDR = "tcp://127.0.0.1:5556"
WS_ADDR  = "ws://127.0.0.1:8765"
RSSI_0   = -40.0    # dBm at 1 m (path-loss model)
N_EXP    = 2.2      # path-loss exponent
NOISE_SD = 1.5      # dB standard deviation
SPEED    = 0.4      # m/s along walk path
STEP_HZ  = 4.0      # packets per second per anchor → 0.25 s per step
MARGIN   = 0.5      # metres from walls for walk rectangle


# ── Rectangular walk path ─────────────────────────────────────────────
class RectPath:
    """Walk a rectangle at MARGIN from room walls at a fixed speed."""

    def __init__(self, width: float = 8.0, height: float = 6.0):
        self._pos = 0.0   # distance along perimeter (m)
        self._corners: list[tuple[float, float]] = []
        self._cum:     list[float] = []
        self._perim    = 0.0
        self.update(width, height)

    def update(self, width: float, height: float):
        m = MARGIN
        self._corners = [
            (m,         m),
            (width - m, m),
            (width - m, height - m),
            (m,         height - m),
        ]
        n = len(self._corners)
        self._cum = [0.0]
        for i in range(n):
            j = (i + 1) % n
            cx, cy = self._corners[i]
            nx, ny = self._corners[j]
            self._cum.append(self._cum[-1] + math.hypot(nx - cx, ny - cy))
        self._perim = self._cum[-1]
        # Clamp current position to new perimeter
        self._pos = self._pos % self._perim if self._perim > 0 else 0.0

    def step(self, dt: float) -> tuple[float, float]:
        if self._perim <= 0:
            return self._corners[0] if self._corners else (0.0, 0.0)
        self._pos = (self._pos + SPEED * dt) % self._perim
        n = len(self._corners)
        for i in range(n):
            j = (i + 1) % n
            seg_len = self._cum[i + 1] - self._cum[i]
            if seg_len <= 0:
                continue
            if self._pos <= self._cum[i + 1] + 1e-9:
                t = (self._pos - self._cum[i]) / seg_len
                cx, cy = self._corners[i]
                nx, ny = self._corners[j]
                return (cx + t * (nx - cx), cy + t * (ny - cy))
        return self._corners[0]


# ── PDU builder ───────────────────────────────────────────────────────
def build_pdu(anchor_id: str, seq: int, rssi: float) -> bytes:
    meta    = {"rssi": round(rssi, 1), "samp_rate": 250000}
    payload = f"UE07,13,{anchor_id},{seq}"
    m_bytes = json.dumps(meta).encode("utf-8")
    p_bytes = payload.encode("utf-8")
    return (
        struct.pack(">I", len(m_bytes)) + m_bytes +
        struct.pack(">I", len(p_bytes)) + p_bytes
    )


def rssi_from_dist(dist: float) -> float:
    d = max(dist, 0.1)
    return RSSI_0 - 10 * N_EXP * math.log10(d) + random.gauss(0, NOISE_SD)


# ── Shared state (written by WS thread, read by main loop) ───────────
class SimState:
    lock    = threading.Lock()
    anchors: list = []        # [{"id": "E0", "x": 0.5, "y": 0.5}, ...]
    width:   float = 8.0
    height:  float = 6.0
    updated: bool  = False    # flag: path needs rebuild

state = SimState()


# ── WebSocket thread ──────────────────────────────────────────────────
def ws_thread():
    while True:
        try:
            ws = websocket.WebSocket()
            ws.connect(WS_ADDR, timeout=5)
            print(f"  [sim] WS connected to {WS_ADDR}")
            while True:
                raw = ws.recv()
                if not raw:
                    break
                data = json.loads(raw)

                if data.get("type") == "anchors_update":
                    with state.lock:
                        state.anchors = data["anchors"]
                    ids = [(a['id'], round(a['x'],2), round(a['y'],2))
                           for a in data["anchors"]]
                    print(f"  [sim] Anchors: {ids}")

                elif data.get("type") == "active_location":
                    loc = data.get("location", {})
                    w   = loc.get("width",  state.width)
                    h   = loc.get("height", state.height)
                    with state.lock:
                        state.width   = w
                        state.height  = h
                        state.updated = True
                        # Also pick up anchors from active_location
                        ancs = data.get("anchors", [])
                        if ancs:
                            state.anchors = ancs
                    print(f"  [sim] Room: {w}x{h} m")

        except Exception as exc:
            print(f"  [sim] WS error: {exc} -- retry in 2 s")
            time.sleep(2)


# ── Main loop ─────────────────────────────────────────────────────────
def main():
    print("=" * 56)
    print("  InDoorLora -- Dynamic LoRa RX Simulator")
    print(f"  ZMQ PUB  -> {ZMQ_ADDR}")
    print(f"  WS       -> {WS_ADDR}")
    print("=" * 56)

    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    # CONNECT (not bind) — the server now binds, publishers connect.
    pub.connect(ZMQ_ADDR)
    print(f"  [sim] ZMQ connected to {ZMQ_ADDR}")

    # Start WebSocket thread
    t = threading.Thread(target=ws_thread, daemon=True)
    t.start()
    time.sleep(1.0)   # give WS time to connect and receive initial state

    path = RectPath(state.width, state.height)
    seq  = 0
    dt   = 1.0 / STEP_HZ

    try:
        while True:
            t0 = time.time()

            # Rebuild path if room changed
            with state.lock:
                if state.updated:
                    path.update(state.width, state.height)
                    state.updated = False
                anchors = list(state.anchors)
                w, h    = state.width, state.height

            tx, ty = path.step(dt)

            for a in anchors:
                dist = math.hypot(tx - a["x"], ty - a["y"])
                rssi = rssi_from_dist(dist)
                pub.send(build_pdu(a["id"], seq, rssi))

            if seq % 20 == 0 and anchors:
                print(f"  [sim] pos=({tx:.2f},{ty:.2f})  seq={seq}"
                      f"  room={w:.0f}×{h:.0f} m  anchors={len(anchors)}")

            seq += 1
            elapsed = time.time() - t0
            time.sleep(max(0.0, dt - elapsed))

    except KeyboardInterrupt:
        pass
    finally:
        pub.close()
        ctx.term()
        print("\n  [sim] Stopped.")


if __name__ == "__main__":
    main()
