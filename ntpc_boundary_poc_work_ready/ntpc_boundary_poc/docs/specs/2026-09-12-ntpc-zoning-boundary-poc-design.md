# NTPC Zoning Boundary POC — Design Spec

Date: 2026-09-12

## 1. Goal

Build a reproducible GIS proof-of-concept that accepts a structured parcel/location input and produces a polygon matching a textual planning-boundary description, without relying on paid APIs or APIs requiring prior application.

Primary test case:

```json
{
  "lat": 24.99384,
  "lon": 121.421118,
  "section": {
    "name": "樹德段",
    "code": "1902"
  },
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

The target output is a road-following polygon comparable to the user-provided brown outline example, plus a debug visualization.

## 2. Non-goals for the first POC

The first POC will not:

- use paid geocoding or cadastral APIs;
- depend on cadastral APIs that require an application or API key;
- use OCR, SAM, or a VLM as the primary boundary source;
- infer a polygon by connecting four manually selected corner points;
- parse free-form natural-language constraints;
- claim legal cadastral or zoning authority.

OCR/image-based extraction remains a fallback for later phases only.

## 3. Input contract

The initial program accepts a structured JSON object with:

- `lat`, `lon`: WGS84 anchor location;
- `section.name`: human-readable section name;
- `section.code`: normalized section code, e.g. `1902`;
- `parcel`: parcel number as a string;
- directional constraints:
  - `north_of`;
  - `west_of`;
  - `south_of`;
  - `east_of`;
- `zone`: target urban planning zone name.

Directional semantics are literal relative-position constraints. For example:

- `north_of: 樹人街` means the target area lies north of 樹人街, therefore 樹人街 is the south boundary;
- `west_of: 長壽街21巷` means 長壽街21巷 is the east boundary;
- `south_of: 啟智街14巷` means 啟智街14巷 is the north boundary;
- `east_of: 樹德街136巷` means 樹德街136巷 is the west boundary.

## 4. Data-source strategy

### 4.1 Road geometry

Primary source: OpenStreetMap / Overpass.

Requirements:

- no API key;
- road names available as tags where mapped;
- LineString/way geometry usable for topology.

The implementation must normalize road names before matching, including whitespace and common punctuation variants.

### 4.2 Urban-planning zoning

Primary source: New Taipei City open data / publicly downloadable urban planning zoning dataset.

The first implementation should prefer vector data. If the public source only exposes a downloadable archive, the program will download/cache the archive and read the contained GIS format.

The POC must not derive zoning solely from raster colors when a public vector dataset is available.

### 4.3 Background/debug basemap

Optional source: free public tiles/WMS from OSM or NLSC where access is anonymous.

Basemap data is for preview and debugging only. It must not determine the final geometry.

## 5. Coordinate systems

- Input anchor: EPSG:4326 (WGS84).
- Working CRS: EPSG:3826 (TWD97 / TM2 zone 121).

All geometry operations, buffers, distances, and topology checks must occur in EPSG:3826.

Outputs may be written in EPSG:4326 for interoperability, while debug files may preserve EPSG:3826.

## 6. Processing pipeline

### 6.1 Normalize input

1. Validate required fields.
2. Normalize road names.
3. Preserve section name/code and parcel number as metadata.
4. Convert anchor from EPSG:4326 to EPSG:3826.

### 6.2 Build the region of interest

Create a default square ROI centered on the anchor.

Initial default: 600 m × 600 m.

If one or more named roads are not found, expand in bounded steps, e.g. 600 m → 1000 m → 1600 m, then fail with a clear diagnostic rather than searching indefinitely.

### 6.3 Fetch and match roads

Query OSM road ways intersecting the ROI.

For each requested road name:

1. exact normalized match;
2. normalized alias match;
3. controlled fuzzy match only if unique and above a strict threshold.

If multiple disjoint ways share the same name, merge connected pieces within the ROI.

The matched road geometries are preserved as LineStrings/MultiLineStrings.

### 6.4 Convert roads into spatial barriers

Roads are not reduced to four points.

Each matched road geometry is buffered by a small configurable distance in EPSG:3826. The buffer exists to form robust cutting barriers and to handle small line discontinuities.

Initial buffer range for testing: 1–4 m, selected conservatively from road geometry quality.

### 6.5 Partition the ROI

Subtract the union of the four road barriers from the ROI.

This produces candidate polygon cells. Candidate selection uses:

1. the anchor location;
2. the directional constraints;
3. proximity to all four named boundary roads.

The anchor-containing cell is preferred, but the algorithm must not silently accept it if it violates any directional constraint.

### 6.6 Obtain zoning polygons

Load the public New Taipei City zoning vector data, reproject to EPSG:3826, and clip it to the ROI.

Normalize zone names and select the target zone, e.g. `第一種住宅區`.

### 6.7 Produce final polygon

Final geometry:

`selected_road_cell ∩ target_zoning_geometry`

Post-processing:

- remove tiny slivers below a configurable area threshold;
- repair invalid geometries where safe;
- preserve road-following shape;
- avoid aggressive simplification that changes the visible boundary materially.

If the intersection yields multiple polygons, rank by:

1. contains/touches the anchor;
2. area;
3. boundary agreement with the four named roads.

### 6.8 Validate result

The result is accepted only if:

- it intersects the target zoning type;
- it satisfies all four directional constraints;
- it is topologically valid or safely repairable;
- it is spatially close to all four named road boundaries;
- it is not an implausibly tiny sliver.

If validation fails, return diagnostics instead of a misleading polygon.

## 7. Outputs

### 7.1 `result.geojson`

Contains the final polygon and metadata:

- section name/code;
- parcel number;
- anchor;
- matched road names;
- target zone;
- source labels;
- validation flags.

### 7.2 `debug.geojson`

Contains:

- ROI;
- anchor point;
- matched road geometries;
- road barriers;
- candidate cells;
- zoning polygons;
- selected final polygon.

### 7.3 `preview.png`

Shows:

- road labels;
- anchor point;
- target zoning fill/outline;
- final polygon with a brown outline;
- section code `1902` and parcel `284` as annotations.

The preview is diagnostic, not authoritative.

## 8. Failure handling

The program must fail explicitly for:

- road not found;
- ambiguous road name;
- zoning dataset unavailable;
- target zone not found in ROI;
- anchor outside all plausible candidate cells;
- invalid/multipart result that cannot be resolved safely.

Each failure must report which stage failed and the relevant candidate names/geometries where useful.

## 9. Testing strategy

### Unit tests

- road-name normalization;
- directional semantic mapping;
- CRS conversion;
- candidate-cell scoring;
- zoning-name normalization;
- sliver filtering.

### Integration test

Use the supplied 樹德段 1902 / 284 case with:

- anchor: 24.99384, 121.421118;
- 樹人街;
- 長壽街21巷;
- 啟智街14巷;
- 樹德街136巷;
- 第一種住宅區.

Success criteria:

- all four roads matched or a precise failure reason is produced;
- zoning polygons load from a free/no-application source;
- a final polygon is generated without manual point picking;
- the preview visually follows roads and resembles the supplied brown-outline example.

## 10. Planned implementation structure

```text
poc/
  input.example.json
  src/
    config.py
    models.py
    crs.py
    roads.py
    zoning.py
    partition.py
    validate.py
    render.py
    main.py
  tests/
    test_direction_rules.py
    test_name_normalization.py
    test_partition.py
  output/
```

Responsibilities remain isolated:

- `roads.py`: acquire and resolve road geometry;
- `zoning.py`: acquire/cache/load zoning polygons;
- `partition.py`: road-barrier topology and cell selection;
- `validate.py`: directional and geometric validation;
- `render.py`: preview only.

## 11. Phase-2 fallbacks (not part of first implementation)

Only after the vector-first POC is validated:

- OCR road labels from NLSC/NTPC map imagery;
- extract road centerlines from map imagery when OSM naming is incomplete;
- georeference screenshots;
- use cadastral/section graphics as a secondary verification layer;
- add free-form natural-language parsing.

## 12. Acceptance criteria

The first POC is complete when it can be run with one structured JSON file and produces either:

1. a valid `result.geojson` + `preview.png`; or
2. an explicit machine-readable failure explaining which required public geometry could not be resolved.

It must not silently fabricate missing roads or zoning boundaries.
