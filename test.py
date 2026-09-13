"""完整 pipeline 的手動測試：座標 → Bedrock → 條件 JSON → 區段圖。

這不是 pytest 測試，是會呼叫真實 NLSC／Overpass／Bedrock 的整合腳本，
所以獨立成一支指令而非測試函式（會花錢、會受外部服務狀態影響）。
不連網的邏輯測試請跑 `python -m pytest test_nlsc.py -q`。

用法
----
    python test.py                    # 全部跑（含 Bedrock，會消耗 token）
    python test.py --offline          # 跳過 Bedrock，只驗出圖鏈
    python test.py --case P003        # 換案子（讀 P003.json）
    python test.py --keep             # 保留產出，不清理暫存
    python test.py --list-models      # 只列出可用的 Bedrock 模型

設計
----
分層檢查，每層獨立回報 PASS / FAIL / SKIP。第一個 FAIL 就是問題所在，
不必從一大坨錯誤訊息裡猜。各層之間有依賴時，前層失敗會讓後層 SKIP。

區段描述由條件 JSON 反推，不寫死座標與敘述，因此任何案子都能測 ——
把 constraints 組回中文敘述丟給 LLM，再檢查它拆回來的結果是否一致。
這同時驗證了「敘述 → JSON」這段轉換的正確性。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from llm_provider import describe_loaded, load_env_file

RELATION_LABEL = {
    "north_of": "以北",
    "west_of": "以西",
    "south_of": "以南",
    "east_of": "以東",
}


class Report:
    """收集各層結果，最後印出總表並決定結束碼。"""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, float, str]] = []
        self.failed = False

    def add(self, stage: str, status: str, seconds: float, detail: str = "") -> None:
        self.rows.append((stage, status, seconds, detail))
        if status == "FAIL":
            self.failed = True

    def summary(self) -> None:
        print()
        print("=" * 74)
        print("總結")
        print("=" * 74)
        width = max(len(row[0]) for row in self.rows)
        for stage, status, seconds, detail in self.rows:
            mark = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "----"}[status]
            line = f"  [{mark}] {stage:<{width}}  {seconds:6.2f}s"
            if detail:
                line += f"  {detail}"
            print(line)
        passed = sum(1 for r in self.rows if r[1] == "PASS")
        failed = sum(1 for r in self.rows if r[1] == "FAIL")
        skipped = sum(1 for r in self.rows if r[1] == "SKIP")
        print()
        print(f"  {passed} 通過 / {failed} 失敗 / {skipped} 略過")


def stage(report: Report, name: str):
    """裝飾器風格的計時包裝，把例外轉成 FAIL 而不中斷後續層。"""

    def runner(function, *args, **kwargs):
        print()
        print("=" * 74)
        print(f"{name}")
        print("=" * 74)
        started = time.perf_counter()
        try:
            detail = function(*args, **kwargs) or ""
            report.add(name, "PASS", time.perf_counter() - started, str(detail))
            return True, detail
        except Exception as exc:  # noqa: BLE001 - 逐層回報，不讓單層中斷全局
            elapsed = time.perf_counter() - started
            print(f"  失敗：{type(exc).__name__}: {exc}")
            report.add(name, "FAIL", elapsed, f"{type(exc).__name__}")
            return False, None

    return runner


def describe_constraints(constraints: dict) -> str:
    """把 constraints 組回中文區段描述，餵給 LLM 做往返測試。"""

    parts = [
        f"{constraints[relation]}{label}"
        for relation, label in RELATION_LABEL.items()
        if constraints.get(relation)
    ]
    return "沿" + "、".join(parts[:-1]) + f"及{parts[-1]}之{constraints['zone']}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="test.py",
        description="完整 pipeline 的手動整合測試（會呼叫真實外部服務）",
    )
    parser.add_argument("--case", default="P001", help="條件 JSON 名稱，預設 P001")
    parser.add_argument(
        "--offline", action="store_true", help="跳過 Bedrock，只驗出圖鏈"
    )
    parser.add_argument("--keep", action="store_true", help="保留產出不清理")
    parser.add_argument(
        "--list-models", action="store_true", help="只列出可用的 Bedrock 模型"
    )
    args = parser.parse_args(argv)

    print("=" * 74)
    print("地價區段圖 pipeline 整合測試")
    print("=" * 74)
    print(f"  {describe_loaded(load_env_file())}")

    if args.list_models:
        return list_models()

    report = Report()
    workspace = Path("_test_out")
    workspace.mkdir(exist_ok=True)
    case_path = Path(f"{args.case}.json")

    # ---- 第 1 層：設定與相依 ------------------------------------------------
    state: dict = {}

    def check_setup() -> str:
        import zone_map

        init = zone_map.init(
            tile_cache_dir="_tile_cache",
            road_cache_dir="_road_cache",
            result_cache_dir="_result_cache",
        )
        print(f"  分區圖   : {init.zoning_features:,} 筆（{init.zoning_seconds:.1f}s）")
        print(f"  中文字型 : {init.font_path}")
        print(
            f"  快取     : 圖磚 {init.cached_tiles}、"
            f"路網 {init.cached_road_cells} 格、結果 {init.cached_results}"
        )
        if init.font_path is None:
            raise RuntimeError("找不到中文字型，圖上文字會變成豆腐框")
        if not init.zoning_features:
            raise RuntimeError("使用分區圖未載入，無法決定區段範圍")
        for problem in init.problems:
            print(f"  注意     : {problem}")
        state["zone_map"] = zone_map
        return f"{init.zoning_features:,} 筆分區"

    ok_setup, _ = stage(report, "1. 設定與相依")(check_setup)

    # ---- 第 2 層：條件 JSON ------------------------------------------------
    def check_case() -> str:
        from render_zone_boundary import load_cases

        cases = load_cases(case_path)
        state["cases"] = cases
        for case in cases:
            print(
                f"  {case['section']['name']}{case['parcel']} "
                f"（段代碼 {case['section']['code']}）"
            )
        description = describe_constraints(cases[0]["constraints"])
        state["description"] = description
        print(f"  反推描述 : {description}")
        return f"{len(cases)} 筆比準地"

    ok_case, _ = stage(report, "2. 條件 JSON 解析")(check_case) if ok_setup else (False, None)
    if not ok_setup:
        report.add("2. 條件 JSON 解析", "SKIP", 0.0, "前置失敗")

    # ---- 第 3 層：NLSC 地號驗證 --------------------------------------------
    def check_nlsc() -> str:
        from nlsc_map_url import LandParcel
        from nlsc_parcel_map import verify_parcels

        parcels = [
            LandParcel.parse(str(c["section"]["code"]), str(c["parcel"]))
            for c in state["cases"]
        ]
        infos = verify_parcels("新北市", parcels, fetch_images=False)
        for parcel, info in zip(parcels, infos):
            if not info.exists:
                raise RuntimeError(f"NLSC 查無 {parcel.code}")
            print(
                f"  {parcel.code} → {info.display}"
                f"（{info.land_office}地政）{info.center[0]:.6f}, {info.center[1]:.6f}"
            )
        state["infos"] = infos
        state["center"] = infos[0].center
        return f"{len(infos)} 筆皆存在"

    if ok_case:
        ok_nlsc, _ = stage(report, "3. NLSC 地號驗證")(check_nlsc)
    else:
        ok_nlsc = False
        report.add("3. NLSC 地號驗證", "SKIP", 0.0, "前置失敗")

    # ---- 第 4 層：段籍查詢（兩條路徑都驗）----------------------------------
    def check_section() -> str:
        import time as _time

        from getSectionCode import (
            parse_basic_info,
            resolve_section,
            resolve_section_by_name,
        )

        longitude, latitude = state["center"]
        by_point = resolve_section(longitude, latitude)
        print(f"  座標路徑 : {by_point.summary}")

        # 文字路徑：只給「縣市＋行政區＋段名」，不給座標。
        basic = (
            f"{by_point.county_name}{by_point.town_name}{by_point.section_name}"
            + "、".join(str(c["parcel"]) for c in state["cases"])
            + "地號"
        )
        parsed = parse_basic_info(basic)
        started = _time.perf_counter()
        by_name = resolve_section_by_name(
            parsed["county_name"], parsed["town_name"], parsed["section_name"]
        )
        elapsed = _time.perf_counter() - started
        print(f"  文字路徑 : {by_name.summary}")
        print(f"             來源 {by_name.source}，耗時 {elapsed * 1000:.1f} ms")
        print(f"  解析地號 : {parsed['parcels']}")

        if by_point.section_code != by_name.section_code:
            raise RuntimeError(
                f"兩條路徑段代碼不一致：座標 {by_point.section_code}、"
                f"文字 {by_name.section_code}"
            )
        print(f"  兩條路徑段代碼一致 ✓（{by_name.section_code}）")

        expected = str(state["cases"][0]["section"]["code"])
        if by_point.section_code != expected:
            print(
                f"  注意：JSON 的段代碼 {expected} 與 API 回傳的 "
                f"{by_point.section_code} 不同（pipeline 會以 API 為準校正）"
            )

        state["context"] = by_point
        state["basic_info"] = basic
        return f"{by_name.section_name}/{by_name.section_code}（{by_name.source}）"

    if ok_nlsc:
        ok_section, _ = stage(report, "4. 段籍查詢（座標 vs 純文字）")(check_section)
    else:
        ok_section = False
        report.add("4. 段籍查詢（座標 vs 純文字）", "SKIP", 0.0, "前置失敗")

    # ---- 第 5 層：只出圖 ----------------------------------------------------
    def check_render() -> str:
        from PIL import Image

        output = workspace / f"{args.case}_render.png"
        info = state["zone_map"].render_to_file(
            case_path,
            output,
            zone_code=f"{args.case}-00",
            refresh=True,
        )
        image = Image.open(output)
        image.load()
        extrema = image.convert("L").getextrema()
        if extrema[0] == extrema[1]:
            raise RuntimeError("產出的 PNG 是純色空白圖")

        print(f"  面積     : {info.area_m2:,.0f} m²")
        print(f"  邊界來源 : {info.boundary_source}   層級 z{info.zoom}")
        print(f"  宗地在內 : {info.parcel_inside_ratio:.1%}")
        print(f"  方位檢核 : {info.direction_audit or '未檢核'}")
        print(f"  路名標註 : {len(info.road_labels)} 條 — {'、'.join(info.road_labels)}")
        print(f"  PNG      : {output}（{output.stat().st_size / 1024:,.0f} KB，{image.size[0]}x{image.size[1]}）")

        # JSON 指定的四條界線道路必須全部標出，這是硬需求。
        required = set(info.matched_roads.values())
        missing = required - set(info.road_labels)
        if missing:
            raise RuntimeError(f"界線道路未標出：{'、'.join(sorted(missing))}")
        print(f"  四條界線道路全部標出 ✓")

        for message in info.warnings:
            print(f"  警告     : {message}")
        state["render_info"] = info
        return f"{info.area_m2:,.0f} m²，{len(info.road_labels)} 條路名"

    if ok_case:
        ok_render, _ = stage(report, "5. 出圖（不經 LLM）")(check_render)
    else:
        ok_render = False
        report.add("5. 出圖（不經 LLM）", "SKIP", 0.0, "前置失敗")

    # ---- 第 6 層：Bedrock 連通 ---------------------------------------------
    def check_bedrock() -> str:
        import llm_provider

        credentials = llm_provider.describe_credentials()
        for key in ("region", "credential_source", "access_key_id", "arn"):
            if key in credentials:
                print(f"  {key:18s} = {credentials[key]}")
        if credentials.get("problem"):
            raise RuntimeError(credentials["problem"])

        llm = llm_provider.make_bedrock_llm()
        answer = llm("只回覆 OK 兩個字。")
        print(f"  模型回覆 : {answer!r}")
        if not answer:
            raise RuntimeError("模型回覆為空")
        state["llm"] = llm
        return credentials.get("credential_source", "?")

    if args.offline:
        ok_bedrock = False
        report.add("6. Bedrock 連通", "SKIP", 0.0, "--offline")
        print("\n（--offline：跳過 Bedrock 相關層）")
    else:
        ok_bedrock, _ = stage(report, "6. Bedrock 連通")(check_bedrock)

    # ---- 第 7 層：LLM 拆解描述 ---------------------------------------------
    def check_llm_parse() -> str:
        from getSectionCode import build_prompt, parse_ai_json, validate_cases

        context = state["context"]
        original = state["cases"][0]["constraints"]
        prompt = build_prompt(context, state["basic_info"], state["description"])
        raw = state["llm"](prompt)
        print(f"  原始回覆長度 : {len(raw)} 字元")

        parsed = parse_ai_json(raw)
        notes = validate_cases(parsed, context)
        for note in notes:
            print(f"  已校正   : {note}")

        if len(parsed) != len(state["cases"]):
            raise RuntimeError(
                f"筆數不符：預期 {len(state['cases'])} 筆，LLM 給 {len(parsed)} 筆"
            )

        # 往返驗證：描述由 constraints 組成，LLM 應該要能原樣拆回來。
        differing = [
            relation
            for relation in (*RELATION_LABEL, "zone")
            if parsed[0]["constraints"].get(relation) != original.get(relation)
        ]
        for relation in (*RELATION_LABEL, "zone"):
            got = parsed[0]["constraints"].get(relation)
            want = original.get(relation)
            mark = "✓" if got == want else "✗"
            print(f"  {mark} {relation:10s} 預期 {want!r:22s} 得到 {got!r}")
        if differing:
            raise RuntimeError(f"往返不一致：{'、'.join(differing)}")

        state["llm_raw"] = raw
        return "往返一致"

    if ok_bedrock and ok_section:
        ok_llm, _ = stage(report, "7. LLM 拆解區段描述（往返驗證）")(check_llm_parse)
    else:
        ok_llm = False
        report.add(
            "7. LLM 拆解區段描述（往返驗證）",
            "SKIP",
            0.0,
            "--offline" if args.offline else "前置失敗",
        )

    # ---- 第 8 層：完整 pipeline 與留檔 -------------------------------------
    def check_pipeline() -> str:
        from getSectionCode import run_pipeline

        # 刻意不傳座標：驗證只靠基本資料文字也能跑完整條流程。
        result = run_pipeline(
            state["basic_info"],
            state["description"],
            zone_code=f"{args.case}-TEST",
            llm=state["llm"],
            archive_root=str(workspace / "artifacts"),
        )
        print(f"  輸入     : 只有基本資料文字，未提供座標")
        print(f"  面積     : {result.area_m2:,.0f} m²")
        print(f"  方位檢核 : {result.direction_audit}")
        print(f"  產出目錄 : {result.archive_dir}")

        expected_files = (
            "request.json",
            "section.json",
            "prompt.txt",
            "llm_raw.txt",
            "corrections.json",
            "case.json",
            "boundary.png",
            "result.json",
        )
        for name in expected_files:
            path = result.archive_dir / name
            if not path.exists() or path.stat().st_size == 0:
                raise RuntimeError(f"留檔缺漏或為空：{name}")
            print(f"    {name:18s} {path.stat().st_size:>9,} bytes")

        # 出圖面積應與第 5 層一致（同一個區段，走不同入口）
        if ok_render:
            direct = state["render_info"].area_m2
            if abs(direct - result.area_m2) > 1.0:
                raise RuntimeError(
                    f"兩條路徑面積不一致：直接出圖 {direct:,.0f}、"
                    f"pipeline {result.area_m2:,.0f}"
                )
            print(f"  與第 5 層面積一致 ✓（{result.area_m2:,.0f} m²）")

        latest = Path(workspace / "artifacts" / f"{args.case}-TEST" / "latest.json")
        if not latest.exists():
            raise RuntimeError("latest.json 未產生")
        pointer = json.loads(latest.read_text(encoding="utf-8"))
        print(f"  latest   : {pointer['created_at']}")
        return f"{result.area_m2:,.0f} m²，8 個留檔"

    if ok_llm:
        stage(report, "8. 完整 pipeline 與留檔")(check_pipeline)
    else:
        report.add(
            "8. 完整 pipeline 與留檔",
            "SKIP",
            0.0,
            "--offline" if args.offline else "前置失敗",
        )

    report.summary()

    if args.keep:
        print(f"\n  產出保留在 {workspace}/")
    else:
        shutil.rmtree(workspace, ignore_errors=True)
        print(f"\n  已清理 {workspace}/（加 --keep 可保留）")

    return 1 if report.failed else 0


def list_models() -> int:
    """列出這個帳號可用的 Bedrock 模型與跨區推論設定。"""

    import os

    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    control = boto3.client("bedrock", region_name=region)

    print(f"\n=== {region} 的 Amazon 自家模型 ===")
    models = control.list_foundation_models(byProvider="Amazon")["modelSummaries"]
    print(f"  共 {len(models)} 個")
    for model in models:
        modes = ",".join(model.get("inferenceTypesSupported", []))
        print(f"    {model['modelId']:48s} {modes}")

    print(f"\n=== {region} 的 inference profiles ===")
    try:
        profiles = control.list_inference_profiles()["inferenceProfileSummaries"]
        for profile in profiles:
            print(f"    {profile['inferenceProfileId']}")
    except Exception as exc:  # noqa: BLE001
        print(f"    查詢失敗：{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
