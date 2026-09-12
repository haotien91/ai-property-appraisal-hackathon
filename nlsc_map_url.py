"""國土測繪圖資服務雲（NLSC S09SOA）圖台網址產生器。

支援三種官方圖台入口：

1. open   ：只套用底圖與額外圖層。
   https://maps.nlsc.gov.tw/open/{底圖}_B/{圖層1,圖層2}
2. goland ：定位至地段地號並著色地籍區塊（可同時套底圖與圖層）。
   https://maps.nlsc.gov.tw/goland/{縣市}/{地段地號}[/{底圖}_B/{圖層}]
3. go     ：僅定位至經緯度，不會著色地籍。
   https://maps.nlsc.gov.tw/go/{經度}/{緯度}[/{層級}/{底圖}_B/{圖層}]

地段地號為 4+8 碼：段代碼(4) + 母號(4) + 子號(4)。
例：段 1027、地號 489 → 1027 + 0489 + 0000 → 102704890000
"""

from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from enum import Enum
from typing import Iterable
from urllib.parse import quote

MAP_BASE_URL = "https://maps.nlsc.gov.tw"


class CountyCode(Enum):
    """地政縣市英文代碼（與身分證字號首字同一套）。

    值為 (代碼, 名稱, 是否為現行行政區, 舊名別稱)。
    改制後沿用同一代碼者以別稱解析（臺北縣→F、桃園縣→H）；
    已併入直轄市而自有代碼者則保留獨立項目（臺中縣 L、臺南縣 R、高雄縣 S）。
    """

    TAIPEI = ("A", "臺北市", True, ())
    TAICHUNG = ("B", "臺中市", True, ())
    KEELUNG = ("C", "基隆市", True, ())
    TAINAN = ("D", "臺南市", True, ())
    KAOHSIUNG = ("E", "高雄市", True, ())
    NEW_TAIPEI = ("F", "新北市", True, ("臺北縣",))
    YILAN = ("G", "宜蘭縣", True, ())
    TAOYUAN = ("H", "桃園市", True, ("桃園縣",))
    CHIAYI_CITY = ("I", "嘉義市", True, ())
    HSINCHU_COUNTY = ("J", "新竹縣", True, ())
    MIAOLI = ("K", "苗栗縣", True, ())
    TAICHUNG_COUNTY = ("L", "臺中縣", False, ())
    NANTOU = ("M", "南投縣", True, ())
    CHANGHUA = ("N", "彰化縣", True, ())
    HSINCHU_CITY = ("O", "新竹市", True, ())
    YUNLIN = ("P", "雲林縣", True, ())
    CHIAYI_COUNTY = ("Q", "嘉義縣", True, ())
    TAINAN_COUNTY = ("R", "臺南縣", False, ())
    KAOHSIUNG_COUNTY = ("S", "高雄縣", False, ())
    PINGTUNG = ("T", "屏東縣", True, ())
    HUALIEN = ("U", "花蓮縣", True, ())
    TAITUNG = ("V", "臺東縣", True, ())
    KINMEN = ("W", "金門縣", True, ())
    PENGHU = ("X", "澎湖縣", True, ())
    LIENCHIANG = ("Z", "連江縣", True, ())

    def __init__(
        self,
        code: str,
        label: str,
        current: bool,
        aliases: tuple[str, ...],
    ) -> None:
        self.code = code
        self.label = label
        self.current = current
        self.aliases = aliases

    @classmethod
    def parse(cls, value: CountyCode | str) -> CountyCode:
        """接受 enum、代碼字母（F）或中文名稱（新北市／新北／臺北縣）。"""

        if isinstance(value, cls):
            return value

        text = value.strip()
        if not text:
            raise ValueError("縣市不可為空")

        if len(text) == 1:
            upper = text.upper()
            for county in cls:
                if county.code == upper:
                    return county
            raise ValueError(f"不支援的縣市代碼 {value!r}")

        # 中文名稱：正規化台/臺，允許省略「市」「縣」，並比對舊名。
        normalized = text.replace("台", "臺")
        for county in cls:
            candidates = {county.label, county.label[:-1]}
            for alias in county.aliases:
                candidates.update({alias, alias[:-1]})
            if normalized in candidates:
                return county

        supported = "、".join(
            f"{county.code}={county.label}" for county in cls if county.current
        )
        raise ValueError(f"無法解析縣市 {value!r}；現行可用：{supported}")


# 常用底圖代碼（放在網址時需加 _B 後綴）。
class BaseMap(str, Enum):
    # 注意：EMAP 系列上的「225號」這類數字是門牌，不是地號。
    # 要判讀地號請搭配 ExtraLayer.DMAPS，並優先選用不含門牌的底圖。
    EMAP = "EMAP"                        # 臺灣通用電子地圖(含門牌)
    EMAP_GRAY = "EMAP01"                 # 臺灣通用電子地圖(灰階)
    EMAP_NO_HOUSE_NUMBER = "EMAP16"      # 不含等高線及門牌
    PHOTO2 = "PHOTO2"                    # 正射影像(航照圖)
    PHOTO_MIX = "PHOTO_MIX"              # 正射影像(航照混合)

    def __str__(self) -> str:
        return self.value


# 常用額外圖層代碼；完整清單見圖台「圖層列表」。
class ExtraLayer(str, Enum):
    URBAN = "URBAN"                  # 都市計畫使用分區圖(115年4月)
    DMAPS = "DMAPS"                  # 地籍圖(僅供參考)；宗地界線與地號文字來源
    LANDSECT = "LANDSECT"            # 地段外圍圖(段籍圖)
    NON_URBAN = "nURBAN"             # 非都市土地使用分區圖
    NON_URBAN_TYPE = "nURBAN2"       # 非都市土地使用地類別圖
    LUIMAP = "LUIMAP"                # 國土利用現況調查成果圖
    PUBLIC_LAND = "LAND_OPENDATA"    # 公有土地(已同意開放者)
    SCHOOL = "SCHOOL"                # 各級學校範圍圖
    HILLSIDE = "Hillside"            # 山坡地範圍圖
    # NLSCVET 為向量文字圖層，提供地名註記而非地號，且非 WMTS 圖磚。
    TEXT_VECTOR = "NLSCVET"          # 臺灣通用電子地圖(文字向量)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class LandParcel:
    """地段地號。section_code 4 碼、parent_number 母號、child_number 子號。"""

    section_code: str
    parent_number: int
    child_number: int = 0

    def __post_init__(self) -> None:
        if not (self.section_code.isdigit() and len(self.section_code) == 4):
            raise ValueError(f"段代碼必須為 4 位數字：{self.section_code!r}")
        if not 0 < self.parent_number <= 9999:
            raise ValueError(f"母號必須介於 1~9999：{self.parent_number}")
        if not 0 <= self.child_number <= 9999:
            raise ValueError(f"子號必須介於 0~9999：{self.child_number}")

    @classmethod
    def parse(
        cls,
        section_code: str | int,
        land_number: str | int,
    ) -> LandParcel:
        """land_number 可為 489、"489" 或帶子號的 "489-1"。

        section_code 不足 4 碼會自動左補 0（例如 12 → "0012"），
        因為地段代碼慣以前置 0 表示；請確認輸入未漏字。
        """

        section = str(section_code).strip()
        if section.isdigit():
            section = section.zfill(4)

        text = str(land_number).strip()
        if "-" in text:
            parent_text, child_text = text.split("-", 1)
        else:
            parent_text, child_text = text, "0"

        parent_text = parent_text.strip()
        child_text = child_text.strip() or "0"
        if not (parent_text.isdigit() and child_text.isdigit()):
            raise ValueError(f"地號格式錯誤：{land_number!r}（應為 489 或 489-1）")

        return cls(
            section_code=section,
            parent_number=int(parent_text),
            child_number=int(child_text),
        )

    @property
    def land_number_code(self) -> str:
        """8 碼地號：母號(4) + 子號(4)。"""

        return f"{self.parent_number:04d}{self.child_number:04d}"

    @property
    def code(self) -> str:
        """goland 使用的 12 碼地段地號。"""

        return f"{self.section_code}{self.land_number_code}"

    @property
    def display(self) -> str:
        if self.child_number:
            return f"{self.section_code} 段 {self.parent_number}-{self.child_number} 地號"
        return f"{self.section_code} 段 {self.parent_number} 地號"

    def __str__(self) -> str:
        return self.code


def _normalize_base_map(base_map: BaseMap | str) -> str:
    """底圖在網址中必須帶 _B 後綴。"""

    text = str(base_map).strip()
    if not text:
        raise ValueError("底圖代碼不可為空")
    return text if text.endswith("_B") else f"{text}_B"


def _normalize_layers(layers: Iterable[ExtraLayer | str]) -> str:
    codes = [str(layer).strip() for layer in layers]
    codes = [code for code in codes if code]
    if not codes:
        return ""
    for code in codes:
        if "," in code or "/" in code:
            raise ValueError(f"圖層代碼不可包含 , 或 /：{code!r}")
    return ",".join(codes)


def build_open_url(
    base_map: BaseMap | str = BaseMap.EMAP,
    layers: Iterable[ExtraLayer | str] = (),
) -> str:
    """只套用底圖與額外圖層（不定位、不著色）。"""

    parts = [MAP_BASE_URL, "open", _normalize_base_map(base_map)]
    layer_text = _normalize_layers(layers)
    if layer_text:
        parts.append(quote(layer_text, safe=","))
    return "/".join(parts)


def build_goland_url(
    county: CountyCode | str,
    parcels: LandParcel | Iterable[LandParcel],
    base_map: BaseMap | str | None = None,
    layers: Iterable[ExtraLayer | str] = (),
) -> str:
    """定位至地段地號並著色地籍區塊；可同時套底圖與圖層。"""

    parsed_county = CountyCode.parse(county)
    parcel_list = [parcels] if isinstance(parcels, LandParcel) else list(parcels)
    if not parcel_list:
        raise ValueError("至少需要一筆地段地號")

    codes = ",".join(parcel.code for parcel in parcel_list)
    parts = [MAP_BASE_URL, "goland", parsed_county.code, codes]

    layer_text = _normalize_layers(layers)
    if base_map is None and layer_text:
        raise ValueError("指定額外圖層時必須同時指定底圖")
    if base_map is not None:
        parts.append(_normalize_base_map(base_map))
        if layer_text:
            parts.append(quote(layer_text, safe=","))
    return "/".join(parts)


def build_coordinate_url(
    longitude: float,
    latitude: float,
    zoom: int | None = None,
    base_map: BaseMap | str | None = None,
    layers: Iterable[ExtraLayer | str] = (),
) -> str:
    """僅定位至經緯度；此入口不會著色地籍區塊。"""

    if not -180 <= longitude <= 180:
        raise ValueError("經度必須介於 -180 到 180")
    if not -90 <= latitude <= 90:
        raise ValueError("緯度必須介於 -90 到 90")

    parts = [MAP_BASE_URL, "go", f"{longitude:.6f}", f"{latitude:.6f}"]
    layer_text = _normalize_layers(layers)

    if zoom is not None:
        if not 1 <= zoom <= 20:
            raise ValueError("顯示層級必須介於 1 到 20")
        parts.append(str(zoom))
    elif base_map is not None or layer_text:
        raise ValueError("指定底圖或圖層時必須同時指定顯示層級")

    if base_map is None and layer_text:
        raise ValueError("指定額外圖層時必須同時指定底圖")
    if base_map is not None:
        parts.append(_normalize_base_map(base_map))
        if layer_text:
            parts.append(quote(layer_text, safe=","))
    return "/".join(parts)


@dataclass(frozen=True)
class MapUrlPlan:
    """兩段式流程：先 open 套圖層，再 goland 定位著色。"""

    layer_url: str
    parcel_url: str
    combined_url: str
    county: CountyCode
    parcels: tuple[LandParcel, ...]

    def open_in_browser(self, *, two_step: bool = False) -> None:
        """預設直接開一次到位的 combined_url；two_step=True 則依序開兩個網址。"""

        if two_step:
            webbrowser.open(self.layer_url)
            webbrowser.open(self.parcel_url)
            return
        webbrowser.open(self.combined_url)


def build_map_plan(
    county: CountyCode | str,
    parcels: LandParcel | Iterable[LandParcel],
    base_map: BaseMap | str = BaseMap.EMAP,
    layers: Iterable[ExtraLayer | str] = (
        ExtraLayer.URBAN,
        ExtraLayer.DMAPS,
    ),
) -> MapUrlPlan:
    """產生 open 套圖層網址、goland 著色網址，以及一次到位的合併網址。"""

    parsed_county = CountyCode.parse(county)
    parcel_list = tuple(
        [parcels] if isinstance(parcels, LandParcel) else list(parcels)
    )
    if not parcel_list:
        raise ValueError("至少需要一筆地段地號")

    return MapUrlPlan(
        layer_url=build_open_url(base_map, layers),
        parcel_url=build_goland_url(parsed_county, parcel_list),
        combined_url=build_goland_url(parsed_county, parcel_list, base_map, layers),
        county=parsed_county,
        parcels=parcel_list,
    )


if __name__ == "__main__":
    # 新北市（F）、地段 1027、地號 489。
    parcel = LandParcel.parse(section_code="1027", land_number="489")
    plan = build_map_plan(
        county="新北市",
        parcels=parcel,
        base_map=BaseMap.EMAP,
        layers=(ExtraLayer.URBAN, ExtraLayer.DMAPS),
    )

    print(f"縣市：{plan.county.label}（{plan.county.code}）")
    for item in plan.parcels:
        print(f"地段地號：{item.display} → {item.code}")

    print("\n1) 只套圖層（open）：")
    print(f"   {plan.layer_url}")
    print("2) 定位並著色地籍區塊（goland）：")
    print(f"   {plan.parcel_url}")
    print("3) 一次到位（goland + 底圖 + 圖層，建議）：")
    print(f"   {plan.combined_url}")

    # 多筆地號：489 與 489-1。
    multi = build_goland_url(
        county="新北市",
        parcels=[
            LandParcel.parse("1027", "489"),
            LandParcel.parse("1027", "489-1"),
        ],
        base_map=BaseMap.EMAP,
        layers=(ExtraLayer.URBAN, ExtraLayer.DMAPS),
    )
    print("\n多筆地號：")
    print(f"   {multi}")
