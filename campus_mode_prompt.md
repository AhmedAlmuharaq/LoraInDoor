# ============================================================
# CLAUDE CODE PROMPT — CAMPUS-WIDE MAP MODE
# Extension to InDoorLora_Dashboard.html
# ============================================================
# Paste this into Claude Code as a single message.
# It instructs Claude Code to extend the EXISTING dashboard, not rebuild it.
# ============================================================


CONTEXT
═══════

I have a working InDoorLora_Dashboard.html that handles single-room indoor
positioning. The room view (SVG) works. The OSM Leaflet overlay works for a
single room at a known GPS reference point.

The next stage of my project moves from a single room to a campus-wide
deployment: I will place LoRa anchors (E0, E1, E2, ...) in DIFFERENT
BUILDINGS across the UFR STGI / Mont-Béliard campus. Each anchor has its
own independent GPS position. There is no longer a single "room" with a
local (x,y) coordinate system — the entire campus is the operating area.

You must extend the dashboard to support this campus-wide mode while
PRESERVING the existing single-room mode. Both must coexist.


WHAT EXISTS — DO NOT BREAK
═══════════════════════════

The current dashboard already has:
  - SVG room view (#room-map) with anchors, grid, position dots
  - OSM Leaflet view (#osm-map) overlayed via "cb-osm" checkbox
  - GPS reference inputs (gps-lat, gps-lng)
  - xyToLatLng() conversion (treats room corner as GPS origin)
  - drawRoomOnOSM() draws the room polygon + anchors on the OSM map
  - updateOSM(algo, x, y) moves algorithm markers on the OSM map

  Preserve all of this. The Indoor Room mode must keep working unchanged.


WHAT TO ADD
════════════

1. LOCATION TYPE FIELD
   ─────────────────────
   Add a new column to the locations table: location_type TEXT NOT NULL
   with values:
     'indoor_room'      — current behavior (room with width/height)
     'campus_outdoor'   — new mode: anchors have absolute GPS coordinates
     'hybrid'           — anchors mix indoor and outdoor (future)

   Default: 'indoor_room' (preserves backward compatibility).

   When creating a new location via the dashboard, add a radio button:
     ( ) Indoor Room
     ( ) Campus / Outdoor

   For campus mode, the form should NOT ask for width/height (no fixed room),
   but instead ask for:
     - Campus reference name (e.g. "UFR STGI — Montbéliard")
     - Campus center GPS (lat, lng)
     - Default zoom level (15-19)
     - Bounding box (optional): NE corner + SW corner GPS

2. ANCHOR GPS COORDINATES
   ──────────────────────
   Extend the anchors table with two optional columns:
     lat REAL    NULL,
     lng REAL    NULL,
     building TEXT NULL,
     floor INTEGER NULL

   For indoor_room locations: lat/lng remain NULL, (x,y) is used.
   For campus_outdoor: (x,y) is computed from lat/lng using a chosen
     local projection (see point 4), and the anchor row must have
     lat/lng filled in.

   The dashboard must let me click on the map to place an anchor at
   that exact GPS spot, and let me drag anchors to reposition them.

3. CAMPUS MAP VIEW
   ────────────────
   When the active location is location_type='campus_outdoor':
     - The SVG room view is HIDDEN by default
     - The Leaflet map is the primary view
     - Default tile layer: OpenStreetMap
     - Add a layer switcher (Leaflet's L.control.layers) with:
         * OSM Standard
         * OSM Humanitarian
         * Esri World Imagery (satellite)
         * CartoDB Dark Matter (dark, matches dashboard theme)
       (All free, no API key needed)
     - The map fits to the campus bounding box on first load.

4. PROJECTION FOR CAMPUS MODE
   ────────────────────────────
   Indoor mode uses (x,y) in meters, with origin at room corner.

   Campus mode uses GPS (lat, lng) DIRECTLY for storage. For the algorithms
   (N-Lateration, Fingerprinting, HMM), distances must still be computed
   in meters. Therefore:

     - For each campus location, define a local tangent-plane projection
       centered at the campus reference GPS.
     - For each anchor (lat_i, lng_i), compute the local (x_i, y_i) in
       meters relative to the reference:
            x_i = (lng_i - lng_ref) * 111111 * cos(lat_ref * π/180)
            y_i = (lat_i - lat_ref) * 111111
     - All algorithm internals run in meters.
     - For display, convert back: lat = lat_ref + y/111111,
                                  lng = lng_ref + x/(111111*cos(lat_ref))

   Wrap this in a single class CampusProjection(lat_ref, lng_ref) with:
     to_local(lat, lng) -> (x_m, y_m)
     to_gps(x_m, y_m)  -> (lat, lng)

5. CAMPUS ANCHOR PLACEMENT UI
   ──────────────────────────
   When in campus mode, the sidebar's ANCHORS section changes:
     - Each anchor row shows: label, building, floor, lat, lng (read-only)
     - "+ Add Anchor" button: clicking it puts the map into "placement mode"
       (cursor becomes a crosshair). Next click on the map adds an anchor
       there. A modal asks for label, building name, floor.
     - "Edit" button per anchor: opens a popup over its marker with editable
       building, floor, label. Drag the marker to update lat/lng.
     - "Delete" button per anchor: removes it from the location.

   All changes go through WebSocket → server → SQLite with conn.commit().
   Broadcast anchors_update so the simulator (or real receiver pipeline)
   sees the new positions immediately.

6. CAMPUS GRID & FINGERPRINTING
   ─────────────────────────────
   Indoor mode has a regular grid of points (auto-generated).
   Campus mode does NOT have a regular grid — calibration points are
   wherever you actually walk and stop.

   Add a new flow:
     - "Add Calibration Point at My Location": user clicks on the map
       where they currently stand, server creates a grid_point at that
       GPS, with auto-label "C001", "C002", etc.
     - Calibration proceeds as before, but the point's coordinates are
       stored as both (lat, lng) and (x, y) using the projection.

   For the radio map building, treat campus calibration points exactly
   like indoor grid points — same K-NN, same HMM. The math is identical
   in meter space.

7. CAMPUS VIEW VISUAL TREATMENT
   ─────────────────────────────
   Anchors on the map:
     - Different ICON per building (small numbered amber circle inside
       a colored ring per building — assign colors by building name)
     - Label tooltip ALWAYS visible (not just on hover) showing
       "E0 · Building X · Floor 2"
     - Click on anchor opens its calibration history (how many points
       were calibrated against it, mean RSSI, etc.)

   Calibration points on the map:
     - Small green dot (matches --calibrated CSS variable)
     - Hover: shows label + calibration sample count
     - Click: focuses on it (option to recalibrate)

   Live position markers (3 algorithms):
     - Same colors as indoor: red (N-Lat), purple (FP), green (HMM)
     - Each as a small dot with a fading trail (last 30 positions)
     - The position dot's accuracy is shown via a semi-transparent
       circle around it whose radius equals current estimated error
       (if ground truth available) or current uncertainty estimate.

8. CAMPUS HEATMAP
   ─────────────
   Add a heatmap layer toggle in the Leaflet layer control:
     "Show RSSI heatmap for E0 ▼"

   Implementation: for each calibration point, draw a Leaflet circle
   with radius proportional to RSSI strength and color from a blue-to-red
   gradient. This visualizes coverage of a chosen anchor across the campus.

   Use leaflet.heat plugin (free, single JS file from CDN):
     <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>

9. CAMPUS-SPECIFIC PRESETS
   ────────────────────────
   Add a "Quick Setup" dropdown for known campus references:

     UFR STGI — Montbéliard:    47.5108, 6.7965    (default)
     Fort du Mont-Bart:         47.4929, 6.8214
     FEMTO-ST Belfort:          47.6379, 6.8636

   Selecting one auto-fills the campus reference GPS field.

10. PERSISTENCE FOR CAMPUS MODE
    ────────────────────────────
    All campus-mode data is stored identically to indoor data, just with
    the location_type and lat/lng fields populated:

      INSERT INTO locations(name, location_type, width, height, grid_step,
                            campus_lat_ref, campus_lng_ref, ...)
      INSERT INTO anchors(location_id, anchor_label, x, y, lat, lng,
                          building, floor, ...)
      INSERT INTO grid_points(location_id, label, x, y, lat, lng, ...)

    Add migration: ALTER TABLE statements that add the new columns to
    existing tables without dropping data.

    Every write followed by conn.commit() — same rule as before.
    On WebSocket connect, send the full campus state so F5 refresh is safe.

11. EXISTING USRP / GR-LORA / SIMULATOR COMPATIBILITY
    ──────────────────────────────────────────────────
    The ZMQ PDU format does not change. The packet's payload still contains
    the anchor label. The server still computes RSSI vectors and runs
    algorithms in meter space. The only difference is that for campus
    locations, the (x,y) the server outputs is converted to (lat,lng)
    before broadcast to the dashboard.

    Add a flag in the position_update WebSocket message:
      {"type": "position_update",
       "mode": "indoor" | "campus",
       "nlat": {"x": 12.3, "y": 45.6, "lat": 47.5108, "lng": 6.7965},
       ...}

    The dashboard chooses which coordinate to render based on the
    location's mode.


DELIVERABLES
═════════════

After this work, I expect these files modified:

  InDoorLora_Dashboard.html
    - Campus mode UI added (radio button in Create Location modal)
    - Layer switcher in Leaflet view (4 tile layers + heatmap)
    - Anchor placement-by-click flow
    - Sidebar shows building/floor info per anchor in campus mode
    - View auto-switches based on location_type

  positioning_server.py
    - Database migration adds new columns
    - CampusProjection class added
    - WebSocket handlers for new actions:
        action: 'set_campus_reference'
        action: 'add_anchor_at_gps'
        action: 'move_anchor_to_gps'
        action: 'add_campus_calibration_point'
    - position_update message includes 'mode' and 'lat/lng' fields when applicable

  database/schema.sql
    - Updated with new columns and constraints

  docs/05_campus_mode.md (NEW)
    - Explains the projection math, the dual-mode design, the migration path
    - Suitable for inclusion in the IPIN paper's system section


VALIDATION CHECKLIST
═════════════════════

  [ ] Existing M2 Classroom indoor location still works unchanged.
      No regression in single-room mode.

  [ ] Create a new campus location "UFR STGI Outdoor Test":
      campus mode, ref GPS = 47.5108, 6.7965, zoom = 17.
      Verify the Leaflet view opens at that location.

  [ ] Place 4 anchors by clicking on the map at known buildings.
      Each anchor stores correct lat/lng.

  [ ] Switch tile layer to satellite. Anchors stay in correct positions.

  [ ] Disconnect and reconnect WebSocket: all anchors reload from DB.
      F5 reload: same.

  [ ] Run lora_rx_sim.py against the campus location:
      simulator receives anchors_update with lat/lng,
      computes RSSI from a simulated walking path in campus coordinates,
      positions appear on the Leaflet view.

  [ ] Add a calibration point at your "current location" on the map.
      Run a 60-packet calibration. Point turns green.

  [ ] After 3 calibrated points, the FP and HMM markers start appearing
      on the campus map. Indoor mode same.

  [ ] Heatmap toggle for E0 shows blue-to-red overlay across calibrated
      points.

  [ ] Export the campus location as JSON. Import it into a clean install:
      everything reappears including all calibration data.


START NOW
═════════

Step 1: Read InDoorLora_Dashboard.html and positioning_server.py.
        Confirm the existing OSM integration is what I described above.

Step 2: Show me the database migration SQL. Wait for my approval.

Step 3: Implement the changes file by file, smallest first.

Step 4: After each file, demonstrate that the indoor mode still works.

Step 5: Test the campus mode with the simulator generating a walking
        path across the campus reference area.

Do not break the indoor mode at any point. If you must refactor shared
code, write a test first proving indoor mode still passes.
