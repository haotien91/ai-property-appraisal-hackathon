# -*- coding: utf-8 -*-
"""
segment_table4_fixtures.py — TABLE4-THREE-COMPARABLE-D1 Task 2.

The THREE official Competition Table4 (表4 比較法調查估價表) transaction
fixtures for shulin_residential_2026's real case contract -- P002-00/
P003-00/P004-00's own "0基本資料" block (交易日期/土地正常單價/調整百分率/
調整至估價基準日單價). P001-00 (比準地) has no transaction of its own (it
is the subject being appraised, not a comparable sale).

SOURCE OF TRUTH: data/sources/competition/shulin_residential_2026/題目.pdf
page 6 (0-indexed page 5), the 表4 比較法調查估價表 page -- READ-ONLY,
never modified. Every value was independently re-verified by rendering
that page to an image and visually inspecting it (never taken on faith
from an earlier round's text extraction, nor from the blank 表4比較法調查
估價表(2).xlsx layout source, which carries NO case values at all -- see
docs/phase7/table4_three_comparable_d1.md's Task 1/2 audit).

IMPORTANT correction (Task 2): 題目.pdf's own "0基本資料"/"地價區段" rows
put each parcel address directly in the SAME column as its segment code
(P001-00/P002-00/P003-00/P004-00), which is the least ambiguous possible
source for this mapping -- used here to CONFIRM (and, for segment_table3_
fixtures.py's parcel_ids, CORRECT a transcription error from the earlier
COMPETITION-DOMAIN-MULTI-SEGMENT-B1 round) which address belongs to which
segment_code:

    P001-00 (比準地, 宗地流水號 0003) -> 新北市樹林區樹德段1415地號
    P002-00 (比較標的1, 實例編號1)    -> 新北市樹林區樹德段284地號
    P003-00 (比較標的2, 實例編號2)    -> 新北市樹林區太平段367、917地號
    P004-00 (比較標的3, 實例編號3)    -> 新北市樹林區文林段317地號

None of P002/P003/P004's 20 individual factor cells (面積/寬度/深度/...../
容積率/有無禁限建/其他) are filled in 題目.pdf -- genuinely not surveyed,
never guessed here.
"""
from __future__ import annotations

SOURCE_DOCUMENT = "題目.pdf"
SOURCE_PAGE = "p.6"


def _transaction(transaction_date: str, land_normal_price: int, price_date_adjustment_pct: str,
                  adjusted_price: int) -> dict:
    # Deliberately plain int/str values (never Decimal) -- this dict is
    # transported as an ordinary JSON request body (json.dumps() cannot
    # serialize Decimal); engine/table4_analysis_engine.py converts to
    # Decimal internally at the point of use, the same convention
    # engine/calculation_engine.py's own _d() helper already establishes.
    return {
        "transaction_date_raw": transaction_date,
        "land_normal_price_raw": land_normal_price,
        "price_date_adjustment_pct_raw": price_date_adjustment_pct,
        "adjusted_price_raw": adjusted_price,
        "source_type": "競賽題目提供固定值",
        "source": f"{SOURCE_DOCUMENT} 表4 比較法調查估價表（{SOURCE_PAGE}）",
    }


# 交易日期 / 土地正常單價 / 調整百分率 / 調整至估價基準日單價(元/M2) --
# 逐一視覺核對 題目.pdf p.6 之「0基本資料」區塊，非依猜測順序填入。
P002_TRANSACTION = _transaction("110年9月14日", 130167, "5.96", 137925)
P003_TRANSACTION = _transaction("111年1月11日", 135275, "4.09", 140808)
P004_TRANSACTION = _transaction("110年10月29日", 170909, "5.49", 180292)

SEGMENT_TABLE4_TRANSACTION = {
    "P002-00": P002_TRANSACTION,
    "P003-00": P003_TRANSACTION,
    "P004-00": P004_TRANSACTION,
}

# P001-00 (比準地) 的 0基本資料 只有地址與流水號，無交易/價格相關欄位
# （它是待估價的標的本身，不是一筆買賣實例）。
P001_PARCEL_SERIAL_NUMBER = "0003"
CASE_NO_LABEL = "1110901-99-XXX"
APPRAISAL_BASE_DATE = "1110901"
