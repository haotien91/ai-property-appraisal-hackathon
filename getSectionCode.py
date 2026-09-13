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
from typing import Iterable

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
4. 一個方位含多個路段時只保留第一段。
   例：「東榮街及未開闢計畫道路以南」→ south_of 填「東榮街」。
5. 一條路可以同時是多個方位的界線（L 形轉折的路很常見）。
   看到「以X及以Y」就要把該路填進兩個欄位。
   例：「潭興街107巷21弄以東及以北」
       → east_of 與 north_of 都填「潭興街107巷21弄」。
6. 四個方位欄位都必須有值，不可留空或省略。
   描述只提到三個方位時，代表其中一條路兼任兩個方位，請依規則 5 判斷。
7. section.name 與 section.code 填上面給定的值；parcel 只填數字，不含「地號」。
8. zone 填分區名稱。括號內的補充說明要保留，例
   「捷運開發區(變更前為第一種住宅區)」照原樣填入。

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
    """段籍識別資料。

    可由兩條路徑取得，欄位相同：
      resolve_section(lon, lat)                    座標 → TownVillagePointQuery
      resolve_section_by_name(county, town, name)  文字 → ListLandSection

    座標路徑會多回傳村里；文字路徑沒有村里資訊，該欄位為空字串。
    座標與村里都只是附帶資訊，出圖不會用到 —— 所有幾何都以 NLSC 依
    地號回傳的實際範圍為準。
    """

    county_code: str
    county_name: str
    town_code: str
    town_name: str
    office_code: str
    office_name: str
    section_code: str
    section_name: str
    village_name: str = ""
    longitude: float | None = None
    latitude: float | None = None
    source: str = "point"

    @property
    def summary(self) -> str:
        extra = f"，{self.village_name}" if self.village_name else ""
        return (
            f"{self.county_name}{self.town_name}{self.section_name}"
            f"（段代碼 {self.section_code}，{self.office_name}地政{extra}）"
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
        source="point",
    )


# ---------------------------------------------------------------------------
# 純文字的段籍查詢：不需要座標
#
# 座標在整個 pipeline 裡只用於「查段代碼」這一件事，之後所有幾何都改用
# NLSC 依地號回傳的實際範圍。因此只要能從文字拿到段代碼，座標就不必要。
#
# 三支清單 API 就夠：
#   ListCounty                        縣市名 → 縣市代碼
#   ListTown/{縣市}                   行政區名 → 行政區代碼
#   ListLandSection/{縣市}/{行政區}    段名 → 段代碼
#
# 注意段名會跨行政區重複（實測新北市 1,218 種段名中有 72 種重複，
# 例如太平段在新店區是 0797、樹林區是 1921），所以行政區是必要條件，
# 不能只給段名。
# ---------------------------------------------------------------------------

LIST_COUNTY_API = "https://api.nlsc.gov.tw/other/ListCounty"
LIST_TOWN_API = "https://api.nlsc.gov.tw/other/ListTown"
LIST_SECTION_API = "https://api.nlsc.gov.tw/other/ListLandSection"

# 段籍索引檔。段代碼幾乎不變（新設地段是罕見事件），每次出圖都去打
# ListLandSection 是浪費：新北市有 29 個行政區、1,218 種段名，
# 全查一輪要三十秒以上，而且多一個對外服務的失敗點。
# 先查本地索引，查不到才回頭問 API。
SECTION_INDEX_PATH = Path(__file__).parent / "data" / "land_sections.json"

_county_cache: dict[str, str] | None = None
_town_cache: dict[str, dict[str, str]] = {}
_section_cache: dict[tuple[str, str], list[tuple[str, str, str, str]]] = {}
_index_cache: dict | None = None
_index_missing_logged = False


def _fetch_xml(url: str):
    response = requests.get(url, verify=False, timeout=25)
    response.raise_for_status()
    try:
        return ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise ValueError(f"NLSC 回傳的 XML 無法解析（{url}）：{exc}") from exc


def list_counties() -> dict[str, str]:
    """縣市名 → 縣市代碼。結果快取，一個行程只查一次。"""

    global _county_cache
    if _county_cache is None:
        _county_cache = {
            element.findtext("countyname"): element.findtext("countycode")
            for element in _fetch_xml(LIST_COUNTY_API)
            if element.findtext("countyname") and element.findtext("countycode")
        }
    return _county_cache


def list_towns(county_code: str) -> dict[str, str]:
    """行政區名 → 行政區代碼（例 樹林區 → F17）。"""

    if county_code not in _town_cache:
        _town_cache[county_code] = {
            element.findtext("townname"): element.findtext("towncode")
            for element in _fetch_xml(f"{LIST_TOWN_API}/{county_code}")
            if element.findtext("townname") and element.findtext("towncode")
        }
    return _town_cache[county_code]


def load_section_index(path: str | Path | None = None) -> dict:
    """載入本地段籍索引；不存在時回空 dict 而非拋錯。

    索引只是加速用的快取，缺了仍可走 API，因此不該讓它成為硬相依。
    """

    global _index_cache, _index_missing_logged
    if _index_cache is not None and path is None:
        return _index_cache

    target = Path(path) if path else SECTION_INDEX_PATH
    if not target.exists():
        if not _index_missing_logged and path is None:
            _index_missing_logged = True
            print(
                f"（無段籍索引 {target.name}，改用 NLSC API；"
                "可執行 `python getSectionCode.py build-index` 建立）",
                file=sys.stderr,
            )
        index: dict = {}
    else:
        try:
            index = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"（段籍索引讀取失敗，改用 API：{exc}）", file=sys.stderr)
            index = {}

    if path is None:
        _index_cache = index
    return index


def _sections_from_index(county_name: str, town_name: str):
    """從本地索引取某行政區的地段清單；查不到回 None。"""

    index = load_section_index()
    county = (index.get("counties") or {}).get(county_name)
    if not county:
        return None
    town = (county.get("towns") or {}).get(town_name)
    if not town:
        return None
    return [
        (
            entry["code"],
            name,
            entry.get("office_code", ""),
            entry.get("office_name", ""),
        )
        for name, entry in (town.get("sections") or {}).items()
    ]


def list_sections(county_code: str, town_code: str):
    """回傳 [(段代碼, 段名, 事務所代碼, 事務所名)]，直接打 API。"""

    key = (county_code, town_code)
    if key not in _section_cache:
        root = _fetch_xml(f"{LIST_SECTION_API}/{county_code}/{town_code}")
        _section_cache[key] = [
            (
                element.findtext("sectcode") or "",
                element.findtext("sectstr") or "",
                element.findtext("office") or "",
                element.findtext("officestr") or "",
            )
            for element in root
        ]
    return _section_cache[key]


def build_section_index(
    county_names: Iterable[str] = ("新北市",),
    path: str | Path | None = None,
) -> Path:
    """把指定縣市的所有地段抓下來寫成 JSON 索引。

    段代碼幾乎不變，所以這是一次性作業，產物可以進版控。
    """

    target = Path(path) if path else SECTION_INDEX_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    counties = list_counties()
    payload: dict = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "https://api.nlsc.gov.tw/other/ListLandSection",
        "counties": {},
    }

    for county_name in county_names:
        resolved_name, county_code = _pick(county_name, counties, "縣市")
        towns = list_towns(county_code)
        print(f"{resolved_name}（{county_code}）：{len(towns)} 個行政區")

        county_entry: dict = {"county_code": county_code, "towns": {}}
        total = 0
        for index, (town_name, town_code) in enumerate(
            sorted(towns.items()), start=1
        ):
            try:
                rows = list_sections(county_code, town_code)
            except (requests.RequestException, ValueError) as exc:
                print(f"  [{index}/{len(towns)}] {town_name} 查詢失敗：{exc}")
                continue
            sections = {
                name: {
                    "code": code,
                    "office_code": office_code,
                    "office_name": office_name,
                }
                for code, name, office_code, office_name in rows
                if name and code
            }
            county_entry["towns"][town_name] = {
                "town_code": town_code,
                "sections": sections,
            }
            total += len(sections)
            print(f"  [{index}/{len(towns)}] {town_name:8s} {len(sections):5d} 個地段")

        county_entry["section_count"] = total
        payload["counties"][resolved_name] = county_entry
        print(f"  合計 {total:,} 個地段")

    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"\n已寫出 {target}（{target.stat().st_size / 1024:,.0f} KB）")
    return target


def _pick(name: str, table: dict[str, str], label: str) -> tuple[str, str]:
    """在名稱表裡找 name；找不到時列出接近的候選幫助排查。"""

    if name in table:
        return name, table[name]
    # 容忍「臺／台」互換，官方資料用「臺」。
    swapped = name.replace("台", "臺")
    if swapped in table:
        return swapped, table[swapped]
    similar = [key for key in table if name in key or key in name]
    hint = f"；接近的有 {'、'.join(similar[:5])}" if similar else ""
    raise ValueError(f"找不到{label}「{name}」{hint}")


def resolve_section_by_name(
    county_name: str,
    town_name: str,
    section_name: str,
) -> SectionContext:
    """由「縣市＋行政區＋段名」查段代碼，不需要座標。

    段名會跨行政區重複，所以三者都要給。段名支援小段
    （例如「石灰坑段石灰坑小段」）。
    """

    county_name = county_name.replace("台", "臺")
    town_name = town_name.replace("台", "臺")

    # 先查本地索引，命中就完全不碰網路。
    sections = _sections_from_index(county_name, town_name)
    if sections is not None:
        index = load_section_index()
        county_code = index["counties"][county_name]["county_code"]
        town_code = index["counties"][county_name]["towns"][town_name]["town_code"]
        lookup_source = "index"
    else:
        counties = list_counties()
        county_name, county_code = _pick(county_name, counties, "縣市")
        towns = list_towns(county_code)
        town_name, town_code = _pick(town_name, towns, "行政區")
        sections = list_sections(county_code, town_code)
        lookup_source = "api"

    wanted = section_name.strip()
    exact = [row for row in sections if row[1] == wanted]
    if not exact:
        # 使用者可能只寫大段名而資料是「大段＋小段」，或反之。
        loose = [row for row in sections if wanted and wanted in row[1]]
        if len(loose) == 1:
            exact = loose
        elif len(loose) > 1:
            names = "、".join(f"{row[1]}({row[0]})" for row in loose[:8])
            raise ValueError(
                f"{county_name}{town_name}有多個段名包含「{wanted}」：{names}；"
                "請提供完整段名"
            )
        else:
            available = "、".join(row[1] for row in sections[:10])
            raise ValueError(
                f"{county_name}{town_name}找不到段名「{wanted}」；"
                f"該區共 {len(sections)} 個地段，前幾個為 {available}"
            )

    section_code, resolved_name, office_code, office_name = exact[0]
    return SectionContext(
        county_code=county_code,
        county_name=county_name,
        town_code=town_code,
        town_name=town_name,
        office_code=office_code,
        office_name=office_name,
        section_code=section_code,
        section_name=resolved_name,
        source=f"name/{lookup_source}",
    )


# 基本資料的格式：{縣市}{行政區}{段名}{地號}[、{地號}…]地號
# 例：新北市樹林區太平段367、917地號
_PARCEL_TAIL = re.compile(r"([0-9\-]+(?:\s*[、,，]\s*[0-9\-]+)*)\s*地號\s*$")


def parse_basic_info(text: str) -> dict:
    """從基本資料文字拆出縣市、行政區、段名與地號清單。

    不用正則猜縣市與行政區，而是拿 NLSC 的官方清單做最長前綴比對 ——
    「新北市」「新莊區」這類名稱用正則很容易切錯。
    """

    # 官方資料一律用「臺」，但使用者常打「台」，先正規化再比對。
    cleaned = re.sub(r"\s+", "", text).replace("台", "臺").strip()
    if not cleaned:
        raise ValueError("基本資料為空")

    # 縣市與行政區的名稱表優先取自本地索引，索引沒有才問 API。
    index = load_section_index()
    indexed_counties = index.get("counties") or {}

    counties = dict.fromkeys(indexed_counties) or None
    county_name = max(
        (name for name in indexed_counties if cleaned.startswith(name)),
        key=len,
        default=None,
    )
    if county_name is None:
        counties = list_counties()
        county_name = max(
            (name for name in counties if cleaned.startswith(name)),
            key=len,
            default=None,
        )
    if county_name is None:
        raise ValueError(
            f"基本資料開頭不是已知縣市：「{cleaned[:12]}…」；"
            "格式應為「新北市樹林區太平段367、917地號」"
        )
    rest = cleaned[len(county_name) :]

    indexed_towns = (indexed_counties.get(county_name) or {}).get("towns") or {}
    town_name = max(
        (name for name in indexed_towns if rest.startswith(name)),
        key=len,
        default=None,
    )
    if town_name is None:
        county_code = (
            indexed_counties.get(county_name, {}).get("county_code")
            or list_counties()[county_name]
        )
        towns = list_towns(county_code)
        town_name = max(
            (name for name in towns if rest.startswith(name)),
            key=len,
            default=None,
        )
    if town_name is None:
        raise ValueError(
            f"「{county_name}」之後不是已知行政區：「{rest[:12]}…」"
        )
    rest = rest[len(town_name) :]

    match = _PARCEL_TAIL.search(rest)
    if not match:
        raise ValueError(
            f"找不到地號部分：「{rest}」；結尾應為「367、917地號」這種格式"
        )
    section_name = rest[: match.start()].strip()
    if not section_name:
        raise ValueError(f"找不到段名：「{rest}」")

    parcels = [
        token.strip()
        for token in re.split(r"[、,，]", match.group(1))
        if token.strip()
    ]
    if not parcels:
        raise ValueError(f"找不到地號：「{rest}」")

    return {
        "county_name": county_name,
        "town_name": town_name,
        "section_name": section_name,
        "parcels": parcels,
    }


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


def _normalize_for_compare(text: str) -> str:
    """比對分區名稱時去掉括號補充與空白。

    「捷運開發區(變更前為第一種住宅區)」與「捷運開發區」應視為相同 ——
    出圖端的 _normalize_zone_name 也是這樣處理。
    """

    return re.sub(r"[（(].*?[）)]|\s+", "", str(text))


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


# ---------------------------------------------------------------------------
# 案件資料來源：地價智審 workflow bundle
#
# 出圖需要的兩個輸入都在 bundle 裡，不必人工轉抄：
#   bundle.segments.{區段編號}.request.base_parcel_id  完整地段地號
#   bundle.segments.{區段編號}.request.segment_scope   區段範圍描述
# ---------------------------------------------------------------------------

LOCAL_CASES_DIR = Path(
    os.environ.get(
        "LOCAL_CASES_DIR",
        str(Path(__file__).parent / "地價智審_AI_Offline_Candidate_v1" / "data" / "local_cases"),
    )
)

_segments_cache: dict[str, dict] | None = None


@dataclass(frozen=True)
class SegmentRequest:
    """一個區段的出圖輸入。"""

    segment_code: str
    basic_info: str
    section_description: str
    zone_from_factor: str | None = None
    case_no: str | None = None
    source_file: str | None = None

    @property
    def summary(self) -> str:
        return f"{self.segment_code}｜{self.basic_info}｜{self.section_description}"


def load_segments(
    directory: str | Path | None = None,
    *,
    refresh: bool = False,
) -> dict[str, SegmentRequest]:
    """掃描 local_cases 的 workflow JSON，回傳 {區段編號: SegmentRequest}。

    同一個區段編號出現在多個檔案時，以較晚的 created_at 為準 ——
    重跑產生的新 bundle 應該覆蓋舊的。
    """

    global _segments_cache
    if _segments_cache is not None and directory is None and not refresh:
        return _segments_cache

    root = Path(directory) if directory else LOCAL_CASES_DIR
    found: dict[str, tuple[str, SegmentRequest]] = {}

    if root.exists():
        for path in sorted(root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            created = str(data.get("created_at") or "")
            case_no = data.get("case_no")
            segments = (data.get("bundle") or {}).get("segments") or {}
            for code, segment in segments.items():
                request = (segment or {}).get("request") or {}
                parcel = (request.get("base_parcel_id") or "").strip()
                scope = (request.get("segment_scope") or "").strip()
                if not parcel or not scope:
                    continue
                zone = None
                for factor in request.get("competition_provided_factors") or []:
                    if "使用分區" in str(factor.get("factor", "")):
                        zone = factor.get("raw_value")
                        break
                entry = SegmentRequest(
                    segment_code=str(code),
                    basic_info=parcel,
                    section_description=scope,
                    zone_from_factor=zone,
                    case_no=case_no,
                    source_file=path.name,
                )
                previous = found.get(str(code))
                if previous is None or created >= previous[0]:
                    found[str(code)] = (created, entry)

    result = {code: entry for code, (_, entry) in found.items()}
    if directory is None:
        _segments_cache = result
    return result


def get_segment(
    segment_code: str,
    directory: str | Path | None = None,
) -> SegmentRequest:
    """取單一區段的出圖輸入；找不到時列出可用編號。"""

    segments = load_segments(directory)
    if segment_code in segments:
        return segments[segment_code]
    if not segments:
        raise ValueError(
            f"在 {Path(directory) if directory else LOCAL_CASES_DIR} 找不到任何"
            "含 base_parcel_id 與 segment_scope 的 workflow JSON；"
            "可用環境變數 LOCAL_CASES_DIR 指定目錄"
        )
    raise ValueError(
        f"找不到區段編號「{segment_code}」；可用的有 "
        f"{'、'.join(sorted(segments))}"
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


def resolve_context(
    basic_info: str,
    *,
    longitude: float | str | None = None,
    latitude: float | str | None = None,
) -> SectionContext:
    """取得段籍資料。有座標就用座標，沒有就從基本資料文字查。

    兩條路徑得到的段代碼應該一致；座標只是另一種輸入方式，
    不是必要條件。實務上評價基準明細表給的是地段地號而非座標，
    所以文字路徑才是主要入口。
    """

    if longitude is not None and latitude is not None:
        return resolve_section(longitude, latitude)

    parsed = parse_basic_info(basic_info)
    return resolve_section_by_name(
        parsed["county_name"], parsed["town_name"], parsed["section_name"]
    )


def run_pipeline(
    basic_info: str,
    section_description: str,
    *,
    longitude: float | str | None = None,
    latitude: float | str | None = None,
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

    context = resolve_context(
        basic_info, longitude=longitude, latitude=latitude
    )
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
            "basic_info": basic_info,
            "section_description": section_description,
            "zone_code": zone_code,
            "county": county,
            "district": district,
            # 座標為選用輸入；未提供時段籍由基本資料文字查得。
            "longitude": float(longitude) if longitude is not None else None,
            "latitude": float(latitude) if latitude is not None else None,
            "section_source": context.source,
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
    basic_info = _read_text(args.basic_info, args.basic_info_file, "--basic-info")
    context = resolve_context(
        basic_info, longitude=args.lon, latitude=args.lat
    )
    print(f"段籍查詢結果：{context.summary}（來源：{context.source}）")
    print("-" * 60)
    prompt = build_prompt(
        context,
        basic_info,
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

    llm = None
    if llm_response is None and args.llm:
        from llm_provider import make_llm

        llm = make_llm(args.llm, model_id=args.model_id, region=args.region)

    if llm_response is None and llm is None:
        # 兩者都沒給時只印 prompt，讓使用者手動貼去問模型，再用
        # --llm-response-file 回來跑完後半段。
        context = resolve_context(
            basic_info, longitude=args.lon, latitude=args.lat
        )
        print(f"段籍查詢結果：{context.summary}（來源：{context.source}）")
        print("\n未指定 --llm 也未提供 --llm-response-file，只產生 prompt。")
        print("接 Bedrock 請加 --llm bedrock，或把模型回覆存檔後用")
        print("  --llm-response-file <回覆檔> 重跑本指令完成出圖。\n")
        print(build_prompt(context, basic_info, description))
        return 0

    result = run_pipeline(
        basic_info,
        description,
        longitude=args.lon,
        latitude=args.lat,
        zone_code=args.zone_code,
        llm=llm,
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


def _command_build_index(args: argparse.Namespace) -> int:
    build_section_index(args.county or ("新北市",), args.output)
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
    prompt_parser.add_argument(
        "--lon", help="經度（WGS84）。選用：未給時段代碼由基本資料文字查得"
    )
    prompt_parser.add_argument("--lat", help="緯度（WGS84）。選用，同 --lon")
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

    index_parser = subparsers.add_parser(
        "build-index",
        help="把縣市的所有地段抓成本地 JSON 索引，之後不必每次查 API",
    )
    index_parser.add_argument(
        "--county",
        action="append",
        default=None,
        help="縣市名，可重複指定；預設只抓新北市",
    )
    index_parser.add_argument(
        "-o",
        "--output",
        default=None,
        help=f"輸出路徑，預設 {SECTION_INDEX_PATH.relative_to(Path(__file__).parent)}",
    )
    index_parser.set_defaults(func=_command_build_index)

    run_parser = subparsers.add_parser(
        "run",
        help="端到端：座標+基本資料+描述 → 條件 JSON → 區段圖，並完整留檔",
    )
    run_parser.add_argument(
        "--lon",
        help="經度（WGS84）。選用：未給時段代碼由 --basic-info 的文字查得",
    )
    run_parser.add_argument("--lat", help="緯度（WGS84）。選用，同 --lon")
    run_parser.add_argument(
        "--basic-info",
        help="基本資料，例：新北市樹林區太平段367、917地號",
    )
    run_parser.add_argument("--basic-info-file", help="基本資料檔案，- 為 stdin")
    run_parser.add_argument("--description", help="區段範圍描述")
    run_parser.add_argument("--description-file", help="描述檔案，- 為 stdin")
    run_parser.add_argument("--zone-code", help="區段編號，例 P001-00")
    run_parser.add_argument(
        "--llm",
        choices=("bedrock", "echo"),
        help="呼叫哪個 LLM。bedrock 需設定 AWS_REGION 與 BEDROCK_MODEL_ID",
    )
    run_parser.add_argument(
        "--model-id",
        help="Bedrock model id，未給則讀環境變數 BEDROCK_MODEL_ID",
    )
    run_parser.add_argument(
        "--region",
        help="Bedrock 區域，未給則讀環境變數 AWS_REGION",
    )
    run_parser.add_argument(
        "--llm-response-file",
        help="已有的 LLM 回覆檔案；與 --llm 二選一，兩者都不給則只印 prompt",
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
    # 先載入 .env，讓 --llm bedrock 能取到憑證與 model id。
    # 已存在的環境變數優先，部署時注入的設定不會被 .env 蓋掉。
    from llm_provider import load_env_file

    load_env_file()

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
