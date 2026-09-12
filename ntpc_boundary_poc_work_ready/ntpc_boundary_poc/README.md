# NTPC Zoning Boundary POC

Vector-first proof of concept for turning a structured New Taipei City planning-boundary description into a road-following polygon.

## Input

See `input.example.json`. The first POC accepts WGS84 latitude/longitude, section name/code, parcel number, four directional road constraints, and a target zoning name.

Composite boundaries are intentionally deferred. If a directional value contains `及`, `與`, `、`, or `和`, only the first explicit road name is used and a warning is recorded in the result metadata.

## Public sources

- Roads: OpenStreetMap Overpass (`https://overpass-api.de/api/interpreter`), no API key.
- Zoning index: New Taipei City Open Data dataset `fe26e0a5-54c2-4876-bbc7-150243c048f5`, no API key. The index CSV points to the current downloadable GIS vector resource.

The zoning data is reference data; legal/official determinations must use the formally promulgated urban-plan documents.

## Run

```bash
python -m src.main --input input.example.json --output output/live
```

Success outputs:

- `result.geojson` — final polygon in WGS84/CRS84
- `debug.geojson` — ROI, roads, barriers, cells, zoning, and final polygon
- `preview.png` — diagnostic rendering with a brown final outline

Failure output:

- `failure.json` — stage, exception class, and explicit diagnostic

## Tests

```bash
python -m pytest -q
```

All geometry operations use EPSG:3826 (TWD97 / TM2 zone 121). Input/output interoperability uses WGS84 where appropriate.

## Strict zoning constraint

The final geometry is always computed as:

```text
selected road-bounded cell ∩ requested zoning polygon
```

For the current POC the requested zoning is `第一種住宅區`. The pipeline writes `constraint_audit.json` and fails if the final geometry has more than 0.01 m² outside the selected zoning geometry.

New presentation output: `boundary_map.png`. It renders the admissible target zoning in yellow and the final road-and-zoning intersection as a thick brown boundary.

## Continue in ChatGPT Work

This repository includes `WORK_HANDOFF.md`. Open ChatGPT Work, attach/import this project, and ask it to follow that handoff. Work is useful for the live probe because it can navigate public web sources while running the multi-step workflow.
