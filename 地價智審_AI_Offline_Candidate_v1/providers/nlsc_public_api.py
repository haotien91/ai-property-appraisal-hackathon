"""NLSC documented no-registration APIs, COM_001/002/003-007/009-017.

Contract: https://maps.nlsc.gov.tw/S09SOA/pro/Api_ajax_list.jsp
CAD/ADR/WFS endpoints are intentionally excluded: they require registration.
"""
from __future__ import annotations
import math
import json
import xml.etree.ElementTree as ET
from urllib.parse import quote
from .public_http import PublicHttpClient, PublicDataError

BASE = "https://api.nlsc.gov.tw"


def xml_rows(raw):
    # Live NLSC content negotiation also returns JSON for the documented XML
    # endpoints when Accept includes application/json (verified 2026-09-13).
    if raw.lstrip().startswith((b"{", b"[")):
        try:
            rows = json.loads(raw)
            if isinstance(rows, dict):
                rows = [rows]
            if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):
                return rows
        except ValueError:
            pass
        raise PublicDataError("NLSC 結構化回應格式錯誤")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise PublicDataError("NLSC XML 格式錯誤") from exc
    def fields(node):
        return {e.tag.rsplit("}", 1)[-1]: (e.text or "").strip() for e in node if len(e) == 0}
    rows = [fields(node) for node in root.iter() if len(node) and all(len(e) == 0 for e in node)]
    if not rows and root.tag.rsplit("}", 1)[-1] not in {"crossRoads", "roads"}:
        raise PublicDataError("NLSC 查無資料或服務回傳錯誤")
    return rows


class NlscPublicApi:
    def __init__(self, http=None):
        self.http = http or PublicHttpClient()

    def xml(self, path):
        raw, meta = self.http.get(BASE + path)
        return xml_rows(raw), meta

    @staticmethod
    def coordinate(lon, lat):
        lon, lat = float(lon), float(lat)
        if not all(math.isfinite(v) for v in (lon, lat)) or not (119 <= lon <= 123 and 21 <= lat <= 26.5):
            raise ValueError("請輸入臺灣範圍的 WGS84 經緯度；經度在前、緯度在後")
        return f"{lon:.7f}/{lat:.7f}"

    def administrative_area(self, lon, lat):
        return self.xml(f"/other/TownVillagePointQuery1/{self.coordinate(lon, lat)}/4326")

    def cadastral_section(self, lon, lat):
        # Returns cadastral SECTION, never a parcel polygon or parcel coordinate.
        return self.xml(f"/other/TownVillagePointQuery/{self.coordinate(lon, lat)}/4326")

    def land_use(self, lon, lat):
        return self.xml(f"/other/LandUsePointQuery/{self.coordinate(lon, lat)}/4326")

    def facilities(self, category, lon, lat, radius=3000):
        if category not in {"edu", "med", "bus", "dis"}:
            raise ValueError("不支援的設施類別")
        radius = int(radius)
        if not 100 <= radius <= 5000:
            raise ValueError("設施查詢半徑必須為 100–5000 公尺")
        rows, meta = self.http.json(f"{BASE}/other/MarkBufferAnlys/{category}/{self.coordinate(lon, lat)}/{radius}")
        if not isinstance(rows, list):
            raise PublicDataError("NLSC 設施查詢沒有回傳陣列")
        return rows, meta

    def road_candidates(self, county, district, road):
        if not county or not road:
            raise ValueError("請提供縣市代碼與主要道路名稱")
        rows, meta = self.xml(f"/idc/TextQueryRoad/{quote(county, safe='')}/{quote(road, safe='')}")
        return rows, meta

    def road_crossings(self, county, town_code, road):
        return self.xml(f"/idc/ListRoadCross/{quote(county, safe='')}/{quote(town_code, safe='')}/{quote(road, safe='')}")

    @staticmethod
    def map_links(lon, lat):
        NlscPublicApi.coordinate(lon, lat)
        return {"viewer": f"https://maps.nlsc.gov.tw/go/{lon}/{lat}/18",
                "wmts_template": "https://wmts.nlsc.gov.tw/wmts/EMAP/default/GoogleMapsCompatible/{z}/{y}/{x}",
                "attribution": "內政部國土測繪中心／國土測繪圖資服務雲"}
