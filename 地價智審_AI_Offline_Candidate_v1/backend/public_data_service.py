"""Automatic data pipeline shared by the local app and backend handlers."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile

from providers.public_data_collector import PublicDataCollector, ROOT, regional_id, survey_id
from providers.nlsc_public_api import NlscPublicApi
from providers.public_http import PublicHttpClient

PROFILE = "shulin_residential_2026"

# Public survey fields that correspond to the regional factors used by
# Table 5-1.  When several facility subtypes represent one official factor,
# the nearest observed facility is used and its original provenance remains
# attached.  These are measured/reference inputs, never invented defaults.
REGIONAL_DISTANCE_FIELDS = {
    "regional_major_station_proximity": (
        "major_station_distance_m", "major_station_hsr_distance_m", "major_station_train_distance_m",
        "major_station_bus_distance_m", "major_station_mrt_distance_m",
    ),
    "regional_bus_stop_proximity": ("bus_stop_distance_m",),
    "regional_interchange_proximity": ("interchange_distance_m",),
    "regional_school_proximity": (
        "school_elementary_distance_m", "school_junior_high_distance_m",
        "school_senior_high_distance_m", "school_college_distance_m",
    ),
    "regional_market_proximity": ("market_distance_m",),
    "regional_park_proximity": ("park_distance_m",),
    "regional_tourism_facility_proximity": ("tourism_facility_distance_m",),
    "regional_parking_convenience": ("parking_lot_distance_m",),
    "regional_service_facility_proximity": (
        "service_facility_distance_m", "financial_institution_distance_m",
    ),
    "regional_utility_facility_proximity": ("substation_distance_m", "gas_tank_distance_m"),
    "regional_funeral_facility_proximity": (
        "cemetery_distance_m", "funeral_home_distance_m", "crematorium_distance_m",
        "columbarium_distance_m",
    ),
    "regional_waste_facility_proximity": ("landfill_distance_m", "incinerator_distance_m"),
}


def regional_factor_points(result):
    """Return one best sourced survey point for every gradeable factor."""
    by_field = {p["field"]: p for p in result.get("points", []) if p.get("value") is not None}
    selected = {}
    priority = {"PROVIDED": 0, "INPUT": 1, "OBSERVED": 2, "REFERENCE": 3}
    for point in by_field.values():
        fid = regional_id(point["field"])
        old = selected.get(fid)
        if old is None or priority.get(point.get("status"), 9) < priority.get(old.get("status"), 9):
            selected[fid] = point
    for fid, fields in REGIONAL_DISTANCE_FIELDS.items():
        candidates = [by_field[field] for field in fields if field in by_field]
        if candidates and fid not in selected:
            selected[fid] = min(candidates, key=lambda p: float(p["value"]))
    return selected


SCHOOL_DISTANCE_FIELDS = REGIONAL_DISTANCE_FIELDS["regional_school_proximity"]
STATION_DISTANCE_FIELDS = REGIONAL_DISTANCE_FIELDS["regional_major_station_proximity"]
COMMERCIAL_DISTANCE_FIELDS = ("department_store_distance_m", "entertainment_facility_distance_m", "market_distance_m")
NUISANCE_DISTANCE_FIELDS = (
    "cemetery_distance_m", "funeral_home_distance_m", "crematorium_distance_m", "columbarium_distance_m",
    "substation_distance_m", "gas_tank_distance_m", "landfill_distance_m", "incinerator_distance_m",
    "sewage_plant_distance_m",
)
FLAT_TERRAIN = {"極平坦堅硬", "平坦地", "平坦"}


def individual_factor_inputs(result, catalog, labels=None):
    """表4 individual factors that can be honestly derived for a segment.

    題目.pdf leaves every individual cell blank, so each value here is either
    the segment's own 表3 fixed value reused as the parcel's, or a public-map
    straight-line distance; every derivation is named in the evidence notes.
    Parcel geometry (面積/寬度/深度/形狀/臨街) and 無尾巷 stay missing.
    `labels`, when given, receives field_id -> road/facility name for the
    name cell printed beside each distance on 表4."""
    from domain.models import FactorInput, Evidence, SourceType
    labels = {} if labels is None else labels
    provided = {f["field_id"]: f.get("raw_value") for f in result.get("request", {}).get("competition_provided_factors", [])}
    points = {p["field"]: p for p in result.get("points", []) if p.get("value") is not None}
    fixed_source = "題目.pdf 表3 區段固定值"
    inputs = []

    def add(field_id, value, unit, source_type, source, notes):
        if value is not None and field_id in catalog:
            inputs.append(FactorInput(field_id=field_id, factor=catalog[field_id], raw_value=value, unit=unit,
                                      evidence=Evidence(source=source, source_type=source_type, notes=notes)))

    def nearest(fields):
        found = [points[f] for f in fields if f in points]
        return min(found, key=lambda p: float(p["value"]), default=None)

    def add_distance(field_id, fields, label):
        point = nearest(fields)
        if point:
            add(field_id, round(float(point["value"])), "M", SourceType.GIS_MEASUREMENT, point.get("source") or "公開圖資",
                f"{label}：{point['field']} 自推估區段中心之直線距離，非步行距離；需人工複核")
            name = points.get(point["field"].replace("_distance_m", "_name"))
            if name and field_id in catalog:
                labels[field_id] = str(name["value"])

    fixed = SourceType.COMPETITION_PROVIDED_FIXED
    derived = SourceType.AI_ASSISTED_FILL
    add("individual_zoning_designation", provided.get("regional_land_use_zone"), None, fixed, fixed_source,
        "以區段使用分區代表宗地；需核對宗地分區")
    add("individual_building_coverage_ratio", provided.get("regional_building_coverage_ratio"), "%", fixed, fixed_source,
        "以區段建蔽率代表宗地")
    add("individual_floor_area_ratio", provided.get("regional_floor_area_ratio"), "%", fixed, fixed_source,
        "僅列示；容積率須以土地開發分析法調整")
    if provided.get("regional_construction_prohibited") == "無" and provided.get("regional_construction_restricted") == "無":
        add("individual_construction_restriction", "無禁止或限制建築", None, derived, fixed_source,
            "由表3「有無禁止建築／限制建築：無」推導")
    if provided.get("regional_terrain") in FLAT_TERRAIN:
        add("individual_terrain_form4", "平坦", None, derived, fixed_source,
            f"由表3地勢「{provided['regional_terrain']}」歸類")
    width = provided.get("regional_main_road_width")
    if width is not None:
        add("individual_frontage_road_width", width, "M", derived, fixed_source,
            "題目未提供宗地面前道路，以區段主要道路寬度推估；需人工複核")
        road_name = result.get("request", {}).get("main_road_name")
        if road_name:
            labels["individual_frontage_road_width"] = road_name
        road_type = "主要道路" if float(width) >= 15 else "次要道路" if float(width) >= 8 else "巷道"
        add("individual_road_type", road_type, None, derived, fixed_source,
            f"依主要道路寬度 {width}M 推估（≥15M 主要、≥8M 次要、其餘巷道）；需人工複核")
    add_distance("individual_school_proximity", SCHOOL_DISTANCE_FIELDS, "最近學校")
    add_distance("individual_market_proximity", ("market_distance_m",), "最近市場")
    add_distance("individual_park_proximity", ("park_distance_m",), "最近公園")
    add_distance("individual_station_proximity", STATION_DISTANCE_FIELDS, "最近大型車站")
    add_distance("individual_commercial_district_proximity", COMMERCIAL_DISTANCE_FIELDS, "最近百貨／娛樂／市場（商圈代理指標）")
    add_distance("individual_nuisance_facility", NUISANCE_DISTANCE_FIELDS, "最近嫌惡設施")
    parking = points.get("parking_lot_distance_m")
    if parking:
        meters = float(parking["value"])
        label = "停車方便性優" if meters < 200 else "停車方便性普通" if meters < 600 else "停車方便性劣"
        add("individual_parking_convenience", label, None, derived, parking.get("source") or "公開圖資",
            f"依最近停車場直線距離 {meters:.0f}M 推估（<200M 優、<600M 普通）；需人工複核")
    return inputs


def _excluded_names(comparisons, results_key):
    names = []
    for comparison in comparisons:
        for result in getattr(comparison, results_key):
            if result.field_id in comparison.excluded_factor_ids and result.factor_name not in names:
                names.append(result.factor_name)
    return names


def _land_price_remark(result):
    point = next((p for p in result.get("points", [])
                  if p["field"] == "announced_land_current_value" and p.get("value") is not None), None)
    return point.get("notes") if point else None


def draft_remarks(table51, table4, segments):
    t51_names = _excluded_names(table51.comparisons, "factor_results")
    table51_remarks = {"whole_case": (
        "PARTIAL_DRAFT：小計／總修正數僅加總已取得資料之因素"
        + (f"（未納入：{'、'.join(t51_names)}）" if t51_names else "")
        + "；距離類為公開圖資自推估區段中心之直線距離，需人工複核。")}
    t4_names = _excluded_names(table4.comparisons, "individual_factor_results")
    parts = ["個別因素僅就可取得資料之項目試算" + (f"（未納入：{'、'.join(t4_names)}）" if t4_names else "")]
    if table4.base_comparison_price is not None:
        parts.append("權重為系統輔助建議（調整百分率絕對值加總之反比），比準地比較價格為草稿，需人工確認")
    parts.append("面前道路寬度／道路種類以區段主要道路推估")
    table4_remarks = {"whole_case": "；".join(parts) + "。"}
    for key, code in (("base", "P001-00"), ("comp1", "P002-00"), ("comp2", "P003-00"), ("comp3", "P004-00")):
        remark = _land_price_remark(segments.get(code, {}))
        if remark:
            table4_remarks[key] = remark
    return table51_remarks, table4_remarks


def presets():
    from backend.competition_fixtures import load_table3
    fx = load_table3()
    from backend.competition_fixtures import load_table4
    fx4 = load_table4()
    return {"profile_id": PROFILE, "source": "data/sources/competition/shulin_residential_2026/題目.pdf",
        "segments": [{**copy.deepcopy(meta), "city": fx.CITY, "appraisal_period": fx.APPRAISAL_PERIOD,
                       "appraisal_base_date": fx4.APPRAISAL_BASE_DATE,
                       "transaction": copy.deepcopy(fx4.SEGMENT_TABLE4_TRANSACTION.get(code))}
                     for code, meta in fx.SEGMENT_META.items()]}


def prepare_request(body):
    request = copy.deepcopy(body)
    if not isinstance(request, dict):
        raise ValueError("請提供 JSON 物件")
    if not isinstance(request.get("case_no"), str) or not request["case_no"].strip():
        raise ValueError("請輸入案號，避免不同案件共用資料")
    if not isinstance(request.get("segment_code"), str) or not request["segment_code"].strip():
        raise ValueError("請輸入區段編號")
    if request.get("use_project_source"):
        if request.get("profile_id") != PROFILE:
            raise ValueError("只有明確選擇樹林題目，才能套用該題目既有數值")
        from backend.competition_fixtures import load_table3
        fx = load_table3()
        from backend.competition_fixtures import load_table4
        fx4 = load_table4()
        code = request["segment_code"]
        if code not in fx.SEGMENT_META:
            raise ValueError("區段不在所選題目中")
        meta = fx.SEGMENT_META[code]
        request.update(city=fx.CITY, district=fx.DISTRICT, land_use_type=fx.LAND_USE_TYPE,
                       base_parcel_id=meta["parcel_ids"][0], segment_scope=meta["zone_range_description"],
                       appraisal_period=fx.APPRAISAL_PERIOD, appraisal_base_date=fx4.APPRAISAL_BASE_DATE,
                       main_road_name=meta["main_road_name"])
        request["competition_provided_factors"] = copy.deepcopy(fx.SEGMENT_TABLE3_FACTORS[code])
        request["provided_values"] = {"building_density": meta["building_density_pct"], "building_type": meta["building_type"],
                                      "land_use_current": "、".join(meta["land_use_status"])}
        request["transaction"] = copy.deepcopy(fx4.SEGMENT_TABLE4_TRANSACTION.get(code))
    return request


class PublicDataService:
    def __init__(self, storage_dir=None, http=None):
        self.storage = Path(storage_dir or os.environ.get("PUBLIC_DATA_CASE_DIR") or ROOT / "data/local_cases")
        self.http = http or PublicHttpClient()

    def _path(self, case_no, segment_code):
        key = hashlib.sha256(json.dumps([case_no, segment_code], ensure_ascii=False).encode()).hexdigest()
        path = os.path.abspath(self.storage / (key + ".json"))
        if os.name == "nt" and not path.startswith("\\\\?\\"):
            path = "\\\\?\\" + path
        return Path(path)

    def load(self, case_no, segment_code):
        try:
            return json.loads(self._path(case_no, segment_code).read_text("utf-8"))
        except FileNotFoundError:
            return None

    def collect(self, body):
        request = prepare_request(body)
        result = PublicDataCollector(self.http).collect(request)
        result["request"] = request
        self.storage.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.storage, delete=False) as f:
            json.dump(result, f, ensure_ascii=False)
        target = self._path(request["case_no"], request["segment_code"])
        # Windows rename still needs extended-length paths in deep checkouts.
        source_name, target_name = os.path.abspath(f.name), os.path.abspath(target)
        if os.name == "nt":
            if not source_name.startswith("\\\\?\\"):
                source_name = "\\\\?\\" + source_name
            if not target_name.startswith("\\\\?\\"):
                target_name = "\\\\?\\" + target_name
        os.replace(source_name, target_name)
        return result

    def locate(self, body):
        county, district, road = body.get("county_code", "F"), body.get("district"), body.get("road")
        api = NlscPublicApi(self.http)
        roads, _ = api.road_candidates(county, district, road)
        roads = [r for r in roads if r.get("name") == road and (not district or r.get("townName") == district)]
        candidates = []
        for row in roads[:3]:
            crossings, meta = api.road_crossings(county, row["townCode"], row["name"])
            for cross in crossings:
                try:
                    lon = float(cross.get("lon", cross.get("longitude", cross.get("x", ""))))
                    lat = float(cross.get("lat", cross.get("latitude", cross.get("y", ""))))
                    api.coordinate(lon, lat)
                except (TypeError, ValueError):
                    continue
                candidates.append({"name": f"{row['townName']} {road}／{cross.get('name', cross.get('roadName', '路口'))}",
                    "longitude": lon, "latitude": lat, "source_url": meta["url"], "precision": "ROAD_INTERSECTION"})
        return {"candidates": candidates, "notice": "請選擇適當路口作為區段參考點；路口不是地號中心。"}

    def bundle(self, case_no, profile_id=PROFILE):
        if profile_id != PROFILE:
            raise ValueError("跨區段規則分析目前支援明確選取的樹林題目；其他案件仍可收集與匯出勘查資料")
        from backend.competition_fixtures import load_table3
        fx = load_table3()
        from backend.competition_fixtures import load_table4
        fx4 = load_table4()
        segments = {}
        for code in fx.SEGMENT_META:
            saved = self.load(case_no, code)
            if saved and saved.get("request", {}).get("profile_id") == PROFILE and saved["request"].get("use_project_source"):
                segments[code] = saved
            else:
                request = prepare_request({"case_no": case_no, "segment_code": code,
                                           "profile_id": PROFILE, "use_project_source": True})
                segments[code] = PublicDataCollector(self.http).collect(request)
                segments[code]["request"] = request
        from rule_engine import RuleEngine
        from grade_engine import GradeEngine
        from adjustment_engine import AdjustmentEngine
        from calculation_engine import CalculationEngine
        from table51_analysis_engine import Table51AnalysisEngine
        from table4_analysis_engine import Table4AnalysisEngine
        from domain.models import FactorInput, Evidence, SourceType
        rules_dir = ROOT / "data/rules/competition" / PROFILE
        reg = json.loads((rules_dir / "regional_rules.json").read_text("utf-8"))["rules"]
        ind = json.loads((rules_dir / "individual_rules.json").read_text("utf-8"))["rules"]
        engine = RuleEngine(reg + ind)
        grade, adjust = GradeEngine(engine), AdjustmentEngine(engine)
        factor_names = {"regional_" + r["rule_id"].rsplit("-", 1)[0][4:].lower(): r["factor"] for r in reg}
        def factors(code):
            values = []
            for fid, p in regional_factor_points(segments[code]).items():
                if fid not in factor_names or p.get("status") == "MISSING" or p["value"] is None:
                    continue
                values.append(FactorInput(field_id=fid, factor=factor_names[fid], raw_value=p["value"], unit=p.get("unit"),
                    evidence=Evidence(source=p["source"],
                        source_type=SourceType.COMPETITION_PROVIDED_FIXED if p.get("status") == "PROVIDED" else SourceType.GIS_MEASUREMENT,
                        notes=(p.get("notes") or "") + ("；公開圖資推導，需人工複核" if p.get("status") in {"OBSERVED", "REFERENCE"} else ""))))
            return values
        from individual_factor_catalog import build_individual_factor_catalog
        # The auto-fill draft opts into PARTIAL_DRAFT: totals cover resolved
        # factors only and list what was left out. Strict handlers are unchanged.
        table51 = Table51AnalysisEngine(grade, adjust, reg, allow_partial=True).build_analysis(case_no, PROFILE, fx.CITY,
            fx.DISTRICT, fx.LAND_USE_TYPE, "P001-00", factors("P001-00"),
            [(code, index, fx.DISTRICT, fx.LAND_USE_TYPE, factors(code)) for index, code in enumerate(
                ["P002-00", "P003-00", "P004-00"], 1)])
        catalog = build_individual_factor_catalog(ind)
        condition_labels = {code: {} for code in segments}
        individual = {code: individual_factor_inputs(segments[code], catalog, condition_labels[code]) for code in segments}
        table4engine = Table4AnalysisEngine(grade, adjust, CalculationEngine(), ind, allow_partial=True)
        comparisons = [table4engine.build_comparison(fx.CITY, fx.DISTRICT, fx.LAND_USE_TYPE, "P001-00",
            fx.DISTRICT, fx.LAND_USE_TYPE, c.comparable_segment_code, c.comparison_index,
            individual["P001-00"], individual[c.comparable_segment_code],
            fx4.SEGMENT_TABLE4_TRANSACTION[c.comparable_segment_code], c) for c in table51.comparisons]
        table4 = table4engine.apply_suggested_weights(table4engine.build_analysis(case_no, PROFILE, "P001-00", comparisons))
        table51_remarks, table4_remarks = draft_remarks(table51, table4, segments)
        table51 = table51.model_copy(update={"remarks": table51_remarks})
        table4 = table4.model_copy(update={"remarks": table4_remarks, "condition_labels": condition_labels})
        return {"schema_version": "automatic-data-draft-1", "case_no": case_no, "profile_id": PROFILE,
                "appraisal_base_date": fx4.APPRAISAL_BASE_DATE, "segments": segments,
                "table51": table51.model_dump(mode="json"), "table4": table4.model_dump(mode="json"),
                "notice": "PARTIAL_DRAFT：規則引擎僅就已取得資料之因素試算，權重為系統輔助建議；尚非最終估價結果，需人工複核。"}
