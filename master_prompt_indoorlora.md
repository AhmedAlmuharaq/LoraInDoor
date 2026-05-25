# ============================================================
# CLAUDE CODE — MASTER PROMPT — InDoorLora
# Academic-Grade Research Engineering Audit + Rebuild
# ============================================================
# Paste this ENTIRE document into Claude Code as a single message.
# It instructs Claude Code to:
#   (1) Restudy the project from scratch with academic rigor
#   (2) Produce a written audit report BEFORE coding
#   (3) Rebuild the system as a flexible, room-agnostic platform
#   (4) Generate IEEE-paper-grade documentation for every component
# ============================================================


You are acting as a senior research engineer and software architect.
Your client is a Master's student building an indoor LoRa positioning system
for a Final Defense + IEEE conference paper (IPIN 2026, deadline 10 May 2026).

Project name:    InDoorLora
Project folder:  D:\UFR STGI\second Semester\final project\InDoorLora
Reference only:  D:\UFR STGI\second Semester\final project\ROOMTEST  (do NOT inherit, only consult)

This is NOT a casual rewrite. The student will submit an IEEE conference paper
based on this system. Every design choice you make must be:
  - Scientifically defensible
  - Traceable to peer-reviewed literature
  - Reproducible
  - Documented in a form that can be cited in the paper

──────────────────────────────────────────────────────────────
PART 1 — DO NOT WRITE ANY CODE YET
──────────────────────────────────────────────────────────────

Before changing or producing any code, you must perform a research audit.

Your first deliverable is a single Markdown file:

    InDoorLora/docs/00_research_audit.md

This file must answer ALL of the following questions with academic depth.
Each answer must cite at least one peer-reviewed source from 2022–2025
(give author, year, journal/conference, and a one-line finding).

  1.  What is the formal problem statement of indoor LoRa positioning?
      Define the input (RF measurements), the output (a position estimate
      in a 2D coordinate frame), and the optimization objective.

  2.  What measurable quantities does a LoRa packet carry that are useful
      for positioning? List each (RSSI, SNR, ToA, CFO, SF, CRC status,
      packet sequence) with its physical meaning, expected variance, and
      reliability indoor.

  3.  Is RSSI alone sufficient, or must SNR / temporal features / packet-
      quality features be fused? Give a quantitative justification from
      the literature (e.g. Islam et al. 2023 reported RSSI+SNR fusion
      reduced trilateration error by 26.58% over RSSI-only).

  4.  What are the realistic accuracy bounds for a 4-anchor RSSI-only LoRa
      system in a room-scale indoor environment (5–15 m diagonal)?
      Distinguish:
        - best case (LOS, fingerprinted, calibrated)
        - typical case (mixed LOS/NLOS, partially calibrated)
        - worst case (NLOS, uncalibrated)

  5.  For each of the following algorithms, state whether you recommend
      it as (a) the main method, (b) a baseline, or (c) reject. Justify
      each verdict in one paragraph.
        - Closed-form RSSI trilateration
        - Iterative RSSI N-Lateration (nonlinear least squares)
        - Weighted Centroid Localization (WCL)
        - K-NN Fingerprinting (uniform weights)
        - Weighted K-NN / IDW Fingerprinting
        - Random Forest on fingerprints
        - Kalman / Extended Kalman tracking
        - Hidden Markov Model with Viterbi decoding
        - Particle Filter
        - Graph-based methods (Dijkstra on connectivity)

  6.  What MUST be calibrated for fingerprinting to work, and what should
      NOT be calibrated (overfitting risk)?

  7.  How should the offline radio map be built? Specify:
        - Sample count per grid point (with statistical justification)
        - Outlier handling
        - Missing-anchor handling (e.g. one emitter not received at a point)
        - Temporal stability (does the radio map degrade over hours/days?)

  8.  What data MUST be stored persistently? Distinguish:
        - Configuration (locations, anchors, grid)
        - Calibration evidence (every raw RSSI sample with timestamp)
        - Live operational logs (RSSI + estimated position)
        - Derived artifacts (radio map mean/std)
      For each, justify why it must persist.

  9.  What should a real-time dashboard show during a live demonstration
      in front of a defense jury? List the minimum-viable visualizations
      and the "nice-to-have" ones. Justify each visualization in terms of
      what scientific claim it supports.

  10. What was wrong, weak, or scientifically risky in the previous
      ROOMTEST implementation? Read the ROOMTEST folder if it exists.
      Be specific. Examples of risks to look for:
        - Hard-coded room dimensions (anti-pattern: not room-agnostic)
        - Missing conn.commit() (data loss on refresh/restart)
        - Algorithms run without calibration (silent failure mode)
        - No raw-sample preservation (irreproducibility)
        - UI freezes on WebSocket reconnect
        - No simulation/real-hardware switch

  11. What architecture would you choose if you rebuilt this cleanly?
      Draw an ASCII data-flow diagram showing:
        - LoRa packet source (simulator OR USRP+gr-lora_sdr OR serial)
        - ZMQ ingestion
        - Persistence layer
        - Algorithm pipeline (per-packet AND per-time-step)
        - WebSocket broadcast
        - Browser dashboard
      Explain why each boundary is where it is.

  12. What are the IEEE-paper-grade evaluation metrics you will need to
      produce? List each with its mathematical definition:
        - Mean Absolute Error (MAE)
        - Root Mean Square Error (RMSE)
        - Median error
        - 95th percentile error
        - Cumulative Distribution Function (CDF) of error
        - Per-region error (heatmap)
        - Trajectory smoothness (for tracking)
      For each, say which figure in the paper it will appear in.

──────────────────────────────────────────────────────────────
PART 2 — DESIGN DOCUMENT (still no code)
──────────────────────────────────────────────────────────────

After 00_research_audit.md, produce:

    InDoorLora/docs/01_system_design.md

This must contain:

  A.  System Architecture
      ──────────────────
      ASCII diagram of all components and their interfaces.
      Each component gets a one-paragraph description.

  B.  Database Schema (SQLite)
      ────────────────────────
      Complete CREATE TABLE statements with FOREIGN KEY constraints,
      INDEXes, and a one-line comment per column explaining its role.

      MANDATORY TABLES (you may add more if justified):
        locations(id, name, width, height, grid_step, created_at,
                  is_active, notes)
        anchors(id, location_id, anchor_label, x, y,
                tx_power_dbm, frequency_hz, sf, bandwidth_hz)
        grid_points(id, location_id, label, x, y, created_at)
        calibration_samples(id, location_id, grid_point_id, anchor_label,
                            rssi, snr, sequence, received_at)
        radio_map(id, location_id, grid_point_id, anchor_label,
                  rssi_mean, rssi_std, snr_mean, sample_count,
                  updated_at)
        live_rssi_log(id, location_id, anchor_label, rssi, snr,
                      sequence, est_x_nlat, est_y_nlat,
                      est_x_fp, est_y_fp, est_x_hmm, est_y_hmm,
                      ground_truth_x, ground_truth_y, received_at)
        sessions(id, location_id, started_at, ended_at, notes)

      KEY DESIGN RULES (justify each):
        - Every WRITE is wrapped in a transaction and followed by commit
        - calibration_samples keeps EVERY raw sample (for paper reproducibility)
        - radio_map is a derived view, rebuildable from calibration_samples
        - live_rssi_log stores all 3 algorithm estimates side-by-side
        - sessions table supports the IEEE paper's experimental campaigns

  C.  WebSocket API Contract
      ──────────────────────
      Complete list of message types in BOTH directions, with example
      payloads. Use this exact format for each message:

        ─────────────────────────────────────
        Message:   <message_type>
        Direction: client → server  OR  server → broadcast
        Trigger:   <when this fires>
        Payload:   <JSON example>
        Effect:    <what changes>
        ─────────────────────────────────────

  D.  ZMQ Packet Format
      ─────────────────
      Document the existing PDU format from lora_rx_sim.py exactly:
        [4-byte big-endian meta_len][meta_json][4-byte big-endian payload_len][payload_string]
        meta   = {"rssi": float, "snr": float (optional), "samp_rate": int}
        payload = "UE07,13,<anchor_label>,<sequence>"

      Specify: this format is REQUIRED for backward compatibility with
      both the simulator and the future GNU Radio receiver.

  E.  Algorithm Specifications
      ────────────────────────
      For each chosen algorithm, write a self-contained pseudo-code
      block with:
        - Inputs (with types and units)
        - Pre-conditions (e.g. "requires calibrated radio map")
        - Output (position estimate, OR None if pre-conditions unmet)
        - Time complexity
        - Reference (author, year)

      Mandatory algorithms (you may add more):
        1. RSSI → distance via log-distance path-loss model
           d_hat = 10^((A - RSSI) / (10 * n))
           Document A and n as PER-ANCHOR calibrated parameters,
           NOT global constants.

        2. N-Lateration: nonlinear least squares solving
           p* = argmin_p Σ_i (||p - p_i|| - d_hat_i)^2
           with box constraints [0, width] × [0, height].

        3. Weighted K-NN Fingerprinting (IDW)
           Distance metric: 4D Euclidean in RSSI space
           K = 3 by default, configurable
           Weights: w_i = 1 / (d_rssi_i + ε)
           Output: weighted mean of K neighbor positions

        4. HMM-Viterbi Tracking
           States: calibrated grid points only
           Transition: A[i,j] ∝ exp(-||p_i - p_j|| / σ_motion)
                       + β * I(i=j)   (self-transition boost)
           Emission: log p(rssi_obs | state_i) = sum over anchors of
                     Gaussian log-likelihood with mean=radio_map.rssi_mean,
                     std=max(radio_map.rssi_std, σ_min)
           Decoding: online forward Viterbi (no backtracking)

      Each algorithm must declare its UNCALIBRATED behavior:
        - N-Lateration: works without calibration but with literature
          default n=2.2, A=-42 dBm. Report this in dashboard as
          "uncalibrated mode".
        - K-NN Fingerprinting: returns None if radio_map is empty.
        - HMM: returns None if fewer than 3 grid points calibrated.

      The dashboard must visually distinguish "running" from "no data".

  F.  Calibration Protocol
      ────────────────────
      Step-by-step procedure for collecting calibration data, including:
        - Why 30–60 samples per (grid_point, anchor) pair
          (cite statistical reasoning, e.g. central limit theorem,
          shadowing variance studies)
        - Outlier rejection (median absolute deviation, threshold 3·MAD)
        - Handling missing anchors at a grid point
        - When to re-calibrate (e.g. after furniture move)
        - How calibration data feeds into the radio_map table
          (atomically, with conn.commit() after the batch)

──────────────────────────────────────────────────────────────
PART 3 — IMPLEMENTATION
──────────────────────────────────────────────────────────────

Only AFTER 00_research_audit.md and 01_system_design.md are written
and saved to disk, begin implementation.

The implementation MUST satisfy these flexibility constraints:

  ROOM-AGNOSTIC
  ─────────────
  - No room dimensions are hard-coded anywhere.
  - Width, height, grid_step, anchor count, and anchor positions are
    runtime parameters loaded from the database.
  - The system must support rooms from 2×2 m to 50×50 m.
  - The system must support 3 to 8 anchors (you may default to 4 but
    do NOT assume 4).
  - The dashboard's SVG room view must redraw automatically when the
    active location changes.

  HARDWARE-AGNOSTIC
  ─────────────────
  - The same backend processes packets from:
      (i) lora_rx_sim.py (existing simulator)
     (ii) gr-lora_sdr via ZMQ (production USRP + GNU Radio receiver)
    (iii) a future serial reader for Feather M0 LoRa boards
  - All three sources publish identical ZMQ PDU format.

  DATABASE-FIRST
  ──────────────
  - Application state never lives only in RAM.
  - Restart of the backend re-derives all live state from SQLite.
  - Browser F5 reload re-fetches everything via WebSocket and resumes.

  EXTENSIBILITY
  ─────────────
  - Adding a new algorithm = adding one Python class in algorithms/
    that implements estimate(rssi_vec) -> (x, y) or None.
  - Adding a new metric = adding one row to live_rssi_log columns +
    one entry in the dashboard's metrics panel.
  - Adding a new visualization = adding one component file in the
    dashboard's frontend layer.

──────────────────────────────────────────────────────────────
PART 4 — FILES TO PRODUCE
──────────────────────────────────────────────────────────────

InDoorLora/
├── docs/
│   ├── 00_research_audit.md        ← Part 1 deliverable
│   ├── 01_system_design.md         ← Part 2 deliverable
│   ├── 02_calibration_protocol.md  ← step-by-step lab procedure
│   ├── 03_algorithm_derivations.md ← full math derivations for paper
│   ├── 04_evaluation_protocol.md   ← how to run the experiments
│   └── refs.bib                    ← BibTeX bibliography for paper
├── positioning_server.py           ← main server
├── algorithms/
│   ├── __init__.py
│   ├── base.py                     ← abstract Algorithm class
│   ├── nlat.py                     ← N-Lateration
│   ├── fingerprint.py              ← Weighted K-NN
│   ├── hmm.py                      ← HMM + Viterbi
│   └── pathloss.py                 ← shared path-loss model + calibration
├── database/
│   ├── schema.sql                  ← full schema with comments
│   ├── migrations/                 ← future schema changes
│   └── seed_default.sql            ← optional example location (NOT auto-loaded)
├── lora_rx_sim.py                  ← already exists, keep + improve
├── dashboard/
│   └── index.html                  ← single-file SPA dashboard
├── tools/
│   ├── export_calibration.py       ← dump DB → JSON for paper
│   ├── replay_session.py           ← replay a logged session
│   └── evaluate.py                 ← compute all paper metrics
├── tests/
│   ├── test_nlat.py
│   ├── test_fingerprint.py
│   ├── test_hmm.py
│   └── test_persistence.py
├── README.md                       ← quickstart for grader/reader
└── requirements.txt

──────────────────────────────────────────────────────────────
PART 5 — DASHBOARD DESIGN
──────────────────────────────────────────────────────────────

The dashboard is dashboard/index.html — a single file with embedded
CSS and JavaScript. No frameworks (no React, no Vue). Pure web standards.

Visual identity:
  - Design language: "engineering instrument panel" — precise,
    data-dense, sober. Inspired by professional scientific instrument
    UIs (oscilloscopes, spectrum analyzers).
  - Dark theme by default, light theme toggle available.
  - Typography: 'IBM Plex Mono' for numeric data, 'IBM Plex Sans'
    for UI labels (Google Fonts CDN).
  - Color palette via CSS variables, semantic naming
    (--algo-nlat, --algo-fp, --algo-hmm, --signal-strong, etc).

Layout (CSS Grid):

  ┌──────────────────────────────────────────────────────────┐
  │  TOP BAR: project name, active location, status pills    │
  ├──────────────┬───────────────────────────────────────────┤
  │              │                                            │
  │  SIDEBAR     │  MAIN VIEW                                 │
  │  ─────────   │  ─────────                                 │
  │              │                                            │
  │  ▸ LOCATIONS │  ┌──────────────────────────────────────┐ │
  │   [select]   │  │                                       │ │
  │   + Create   │  │  ROOM SVG (scales to any dimensions) │ │
  │              │  │  - axes with meter ticks             │ │
  │  ▸ ROOM      │  │  - room outline                       │ │
  │   W×H        │  │  - anchors (squares)                 │ │
  │              │  │  - grid points (dots)                │ │
  │  ▸ ANCHORS   │  │  - calibrated points (green)         │ │
  │   E0-EN list │  │  - 3 algorithm position dots         │ │
  │              │  │  - optional heatmap layer            │ │
  │  ▸ GRID      │  │  - trail (last N positions)          │ │
  │   step + cnt │  │                                       │ │
  │              │  └──────────────────────────────────────┘ │
  │  ▸ CALIBR    │                                            │
  │   point list │  ┌──RSSI──┐ ┌──METRICS──┐ ┌──LOG──┐      │
  │   progress   │  │ E0 bar │ │ N-Lat MAE │ │ tail  │      │
  │              │  │ E1 bar │ │ FP    MAE │ │ of    │      │
  │  ▸ SESSION   │  │ E2 bar │ │ HMM   MAE │ │ pkts  │      │
  │   start/stop │  │ E3 bar │ │           │ │       │      │
  │   record     │  └────────┘ └───────────┘ └───────┘      │
  │              │                                            │
  │  ▸ ALGORITHM │                                            │
  │   toggles    │                                            │
  │              │                                            │
  │  ▸ DEBUG     │                                            │
  │   pkt rate   │                                            │
  │   WS state   │                                            │
  └──────────────┴───────────────────────────────────────────┘

Sidebar sections are collapsible. State persisted in localStorage.

Create-Location modal MUST collect:
  - Name (text)
  - Width and Height (meters, float)
  - Grid step (meters, float, default 0.5)
  - Number of anchors (3–8, default 4)
  - Per-anchor: label, x, y, tx_power, frequency, SF, bandwidth
  - Optional: notes
  - "Auto-place anchors at corners" helper button

On location switch:
  - Send set_active to server
  - Server responds with full active_location payload
  - Dashboard wipes all state and redraws
  - Toast: "✓ Switched to <name> (<width>×<height> m, <N> grid points)"

Hover behavior:
  - Hover over grid point: tooltip with label, coords, calibration status
  - Hover over anchor: tooltip with label, coords, last RSSI received
  - Hover over algorithm dot: tooltip with method, position, error if GT set

Click behavior:
  - Click empty spot in room: set ground truth marker for live error
  - Click grid point: select it for calibration
  - Click anchor: open anchor edit popover
  - Click + drag anchor: move it (with confirmation modal)

──────────────────────────────────────────────────────────────
PART 6 — PAPER-GRADE OUTPUTS
──────────────────────────────────────────────────────────────

The system must produce, on demand, IEEE-paper-grade artifacts:

  Figure 1 (system architecture):
    Generated from docs/01_system_design.md ASCII diagram.
    Saved as figures/01_architecture.pdf via a build script.

  Figure 2 (room layout):
    Exported from dashboard SVG with anchor/grid annotations.
    Saved as figures/02_room.pdf.

  Figure 3 (path-loss fits per anchor):
    Generated by tools/evaluate.py from calibration_samples.
    Saved as figures/03_pathloss.pdf.

  Figure 4 (CDF of error per algorithm):
    Generated by tools/evaluate.py from a session's live_rssi_log
    + ground_truth.
    Saved as figures/04_cdf.pdf.

  Figure 5 (heatmap of error over the room):
    Spatial interpolation of per-grid-point error.
    Saved as figures/05_heatmap.pdf.

  Figure 6 (trajectory tracking comparison):
    For a recorded walking session, overlay ground truth and the
    three algorithm trajectories.
    Saved as figures/06_trajectory.pdf.

  Table I (numerical comparison):
    Generated as CSV → LaTeX by tools/evaluate.py.
    Columns: method, MAE, RMSE, median, p95, samples.

All plots use matplotlib with the IEEE conference style:
  - Font: serif, 9pt
  - Line widths: 1–1.5pt
  - Grayscale-safe colors (still distinguishable in B&W print)
  - Tight layout, no titles (captions live in the LaTeX paper)

──────────────────────────────────────────────────────────────
PART 7 — VALIDATION CHECKLIST
──────────────────────────────────────────────────────────────

Before declaring the system complete, verify:

  [ ] Create a new location "TestRoom 4x3" (4m × 3m, 3 anchors).
      System accepts non-default dimensions.

  [ ] Create a new location "BigHall 15x12" (15m × 12m, 6 anchors).
      System accepts non-default anchor count.

  [ ] Backend restart preserves all locations, anchors, calibrations.
      Browser refresh (F5) reloads complete state via WebSocket.

  [ ] Run lora_rx_sim.py against the default location: position
      estimates appear on dashboard.

  [ ] Switch to a different active location while simulator running:
      simulator receives anchors_update and adapts.

  [ ] Calibrate 5 grid points: fingerprinting starts working,
      HMM activates after the 3rd point.

  [ ] Set a ground-truth marker on the map: live error metrics update.

  [ ] Export session as CSV: all RSSI + algorithm estimates present.

  [ ] Run tools/evaluate.py on the exported session:
      produces all 6 figures + Table I CSV.

  [ ] Read docs/00_research_audit.md aloud — it sounds like a
      research paper section, not like ChatGPT marketing copy.

──────────────────────────────────────────────────────────────
PART 8 — RULES OF ENGAGEMENT
──────────────────────────────────────────────────────────────

1. If you find that some feature in the user's previous code is
   scientifically unjustified, REMOVE it. Do not preserve it just to
   please the user.

2. If you find that some new feature would significantly improve the
   paper's defensibility, ADD it and justify it in docs/.

3. Cite all literature in IEEE format. Maintain refs.bib.

4. Use exact units everywhere (meters, dBm, dB, Hz, seconds).
   Never use ambiguous numbers.

5. Write code that a peer reviewer could read and understand without
   running it. Use long names, docstrings, and type hints.

6. Tests are not optional. tests/ must pass with `pytest`.

7. README.md must let a grader run the full system in under 5 minutes.

──────────────────────────────────────────────────────────────
START NOW
──────────────────────────────────────────────────────────────

Step 1: Read existing files in InDoorLora/ and ROOMTEST/.
Step 2: Write InDoorLora/docs/00_research_audit.md.
Step 3: Show me the audit. Wait for my approval.
Step 4: Write InDoorLora/docs/01_system_design.md. Wait for approval.
Step 5: Begin implementation, file by file, smallest first.
Step 6: After each file, run its tests. Do not proceed until green.

Do not skip steps. Do not produce code before producing the audit.
