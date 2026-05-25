#!/usr/bin/env python3
"""
InDoorLora — Serial-to-ZMQ Bridge
Reads LoRa packets from receiver.ino and forwards to positioning_server.py

Usage:
  python serial_bridge.py              # auto-detect port
  python serial_bridge.py --port COM4  # specify port
"""

import argparse
import json
import re
import struct
import sys
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("ERROR: Run:  pip install pyserial")
    sys.exit(1)

try:
    import zmq
except ImportError:
    print("ERROR: Run:  pip install pyzmq")
    sys.exit(1)

ZMQ_ADDR = "tcp://127.0.0.1:5556"
BAUD     = 115200
PATTERN  = re.compile(
    r"Received packet:\s*(UE07,\d+,(\d+),\d+)\s*\|.*RSSI:\s*(-?\d+)"
)


def build_pdu(payload: str, rssi: float) -> bytes:
    meta    = {"rssi": round(rssi, 1)}
    m_bytes = json.dumps(meta).encode()
    p_bytes = payload.encode()
    return (struct.pack(">I", len(m_bytes)) + m_bytes +
            struct.pack(">I", len(p_bytes)) + p_bytes)


def find_port_windows():
    """Scan COM1-COM30 directly — bypasses pyserial list issues on Windows."""
    found = []
    for i in range(1, 31):
        port = f"COM{i}"
        try:
            s = serial.Serial(port, timeout=0.1)
            s.close()
            found.append(port)
        except (serial.SerialException, OSError):
            pass
    return found


def auto_detect():
    # Try pyserial list first
    ports = [p.device for p in serial.tools.list_ports.comports()]
    if not ports:
        print("  pyserial list empty -- scanning COM1-COM30 directly...")
        ports = find_port_windows()
    return ports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=None)
    parser.add_argument("--baud", default=BAUD, type=int)
    args = parser.parse_args()

    port = args.port

    if port is None:
        print("  Scanning for serial ports...")
        ports = auto_detect()
        if not ports:
            print("\n  No serial ports found. Try:")
            print("  1. Unplug and replug the Feather M0")
            print("  2. Check Device Manager for the COM number")
            print("  3. Run:  python serial_bridge.py --port COM4")
            sys.exit(1)
        if len(ports) == 1:
            port = ports[0]
            print(f"  Auto-selected: {port}")
        else:
            print(f"  Found ports: {ports}")
            print(f"  Auto-selecting first: {ports[0]}")
            port = ports[0]

    # ZMQ
    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.connect(ZMQ_ADDR)
    print("=" * 50)
    print(f"  InDoorLora Serial Bridge")
    print(f"  Port : {port} @ {args.baud} baud")
    print(f"  ZMQ  : {ZMQ_ADDR}")
    print("=" * 50)
    time.sleep(0.5)

    pkt_count = 0
    while True:
        try:
            with serial.Serial(port, args.baud, timeout=2) as ser:
                print(f"  [bridge] Connected to {port} -- waiting for packets...")
                print(f"  [bridge] Press Ctrl+C to stop\n")
                consecutive_errors = 0
                while True:
                    try:
                        raw  = ser.readline()
                        if not raw:
                            continue
                        line = raw.decode("utf-8", errors="replace").strip()
                        m    = PATTERN.search(line)
                        if not m:
                            if line:
                                print(f"  [serial] {line}")
                            continue
                        payload = m.group(1)
                        anchor  = m.group(2)
                        rssi    = float(m.group(3))
                        pub.send(build_pdu(payload, rssi))
                        pkt_count += 1
                        print(f"  -> E{anchor}  RSSI={rssi:+.0f} dBm  "
                              f"  [{pkt_count} pkts]")
                        consecutive_errors = 0
                    except UnicodeDecodeError:
                        pass
        except serial.SerialException as exc:
            print(f"\n  [bridge] Port error: {exc}")
            print(f"  [bridge] Retrying in 3 s... (Ctrl+C to stop)")
            time.sleep(3)
        except KeyboardInterrupt:
            print(f"\n  [bridge] Stopped -- {pkt_count} packets forwarded.")
            pub.close()
            ctx.term()
            break


if __name__ == "__main__":
    main()
