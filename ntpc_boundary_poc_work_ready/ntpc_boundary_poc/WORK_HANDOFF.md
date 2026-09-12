# ChatGPT Work Handoff — NTPC Boundary POC

## Objective

Run and continue this project in ChatGPT Work using its browser/cloud-computer capabilities so the live public sources can be queried directly.

The required business rule is strict:

> The final boundary is **road-constraint candidate ∩ 第一種住宅區**.

A road-bounded area must never be returned if any material part lies outside the requested zoning polygon.

## Current test case

```json
{
  "lat": 24.99384,
  "lon": 121.421118,
  "section": {"name": "樹德段", "code": "1902"},
  "parcel": "284",
  "constraints": {
    "north_of": "樹人街",
    "west_of": "長壽街21巷",
    "south_of": "啟智街14巷",
    "east_of": "樹德街136巷",
    "zone": "第一種住宅區"
  }
}
```

Composite boundary descriptions remain out of scope for this POC. If a field contains a composite phrase, use only the first explicit road name and record a warning.

## Required live workflow in Work

1. Run `pytest -q` first.
2. Run `python -m src.main --input input.example.json --output output/live`.
3. Use the browser only to diagnose/fix public-source access when necessary; do not fabricate missing geometry.
4. Confirm `constraint_audit.json` reports:
   - `target_zone = 第一種住宅區`
   - `zone_constraint_passed = true`
   - `outside_target_zone_area_m2 <= 0.01`
5. Inspect `boundary_map.png` visually against `reference/user_reference_map.png`.
6. If OSM lacks a small lane, add a free/no-key fallback, but keep the zoning hard constraint unchanged.

## Outputs

- `result.geojson`: final GIS boundary
- `debug.geojson`: road/cell/zoning diagnostics
- `preview.png`: engineering debug preview
- `boundary_map.png`: presentation boundary map
- `constraint_audit.json`: machine-verifiable zoning constraint audit

## Data policy

Use only free, anonymous/no-application public sources for the POC. The final result is a planning-data aid, not an authoritative cadastral/legal determination.
