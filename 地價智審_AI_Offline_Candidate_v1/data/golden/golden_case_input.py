# -*- coding: utf-8 -*-
"""Builds the Golden Case CompetitionCase structured input (案號1140901-99-001)
from values independently verified against 查估書表範本.pdf in Phase 2/3."""
import sys, os
sys.path.insert(0, "/mnt/user-data/outputs")
from decimal import Decimal
from domain.models import CompetitionCase, FactorInput, Evidence, SourceType

EV = Evidence(source="查估書表範本.pdf", source_type=SourceType.OFFICIAL_FORM_FIELD,
              source_document="查估書表範本.pdf")

def fi(field_id, factor, value, unit=None):
    return FactorInput(field_id=field_id, factor=factor, raw_value=value, unit=unit, evidence=EV)

# ---- 19 individual factors: 比準地 (base) values ----
base_individual = [
    fi("individual_land_area", "面積", 113.21, "M2"),
    fi("individual_land_width", "寬度", 5, "M"),
    fi("individual_land_depth", "深度", 23, "M"),
    fi("individual_land_shape", "形狀", "方形"),
    fi("individual_street_frontage", "臨路情形", "單面臨街"),
    fi("individual_land_terrain", "地勢", "平坦"),
    fi("individual_road_type", "道路種類", "主要道路"),
    fi("individual_frontage_road_width", "面前道路寬度", 18, "M"),
    fi("individual_school_proximity", "接近學校之程度", 150, "M"),
    fi("individual_market_proximity", "接近市場之程度", 30, "M"),
    fi("individual_park_proximity", "接近公園、廣場之程度", 190, "M"),
    fi("individual_station_proximity", "接近車站之程度", 80, "M"),
    fi("individual_commercial_district_proximity", "接近商圈之程度", 0, "M"),
    fi("individual_nuisance_facility", "嫌惡設施之有無", 260, "M"),
    fi("individual_parking_convenience", "停車方便性", "可路邊停車"),
    fi("individual_zoning_designation", "使用分區或編定", "商業區"),
    fi("individual_building_coverage_ratio", "建蔽率", 70, "%"),
    fi("individual_floor_area_ratio", "容積率", 240, "%"),
    fi("individual_construction_restriction", "有無禁限建", "無禁止或限制建築"),
]

# ---- 19 individual factors: 比較標的1 (comparable1) values ----
comp1_individual = [
    fi("individual_land_area", "面積", 111.85, "M2"),
    fi("individual_land_width", "寬度", 7, "M"),
    fi("individual_land_depth", "深度", 16, "M"),
    fi("individual_land_shape", "形狀", "方形"),
    fi("individual_street_frontage", "臨路情形", "單面臨街"),
    fi("individual_land_terrain", "地勢", "平坦"),
    fi("individual_road_type", "道路種類", "次要道路"),
    fi("individual_frontage_road_width", "面前道路寬度", 6, "M"),
    fi("individual_school_proximity", "接近學校之程度", 100, "M"),
    fi("individual_market_proximity", "接近市場之程度", 92, "M"),
    fi("individual_park_proximity", "接近公園、廣場之程度", 200, "M"),
    fi("individual_station_proximity", "接近車站之程度", 190, "M"),
    fi("individual_commercial_district_proximity", "接近商圈之程度", 0, "M"),
    fi("individual_nuisance_facility", "嫌惡設施之有無", 80, "M"),
    fi("individual_parking_convenience", "停車方便性", "不可路邊停車"),
    fi("individual_zoning_designation", "使用分區或編定", "商業區"),
    fi("individual_building_coverage_ratio", "建蔽率", 70, "%"),
    fi("individual_floor_area_ratio", "容積率", 240, "%"),
    fi("individual_construction_restriction", "有無禁限建", "無禁止或限制建築"),
]

# ---- 28 regional factors: 比準地 region (P002-00) and 比較標的1 region (also P002-00, same) ----
_regional_pairs = [
    ("regional_zoning_inside_outside","都市計畫（內、外）","都市計畫內",None),
    ("regional_land_use_zone","使用分區(使用地類別)","第二種商業區",None),
    ("regional_building_coverage_ratio","建蔽率",70,"%"),
    ("regional_floor_area_ratio","容積率",240,"%"),
    ("regional_construction_prohibited","有無禁止建築","無",None),
    ("regional_construction_restricted","有無限制建築（整體開發、面積限制、高度限制……等）","無",None),
    ("regional_main_road_width","主要道路寬度",18,"M"),
    ("regional_avg_road_width","區段內道路平均寬度",12,"M"),
    ("regional_major_station_proximity","接近大型車站之程度",300,"M"),
    ("regional_bus_stop_proximity","站牌之接近程度或密集程度","區段內有",None),
    ("regional_interchange_proximity","交流道之有無及接近交流道之程度",5000,"M"),
    ("regional_road_development","區段內道路規劃及闢建程度","已完全開發",None),
    ("regional_drainage_quality","排水之良否","有排水系統不易淹水",None),
    ("regional_terrain","地勢","該區地勢平坦",None),
    ("regional_market_proximity","接近市場之程度（傳統市場、超級市場、超大型購物中心）","區段內有",None),
    ("regional_park_proximity","接近公園（里鄰公園、一般公園）、廣場、徒步區之程度","區段內有",None),
    ("regional_tourism_proximity","接近觀光遊憩設施之程度","區段內有",None),
    ("regional_parking_convenience","停車場地之便利程度",120,"M"),
    ("regional_utility_facility_proximity","電業設施及公用氣體燃料設施之有無及接近程度",440,"M"),
    ("regional_funeral_facility_proximity","殯葬設施之有無及接近程度",80,"M"),
    ("regional_waste_facility_proximity","廢棄物處理設施之有無及接近程度",5000,"M"),
    ("regional_pollution_proximity","水污染、噪音污染、廢氣污染、廢棄物污染等之有無及接近程度",5000,"M"),
    ("regional_department_store_proximity","百貨公司之有無、數量、接近程度",5000,"M"),
    ("regional_financial_institution_proximity","金融機構之有無、數量、接近程度",210,"M"),
    ("regional_entertainment_proximity","娛樂設施之有無、數量、接近程度",5000,"M"),
    ("regional_exhibition_hotel_proximity","大型展示中心或觀光飯店之有無、數量、接近程度",850,"M"),
    ("regional_customer_traffic","顧客通行量之多寡","顧客通行量多",None),
    ("regional_shop_contiguity","店舖之毗連狀態",90,"%"),
]
base_regional = [fi(fid, factor, val, unit) for fid, factor, val, unit in _regional_pairs]
comp1_regional = [fi(fid, factor, val, unit) for fid, factor, val, unit in _regional_pairs]  # same region P002-00

case = CompetitionCase(
    case_no="1140901-99-001",
    appraisal_period="1140901",
    appraisal_base_date="1140901",
    segment_code="P002-00",
    segment_scope="北側至金包里街以北臨路第一筆宗地，南側至中山路以南第一筆宗地，西側至中正路，東側至福德街之第二種商業區土地劃為P002-00區段。",
    city="新北市", district="金山區", land_use_type="商業用地",
    base_parcel_id="金美段489地號",
    base_parcel_factors=base_individual,
    comparable_ids=["溫泉段218地號"],
    comparable_factors={"溫泉段218地號": comp1_individual},
    comparable_land_normal_price={"溫泉段218地號": Decimal("184763")},
    comparable_transaction_date={"溫泉段218地號": "114.05.28"},
    comparable_price_date_adjustment_rate={"溫泉段218地號": Decimal("2.00")},
    comparable_weight={"溫泉段218地號": Decimal("100")},
)

BASE_REGIONAL = base_regional
COMP_REGIONAL = {"溫泉段218地號": comp1_regional}

if __name__ == "__main__":
    print("Golden Case built:", case.case_no)
    print("base individual factors:", len(base_individual))
    print("comp1 individual factors:", len(comp1_individual))
    print("base regional factors:", len(base_regional))
