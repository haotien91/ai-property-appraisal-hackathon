"""地價區段圖 pipeline 的前段：座標 → 段籍資料 → LLM prompt → 條件 JSON → 出圖。

流程分三步，中間刻意可以斷點，方便人工檢查 LLM 產出的 JSON：

  1. prompt  座標 + 基本資料 + 區段範圍描述 → 印出給 LLM 的 prompt
  2. save    貼回 LLM 的輸出 → 驗證並存成條件 JSON
  3. render  條件 JSON → 呼叫 render_zone_boundary 產出區段圖 PNG

段名與段代碼一律由 NLSC API 決定，不交給 LLM：
實測 LLM 會照抄 prompt 範例裡的「樹德段 / 1902」，導致太平段（1921）
被寫成 1902，NLSC 仍查得到地號但位置整個錯掉。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import requests
import urllib3

# 停用 SSL 略過驗證時產生的警告訊息，避免干擾終端機輸出
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOWN_VILLAGE_API = "https://api.nlsc.gov.tw/other/TownVillagePointQuery"

# LLM 只需要做一件事：把中文的方位敘述拆成四個欄位。
# 段名、段代碼、地號都已由 API 或基本資料決定，因此在 prompt 裡直接給定。
_PROMPT_TEMPLATE = """基本資料：{basic_info}
區段範圍描述：{section_description}

已知（請直接沿用，不要改寫、不要自行推測）：
  縣市 = {county_name}
  行政區 = {town_name}
  段名 = {section_name}
  段代碼 = {section_code}

請把區段範圍描述整理成下列 JSON。規則：
1. 只輸出 JSON，不要加說明文字或 markdown 標記。
2. 一律輸出陣列。基本資料有幾個地號就有幾個元素，每個元素的 constraints 完全相同。
3. 「沿A以北」代表區段在 A 之北 → north_of 填 A，其餘方位同理。
4. 一個方位含多個路段時只保留第一段（例："東榮街、東華街" 取 "東榮街"）。
5. section.name 與 section.code 填上面給定的值；parcel 只填數字，不含「地號」。
6. zone 填分區名稱，例："第一種住宅區"。

輸出格式：
[
  {{
    "section": {{ "name": "{section_name}", "code": "{section_code}" }},
    "parcel": "<地號>",
    "constraints": {{
      "north_of": "<以北的路>",
      "west_of":  "<以西的路>",
      "south_of": "<以南的路>",
      "east_of":  "<以東的路>",
      "zone": "<使用分區>"
    }}
  }}
]
"""

_RELATIONS = ("north_of", "west_of", "south_of", "east_of")


@dataclass(frozen=True)
class SectionContext:
    """NLSC TownVillagePointQuery 的完整回傳。"""

    longitude: float
    latitude: float
    county_code: str
    county_name: str
    town_code: str
    town_name: str
    office_code: str
    office_name: str
    section_code: str
    section_name: str
    village_name: str

    @property
    def summary(self) -> str:
        return (
            f"{self.county_name}{self.town_name}{self.section_name}"
            f"（段代碼 {self.section_code}，{self.office_name}地政，"
            f"{self.village_name}）"
        )


def resolve_section(longitude: float | str, latitude: float | str) -> SectionContext:
    """輸入經緯度（WGS84），取回段代碼、段名與所屬縣市／行政區／地政事務所。

    這支 API 一次就給齊 pipeline 需要的所有識別資料，
    因此不需要再另外查 ListLandSection 做名稱比對。
    """

    url = f"{TOWN_VILLAGE_API}/{longitude}/{latitude}/4326"
    response = requests.get(url, verify=False, timeout=20)
    response.raise_for_status()

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise ValueError(f"NLSC 回傳的 XML 無法解析：{exc}") from exc

    def field(tag: str) -> str:
        element = root.find(f".//{tag}")
        if element is None or not (element.text or "").strip():
            raise ValueError(f"NLSC 回傳缺少 <{tag}>；座標可能不在陸域段籍範圍內")
        return element.text.strip()

    return SectionContext(
        longitude=float(longitude),
        latitude=float(latitude),
        county_code=field("ctyCode"),
        county_name=field("ctyName"),
        town_code=field("townCode"),
        town_name=field("townName"),
        office_code=field("officeCode"),
        office_name=field("officeName"),
        section_code=field("sectCode"),
        section_name=field("sectName"),
        village_name=field("villageName"),
    )


def get_sect_code(lon, lat):
    """僅取 sectCode 的簡化版，保留原有呼叫方式。"""

    try:
        return resolve_section(lon, lat).section_code
    except requests.exceptions.RequestException as exc:
        return f"網路請求失敗: {exc}"
    except ValueError as exc:
        return str(exc)


def build_prompt(
    context: SectionContext,
    basic_info: str,
    section_description: str,
) -> str:
    """組出給 LLM 的 prompt；段名與段代碼由 context 決定。"""

    return _PROMPT_TEMPLATE.format(
        basic_info=basic_info.strip(),
        section_description=section_description.strip(),
        county_name=context.county_name,
        town_name=context.town_name,
        section_name=context.section_name,
        section_code=context.section_code,
    )


def getJson(sectionCode, BasicInfo, SectionDes):
    """保留舊介面：只有段代碼時仍可組 prompt（段名留空由描述帶入）。"""

    return _PROMPT_TEMPLATE.format(
        basic_info=str(BasicInfo).strip(),
        section_description=str(SectionDes).strip(),
        county_name="（依基本資料）",
        town_name="（依基本資料）",
        section_name="（依基本資料）",
        section_code=sectionCode,
    )


def parse_ai_json(text: str) -> list[dict]:
    """解析 LLM 輸出的 JSON；容忍 markdown 圍欄與前後贅字。"""

    payload = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", payload, re.DOTALL)
    if fence:
        payload = fence.group(1).strip()
    else:
        # 沒有圍欄時取第一個 [ 或 { 到最後一個 ] 或 } 之間的內容。
        start = min(
            (position for position in (payload.find("["), payload.find("{")) if position >= 0),
            default=-1,
        )
        end = max(payload.rfind("]"), payload.rfind("}"))
        if start >= 0 and end > start:
            payload = payload[start : end + 1]

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM 輸出不是合法 JSON：{exc}") from exc

    cases = data if isinstance(data, list) else [data]
    if not cases:
        raise ValueError("LLM 輸出的陣列為空")
    return cases


def validate_cases(cases: list[dict], context: SectionContext | None = None) -> list[str]:
    """檢查條件 JSON 是否夠出圖，並把段名段代碼校正回 API 的值。

    回傳修正說明；結構性缺漏則直接丟 ValueError。
    """

    notes: list[str] = []
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"第 {index} 筆不是物件")

        section = case.get("section")
        if not isinstance(section, dict):
            raise ValueError(f"第 {index} 筆缺少 section")
        if not str(case.get("parcel") or "").strip():
            raise ValueError(f"第 {index} 筆缺少 parcel")

        # 地號常被寫成「367地號」或「367、917」，前者可修、後者是漏拆。
        raw_parcel = str(case["parcel"]).strip()
        cleaned = raw_parcel.replace("地號", "").strip()
        if any(separator in cleaned for separator in ("、", ",", "，")):
            raise ValueError(
                f"第 {index} 筆的 parcel 是「{raw_parcel}」，"
                "多個地號必須拆成陣列中的多筆"
            )
        if cleaned != raw_parcel:
            case["parcel"] = cleaned
            notes.append(f"第 {index} 筆 parcel「{raw_parcel}」→「{cleaned}」")

        constraints = case.get("constraints")
        if not isinstance(constraints, dict):
            raise ValueError(f"第 {index} 筆缺少 constraints")
        missing = [
            relation for relation in _RELATIONS if not str(constraints.get(relation) or "").strip()
        ]
        if missing:
            raise ValueError(f"第 {index} 筆缺少方位條件：{'、'.join(missing)}")
        if not str(constraints.get("zone") or "").strip():
            raise ValueError(f"第 {index} 筆缺少 constraints.zone")

        if context is not None:
            if str(section.get("code") or "") != context.section_code:
                notes.append(
                    f"第 {index} 筆段代碼「{section.get('code')}」"
                    f"→「{context.section_code}」（依 NLSC API 校正）"
                )
                section["code"] = context.section_code
            if str(section.get("name") or "") != context.section_name:
                notes.append(
                    f"第 {index} 筆段名「{section.get('name')}」"
                    f"→「{context.section_name}」（依 NLSC API 校正）"
                )
                section["name"] = context.section_name

    # 同一張圖只能有一組界線條件，出圖端也會擋，這裡先講清楚。
    first = cases[0]["constraints"]
    for index, case in enumerate(cases[1:], start=2):
        differing = [
            relation
            for relation in (*_RELATIONS, "zone")
            if case["constraints"].get(relation) != first.get(relation)
        ]
        if differing:
            raise ValueError(
                f"第 1 筆與第 {index} 筆的 {'、'.join(differing)} 不同，"
                "屬於不同區段，請分成兩份 JSON"
            )
    return notes


def save_cases(cases: list[dict], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(cases, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return target


def render_case_json(
    json_path: str | Path,
    output_path: str | Path,
    zone_code: str | None = None,
    *,
    county: str | None = None,
    district: str | None = None,
    **render_kwargs,
):
    """呼叫 render_zone_boundary 出圖；晚匯入以免只想產 prompt 時付載入成本。"""

    from render_zone_boundary import render_zone_boundary

    if county:
        render_kwargs["county"] = county
    if district:
        render_kwargs["district"] = district
    return render_zone_boundary(
        json_path,
        output_path,
        zone_code=zone_code,
        **render_kwargs,
    )


@dataclass
class PipelineResult:
    """一次完整出圖的產出位置與摘要。"""

    archive_dir: Path
    case_json: Path
    png: Path
    zone_code: str | None
    section: SectionContext
    cases: list[dict]
    corrections: list[str]
    area_m2: float
    direction_audit: dict
    warnings: list[str]


# 產出檔案庫的根目錄。與快取分開是刻意的：
#   快取（_road_cache/_tile_cache/_result_cache）可以隨時刪掉重建，檔名是雜湊
#   產出（ARCHIVE_ROOT）要能追溯，路徑人看得懂，含 LLM 原始回覆
ARCHIVE_ROOT = os.environ.get("ZONE_MAP_ARCHIVE", "artifacts")


def run_pipeline(
    longitude: float | str,
    latitude: float | str,
    basic_info: str,
    section_description: str,
    *,
    zone_code: str | None = None,
    llm=None,
    llm_response: str | None = None,
    archive_root: str | Path = ARCHIVE_ROOT,
    county: str = "新北市",
    district: str | None = None,
    render_kwargs: dict | None = None,
) -> PipelineResult:
    """座標＋基本資料＋區段描述 → 條件 JSON → 區段圖，並完整留檔。

    llm 是可注入的函式，簽名 llm(prompt: str) -> str。
    要用 Bedrock、OpenAI 或任何服務都只換這個參數，本函式不綁定廠商。
    先給 llm_response 則跳過呼叫，直接用既有回覆（測試與重跑用）。

    留檔內容（每次一個時間戳目錄）：
        request.json      原始輸入
        section.json      NLSC 段籍查詢結果
        prompt.txt        送給 LLM 的 prompt
        llm_raw.txt       LLM 原始回覆        ← 稽核關鍵
        corrections.json  系統校正了什麼      ← 稽核關鍵
        case.json         校正後的條件 JSON
        boundary.png      區段圖
        result.json       面積、方位檢核、警告
    """

    if llm is None and llm_response is None:
        raise ValueError("必須提供 llm 函式或 llm_response 內容")

    context = resolve_section(longitude, latitude)
    prompt = build_prompt(context, basic_info, section_description)
    raw = llm_response if llm_response is not None else llm(prompt)

    cases = parse_ai_json(raw)
    corrections = validate_cases(cases, context)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    label = zone_code or f"{context.section_code}-{cases[0]['parcel']}"
    # 區段編號可能含路徑不安全字元，只留字母數字與連字號。
    safe_label = re.sub(r"[^0-9A-Za-z\-_]", "_", label)
    archive_dir = Path(archive_root) / safe_label / stamp
    archive_dir.mkdir(parents=True, exist_ok=True)

    def dump(name: str, payload) -> Path:
        path = archive_dir / name
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return path

    dump(
        "request.json",
        {
            "longitude": float(longitude),
            "latitude": float(latitude),
            "basic_info": basic_info,
            "section_description": section_description,
            "zone_code": zone_code,
            "county": county,
            "district": district,
            "created_at": stamp,
        },
    )
    dump("section.json", context.__dict__)
    dump("prompt.txt", prompt)
    dump("llm_raw.txt", raw)
    dump("corrections.json", corrections)
    case_path = dump("case.json", cases)

    png_path = archive_dir / "boundary.png"
    result = render_case_json(
        case_path,
        png_path,
        zone_code,
        county=county,
        district=district or f"{context.town_name}",
        **(render_kwargs or {}),
    )

    dump(
        "result.json",
        {
            "area_m2": round(result.area_m2, 1),
            "boundary_source": result.boundary_source,
            "zoom": result.zoom,
            "parcel_inside_ratio": round(result.parcel_inside_ratio, 4),
            "parcels": [item.code for item in result.parcels],
            "matched_roads": result.matched_roads,
            "direction_audit": result.direction_audit,
            "road_labels": list(result.road_labels),
            "warnings": result.warnings,
        },
    )

    # 指向最新一次，讓呼叫方不用自己排序時間戳目錄。
    (Path(archive_root) / safe_label / "latest.json").write_text(
        json.dumps(
            {
                "archive_dir": str(archive_dir),
                "case_json": str(case_path),
                "png": str(png_path),
                "created_at": stamp,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return PipelineResult(
        archive_dir=archive_dir,
        case_json=case_path,
        png=png_path,
        zone_code=zone_code,
        section=context,
        cases=cases,
        corrections=corrections,
        area_m2=result.area_m2,
        direction_audit=result.direction_audit,
        warnings=result.warnings,
    )


def _read_text(value: str | None, path: str | None, label: str) -> str:
    if path:
        if path == "-":
            return sys.stdin.read()
        return Path(path).read_text(encoding="utf-8")
    if value:
        return value
    raise ValueError(f"請提供 {label}")


def _command_prompt(args: argparse.Namespace) -> int:
    context = resolve_section(args.lon, args.lat)
    print(f"段籍查詢結果：{context.summary}")
    print("-" * 60)
    prompt = build_prompt(
        context,
        _read_text(args.basic_info, args.basic_info_file, "--basic-info"),
        _read_text(args.description, args.description_file, "--description"),
    )
    print("【產生的 Prompt 如下】\n")
    print(prompt)
    if args.save_prompt:
        Path(args.save_prompt).write_text(prompt, encoding="utf-8")
        print(f"（prompt 已另存：{args.save_prompt}）")
    return 0


def _command_save(args: argparse.Namespace) -> int:
    context = None
    if args.lon is not None and args.lat is not None:
        context = resolve_section(args.lon, args.lat)
        print(f"段籍查詢結果：{context.summary}")

    cases = parse_ai_json(
        _read_text(args.response, args.response_file, "--response 或 --response-file")
    )
    notes = validate_cases(cases, context)
    for note in notes:
        print(f"  已校正：{note}")

    target = save_cases(cases, args.output)
    print(f"已存出條件 JSON：{target}（{len(cases)} 筆比準地）")
    for case in cases:
        print(f"  {case['section']['name']}{case['parcel']} 地號")
    return 0


def _command_render(args: argparse.Namespace) -> int:
    result = render_case_json(
        args.input,
        args.output,
        args.zone_code,
        county=args.county,
        district=args.district,
    )
    print(f"已存出：{result.output_path}（{result.area_m2:,.0f} m²）")
    for message in result.warnings:
        print(f"  - {message}")
    return 0


def _command_run(args: argparse.Namespace) -> int:
    basic_info = _read_text(args.basic_info, args.basic_info_file, "--basic-info")
    description = _read_text(args.description, args.description_file, "--description")

    llm_response = None
    if args.llm_response_file:
        llm_response = _read_text(None, args.llm_response_file, "--llm-response-file")

    if llm_response is None:
        # 還沒接 LLM 時，先把 prompt 印出來讓使用者貼去問，再用
        # --llm-response-file 回來跑完後半段。
        context = resolve_section(args.lon, args.lat)
        print(f"段籍查詢結果：{context.summary}")
        print("\n尚未接上 LLM。請把下列 prompt 送給模型，再用")
        print("  --llm-response-file <回覆檔> 重跑本指令完成出圖。\n")
        print(build_prompt(context, basic_info, description))
        return 0

    result = run_pipeline(
        args.lon,
        args.lat,
        basic_info,
        description,
        zone_code=args.zone_code,
        llm_response=llm_response,
        archive_root=args.archive_root,
        county=args.county,
        district=args.district,
    )

    print(f"段籍：{result.section.summary}")
    for note in result.corrections:
        print(f"  已校正：{note}")
    print(f"比準地 {len(result.cases)} 筆：" + "、".join(
        f"{case['section']['name']}{case['parcel']}" for case in result.cases
    ))
    print(f"區段面積：{result.area_m2:,.0f} m²")
    audit = result.direction_audit
    print(
        "方位檢核："
        + ("全數通過" if audit and all(audit.values()) else str(audit or "未檢核"))
    )
    print(f"\n產出目錄：{result.archive_dir}")
    print(f"  條件 JSON：{result.case_json.name}")
    print(f"  區段圖　：{result.png.name}")
    for message in result.warnings:
        print(f"  - {message}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="getSectionCode.py",
        description="地價區段圖 pipeline：座標 → prompt → 條件 JSON → 區段圖",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prompt_parser = subparsers.add_parser(
        "prompt", help="查段籍並印出給 LLM 的 prompt"
    )
    prompt_parser.add_argument("--lon", required=True, help="經度（WGS84）")
    prompt_parser.add_argument("--lat", required=True, help="緯度（WGS84）")
    prompt_parser.add_argument("--basic-info", help="基本資料，例：新北市樹林區太平段367、917地號")
    prompt_parser.add_argument("--basic-info-file", help="基本資料改由檔案讀入，- 為 stdin")
    prompt_parser.add_argument("--description", help="區段範圍描述")
    prompt_parser.add_argument("--description-file", help="區段範圍描述改由檔案讀入，- 為 stdin")
    prompt_parser.add_argument("--save-prompt", help="同時把 prompt 另存成檔案")
    prompt_parser.set_defaults(func=_command_prompt)

    save_parser = subparsers.add_parser(
        "save", help="把 LLM 回覆驗證後存成條件 JSON"
    )
    save_parser.add_argument("--response", help="LLM 回覆內容")
    save_parser.add_argument("--response-file", help="LLM 回覆檔案，- 為 stdin")
    save_parser.add_argument("-o", "--output", required=True, help="輸出 JSON 路徑")
    save_parser.add_argument("--lon", help="經度；提供時會用 API 校正段名段代碼")
    save_parser.add_argument("--lat", help="緯度；提供時會用 API 校正段名段代碼")
    save_parser.set_defaults(func=_command_save)

    render_parser = subparsers.add_parser("render", help="條件 JSON → 區段圖 PNG")
    render_parser.add_argument("input", help="條件 JSON")
    render_parser.add_argument("-o", "--output", required=True, help="輸出 PNG")
    render_parser.add_argument("--zone-code", help="區段編號，例 P001-00")
    render_parser.add_argument("--county", default="新北市")
    render_parser.add_argument("--district", help="行政區名，預設由 NLSC 段籍推得")
    render_parser.set_defaults(func=_command_render)

    run_parser = subparsers.add_parser(
        "run",
        help="端到端：座標+基本資料+描述 → 條件 JSON → 區段圖，並完整留檔",
    )
    run_parser.add_argument("--lon", required=True, help="經度（WGS84）")
    run_parser.add_argument("--lat", required=True, help="緯度（WGS84）")
    run_parser.add_argument("--basic-info", help="基本資料")
    run_parser.add_argument("--basic-info-file", help="基本資料檔案，- 為 stdin")
    run_parser.add_argument("--description", help="區段範圍描述")
    run_parser.add_argument("--description-file", help="描述檔案，- 為 stdin")
    run_parser.add_argument("--zone-code", help="區段編號，例 P001-00")
    run_parser.add_argument(
        "--llm-response-file",
        help="LLM 回覆檔案；未提供時只印 prompt 不出圖",
    )
    run_parser.add_argument(
        "--archive-root",
        default=ARCHIVE_ROOT,
        help=f"產出檔案庫根目錄，預設 {ARCHIVE_ROOT}",
    )
    run_parser.add_argument("--county", default="新北市")
    run_parser.add_argument("--district", help="行政區名，預設由段籍推得")
    run_parser.set_defaults(func=_command_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError) as exc:
        print(f"失敗：{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"檔案存取失敗：{exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"連線失敗：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
