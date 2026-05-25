# IndoorLoRa

**A Real-Time Indoor Positioning Platform Using LoRa RSSI with N-Lateration, Fingerprinting, and HMM Trajectory Smoothing**

IndoorLoRa estimates the location of a mobile node from the Received Signal Strength Indicator (RSSI) of four fixed LoRa anchors operating in the 868 MHz band. Three positioning algorithms run concurrently on the same RSSI stream, and an interactive web dashboard exposes their intermediate computations in real time. All raw measurements are persisted to support reproducibility.

This work was carried out within the multi-year tutored project of the Master International Internet of Things programme of the EIPHI Graduate School, at the FEMTO-ST Institute (Université Marie et Louis Pasteur, Montbéliard, France).

---

## System Overview

The platform is organised in four tiers:

1. **Physical deployment** — four LoRa anchors (E0–E3) in the corners of an 8 × 6 m indoor room, broadcasting to a mobile receiver.
2. **Signal reception & ingestion** — a USRP B200mini software-defined radio captures the LoRa packets, a GNU Radio demodulator recovers per-anchor RSSI and SNR, and a ZeroMQ publisher streams the measurements.
3. **Positioning server** — runs N-Lateration, weighted *K*-NN fingerprinting, and HMM-Viterbi tracking concurrently, persisting locations, anchors, calibration data, and the RSSI log in SQLite.
4. **Dashboard** — a real-time web dashboard renders the room view, per-anchor RSSI, and an event log, communicating with the server over WebSocket.

---

## Positioning Algorithms

| Method | Description |
|---|---|
| **N-Lateration** | RSSI → distance via a log-distance path-loss model, weighted nonlinear least squares (Levenberg–Marquardt), with exponential moving-average smoothing. |
| **Fingerprinting** | Weighted *K*-NN (K = 3) over a calibrated radio map in RSSI signal space. |
| **HMM-Viterbi** | Online Viterbi tracking over calibrated reference points, with a Gaussian motion-continuity transition model for smooth trajectories. |

In the reference evaluation (calibrated radio map of 221 points), fingerprinting and HMM smoothing reduced the mean absolute error of N-Lateration by roughly 30% and 45% respectively, with the HMM tracker reaching a mean absolute error of 0.86 m.

---

## Hardware & Radio Configuration

- **SDR receiver:** USRP B200mini
- **Demodulation:** GNU Radio LoRa demodulator
- **Band:** 868.1 MHz (EU ISM)
- **LoRa parameters:** SF7, bandwidth 125 kHz, coding rate 4/5
- **Transmit power:** 17 dBm
- **Beacon interval:** 500 ms (TDMA cycle, 50 ms slots)
- **Anchors:** 4 (E0–E3), placed near the room corners

---

## Repository Structure

```
InDoorLora/
├── server/              # Python positioning server (ingestion, algorithms, persistence)
├── gnuradio/            # GNU Radio flowgraph(s) for SDR reception & LoRa demodulation
├── dashboard/           # Single-page HTML/JavaScript web dashboard
├── simulator/           # RSSI simulator for testing without hardware
├── data/                # Radio-map JSON exports, sample calibration data
├── figures/             # System architecture diagram and result plots
├── paper/               # IEEE / IPIN paper (.tex, .pdf)
└── README.md
```

> Adjust the folder names above to match your actual layout.

---

## Getting Started

### Requirements

- Python 3.10+
- GNU Radio (with a LoRa demodulation block) for live hardware operation
- A USRP B200mini (or use the simulator for hardware-free testing)

### Install

```bash
git clone https://github.com/AhmedAlmuharaq/IndoorLoRa.git
cd IndoorLoRa
pip install -r requirements.txt
```

### Run with simulated data (no hardware)

```bash
# Terminal 1 — start the RSSI simulator
python simulator/lora_rx_sim.py

# Terminal 2 — start the positioning server
python server/positioning_server.py

# Then open the dashboard in a browser
dashboard/InDoorLora_Dashboard.html
```

### Run with live hardware

1. Launch the GNU Radio flowgraph to demodulate LoRa packets from the USRP B200mini.
2. Start the positioning server (it subscribes to the ZeroMQ packet stream).
3. Open the dashboard and connect to the server's WebSocket endpoint.

> Update the run commands to match your actual entry-point filenames.

---

## Calibration

A radio map is built over a regular grid (0.5 m spacing) by collecting RSSI samples per anchor at each reference point. The map is rebuilt automatically when calibration data change; fingerprinting and HMM estimation become active once at least three reference points are calibrated. Each location's radio map is exported to a standalone JSON file for archival and reuse.

---

## Development

The platform was developed iteratively following an agile, eXtreme-Programming-inspired workflow, with each iteration closing on a working increment (simulator → ingestion → N-Lateration → fingerprinting → HMM → dashboard). Pair programming was used for the SDR-integration and signal-processing tasks. Each algorithm was first validated on synthetic RSSI traces before being exercised on live hardware.

---

## Authors

Ahmed Al-Muharaq, Malick Diop, Oumnia Chiouikh, Cynthia Ayetolou — Master 1 Internet of Things, UFR STGI, Université Marie et Louis Pasteur.

Supervised in collaboration with Philippe Canalda and François Spies (FEMTO-ST Institute), with Mariame Niang and Ibra Dioum (University Cheikh Anta Diop of Dakar).

---

## Acknowledgment

This work has been supported by the EIPHI Graduate Schools.

---

## License

Add a license of your choice (e.g., MIT) in a `LICENSE` file. If you do not add one, the code defaults to "all rights reserved."
