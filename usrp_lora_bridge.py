#!/usr/bin/env python3
"""
InDoorLora — USRP B200mini LoRa Bridge (RadioConda)
Receives LoRa beacons and forwards RSSI to positioning_server.py via ZMQ.

The bridge uses burst-pattern sync (not laptop clock) to identify emitters.
If emitter IDs are still wrong, use --map to correct them.

Usage:
  python usrp_lora_bridge.py
  python usrp_lora_bridge.py --map 0:1,1:2,2:3,3:0   # rotate IDs
  python usrp_lora_bridge.py --gain 60 --debug

How to find the correct --map:
  1. Stand near physical corner E0 (0,0)
  2. Note which system "Ex" shows STRONGEST in the terminal
  3. That Ex's slot should map to 0
  Example: If E2 is strongest near physical E0 corner → use --map 2:0,0:2
"""

import argparse
import json
import math
import struct
import sys
import time
from collections import deque

import numpy as np
import zmq

# ── Parameters ────────────────────────────────────────────────────────
CENTER_FREQ  = 868.1e6
BANDWIDTH    = 125e3
SF           = 7
OVERSAMPLE   = 2
SAMPLE_RATE  = BANDWIDTH * OVERSAMPLE   # 250 kHz
RX_GAIN_DEFAULT = 50
RX_GAIN         = 50
ZMQ_ADDR        = "tcp://127.0.0.1:5556"
DEVICE_ARGS  = "num_recv_frames=512,recv_frame_size=4096"
MIN_RSSI_DBM = -72.0   # discard weaker detections (noise)

TDMA_CYCLE   = 0.500   # seconds
TDMA_WINDOWS = {0:(0.000,0.050), 1:(0.100,0.150),
                2:(0.200,0.250), 3:(0.300,0.350)}


# ── PDU builder ───────────────────────────────────────────────────────
def build_pdu(emitter_id: int, rssi_dbm: float, seq: int) -> bytes:
    payload = f"UE07,13,{emitter_id},{seq}"
    meta    = {"rssi": round(rssi_dbm, 1)}
    mb = json.dumps(meta).encode()
    pb = payload.encode()
    return struct.pack(">I",len(mb))+mb+struct.pack(">I",len(pb))+pb


# ── Burst-pattern TDMA detector ───────────────────────────────────────
class BurstDetector:
    """
    Detects energy bursts and maps to emitter IDs using burst-pattern sync.
    Uses the INTER-BURST spacing (~100ms) rather than absolute laptop time,
    making it robust to emitter clock drift.
    """
    NOISE_ALPHA   = 0.997
    BURST_HOLDOFF = 0.080   # min gap between detections for same slot
    SNR_THRESH_DB = 5.0     # min SNR above noise floor

    def __init__(self, id_map: dict):
        self._noise   = None
        self._bursts  = deque()   # (time, rssi_dbm) of recent bursts
        self._last_t  = {i: 0.0 for i in range(4)}
        self._counts  = {i: 0 for i in range(4)}
        self._id_map  = id_map    # slot→emitter remapping

    def process(self, samples: np.ndarray) -> list:
        pwr  = float(np.mean(np.abs(samples)**2))
        pdbm = 10*math.log10(max(pwr,1e-15))

        # Adaptive noise floor
        if self._noise is None:
            self._noise = pwr; return []
        ndbm = 10*math.log10(max(self._noise,1e-15))
        snr  = pdbm - ndbm

        if snr < self.SNR_THRESH_DB:
            self._noise = self.NOISE_ALPHA*self._noise + (1-self.NOISE_ALPHA)*pwr
            return []

        # Burst detected
        t_now    = time.time()
        rssi_dbm = round(pdbm + 30 - RX_GAIN, 1)

        if rssi_dbm < MIN_RSSI_DBM:
            return []

        # Remove old bursts > 600ms ago
        while self._bursts and t_now - self._bursts[0][0] > 0.6:
            self._bursts.popleft()
        self._bursts.append((t_now, rssi_dbm))

        # ── Identify emitter slot from burst pattern ────────────────
        slot = self._pattern_slot(t_now)
        if slot is None:
            return []

        # Debounce
        if t_now - self._last_t[slot] < self.BURST_HOLDOFF:
            return []

        self._last_t[slot] = t_now
        eid = self._id_map.get(slot, slot)   # apply user remapping
        self._counts[eid] += 1
        return [(eid, rssi_dbm)]

    def _pattern_slot(self, t_now) -> int | None:
        """
        Identify slot from relative position in the burst pattern.
        Groups bursts within 500ms and uses their ORDER (not laptop clock).
        """
        recent = list(self._bursts)   # already sorted by time
        if len(recent) < 2:
            # First burst: use laptop time as fallback
            cyc = t_now % TDMA_CYCLE
            for s, (lo, hi) in TDMA_WINDOWS.items():
                if lo-0.06 <= cyc < hi+0.06:
                    return s
            return None

        # Find bursts within the last 500ms and check spacing
        grp = [b for b in recent if t_now - b[0] <= 0.5]
        if len(grp) < 2:
            return None

        # Compute spacings between consecutive bursts
        spacings = [grp[i+1][0]-grp[i][0] for i in range(len(grp)-1)]
        good_spacing = [0.06 < s < 0.145 for s in spacings]

        if any(good_spacing):
            # Use position in group as slot index
            pos = (len(grp)-1) % 4   # current burst is last in group
            return pos

        # Fallback: laptop clock
        cyc = t_now % TDMA_CYCLE
        for s, (lo, hi) in TDMA_WINDOWS.items():
            if lo-0.06 <= cyc < hi+0.06:
                return s
        return None


# ── GNU Radio flowgraph ───────────────────────────────────────────────
def run_gnuradio(args, pub, det):
    from gnuradio import gr, uhd, blocks

    class RxBlock(gr.sync_block):
        def __init__(self):
            gr.sync_block.__init__(self,"LoRaRx",[np.complex64],[])
            self.seq   = {i:0 for i in range(4)}
            self.total = 0
        def work(self, input_items, _):
            samples = np.array(input_items[0], dtype=np.complex64)
            for eid, rssi in det.process(samples):
                pub.send(build_pdu(eid, rssi, self.seq[eid]))
                self.seq[eid] += 1; self.total += 1
                cnts = " ".join(f"E{i}={det._counts[i]}" for i in range(4))
                if args.debug or self.total % 10 == 1:
                    print(f"  [B200] E{eid}  RSSI={rssi:+.1f} dBm"
                          f"  pkt={self.total}  [{cnts}]")
            return len(input_items[0])

    tb  = gr.top_block()
    src = uhd.usrp_source(DEVICE_ARGS,
                          uhd.stream_args(cpu_format="fc32",channels=[0]))
    src.set_samp_rate(SAMPLE_RATE)
    src.set_center_freq(CENTER_FREQ)
    src.set_gain(args.gain)
    src.set_antenna("RX2")
    rx  = RxBlock()
    tb.connect(src, rx)
    tb.start()
    print(f"  Freq={CENTER_FREQ/1e6:.3f} MHz  BW={BANDWIDTH/1e3:.0f}kHz"
          f"  SF{SF}  Gain={args.gain}dB")
    print(f"  ID map: {det._id_map if det._id_map else 'default (no remap)'}")
    print(f"  Press Ctrl+C to stop\n")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        tb.stop(); tb.wait()


def run_uhd_direct(args, pub, det):
    import uhd as _uhd
    usrp = _uhd.usrp.MultiUSRP(DEVICE_ARGS)
    usrp.set_rx_rate(SAMPLE_RATE)
    usrp.set_rx_freq(_uhd.libpyuhd.types.tune_request(CENTER_FREQ))
    usrp.set_rx_gain(args.gain)
    usrp.set_rx_antenna("RX2")
    time.sleep(0.5)
    st   = _uhd.usrp.StreamArgs("fc32","sc16")
    rx   = usrp.get_rx_stream(st)
    meta = _uhd.types.RXMetadata()
    rx.issue_stream_cmd(_uhd.types.StreamCMD(_uhd.types.StreamMode.start_cont))
    BLOCK = int(SAMPLE_RATE*0.025)
    buf   = np.empty(BLOCK, dtype=np.complex64)
    seqs  = {i:0 for i in range(4)}
    total = 0
    print(f"  Freq={CENTER_FREQ/1e6:.3f} MHz  Gain={args.gain}dB  (UHD direct)")
    print(f"  ID map: {det._id_map if det._id_map else 'default'}\n")
    try:
        while True:
            n = rx.recv(buf, meta, timeout=1.0)
            if n < 10: continue
            for eid, rssi in det.process(buf[:n].copy()):
                pub.send(build_pdu(eid, rssi, seqs[eid]))
                seqs[eid] += 1; total += 1
                cnts = " ".join(f"E{i}={det._counts[i]}" for i in range(4))
                if args.debug or total % 10 == 1:
                    print(f"  [B200] E{eid}  RSSI={rssi:+.1f} dBm"
                          f"  pkt={total}  [{cnts}]")
    except KeyboardInterrupt:
        rx.issue_stream_cmd(
            _uhd.types.StreamCMD(_uhd.types.StreamMode.stop_cont))


# ── Entry point ───────────────────────────────────────────────────────
def parse_map(s: str) -> dict:
    """Parse '0:1,1:2,2:3,3:0' into {0:1, 1:2, 2:3, 3:0}."""
    if not s:
        return {}
    result = {}
    for pair in s.split(","):
        a, b = pair.strip().split(":")
        result[int(a)] = int(b)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--gain",  default=RX_GAIN_DEFAULT, type=float)
    p.add_argument("--debug", action="store_true")
    p.add_argument("--map",   default="",
                   help="Remap slot→emitter ID, e.g. '2:0,0:2' swaps E0↔E2")
    args = p.parse_args()

    id_map = parse_map(args.map)

    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.connect(ZMQ_ADDR)
    time.sleep(0.3)

    det = BurstDetector(id_map)

    print("="*56)
    print("  InDoorLora -- USRP B200mini Bridge")
    print(f"  Freq : {CENTER_FREQ/1e6:.3f} MHz")
    print(f"  BW   : {BANDWIDTH/1e3:.0f} kHz  SF{SF}")
    print(f"  Gain : {args.gain} dB")
    if id_map:
        print(f"  Map  : {args.map}")
    else:
        print("  Map  : auto (use --map X:Y to correct emitter IDs)")
    print("="*56)

    for runner, name in [
        (run_gnuradio,   "GNU Radio"),
        (run_uhd_direct, "UHD Python"),
    ]:
        try:
            print(f"\n  Trying {name}...")
            runner(args, pub, det)
            break
        except ImportError as e:
            print(f"  {name} not available: {e}")
        except Exception as e:
            print(f"  {name} error: {e}")
    else:
        print("\n  ERROR: GNU Radio and UHD not found.")
        print("  Use serial_bridge.py with Feather M0 receiver instead.")
        sys.exit(1)

    pub.close()
    ctx.term()


if __name__ == "__main__":
    main()
