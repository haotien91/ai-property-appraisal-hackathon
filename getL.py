from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

API_BASE_URL = "https://api.nlsc.gov.tw/dmaps/CadasMapPosition"

@dataclass
class CadastralPosition:
    representative_x: float
    representative_y: float 
    lower_left_x: float
    lower_left_y: float
    upper_right_x: float
    upper_right_y: float

def get_cadastral_position(
    county_code: str,
    section_code: str,
    land_number: str,
    coordinate_system: str = "4326",
) -> CadastralPosition:
    """
    查詢宗地代表點及外包矩形座標。

    Args:
        county_code: 縣市代碼，例如 B。
        section_code: 地段代碼，例如 0012。
        land_number: 8 碼地號，例如 00010000。
        coordinate_system: 4326（經緯度）或 3826（TWD97 TM2）。

    Returns:
        宗地代表點、左下角及右上角座標。
    """
    county_code = county_code.strip().upper()
    section_code = section_code.strip()
    land_number = land_number.strip()
    coordinate_system = coordinate_system.strip()

    if not county_code:
        raise ValueError("縣市代碼不可為空")
    if not section_code:
        raise ValueError("地段代碼不可為空")
    if len(land_number) != 8 or not land_number.isdigit():
        raise ValueError("地號必須是 8 碼數字")
    if coordinate_system not in {"4326", "3826"}:
        raise ValueError("坐標類別代碼只能是 4326 或 3826")

    url = (
        f"{API_BASE_URL}/{county_code}/"
        f"{section_code}/{land_number}/{coordinate_system}"
    )

    response = requests.get(
        url,
        timeout=30,
        headers={
            "Accept": "application/xml",
            "User-Agent": "cadastral-position-client/1.0",
        },
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)

    # API 的 XML 標籤若包含 namespace，移除 namespace 後再比對。
    values = {
        element.tag.rsplit("}", 1)[-1].lower(): element.text
        for element in root.iter()
        if element.text and element.text.strip()
    }

    def get_number(*possible_names: str) -> float:
        for name in possible_names:
            value = values.get(name.lower())
            if value is not None:
                return float(value)
        raise ValueError(
            f"XML 找不到欄位 {possible_names}；實際欄位為 {sorted(values)}"
        )

    return CadastralPosition(
        representative_x=get_number("x", "centerx", "representativex"),
        representative_y=get_number("y", "centery", "representativey"),
        lower_left_x=get_number("minx", "lowerleftx", "leftbottomx"),
        lower_left_y=get_number("miny", "lowerlefty", "leftbottomy"),
        upper_right_x=get_number("maxx", "upperrightx", "righttopx"),
        upper_right_y=get_number("maxy", "upperrighty", "righttopy"),
    )

if __name__ == "__main__":
    try:
        result = get_cadastral_position(
            county_code="B",
            section_code="0012",
            land_number="00010000",
            coordinate_system="4326",
        )
        print(result)
    except requests.HTTPError as exc:
        print(f"API HTTP 錯誤：{exc}")
        if exc.response is not None:
            print(exc.response.text)
    except (requests.RequestException, ET.ParseError, ValueError) as exc:
        print(f"查詢失敗：{exc}")
