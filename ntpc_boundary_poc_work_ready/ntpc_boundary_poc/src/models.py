from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SectionInfo:
    name: str
    code: str


@dataclass(frozen=True)
class Constraints:
    north_of: str
    west_of: str
    south_of: str
    east_of: str
    zone: str


@dataclass(frozen=True)
class BoundaryCase:
    lat: float
    lon: float
    section: SectionInfo
    parcel: str
    constraints: Constraints


def load_case(path: str | Path) -> BoundaryCase:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return BoundaryCase(
        lat=float(raw["lat"]),
        lon=float(raw["lon"]),
        section=SectionInfo(
            name=str(raw["section"]["name"]),
            code=str(raw["section"]["code"]),
        ),
        parcel=str(raw["parcel"]),
        constraints=Constraints(
            north_of=str(raw["constraints"]["north_of"]),
            west_of=str(raw["constraints"]["west_of"]),
            south_of=str(raw["constraints"]["south_of"]),
            east_of=str(raw["constraints"]["east_of"]),
            zone=str(raw["constraints"]["zone"]),
        ),
    )
