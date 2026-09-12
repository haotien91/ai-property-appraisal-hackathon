# NTPC Zoning Boundary POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable Python POC that accepts structured location/road/zoning input, resolves four named road geometries from free anonymous sources, combines them with New Taipei City public zoning geometry, and outputs a road-following GeoJSON polygon plus diagnostic artifacts.

**Architecture:** The POC is vector-first. It converts the anchor to EPSG:3826, queries and resolves named roads inside a bounded ROI, builds robust road barriers, polygonizes/selects the anchor-consistent cell, intersects that cell with the requested zoning polygon, validates the result, and writes GeoJSON/PNG outputs. Composite boundary phrases are explicitly out of scope for this first implementation; each directional field resolves only its first explicit road name.

**Tech Stack:** Python 3.11+, requests, pyproj, shapely, geopandas, pandas, matplotlib, pytest. Optional OSMnx is avoided so Overpass payloads stay explicit and testable.

**Spec:** `/mnt/data/docs/superpowers/specs/2026-09-12-ntpc-zoning-boundary-poc-design.md`

## Global Constraints

- All external data used by the POC must be free to access and require no API key or prior application.
- Input anchor is EPSG:4326; all buffers, distances, topology, and partitioning operate in EPSG:3826.
- Road geometry is vector-first; OCR/SAM/VLM are not primary boundary sources.
- The POC must not infer the final polygon by connecting four manually selected corner points.
- Composite boundaries are deferred; if a field contains multiple phrases, resolve only the first explicit road name and record a diagnostic warning.
- Fail explicitly when a required road or zoning geometry cannot be resolved; never fabricate geometry.

---

### Task 1: Project skeleton, input model, and normalization

**Files:**
- Create: `/mnt/data/ntpc_boundary_poc/requirements.txt`
- Create: `/mnt/data/ntpc_boundary_poc/input.example.json`
- Create: `/mnt/data/ntpc_boundary_poc/src/__init__.py`
- Create: `/mnt/data/ntpc_boundary_poc/src/models.py`
- Create: `/mnt/data/ntpc_boundary_poc/src/normalize.py`
- Test: `/mnt/data/ntpc_boundary_poc/tests/test_normalize.py`

**Interfaces:**
- Produces: `load_case(path: str) -> BoundaryCase`
- Produces: `normalize_name(text: str) -> str`
- Produces: `first_explicit_road(text: str) -> tuple[str, list[str]]`
- Produces: immutable dataclasses `BoundaryCase`, `SectionInfo`, `Constraints`

- [ ] **Step 1: Write failing tests for road-name normalization and first-road extraction**

```python
from src.normalize import normalize_name, first_explicit_road


def test_normalize_name_removes_spaces_and_fullwidth_punctuation():
    assert normalize_name(" 長壽街 21 巷，") == "長壽街21巷"


def test_first_explicit_road_keeps_first_named_road_and_warns_on_composite():
    road, warnings = first_explicit_road("啟智街及未開闢計畫道路")
    assert road == "啟智街"
    assert warnings
```

- [ ] **Step 2: Run the tests and verify they fail because the module does not exist**

Run: `cd /mnt/data/ntpc_boundary_poc && pytest tests/test_normalize.py -v`

Expected: collection/import failure for `src.normalize`.

- [ ] **Step 3: Implement minimal normalization and dataclass loading**

`models.py` defines the exact JSON contract. `normalize.py` strips whitespace and common Chinese punctuation; `first_explicit_road()` splits only on composite conjunctions such as `及`, `與`, `、` and emits a warning when content is discarded.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `cd /mnt/data/ntpc_boundary_poc && pytest tests/test_normalize.py -v`

Expected: PASS.

---

### Task 2: CRS and ROI utilities

**Files:**
- Create: `/mnt/data/ntpc_boundary_poc/src/crs.py`
- Test: `/mnt/data/ntpc_boundary_poc/tests/test_crs.py`

**Interfaces:**
- Produces: `anchor_3826(lat: float, lon: float) -> shapely.geometry.Point`
- Produces: `roi_square(anchor: Point, size_m: float = 600.0) -> Polygon`
- Produces: `to_4326(geometry) -> geometry`

- [ ] **Step 1: Write failing CRS tests**

```python
from src.crs import anchor_3826, roi_square


def test_anchor_and_roi_are_metric():
    p = anchor_3826(24.99384, 121.421118)
    roi = roi_square(p, 600)
    assert 359000 < roi.area < 361000
    assert roi.contains(p)
```

- [ ] **Step 2: Run and observe import failure**

Run: `pytest tests/test_crs.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement EPSG:4326 ↔ EPSG:3826 transformers and square ROI**

Use `pyproj.Transformer(..., always_xy=True)` and `shapely.ops.transform`.

- [ ] **Step 4: Run tests**

Expected: PASS.

---

### Task 3: Overpass road acquisition and deterministic road matching

**Files:**
- Create: `/mnt/data/ntpc_boundary_poc/src/roads.py`
- Test: `/mnt/data/ntpc_boundary_poc/tests/test_roads.py`

**Interfaces:**
- Produces: `build_overpass_query(lat: float, lon: float, radius_m: int, names: list[str]) -> str`
- Produces: `parse_overpass_ways(payload: dict) -> list[RoadFeature]`
- Produces: `resolve_road(features: list[RoadFeature], requested: str) -> RoadFeature`
- Produces: `fetch_roads(...)` as the only network-facing road function.

- [ ] **Step 1: Write tests against a fixed synthetic Overpass payload**

```python
from src.roads import parse_overpass_ways, resolve_road


def test_resolve_exact_named_road(sample_overpass_payload):
    roads = parse_overpass_ways(sample_overpass_payload)
    road = resolve_road(roads, "樹人街")
    assert road.name == "樹人街"
    assert road.geometry.length > 0
```

- [ ] **Step 2: Run and verify failure**

Run: `pytest tests/test_roads.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement request building, geometry parsing, normalization, and connected-piece merging**

The Overpass query must request ways with `highway` and `name` inside a radius around the anchor and request `out geom;`. Exact normalized matches are accepted; ambiguous multiple non-touching groups raise `RoadAmbiguousError`.

- [ ] **Step 4: Run tests**

Expected: PASS.

---

### Task 4: Zoning-source adapter and local cache contract

**Files:**
- Create: `/mnt/data/ntpc_boundary_poc/src/zoning.py`
- Create: `/mnt/data/ntpc_boundary_poc/src/config.py`
- Test: `/mnt/data/ntpc_boundary_poc/tests/test_zoning.py`

**Interfaces:**
- Produces: `discover_ntpc_zoning_download() -> ZoningSource`
- Produces: `load_zoning(source: ZoningSource, roi_3826: Polygon) -> GeoDataFrame`
- Produces: `select_zone(gdf, requested_name: str) -> GeoDataFrame`

- [ ] **Step 1: Write tests using a tiny generated GeoJSON fixture**

Verify CRS reprojection, clipping to ROI, and normalized zone-name selection.

- [ ] **Step 2: Run and verify failure**

Run: `pytest tests/test_zoning.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement a source adapter that supports direct GeoJSON/GPKG/SHP/ZIP URLs and a cached local path**

The source discovery function must be isolated from geometry loading so public-data URL changes affect one function only. The loader must never infer zoning from raster colors.

- [ ] **Step 4: Run tests**

Expected: PASS.

---

### Task 5: Barrier partitioning and candidate selection

**Files:**
- Create: `/mnt/data/ntpc_boundary_poc/src/partition.py`
- Test: `/mnt/data/ntpc_boundary_poc/tests/test_partition.py`

**Interfaces:**
- Produces: `build_barriers(roads: dict[str, BaseGeometry], width_m: float) -> BaseGeometry`
- Produces: `partition_roi(roi: Polygon, barriers: BaseGeometry) -> list[Polygon]`
- Produces: `select_candidate(cells, anchor, directional_roads) -> Polygon`

- [ ] **Step 1: Write a synthetic rectangular-road test**

Create four line geometries around an anchor and assert the selected cell contains the anchor and lies on the requested side of each boundary road.

- [ ] **Step 2: Run and verify failure**

Run: `pytest tests/test_partition.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement road buffers, ROI subtraction, polygon extraction, and directional validation**

Directional rules use representative coordinates relative to the nearest boundary geometry and must reject a cell that violates any requested side.

- [ ] **Step 4: Run tests**

Expected: PASS.

---

### Task 6: Final zoning intersection, validation, and GeoJSON serialization

**Files:**
- Create: `/mnt/data/ntpc_boundary_poc/src/validate.py`
- Create: `/mnt/data/ntpc_boundary_poc/src/output.py`
- Test: `/mnt/data/ntpc_boundary_poc/tests/test_validate.py`

**Interfaces:**
- Produces: `intersect_target_zone(candidate, zone_gdf, anchor) -> BaseGeometry`
- Produces: `validate_result(...) -> ValidationReport`
- Produces: `write_result_geojson(...)`
- Produces: `write_debug_geojson(...)`

- [ ] **Step 1: Write tests for sliver removal and multipart ranking**

Use synthetic polygons where one component contains the anchor and another is a tiny sliver.

- [ ] **Step 2: Run and verify failure**

Run: `pytest tests/test_validate.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement safe `make_valid`, area threshold filtering, anchor-first ranking, and metadata serialization**

All result metadata must include matched roads, source labels, section code/name, parcel, requested zone, and validation flags.

- [ ] **Step 4: Run tests**

Expected: PASS.

---

### Task 7: Diagnostic preview renderer

**Files:**
- Create: `/mnt/data/ntpc_boundary_poc/src/render.py`
- Test: `/mnt/data/ntpc_boundary_poc/tests/test_render.py`

**Interfaces:**
- Produces: `render_preview(path, roi, roads, zoning, final_geom, anchor, case) -> None`

- [ ] **Step 1: Write a smoke test**

Generate synthetic geometries and assert `preview.png` exists and has non-zero size.

- [ ] **Step 2: Run and verify failure**

Run: `pytest tests/test_render.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement a plain Matplotlib diagnostic render**

The final geometry uses a brown outline; road names and `section-code / parcel` are annotated. No remote basemap is required for the first POC.

- [ ] **Step 4: Run tests**

Expected: PASS.

---

### Task 8: CLI orchestration and live integration probe

**Files:**
- Create: `/mnt/data/ntpc_boundary_poc/src/main.py`
- Create: `/mnt/data/ntpc_boundary_poc/README.md`
- Test: `/mnt/data/ntpc_boundary_poc/tests/test_main.py`

**Interfaces:**
- Produces CLI: `python -m src.main --input input.example.json --output output/`

- [ ] **Step 1: Write a CLI test with dependency injection/mocked network adapters**

The test asserts that a valid synthetic road/zoning case produces `result.geojson`, `debug.geojson`, and `preview.png`.

- [ ] **Step 2: Run and verify failure**

Run: `pytest tests/test_main.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement orchestration and machine-readable failure JSON**

Stages: input → CRS/ROI → roads → zoning → barriers/cells → final intersection → validation → outputs. On failure write `failure.json` with `stage`, `message`, and `details`.

- [ ] **Step 4: Run the full unit/integration suite**

Run: `pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Run a live public-data probe for the supplied case**

Run:

```bash
python -m src.main --input input.example.json --output output/live
```

Expected: either the three success artifacts or an explicit `failure.json` identifying the unresolved public geometry. A live failure is acceptable only when caused by verifiable upstream data/source limitations; code/test failures are not acceptable.

---

## Self-review

- Spec coverage: input, CRS, bounded ROI, named roads, vector zoning, barrier partitioning, directional validation, intersection, diagnostics, explicit failures, and outputs are each mapped to a task.
- Deferred composite-boundary handling is explicit and does not silently enter the geometry algorithm.
- No task requires a paid/keyed/application-only API.
- Function names and data-flow contracts are consistent across tasks.
