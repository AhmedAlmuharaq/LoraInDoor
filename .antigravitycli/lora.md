# InDoorLora - Project Reference & Context (/lora)

This document contains the complete context, architecture, schema details, and key configurations for the InDoorLora project.

---

## 1. Project Overview & Architecture
InDoorLora is a database-first positioning and tracking server for LoRa signals. It supports both **Indoor Room** localization (using grid points and relative coordinates) and **Campus Outdoor** localization (using GPS coordinate projections).

### Core Components
- **Positioning Server** (`positioning_server.py`):
  - Binds a **ZMQ SUB** socket on `tcp://127.0.0.1:5556` to receive packets from bridges or simulator.
  - Runs a **WebSocket Server** on `ws://127.0.0.1:8765` for client connections (e.g. dashboards).
  - Manages active locations, loads grid points, and calculates positions using algorithms.
- **LoRa RX Simulator** (`lora_rx_sim.py`):
  - Generates synthetic RSSI packets and sends them over ZMQ.
  - Syncs room/anchor parameters with the server over WebSockets.
- **USRP LoRa Bridge** (`usrp_lora_bridge.py`):
  - Receives physical signals using GNU Radio / UHD (RadioConda) on a USRP B200mini, parses packets, and forwards to ZMQ.
- **Serial Bridge** (`serial_bridge.py`):
  - Interfaces with a Feather M0 LoRa receiver over serial and forwards packages to ZMQ.
- **Emitter Mapping Wizard** (`find_emitter_map.py`):
  - Calibrates physical emitter slot mappings by correlation of TDMA slots to corner positions.

---

## 2. Localization Algorithms
The server includes three primary positioning models:
1. **N-Lateration (N-Lat)**: Distance-based multilateration with bounds checking.
2. **K-NN Fingerprinting**: Calibration database lookup comparing current RSSI to fingerprint maps.
3. **HMM Viterbi**: State-transition tracker using physics-based motion profiles to estimate trajectory paths.

*Note: Legacy standalone implementations are placed in [algorithms/](file:///D:/UFR%20STGI/second%20Semester/final%20project/InDoorLora/algorithms).*

---

## 3. Database Schema (SQLite: `indoorlora.db`)
The schema is versioned at **v2** (Campus Mode migration).

### Migration v2 Additions
- `locations`: Added `location_type` (`'indoor_room'` or `'campus_outdoor'`), projection reference point (`campus_lat_ref`, `campus_lng_ref`), bounding box corners, and zoom levels.
- `anchors`: Added GPS coordinates (`lat`, `lng`) and `building` label.

### Main Tables
- `locations`: Root entity of rooms/campuses.
- `anchors`: Physical positions of receivers.
- `grid_points`: Discretized points representing possible receiver positions.
- `calibration_data`: Recorded RSSI fingerprinted points.
- `rssi_log`: Log of received packets.

---

## 4. Key Fixes & Constraints
- **ASCII Safety**: Windows consoles using default codepages (e.g. CP1252) will crash on printing non-ASCII characters. All printed logs across `positioning_server.py`, `lora_rx_sim.py`, `serial_bridge.py`, `find_emitter_map.py`, and `usrp_lora_bridge.py` have been made ASCII-safe (replacing checkmarks, warning icons, division/times signs, arrows, and dashes with standard characters).
- **Process Cleanup**: Ensure ports `5556` and `8765` are freed before launching the server.
- **Unit Tests**: All unit tests in [tests/test_projection.py](file:///D:/UFR%20STGI/second%20Semester/final%20project/InDoorLora/tests/test_projection.py) pass successfully (`13/13 passed`).
