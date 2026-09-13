"""Backend for the unchanged simplified-workflow frontend."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import zipfile

from backend.public_data_service import PROFILE, PublicDataService, prepare_request, regional_factor_points
from providers.nlsc_public_api import NlscPublicApi
from providers.public_http import PublicHttpClient, PublicDataError
from providers.public_data_collector import ROOT, distance

TABLE3_PAGE_ORDER = ("P002-00", "P003-00", "P004-00", "P001-00")
CROSSING_HULL_RADIUS_M = 200


class LocalWorkflowError(RuntimeError):
    pass


def _canonical_road(name):
    # NLSC spells 啟 as 啓 and appends bridge names in parentheses.
    return re.sub(r"[（(][^）)]*[）)]", "", str(name or "")).replace("啓", "啟").strip()


def boundary_roads(description):
    """Road names bounding a segment, read from its 表3 range text such as
    「沿八德街以西、啟智街及未開闢計畫道路以南…之捷運開發區」. Unbuilt
    planned roads have no NLSC crossings, so they are skipped."""
    text = re.sub(r"[（(][^）)]*[）)]", "", str(description or "")).strip().removeprefix("沿")
    if "之" in text:
        text = text.rsplit("之", 1)[0]
    roads = []
    for part in re.split(r"[、，,]", text):
        part = re.sub(r"(?:以[東西南北])+(?:及以?[東西南北])*$", "", part.strip())
        for road in part.split("及"):
            road = road.strip()
            if road and not re.search(r"未開闢|計畫道路", road) and road not in roads:
                roads.append(road)
    return roads


def _document_text(data: bytes) -> str:
    if not data:
        return ""
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
    except Exception:
        return ""


def extract_case_metadata(appraisal_documents, criteria_document=b""):
    """Extract the five editable values already present on case-new.html."""
    texts = [_document_text(data) for _name, data in appraisal_documents]
    if criteria_document:
        texts.append(_document_text(criteria_document))
    text = "\n".join(texts)
    compact = re.sub(r"[\s　]+", "", text)
    codes = ("P001-00", "P002-00", "P003-00", "P004-00")
    if "新北市樹林區" in compact and any(code in compact for code in codes):
        from backend.competition_fixtures import load_table3
        fx = load_table3()
        code = "P001-00" if "P001-00" in compact else next(c for c in codes if c in compact)
        case_match = re.search(r"\b\d{7,8}-\d{2}-(?:\d{3}|XXX)\b", text, re.I)
        return {
            "case_no": case_match.group(0) if case_match else "1110901-99-001",
            "segment_code": code,
            "district": fx.DISTRICT,
            "land_use_type": fx.LAND_USE_TYPE,
            "segment_scope": fx.SEGMENT_META[code]["zone_range_description"],
            "profile_id": PROFILE,
        }

    case_match = re.search(r"\b\d{7,8}-\d{2}-[A-Z0-9]{3,}\b", text, re.I)
    segment_match = re.search(r"\bP?\d{3,4}-\d{2}\b", text, re.I)
    district_match = re.search(r"新北市\s*([^\s()（）]{1,6}(?:區|鄉|鎮|市))", text)
    land_use_match = re.search(r"新北市\s*[^()（）]+[（(]([^()（）]{2,20}用地)[)）]", text)
    return {
        "case_no": case_match.group(0) if case_match else "",
        "segment_code": segment_match.group(0).upper() if segment_match else "",
        "district": district_match.group(1) if district_match else "",
        "land_use_type": land_use_match.group(1) if land_use_match else "",
        "segment_scope": "",
        "profile_id": None,
    }


class LocalWorkflowService:
    def __init__(self, storage_dir=None, http=None):
        storage = os.path.abspath(storage_dir or ROOT / "data/local_cases")
        # Deep checkouts plus 64-hex file names pass Windows' 260-char limit.
        if os.name == "nt" and not storage.startswith("\\\\?\\"):
            storage = "\\\\?\\" + storage
        self.storage = Path(storage)
        self.http = http or PublicHttpClient()
        self.public = PublicDataService(self.storage / "segments", self.http)
        self._lock = threading.Lock()

    @staticmethod
    def _key(case_no):
        return hashlib.sha256(case_no.encode("utf-8")).hexdigest()

    def _record_path(self, case_no):
        return self.storage / ("workflow-" + self._key(case_no) + ".json")

    def _pdf_path(self, case_no):
        return self.storage / ("official-" + self._key(case_no) + ".pdf")

    def _zip_path(self, case_no):
        return self.storage / ("bundle-" + self._key(case_no) + ".zip")

    def _excel_path(self, case_no):
        return self.storage / ("excel-" + self._key(case_no) + ".zip")

    def load(self, case_no):
        try:
            return json.loads(self._record_path(case_no).read_text("utf-8"))
        except FileNotFoundError:
            return None

    def _autolocate(self, segment):
        """Pick a documented NLSC intersection named in the segment scope."""
        api = NlscPublicApi(self.http)
        road = segment["main_road_name"]
        roads, _ = api.road_candidates("F", segment["district"], road)
        exact = [r for r in roads if r.get("name") == road and r.get("townName") == segment["district"]]
        candidates = []
        for row in exact[:3]:
            crossings, meta = api.road_crossings("F", row["townCode"], road)
            for cross in crossings:
                try:
                    lon = float(cross.get("lon", cross.get("longitude", cross.get("x"))))
                    lat = float(cross.get("lat", cross.get("latitude", cross.get("y"))))
                    api.coordinate(lon, lat)
                except (TypeError, ValueError):
                    continue
                name = cross.get("name", cross.get("roadName", ""))
                score = 2 if name and name in segment["zone_range_description"] else 0
                candidates.append((score, name, lon, lat, meta.get("url")))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1] or ""))
        score, name, lon, lat, url = candidates[0]
        return {
            "longitude": lon, "latitude": lat,
            "source": f"NLSC 道路交叉點：{road}／{name or '未命名道路'}",
            "source_url": url,
            "selection": "BOUNDARY_ROAD_MATCH" if score else "FIRST_DOCUMENTED_CROSSING",
        }

    def _segment_geometry(self, segment):
        """Estimate a segment's extent from NLSC crossings between the roads
        named in its range text. Corners are crossings of two boundary roads;
        with fewer than three, the other crossings within 200 m of a corner
        widen the hull. An estimate for distances and inside checks, not a
        surveyed cadastral boundary."""
        from shapely.geometry import MultiPoint, mapping
        api = NlscPublicApi(self.http)
        roads = boundary_roads(segment.get("zone_range_description"))
        wanted = {_canonical_road(r) for r in roads}
        corners, crossings, urls = {}, {}, []
        for road in roads:
            for variant in dict.fromkeys((road, road.replace("啟", "啓"))):
                try:
                    found, _ = api.road_candidates("F", segment["district"], variant)
                except (PublicDataError, ValueError):
                    continue
                rows = [r for r in found if _canonical_road(r.get("name")) == _canonical_road(road)
                        and r.get("townName") == segment["district"]]
                for row in rows[:2]:
                    try:
                        items, meta = api.road_crossings("F", row["townCode"], row["name"])
                    except (PublicDataError, ValueError):
                        continue
                    urls.append(meta.get("url"))
                    for cross in items:
                        try:
                            lon = float(cross.get("lon", cross.get("longitude", cross.get("x"))))
                            lat = float(cross.get("lat", cross.get("latitude", cross.get("y"))))
                            api.coordinate(lon, lat)
                        except (TypeError, ValueError):
                            continue
                        other = cross.get("name", cross.get("roadName", ""))
                        # ~10 m key: the same corner is listed under both roads.
                        key = (round(lon, 4), round(lat, 4))
                        crossings.setdefault(key, (lon, lat))
                        if _canonical_road(other) in wanted - {_canonical_road(road)}:
                            corners.setdefault(key, ((lon, lat), f"{road}／{other}"))
                if rows:
                    break
        if not corners:
            return None
        if len(corners) >= 3:
            points, method = [point for point, _label in corners.values()], "BOUNDARY_ROAD_CORNERS"
        else:
            anchors = [point for point, _label in corners.values()]
            points = anchors + [p for p in crossings.values()
                                if any(distance(a[0], a[1], p[0], p[1]) <= CROSSING_HULL_RADIUS_M for a in anchors)]
            method = "BOUNDARY_ROAD_CROSSINGS_HULL"
        hull = MultiPoint(points).convex_hull
        center = hull.centroid
        return {
            "longitude": round(center.x, 7), "latitude": round(center.y, 7),
            "source": "NLSC 道路交叉點推估區段範圍：" + "、".join(label for _point, label in corners.values()),
            "source_url": urls[0] if urls else None, "selection": method,
            "geometry": mapping(hull) if hull.geom_type == "Polygon" else None,
            "notice": "邊界道路交叉點凸包推估之區段範圍與中心點，非地籍測量界線；距離為自此中心點之直線距離",
        }

    def _collect_all_segments(self, case_no):
        from backend.competition_fixtures import load_table3
        fx = load_table3()
        locations = {}
        for code, meta in fx.SEGMENT_META.items():
            request = prepare_request({"case_no": case_no, "segment_code": code,
                                       "profile_id": PROFILE, "use_project_source": True})
            try:
                location = self._segment_geometry(meta) or self._autolocate(meta)
            except (PublicDataError, ValueError, OSError):
                location = None
            if location:
                geometry = location.pop("geometry", None)
                request["center_coordinate"] = {"longitude": location["longitude"], "latitude": location["latitude"]}
                request["coordinate_evidence"] = location
                if geometry:
                    request["segment_geometry"] = geometry
                    request["segment_geometry_source"] = "NLSC 道路交叉點推估區段範圍＋設施代表點"
                locations[code] = location
            self.public.collect(request)
        return self.public.bundle(case_no), locations

    @staticmethod
    def _table3_data(bundle):
        from backend.competition_fixtures import load_table3
        fx = load_table3()
        output = {}
        for code, result in bundle["segments"].items():
            request = result.get("request", {})
            values = {"year_period": request.get("appraisal_period"), "segment_code": code,
                      "zone_range_description": request.get("segment_scope"),
                      "land_use_status": fx.SEGMENT_META[code]["land_use_status"]}
            # Table 3 accepts raw survey names/distances.  Only values with
            # actual evidence are passed through; missing points remain blank.
            for point in result.get("points", []):
                if point.get("value") is not None:
                    values[point["field"]] = point["value"]
            for fid, point in regional_factor_points(result).items():
                if point.get("value") is not None:
                    values[fid] = point["value"]
            output[code] = values
        return output

    def _render_official_pdf(self, case_no, bundle):
        from domain.models import Table51Analysis, Table4Analysis
        from pdf.shulin_official_pdf_renderer import render_shulin_official_six_page_pdf
        return render_shulin_official_six_page_pdf(
            self._table3_data(bundle), Table51Analysis.model_validate(bundle["table51"]),
            Table4Analysis.model_validate(bundle["table4"]), case_no=case_no,
            appraisal_base_date=bundle.get("appraisal_base_date"))

    def create(self, payload, appraisal_documents=(), criteria_document=b""):
        if not isinstance(payload, dict):
            raise ValueError("案件資料必須是 JSON 物件")
        case_no = str(payload.get("case_no") or "").strip()
        if not case_no:
            raise ValueError("請輸入案號")
        district = str(payload.get("district") or "").strip()
        segment_code = str(payload.get("segment_code") or "").strip()
        document_text = "\n".join(_document_text(data) for _name, data in appraisal_documents)
        codes = {"P001-00", "P002-00", "P003-00", "P004-00"}
        is_shulin = district == "樹林區" and segment_code in codes
        is_shulin = is_shulin or ("新北市樹林區" in document_text and "P001-00" in document_text)
        if not is_shulin:
            raise LocalWorkflowError("目前六頁表3／表5-1／表4規則包只支援樹林區 P001-00～P004-00 案件")

        with self._lock:
            bundle, locations = self._collect_all_segments(case_no)
            pdf_bytes = self._render_official_pdf(case_no, bundle)
            self.storage.mkdir(parents=True, exist_ok=True)
            self._pdf_path(case_no).write_bytes(pdf_bytes)
            record = {
                "schema_version": "local-automatic-workflow-1", "case_no": case_no,
                "status": "MANUAL_REVIEW_REQUIRED", "created_at": datetime.now(timezone.utc).isoformat(),
                "metadata": {**payload, "profile_id": PROFILE},
                "documents": [{"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                              for name, data in appraisal_documents],
                "criteria_sha256": hashlib.sha256(criteria_document).hexdigest() if criteria_document else None,
                "locations": locations, "bundle": bundle,
            }
            temp = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.storage, delete=False) as handle:
                    temp = handle.name
                    json.dump(record, handle, ensure_ascii=False)
                os.replace(temp, self._record_path(case_no))
            finally:
                if temp and os.path.exists(temp):
                    os.unlink(temp)
            self._write_exports(record, pdf_bytes)
        return {"case_no": case_no, "status": record["status"], "profile_id": PROFILE}

    def _write_exports(self, record, pdf_bytes):
        from export.json_exporter import export_bundle_to_json_bytes, json_filename
        from export.zip_bundle import excel_only_zip_bytes
        case_no = record["case_no"]
        export = self.export_bundle(case_no, record)
        excel_bytes = excel_only_zip_bytes(export)
        self._excel_path(case_no).write_bytes(excel_bytes)
        with zipfile.ZipFile(self._zip_path(case_no), "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(json_filename(case_no), export_bundle_to_json_bytes(export))
            with zipfile.ZipFile(io.BytesIO(excel_bytes)) as excel:
                for name in excel.namelist():
                    archive.writestr("excel/" + name, excel.read(name))
            archive.writestr("official_6_page.pdf", pdf_bytes)
            archive.writestr("public-data-record.json", json.dumps(record, ensure_ascii=False, indent=2))

    def export_bundle(self, case_no, record=None):
        """The shared CaseExportBundle contract (JSON/Excel/ZIP), built from
        the local workflow record instead of DynamoDB."""
        from backend.competition_fixtures import load_table3
        fx = load_table3()
        from export.manual_review_items import all_manual_review_items
        from export.models import CaseExportBundle, SCHEMA_VERSION
        record = record or self.load(case_no)
        if not record:
            raise KeyError(case_no)
        bundle = record["bundle"]
        table3_values = self._table3_data(bundle)
        segments, table3 = {}, {}
        for index, code in enumerate(TABLE3_PAGE_ORDER, 1):
            request = bundle["segments"][code].get("request", {})
            is_base = code == "P001-00"
            segments[code] = {
                "segment_code": code, "segment_role": "BASE_SEGMENT" if is_base else f"COMPARABLE_SEGMENT_{index}",
                "comparison_index": None if is_base else index, "district": fx.DISTRICT,
                "land_use_type": fx.LAND_USE_TYPE, "parcel_ids": fx.SEGMENT_META[code]["parcel_ids"],
                "coordinate_evidence": request.get("coordinate_evidence"),
            }
            table3[code] = {"segment_code": code, "factors": [
                {"field_id": field, "raw_value": value} for field, value in table3_values[code].items()
                if not isinstance(value, (list, dict))]}
        return CaseExportBundle(
            schema_version=SCHEMA_VERSION, generated_at=datetime.now(timezone.utc), case_no=case_no,
            profile_id=PROFILE, appraisal_base_date=bundle.get("appraisal_base_date"),
            base_segment_code="P001-00", comparable_segment_codes=["P002-00", "P003-00", "P004-00"],
            case={"case_no": case_no, "city": fx.CITY, "district": fx.DISTRICT, "land_use_type": fx.LAND_USE_TYPE,
                  "appraisal_period": fx.APPRAISAL_PERIOD, "rule_profile_id": PROFILE},
            segments=segments, table3=table3, table5_1=bundle["table51"], table4=bundle["table4"],
            review=None, review_status="NOT_AVAILABLE_FOR_SHULIN_YET",
            manual_review_items=all_manual_review_items(bundle["table51"], bundle["table4"]),
            provenance={"source": "backend/local_workflow_service.py public-data PARTIAL_DRAFT workflow",
                        "notice": bundle.get("notice")},
        )

    def result(self, case_no):
        record = self.load(case_no)
        if not record:
            raise KeyError(case_no)
        bundle = record["bundle"]
        missing = sum(len(seg.get("missing_field_ids", [])) for seg in bundle["segments"].values())
        table4 = bundle["table4"]
        warnings = (sum(r.get("requires_manual_review", False) for c in bundle["table51"]["comparisons"]
                        for r in c["factor_results"])
                    + sum(r.get("requires_manual_review", False) for c in table4["comparisons"]
                          for r in c["individual_factor_results"]))
        # error/inconsistent come from a segment-aware review that does not
        # exist yet: unknown (None), never a claimed 0.
        return {"case_no": case_no, "status": record["status"],
                "final_value": {"base_parcel_comparison_price": table4.get("base_comparison_price"),
                                "calculation_mode": table4.get("calculation_mode"),
                                "basis": table4.get("base_comparison_price_basis")},
                "review_summary": {"passed": None, "error": None, "warning": warnings,
                                   "missing": missing, "inconsistent": None}}

    def pdf_response(self, case_no):
        if not self._pdf_path(case_no).is_file():
            raise KeyError(case_no)
        token = self._key(case_no)
        return {"case_no": case_no, "generated_at": datetime.now(timezone.utc).isoformat(),
                "official_pdf_url": f"/local-files/official-{token}.pdf",
                "official_pdf_status": "READY", "official_pdf_page_count": 6,
                "forms": {"official_form": {"title": "官方六頁式查估書表（表3x4+表5-1+表4）"}}}

    def analysis(self, case_no):
        record = self.load(case_no)
        if not record:
            raise KeyError(case_no)
        grades, adjustments = [], []
        for comparison in record["bundle"]["table51"]["comparisons"]:
            for factor in comparison["factor_results"]:
                label = factor.get("factor_name", factor["field_id"])
                grades.append({"factor": f"{comparison['comparable_segment_code']}｜{label}",
                               "grade": factor.get("comparable_grade") or "待人工複核",
                               "rule_id": factor.get("rule_id") or "PUBLIC_DATA"})
                adjustments.append({"factor": f"{comparison['comparable_segment_code']}｜{label}",
                                    "adjustment_pct": factor.get("adjustment_pct"),
                                    "rule_id": factor.get("rule_id") or "PUBLIC_DATA"})
        return {"case_no": case_no, "grades": grades, "adjustments": adjustments}

    def collection(self, case_no):
        record = self.load(case_no)
        if not record:
            raise KeyError(case_no)
        code = record["metadata"].get("segment_code") or "P001-00"
        return record["bundle"]["segments"].get(code, record["bundle"]["segments"]["P001-00"])

    def form_completion(self, case_no):
        record = self.load(case_no)
        if not record:
            raise KeyError(case_no)
        fields = []
        for code, segment in record["bundle"]["segments"].items():
            for point in segment["points"]:
                if point.get("value") is not None:
                    status = "COMPLETED" if point.get("status") in {"PROVIDED", "INPUT"} else "MANUAL_REVIEW_REQUIRED"
                    fields.append({"field_id": f"{code}:{point['field']}", "chinese_label": f"{code}｜{point['label']}",
                                   "status": status, "final_value": point["value"], "source": point.get("source")})
        manual = sum(field["status"] == "MANUAL_REVIEW_REQUIRED" for field in fields)
        unknown = sum(len(s["missing_field_ids"]) for s in record["bundle"]["segments"].values())
        return {"case_no": case_no, "completed_count": len(fields)-manual,
                "manual_review_count": manual, "unknown_count": unknown, "fields": fields}

    def file_path(self, name):
        if not re.fullmatch(r"(?:official|bundle|excel)-[0-9a-f]{64}\.(?:pdf|zip)", name):
            return None
        path = self.storage / name
        return path if path.is_file() else None

    def list_cases(self):
        cases = []
        for path in self.storage.glob("workflow-*.json"):
            try:
                record = json.loads(path.read_text("utf-8"))
                cases.append({**record.get("metadata", {}), "case_no": record["case_no"], "status": record["status"],
                              "segments": {code: {"segment_code": code} for code in TABLE3_PAGE_ORDER
                                           if code in record.get("bundle", {}).get("segments", {})}})
            except (OSError, ValueError, KeyError):
                continue
        return {"cases": cases}
