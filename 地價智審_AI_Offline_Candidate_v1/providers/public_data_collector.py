"""Public and local-source survey collection shared by HTTP and Lambda.

Facts, spatial approximations and missing values stay distinguishable. Current
map observations do not silently replace historical question-sheet facts.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import quote, urlencode

from .public_http import PublicHttpClient, PublicDataError
from .nlsc_public_api import NlscPublicApi

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "engine", ROOT / "providers"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from domain.models import Coordinate
from engine.geo_distance_engine import GeoDistanceEngine

# Existing field_dictionary IDs, not a second form schema.
SPECS = {
    "school_elementary": ["amenity=school"], "school_junior_high": ["amenity=school"],
    "school_senior_high": ["amenity=school"], "school_college": ["amenity=college", "amenity=university"],
    "major_station": ["amenity=bus_station", "railway=station"], "bus_stop": ["highway=bus_stop"],
    # Table 3 prints four separate 大型車站 lines; station_kind() splits them.
    "major_station_hsr": ["railway=station"], "major_station_train": ["railway=station"],
    "major_station_bus": ["amenity=bus_station"], "major_station_mrt": ["railway=station", "station=subway"],
    "interchange": ["highway=motorway_junction"], "market": ["amenity=marketplace"],
    "park": ["leisure=park"], "tourism_facility": ["tourism=attraction"],
    "parking_lot": ["amenity=parking"], "substation": ["power=substation"],
    "gas_tank": ["man_made=gasometer"], "cemetery": ["landuse=cemetery", "amenity=grave_yard"],
    "funeral_home": ["amenity=funeral_hall"], "crematorium": ["amenity=crematorium"],
    "columbarium": ["building=columbarium", "amenity=columbarium"],
    "sewage_plant": ["man_made=wastewater_plant"], "wastewater_facility": ["man_made=wastewater_plant"],
    "landfill": ["landuse=landfill"], "incinerator": ["industrial=incinerator"],
    "department_store": ["shop=department_store", "shop=mall"],
    "financial_institution": ["amenity=bank"], "entertainment_facility": ["amenity=cinema", "amenity=theatre"],
    "exhibition_hotel": ["tourism=hotel", "amenity=conference_centre", "amenity=exhibition_centre"],
    "service_facility": ["amenity=post_office", "amenity=hospital", "amenity=townhall", "amenity=police"],
}
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
)
NUISANCE = {"substation", "gas_tank", "cemetery", "funeral_home", "crematorium", "columbarium",
            "sewage_plant", "wastewater_facility", "landfill", "incinerator"}
SCHOOL_NAMES = {"school_elementary": r"國民小學|國小|小學", "school_junior_high": r"國民中學|國中",
                "school_senior_high": r"高級中學|高級職業|高中|高職", "school_college": r"大學|學院|專科"}


def school_prefixes(title):
    # A kindergarten operated by a university or attached to an elementary
    # school is still a kindergarten. Attached junior divisions override the
    # host senior-school name. Verified against actual NLSC edu responses.
    if re.search(r"幼兒園|幼稚園|托兒所|補習班", title):
        return []
    if re.search(SCHOOL_NAMES["school_junior_high"], title):
        return ["school_junior_high"]
    return [p for p, pattern in SCHOOL_NAMES.items() if re.search(pattern, title)]


# Name rules keep look-alike places out of the form's categories: a 百貨商行
# is a small shop, a 賓館 is not a 觀光飯店, a university's off-campus 產學園區
# is not the campus, and a long-term-care centre is not a 郵局/銀行/醫院/機關.
EXCLUDE_NAMES = {
    "department_store": r"商行|企業社|五金|雜貨|百貨行|商店|批發",
    "exhibition_hotel": r"賓館|旅社|旅館|民宿|Motel|MOTEL",
    "school_college": r"園區|產學|推廣|育成|宿舍",
    "service_facility": r"長照|長期照顧|托育|托老|日間照顧|老人|護理之家|幼兒園|安養|養護",
}
REQUIRE_NAMES = {
    "department_store": r"百貨公司|購物中心|購物廣場|[Mm]all|MALL|遠百|SOGO|新光三越|家樂福|好市多|[Cc]ostco|大潤發|愛買",
    "exhibition_hotel": r"飯店|酒店|[Hh]otel|HOTEL|會展|展覽|會議中心",
    "service_facility": r"醫院|診所|衛生所|郵局|區公所|戶政|地政|派出所|分局|稅務|監理",
}


def name_allowed(prefix, title, require=False):
    title = title or ""
    if prefix in EXCLUDE_NAMES and re.search(EXCLUDE_NAMES[prefix], title):
        return False
    if require and prefix in REQUIRE_NAMES:
        return bool(re.search(REQUIRE_NAMES[prefix], title))
    return True


def nuisance_prefixes(title):
    # One facility fills one 殯葬 line: 殯儀館附設火化場 is a crematorium and
    # 公墓納骨塔 is a columbarium, never both rows at once.
    if re.search(r"火化|火葬", title):
        return ["crematorium"]
    if re.search(r"納骨", title):
        return ["columbarium"]
    if re.search(r"殯儀館", title):
        return ["funeral_home"]
    if re.search(r"公墓|墓園|墓地", title):
        return ["cemetery"]
    return []


def station_kind(title, tags):
    if tags.get("amenity") == "bus_station":
        return "major_station_bus"
    if tags.get("railway") != "station" and tags.get("station") != "subway":
        return None
    if "高鐵" in title:
        return "major_station_hsr"
    if "捷運" in title or tags.get("station") in {"subway", "light_rail"} or tags.get("subway") == "yes":
        return "major_station_mrt"
    return "major_station_train"


REGIONAL_ALIASES = {"segment_avg_road_width": "regional_avg_road_width",
                    "building_site_improvement": "regional_land_improvement", "slope_degree": "regional_slope"}


def regional_id(field):
    return REGIONAL_ALIASES.get(field, "regional_" + field)


def survey_id(field):
    reverse = {v: k for k, v in REGIONAL_ALIASES.items()}
    return reverse.get(field, field.removeprefix("regional_"))


def distance(lon, lat, x, y):
    return GeoDistanceEngine().straight_line_distance(Coordinate(longitude=lon, latitude=lat), "區段查詢點",
        Coordinate(longitude=x, latitude=y), "設施代表點").distance_m


class PublicDataCollector:
    def __init__(self, http=None):
        self.http = http or PublicHttpClient()
        self.nlsc = NlscPublicApi(self.http)

    def _osm(self, lon, lat, radius):
        selectors = sorted({s for items in SPECS.values() for s in items})
        clauses = []
        for selector in selectors:
            k, v = selector.split("=", 1)
            clauses.append(f'nwr["{k}"="{v}"](around:{radius},{lat},{lon});')
        clauses.append(f'way["highway"]["width"](around:200,{lat},{lon});')
        query = '[out:json][timeout:25];(' + ''.join(clauses) + ');out center tags;'
        payload = urlencode({"data": query}).encode()
        error = PublicDataError("Overpass 查詢不完整或逾時，不能當作設施不存在")
        # The public instance rate-limits bursts; trying mirrors keeps one busy
        # server from blanking a whole segment.
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                rows, meta = self.http.json(endpoint, data=payload, timeout=40)
            except PublicDataError as exc:
                error = exc
                continue
            if isinstance(rows, dict) and "remark" not in rows and isinstance(rows.get("elements"), list):
                return rows["elements"], meta
        raise error

    @staticmethod
    def _land_price(district, parcel_id):
        """公告土地現值 read straight from the local cadastral snapshot, keyed
        by 段 + 地號 (so no coordinate is needed). Handles multi-lot ids such
        as 太平段367、917地號. Returns None when nothing matches."""
        path = ROOT / "data/cadastral_dataset_cache.sqlite3"
        if not district or not parcel_id or not path.exists():
            return None
        text = re.sub(r"^.*?[市縣]", "", str(parcel_id)).removeprefix(district)
        match = re.search(r"([一-鿿]+段)([\d、,，\-之]+)地號", text)
        if not match:
            return None
        section, lots = match.group(1), []
        for token in re.split(r"[、,，]", match.group(2)):
            main, _, sub = token.replace("之", "-").partition("-")
            if main.isdigit():
                lots.append((token, f"{int(main):04d}{int(sub) if sub.isdigit() else 0:04d}"))
        found = []
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
                for token, lid in lots:
                    hit = conn.execute(
                        "SELECT dataset_year, official_value_busiprval FROM land_price_records "
                        "WHERE district=? AND segment=? AND lid=? ORDER BY dataset_year DESC LIMIT 1",
                        (district, section, lid)).fetchone()
                    if hit and str(hit[1]).strip():
                        found.append((token, hit[0], float(hit[1])))
        except (sqlite3.Error, ValueError):
            return None
        if not found:
            return None
        detail = "；".join(f"{section}{token}地號 {value:,.0f} 元/M²（{year}年）" for token, year, value in found)
        return {"value": found[0][2], "notes": f"公告土地現值：{detail}；非成交價，不代入比較法試算",
                "meta": {"url": "data/cadastral_dataset_cache.sqlite3", "retrieved_at": path.stat().st_mtime,
                         "cached": True, "stale": True}}

    def _local_facilities(self, lon, lat, radius):
        path = ROOT / "data/facility_dataset_cache.sqlite3"
        if not path.exists():
            return [], {}
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            delta = radius / 100000.0
            rows = conn.execute("SELECT * FROM facility_records WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?",
                (lat-delta, lat+delta, lon-delta*1.2, lon+delta*1.2)).fetchall()
        return [dict(r) for r in rows], {"url": "data/facility_dataset_cache.sqlite3", "cached": True,
                                        "retrieved_at": path.stat().st_mtime, "stale": True}

    def collect(self, request):
        if not isinstance(request, dict):
            raise ValueError("請提供 JSON 物件")
        coordinate = request.get("center_coordinate") or {}
        lon, lat = coordinate.get("longitude"), coordinate.get("latitude")
        if lon is not None or lat is not None:
            NlscPublicApi.coordinate(lon, lat)
            lon, lat = float(lon), float(lat)
        radius = int(request.get("radius_m", 3000))
        if not 100 <= radius <= 5000:
            raise ValueError("查詢半徑需介於 100–5000 公尺")
        definitions = json.loads((ROOT / "schemas/field_dictionary.json").read_text("utf-8"))["fields"]
        definitions = [f for f in definitions if f["form"] == "表1"]
        fields = {f["field_id"]: {"field": f["field_id"], "label": f["chinese_label"], "category": f["category"],
                    "value": None, "unit": f.get("unit"), "source": "", "source_type": "UNKNOWN",
                    "confidence": "UNKNOWN", "status": "MISSING", "notes": "未取得可對應資料；需補充或現場勘查",
                    "alternatives": []} for f in definitions}
        sources = []
        fixed_ids = set()

        def put(field, value, source, meta=None, notes="", status="OBSERVED", **extra):
            if value is None or value == "":
                return
            meta = meta or {}
            item = dict(field=field, value=value, source=source, source_type="PublicData",
                        status=status, confidence="中", notes=notes,
                        retrieved_at=datetime.fromtimestamp(meta.get("retrieved_at", datetime.now().timestamp()), timezone.utc).isoformat(),
                        source_url=meta.get("url"), cached=meta.get("cached", False), stale=meta.get("stale", False), **extra)
            if field not in fields:
                fields[field] = {"field": field, "label": field, "category": "補充資料", "unit": None, "alternatives": [], "value": None}
            old = fields[field]
            if old["value"] is not None:
                if old["value"] != value:
                    old["alternatives"].append(item)
                return
            fields[field].update(item)

        for field in ("case_no", "appraisal_period", "segment_code", "district", "segment_scope", "main_road_name"):
            put(field, request.get(field), "案件輸入", status="INPUT")
        # Only explicit, case-scoped inputs qualify as question-sheet facts.
        for factor in request.get("competition_provided_factors", []):
            fid = survey_id(factor["field_id"])
            put(fid, factor.get("raw_value"), factor.get("evidence", {}).get("source", "匯入書表"),
                notes=factor.get("evidence", {}).get("notes", ""), status="PROVIDED")
            fixed_ids.add(fid)
        for field, value in (request.get("provided_values") or {}).items():
            put(field, value, "所選案件／題目既有資料", status="PROVIDED")
            fixed_ids.add(field)
        for field, value in (request.get("manual_values") or {}).items():
            if field in fixed_ids:
                continue
            put(field, value, "使用者補充", notes="使用者輸入，非公開 API 驗證值", status="INPUT")
        land_price = self._land_price(request.get("district"), request.get("base_parcel_id"))
        if land_price:
            put("announced_land_current_value", land_price["value"], "本地公告土地現值快照", land_price["meta"],
                notes=land_price["notes"], status="REFERENCE", unit="元/M2")

        if lon is None or lat is None:
            sources.append({"source": "座標", "status": "MISSING", "message": "請選擇路口或輸入座標；不以行政區中心冒充宗地位置"})
            return self._result(request, fields, sources, None)

        put("coordinate_reference", f"{lon:.7f}, {lat:.7f}", "使用者選定查詢點", status="INPUT",
            notes="代表點／路口座標，不保證是地號中心；距離與圖資套疊均基於此點")
        polygon = request.get("segment_geometry")
        boundary = None
        if polygon:
            from shapely.geometry import shape, Point
            boundary = shape(polygon)
            if boundary.geom_type not in {"Polygon", "MultiPolygon"} or not boundary.is_valid:
                raise ValueError("區段邊界必須是有效的 WGS84 Polygon/MultiPolygon")

        jobs = {"行政區": lambda: self.nlsc.administrative_area(lon, lat),
                "地籍段名": lambda: self.nlsc.cadastral_section(lon, lat),
                "土地利用現況": lambda: self.nlsc.land_use(lon, lat),
                **{f"NLSC:{c}": (lambda c=c: self.nlsc.facilities(c, lon, lat, min(radius, 1000) if c == "bus" else radius)) for c in ("edu", "med", "bus", "dis")},
                "OpenStreetMap": lambda: self._osm(lon, lat, radius),
                "本地設施": lambda: self._local_facilities(lon, lat, radius)}
        results = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(job): name for name, job in jobs.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    rows, meta = future.result()
                    results[name] = rows, meta
                    sources.append({"source": name, "status": "OK" if rows else "NO_MATCH", "count": len(rows), **meta})
                except (PublicDataError, ValueError, sqlite3.Error, OSError) as exc:
                    sources.append({"source": name, "status": "UNAVAILABLE", "message": str(exc)})

        for name, mapping in {"行政區": {"ctyName": "city", "townName": "district", "villageName": "village_name"},
                              "地籍段名": {"sectName": "cadastral_section_name", "sectCode": "cadastral_section_code"}}.items():
            rows, meta = results.get(name, ([], {}))
            if rows:
                for key, field in mapping.items():
                    put(field, rows[0].get(key), "國土測繪圖資服務雲", meta)
        rows, meta = results.get("土地利用現況", ([], {}))
        if rows:
            put("land_use_current", rows[0].get("NAME"), "NLSC 國土利用現況調查", meta,
                notes=f"調查年月 {rows[0].get('LYEAR', '')}-{rows[0].get('LMONTH', '')}；點位現況分類，不是法定使用分區")

        candidates = {key: [] for key in SPECS}
        candidates["service_facility"] = []
        def add(prefix, row, meta, source, x, y):
            try:
                x, y = float(x), float(y)
                if not math.isfinite(x+y) or not (119 <= x <= 123 and 21 <= y <= 26.5):
                    return
                d = distance(lon, lat, x, y)
                if d > radius:
                    return
                candidates[prefix].append({"name": row.get("name") or None, "longitude": x, "latitude": y,
                    "distance": d, "source": source, "meta": meta, "id": str(row.get("id", row.get("facility_id", "")))})
            except (TypeError, ValueError):
                return

        # Names disambiguate school levels; a generic amenity=school never implies primary school.
        for name in ("NLSC:edu", "NLSC:med", "NLSC:bus", "NLSC:dis", "本地設施"):
            rows, meta = results.get(name, ([], {}))
            for row in rows:
                title = row.get("name") or ""
                prefixes = [p for p in school_prefixes(title) if name_allowed(p, title)]
                patterns = {"market": "市場", "financial_institution": "銀行|信用合作社|農會信用",
                            "park": "公園", "substation": "變電所|變電站", "incinerator": "焚化廠",
                            "landfill": "掩埋場", "sewage_plant": "污水處理廠"}
                prefixes += [p for p, pattern in patterns.items() if re.search(pattern, title)]
                prefixes += nuisance_prefixes(title)
                if name_allowed("department_store", title, require=True):
                    prefixes.append("department_store")
                if name == "NLSC:med" and name_allowed("service_facility", title, require=True):
                    prefixes.append("service_facility")
                for prefix in prefixes:
                    add(prefix, row, meta, name, row.get("lon", row.get("longitude")), row.get("lat", row.get("latitude")))
            if name == "NLSC:med" and rows:
                valid = [r for r in rows if r.get("name")]
                if valid:
                    put("medical_facility_name", valid[0]["name"], "NLSC 醫療設施", meta, notes="補充參考設施")

        rows, meta = results.get("OpenStreetMap", ([], {}))
        roads = []
        for row in rows:
            tags, pos = row.get("tags", {}), row.get("center", row)
            title = tags.get("name", "")
            kind = station_kind(title, tags)
            funeral = nuisance_prefixes(title)
            for prefix, selectors in SPECS.items():
                if any(tags.get(k) == v for k, v in (s.split("=", 1) for s in selectors)):
                    if prefix in SCHOOL_NAMES and prefix not in school_prefixes(title):
                        continue
                    if prefix.startswith("major_station_") and prefix != kind:
                        continue
                    if prefix in {"cemetery", "funeral_home", "crematorium", "columbarium"} and funeral and prefix not in funeral:
                        continue
                    hotel = prefix == "exhibition_hotel" and tags.get("tourism") == "hotel"
                    if not name_allowed(prefix, title, require=hotel):
                        continue
                    add(prefix, {**row, "name": title}, meta, "© OpenStreetMap contributors (ODbL)", pos.get("lon"), pos.get("lat"))
            if tags.get("highway") and tags.get("width"):
                match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:m|公尺)?\s*", tags["width"])
                if match and 0 < float(match[1]) <= 100:
                    roads.append((title, float(match[1]), row.get("id")))
        # Only exact road-name matches. width estimates based on lane counts are not measurements.
        target_road = request.get("main_road_name") or (request.get("provided_values") or {}).get("main_road_name")
        matching = [r for r in roads if target_road and r[0] == target_road]
        if matching:
            put("main_road_width", matching[0][1], "OpenStreetMap 道路 width 標籤", meta,
                notes=f"同名道路有 {len(matching)} 個片段，非全區段平均或法定道路寬度；需核對", status="REFERENCE", unit="M")

        for prefix, items in candidates.items():
            items.sort(key=lambda r: r["distance"])
            if not items:
                continue
            candidate = items[0]
            put(prefix + "_name", candidate["name"], candidate["source"], candidate["meta"],
                notes="查詢半徑內直線最近的已收錄設施；未收錄不代表不存在")
            put(prefix + "_distance_m", candidate["distance"], candidate["source"], candidate["meta"],
                notes="Haversine 直線距離，起點為所選代表點、終點為設施代表點；一般設施仍需核對步行路線",
                status="REFERENCE", unit="M", distance_method="STRAIGHT_LINE", target_coordinate=candidate)
            if boundary is not None:
                from shapely.geometry import Point
                inside = boundary.covers(Point(candidate["longitude"], candidate["latitude"]))
                put(prefix + "_within_segment", inside,
                    request.get("segment_geometry_source") or "使用者提供區段邊界＋設施代表點", status="REFERENCE",
                    notes="依區段 Polygon 判斷；不是行政區界，也未假設距離門檻")
            if prefix + "_qty" in fields:
                unique = {(r["name"], round(r["longitude"], 4), round(r["latitude"], 4)) for r in items}
                put(prefix + "_nearby_count", len(unique), "NLSC／OSM／本地資料", notes=f"{radius} 公尺查詢半徑內收錄數；不是區段內總數")
                if boundary is not None:
                    from shapely.geometry import Point
                    count = len({(r["name"], round(r["longitude"], 4), round(r["latitude"], 4)) for r in items
                                 if boundary.covers(Point(r["longitude"], r["latitude"]))})
                    put(prefix + "_qty", count, "區段邊界＋已收錄設施", notes="資料庫收錄數，可能有缺漏", status="REFERENCE")

        # Reuse existing local zoning/plan/ratio providers and cadastral parser.
        try:
            from base import ProviderContext
            from land_use_provider import RealLandUseProvider
            from urban_plan_boundary_provider import RealUrbanPlanBoundaryProvider
            center = Coordinate(longitude=lon, latitude=lat)
            plan = RealUrbanPlanBoundaryProvider().resolve_urban_plan(center)
            ctx = ProviderContext(request.get("case_no", ""), request.get("city", "新北市"), request.get("district", ""),
                request.get("segment_code", ""), parcel_id=request.get("base_parcel_id"), center_coordinate=center,
                plan_id=request.get("plan_id") or plan.plan_id)
            for point in RealLandUseProvider().fetch(ctx):
                put(point.field, point.value, point.source, notes=point.notes or "", status="REFERENCE", unit=point.unit)
            if plan.plan_name:
                put("urban_plan_name", plan.plan_name, plan.source_dataset, status="REFERENCE")
                put("zoning_inside_outside", "都市計畫內", plan.source_dataset, status="REFERENCE")
            sources.append({"source": "新北市分區／都市計畫快照", "status": "OK" if plan.plan_name else "NO_MATCH"})
        except (ImportError, ValueError, AttributeError, OSError, sqlite3.Error) as exc:
            sources.append({"source": "本地分區資料", "status": "UNAVAILABLE", "message": str(exc)})
        try:
            from base import ProviderContext
            from land_price_provider import RealLandPriceProvider
            ctx = ProviderContext(request.get("case_no", ""), request.get("city", ""), request.get("district", ""),
                request.get("segment_code", ""), parcel_id=request.get("base_parcel_id"))
            if ctx.parcel_id:
                for point in RealLandPriceProvider().fetch(ctx):
                    put(point.field, point.value, point.source, notes=(point.notes or "") + "；公告現值不是成交價，不代入比較法單價", status="REFERENCE")
        except (ImportError, ValueError, OSError, sqlite3.Error) as exc:
            sources.append({"source": "公告現值", "status": "UNAVAILABLE", "message": str(exc)})
        return self._result(request, fields, sources, NlscPublicApi.map_links(lon, lat))

    @staticmethod
    def _result(request, fields, sources, links):
        points = list(fields.values())
        survey = [p for p in points if p["category"] != "補充資料"]
        known = [p for p in survey if p["value"] is not None]
        return {"case_no": request.get("case_no"), "segment_code": request.get("segment_code"),
            "generated_at": datetime.now(timezone.utc).isoformat(), "status": "DRAFT",
            "points": points, "sources": sources, "map_links": links,
            "collected_field_count": len(known), "total_field_count": len(survey),
            "missing_field_ids": [p["field"] for p in survey if p["value"] is None],
            "reference_field_count": sum(p.get("status") == "REFERENCE" for p in survey),
            "coverage_pct": round(100 * len(known) / len(survey), 1) if survey else 0,
            "notice": "公開資料輔助填表草稿；有值不等於完成審查。歷史題目與現在圖資可能不同，距離方法與來源需核對。"}
