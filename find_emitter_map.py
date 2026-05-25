#!/usr/bin/env python3
"""
InDoorLora — Auto Emitter Mapping Wizard
Stand at each corner when prompted → finds the correct --map parameter.
Run this INSTEAD of usrp_lora_bridge.py (server must be running).

Usage:
  python find_emitter_map.py
"""
import json, struct, time, sys
from collections import defaultdict
import numpy as np
import zmq

ZMQ_ADDR    = "tcp://127.0.0.1:5557"   # separate port so server is unaffected
CORNERS     = [(0,0,"bottom-left"), (6,0,"bottom-right"),
               (6,8,"top-right"),    (0,8,"top-left")]
SAMPLE_SECS = 8   # seconds to collect per corner


def recv_usrp():
    """Read RSSI from the USRP bridge via a tap on ZMQ."""
    pass   # handled below via direct USRP


def collect(det, secs) -> dict:
    """Collect RSSI for each slot for `secs` seconds."""
    totals = defaultdict(list)
    t_end  = time.time() + secs
    while time.time() < t_end:
        for eid, rssi in det.process_once():
            totals[eid].append(rssi)
        time.sleep(0.01)
    return {eid: sum(v)/len(v) for eid, v in totals.items() if v}


def main():
    print("="*56)
    print("  InDoorLora -- Emitter Mapping Wizard")
    print("="*56)
    print()

    # Import USRP
    try:
        from gnuradio import gr, uhd, blocks
        import uhd as _uhd_raw
        use_gr = True
    except ImportError:
        use_gr = False
        try:
            import uhd as _uhd_raw
        except ImportError:
            print("ERROR: GNU Radio / UHD not found.")
            sys.exit(1)

    # Simple energy detector
    SAMPLE_RATE = 250e3
    RX_GAIN     = 50
    CENTER_FREQ = 868.1e6
    TDMA_CYCLE  = 0.500
    WINDOWS     = {0:(0.000,0.050),1:(0.100,0.150),
                   2:(0.200,0.250),3:(0.300,0.350)}
    HOLDOFF     = 0.08
    NOISE_ALPHA = 0.997
    MIN_RSSI    = -72.0

    class SimpleDetector:
        def __init__(self, usrp_rx, buf):
            self.rx   = usrp_rx
            self.buf  = buf
            self.meta = _uhd_raw.types.RXMetadata()
            self._n   = None
            self._lt  = {i:0.0 for i in range(4)}

        def process_once(self):
            n = self.rx.recv(self.buf, self.meta, timeout=0.1)
            if n < 10:
                return []
            samples = self.buf[:n].copy()
            pwr  = float(np.mean(np.abs(samples)**2))
            pdbm = 10*(np.log10(max(pwr,1e-15)))
            if self._n is None:
                self._n = pwr; return []
            ndbm = 10*np.log10(max(self._n,1e-15))
            snr  = pdbm - ndbm
            if snr < 5.0:
                self._n = NOISE_ALPHA*self._n+(1-NOISE_ALPHA)*pwr
                return []
            rssi = round(pdbm+30-RX_GAIN,1)
            if rssi < MIN_RSSI:
                return []
            t = time.time()
            cyc = t % TDMA_CYCLE
            for s,(lo,hi) in WINDOWS.items():
                if lo-0.06 <= cyc < hi+0.06:
                    if t-self._lt[s] > HOLDOFF:
                        self._lt[s]=t
                        return [(s, rssi)]
            return []

    usrp = _uhd_raw.usrp.MultiUSRP("num_recv_frames=256")
    usrp.set_rx_rate(SAMPLE_RATE)
    usrp.set_rx_freq(_uhd_raw.libpyuhd.types.tune_request(CENTER_FREQ))
    usrp.set_rx_gain(RX_GAIN)
    usrp.set_rx_antenna("RX2")
    time.sleep(0.5)
    st   = _uhd_raw.usrp.StreamArgs("fc32","sc16")
    rx   = usrp.get_rx_stream(st)
    rx.issue_stream_cmd(_uhd_raw.types.StreamCMD(_uhd_raw.types.StreamMode.start_cont))
    BLOCK = int(SAMPLE_RATE*0.025)
    buf   = np.empty(BLOCK, dtype=np.complex64)
    det   = SimpleDetector(rx, buf)

    print("USRP ready.\n")
    corner_slot = {}   # corner_idx → strongest slot

    for idx, (cx, cy, label) in enumerate(CORNERS):
        print(f"-"*56)
        print(f"  Corner {idx+1}/4: ({cx},{cy}) {label.upper()}")
        print(f"  * Stand NEXT to the emitter at this corner")
        input(f"  ► Press ENTER when ready...")
        print(f"  Collecting {SAMPLE_SECS}s of RSSI data...", end="", flush=True)

        totals = defaultdict(list)
        t_end  = time.time() + SAMPLE_SECS
        while time.time() < t_end:
            for s, rssi in det.process_once():
                totals[s].append(rssi)
            time.sleep(0.005)
            remaining = t_end - time.time()
            if int(remaining*2) % 4 == 0:
                print(".", end="", flush=True)

        if not totals:
            print("\n  WARNING: No signal detected! Check USRP and emitters.")
            corner_slot[idx] = None
            continue

        avgs = {s: sum(v)/len(v) for s,v in totals.items() if v}
        best = max(avgs, key=avgs.get)
        print(f"\n  RSSI: {', '.join(f'E{s}={avgs[s]:+.1f}' for s in sorted(avgs))}")
        print(f"  -> Strongest: slot E{best} ({avgs[best]:+.1f} dBm) = emitter at ({cx},{cy})")
        corner_slot[idx] = best

    # Build mapping
    print("\n" + "="*56)
    slot_to_eid = {}
    for idx, (cx, cy, _) in enumerate(CORNERS):
        slot = corner_slot.get(idx)
        if slot is not None:
            slot_to_eid[slot] = idx   # slot → correct emitter ID (0=E0,1=E1...)

    if len(slot_to_eid) < 2:
        print("  Not enough data to build mapping.")
    else:
        map_str = ",".join(f"{s}:{e}" for s,e in sorted(slot_to_eid.items()))
        print(f"\n  [OK] Correct emitter mapping found:")
        print(f"\n  python usrp_lora_bridge.py --map {map_str}")
        print(f"\n  Run this command to start the bridge with correct IDs.")

        # Save to file
        with open("emitter_map.txt","w") as f:
            f.write(f"--map {map_str}\n")
        print(f"\n  Also saved to: emitter_map.txt")

    rx.issue_stream_cmd(_uhd_raw.types.StreamCMD(_uhd_raw.types.StreamMode.stop_cont))


if __name__ == "__main__":
    main()
