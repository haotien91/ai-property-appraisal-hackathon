# -*- coding: utf-8 -*-
"""
Tests for the competition-day rule-table ingestion path:
engine/rule_table_ingest.py (CSV -> rule records) and
engine/rule_table_validator.py (sanity checks before RuleEngine ever sees
the data). See engine/rule_table_ingest.py module docstring for why this
exists: on competition day the team receives a brand-new 評價基準明細表 for
a segment/land-use category never seen before, and this is the fast,
testable-offline path to turn it into something RuleEngine can load.
"""
import copy
import io
import json
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))

from rule_table_ingest import build_rules_from_csv, RuleTableCsvError  # noqa: E402
from rule_table_validator import RuleTableValidator  # noqa: E402
from rule_engine import RuleEngine  # noqa: E402


FACTORS_CSV = """rule_id_prefix,version,city,district,land_use_type,category,factor,value_type,unit,direction,effective_date,source_document,source_page,source_note
REG-ZONING_INSIDE_OUTSIDE,1.0,新北市,金山區,商業用地,土地使用管制(1),都市計畫（內、外）,boolean,,positive,1140901,評價基準明細表範例.pdf,p.1,測試用資料
REG-MAIN_ROAD_WIDTH,1.0,新北市,金山區,商業用地,交通運輸(2),主要道路寬度,numeric_range,M,positive,1140901,評價基準明細表範例.pdf,p.2,測試用資料
"""

BANDS_CSV = """rule_id_prefix,grade,grade_code,grade_label,lower_bound,upper_bound,lower_inclusive,upper_inclusive,anomaly_flag
REG-ZONING_INSIDE_OUTSIDE,優,1,都市計畫內,,,,,
REG-ZONING_INSIDE_OUTSIDE,劣,5,都市計畫外,,,,,
REG-MAIN_ROAD_WIDTH,劣,5,未滿8M,0,8,TRUE,FALSE,
REG-MAIN_ROAD_WIDTH,普通,3,8M以上未滿15M,8,15,TRUE,FALSE,
REG-MAIN_ROAD_WIDTH,優,1,15M以上,15,,TRUE,,
"""

MATRIX_CSV = """rule_id_prefix,from_grade_code,to_grade_code,adjustment_pct
REG-ZONING_INSIDE_OUTSIDE,1,1,0
REG-ZONING_INSIDE_OUTSIDE,1,5,20
REG-ZONING_INSIDE_OUTSIDE,5,1,-20
REG-ZONING_INSIDE_OUTSIDE,5,5,0
REG-MAIN_ROAD_WIDTH,1,1,0
REG-MAIN_ROAD_WIDTH,1,3,5
REG-MAIN_ROAD_WIDTH,1,5,10
REG-MAIN_ROAD_WIDTH,3,1,-5
REG-MAIN_ROAD_WIDTH,3,3,0
REG-MAIN_ROAD_WIDTH,3,5,5
REG-MAIN_ROAD_WIDTH,5,1,-10
REG-MAIN_ROAD_WIDTH,5,3,-5
REG-MAIN_ROAD_WIDTH,5,5,0
"""


def _build(factors=FACTORS_CSV, bands=BANDS_CSV, matrix=MATRIX_CSV):
    return build_rules_from_csv(io.StringIO(factors), io.StringIO(bands), io.StringIO(matrix))


class TestBuildRulesFromCsv:
    def test_produces_one_record_per_factor_grade(self):
        rules = _build()
        assert len(rules) == 5  # 2 grades for zoning + 3 grades for road width

    def test_rule_id_sequence_matches_ascending_grade_code(self):
        rules = _build()
        zoning = sorted(
            [r for r in rules if r["rule_id"].startswith("REG-ZONING")],
            key=lambda r: r["grade_code"],
        )
        assert [r["rule_id"] for r in zoning] == [
            "REG-ZONING_INSIDE_OUTSIDE-01", "REG-ZONING_INSIDE_OUTSIDE-02",
        ]

    def test_every_row_of_a_factor_carries_the_full_matrix(self):
        rules = _build()
        road = [r for r in rules if r["rule_id"].startswith("REG-MAIN_ROAD_WIDTH")]
        assert all(r["adjustment_matrix"] == road[0]["adjustment_matrix"] for r in road)
        assert set(road[0]["adjustment_matrix"].keys()) == {"1", "3", "5"}

    def test_max_adjustment_computed_from_matrix(self):
        rules = _build()
        zoning = next(r for r in rules if r["rule_id"] == "REG-ZONING_INSIDE_OUTSIDE-01")
        assert zoning["max_adjustment"] == 20

    def test_bounds_and_inclusivity_parsed(self):
        rules = _build()
        # rule_id sequence follows ascending grade_code, so -01 is grade_code=1 (優)
        best = next(r for r in rules if r["rule_id"] == "REG-MAIN_ROAD_WIDTH-01")
        assert best["grade_code"] == 1
        assert best["lower_bound"] == 15
        assert best["upper_bound"] is None
        assert best["lower_inclusive"] is True

        worst = next(r for r in rules if r["grade_code"] == 5 and "MAIN_ROAD_WIDTH" in r["rule_id"])
        assert worst["lower_bound"] == 0
        assert worst["upper_bound"] == 8
        assert worst["lower_inclusive"] is True
        assert worst["upper_inclusive"] is False

    def test_unknown_prefix_in_bands_raises(self):
        bad_bands = BANDS_CSV + "REG-DOES_NOT_EXIST,優,1,x,,,,,\n"
        try:
            _build(bands=bad_bands)
            assert False, "expected RuleTableCsvError"
        except RuleTableCsvError as e:
            assert "REG-DOES_NOT_EXIST" in str(e)

    def test_factor_with_no_bands_raises(self):
        only_zoning_bands = "\n".join(BANDS_CSV.splitlines()[:1] + BANDS_CSV.splitlines()[1:2]) + "\n"
        try:
            _build(bands=only_zoning_bands)
            assert False, "expected RuleTableCsvError"
        except RuleTableCsvError as e:
            assert "REG-MAIN_ROAD_WIDTH" in str(e)

    def test_the_generated_rules_actually_work_in_rule_engine(self):
        """End-to-end proof: CSV -> rules -> RuleEngine.select() succeeds,
        which is the real acceptance bar (not just 'JSON looks plausible')."""
        rules = _build()
        engine = RuleEngine(rules)
        result = engine.grade(
            city="新北市", district="金山區", land_use_type="商業用地",
            factor="主要道路寬度", value=10, unit="M",
        )
        assert result.grade_code == 3


class TestRuleTableValidatorOnRealData:
    def test_regional_rules_has_no_errors(self, regional_rules):
        issues = RuleTableValidator().validate(regional_rules)
        errors = [i for i in issues if i.severity == "ERROR"]
        assert errors == [], "\n".join(str(e) for e in errors)

    def test_individual_rules_has_no_errors(self, individual_rules):
        issues = RuleTableValidator().validate(individual_rules)
        errors = [i for i in issues if i.severity == "ERROR"]
        assert errors == [], "\n".join(str(e) for e in errors)

    def test_csv_round_trip_output_has_no_errors(self):
        rules = _build()
        issues = RuleTableValidator().validate(rules)
        errors = [i for i in issues if i.severity == "ERROR"]
        assert errors == [], "\n".join(str(e) for e in errors)


class TestRuleTableValidatorCatchesBrokenInput:
    def _base(self):
        return copy.deepcopy(_build())

    def test_catches_duplicate_rule_id(self):
        rules = self._base()
        rules[1]["rule_id"] = rules[0]["rule_id"]
        issues = RuleTableValidator().validate(rules)
        assert any("重複出現" in str(i) and i.severity == "ERROR" for i in issues)

    def test_catches_non_square_matrix(self):
        rules = self._base()
        for r in rules:
            if r["rule_id"].startswith("REG-ZONING"):
                r["adjustment_matrix"] = {"1": {"1": 0, "5": 20}}  # missing row "5"
        issues = RuleTableValidator().validate(rules)
        assert any("矩陣的列" in str(i) or "矩陣的欄" in str(i) for i in issues)

    def test_catches_wrong_max_adjustment(self):
        rules = self._base()
        for r in rules:
            if r["rule_id"].startswith("REG-ZONING"):
                r["max_adjustment"] = 999
        issues = RuleTableValidator().validate(rules)
        assert any("max_adjustment" in str(i) and i.severity == "ERROR" for i in issues)

    def test_catches_inconsistent_matrix_across_grade_rows(self):
        rules = self._base()
        target = next(r for r in rules if r["rule_id"] == "REG-ZONING_INSIDE_OUTSIDE-02")
        target["adjustment_matrix"] = {"1": {"1": 0, "5": 999}, "5": {"1": -999, "5": 0}}
        issues = RuleTableValidator().validate(rules)
        assert any("不一致" in str(i) for i in issues)

    def test_catches_invalid_value_type(self):
        rules = self._base()
        rules[0]["value_type"] = "not_a_real_type"
        issues = RuleTableValidator().validate(rules)
        assert any("value_type" in str(i) and i.severity == "ERROR" for i in issues)

    def test_catches_missing_required_field(self):
        rules = self._base()
        del rules[0]["source_document"]
        issues = RuleTableValidator().validate(rules)
        assert any("缺少必要欄位" in str(i) for i in issues)

    def test_flags_asymmetric_matrix_as_warning_not_error(self):
        rules = self._base()
        for r in rules:
            if r["rule_id"].startswith("REG-ZONING"):
                r["adjustment_matrix"] = {"1": {"1": 0, "5": 20}, "5": {"1": -15, "5": 0}}
        issues = RuleTableValidator().validate(rules)
        antisym = [i for i in issues if "正負相反" in str(i)]
        assert antisym and all(i.severity == "WARNING" for i in antisym)


class TestIngestCliWritesValidJson:
    def test_cli_end_to_end(self, tmp_path):
        factors_path = tmp_path / "factors.csv"
        bands_path = tmp_path / "bands.csv"
        matrix_path = tmp_path / "matrix.csv"
        out_path = tmp_path / "out.json"
        factors_path.write_text(FACTORS_CSV, encoding="utf-8")
        bands_path.write_text(BANDS_CSV, encoding="utf-8")
        matrix_path.write_text(MATRIX_CSV, encoding="utf-8")

        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        import ingest_rule_table  # noqa: E402

        old_argv = sys.argv
        try:
            sys.argv = [
                "ingest_rule_table.py",
                "--factors", str(factors_path),
                "--bands", str(bands_path),
                "--matrix", str(matrix_path),
                "--out", str(out_path),
            ]
            exit_code = ingest_rule_table.main()
        finally:
            sys.argv = old_argv

        assert exit_code == 0
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["rule_count"] == 5
        assert data["factor_count"] == 2
        assert len(data["rules"]) == 5


def _rule(factor, grade, grade_code, matrix, rule_id=None, **overrides):
    """Minimal-but-complete synthetic rule record for testing
    RuleTableValidator directly (not via the CSV pipeline) -- used to
    exercise grading systems (7-level, 9-level, locally-decided) that
    data/rules/*.json's real Golden Case data doesn't happen to contain."""
    base = {
        "rule_id": rule_id or f"TEST-{factor}-{grade_code:02d}",
        "version": "1.0", "city": "新北市", "district": "測試區", "land_use_type": "商業用地",
        "category": "測試分類", "factor": factor, "value_type": "boolean", "unit": None,
        "direction": "positive", "lower_bound": None, "upper_bound": None,
        "lower_inclusive": None, "upper_inclusive": None,
        "grade": grade, "grade_code": grade_code, "grade_label": grade,
        "adjustment_matrix": matrix, "max_adjustment": max(
            (abs(v) for row in matrix.values() for v in row.values()), default=0
        ),
        "effective_date": "1150101", "source_document": "測試", "source_page": "p.1",
        "source_note": "測試用資料", "anomaly_flag": None,
    }
    base.update(overrides)
    return base


class TestGradeVocabularyFlexibility:
    """作業手冊 p.48-49／p.52-53 officially documents 5 grading systems
    (2/3/5/7/9-level) with 7 distinct label vocabularies total, not just
    Golden Case's 5-level 優/稍優/普通/稍劣/劣. A validator that only
    accepted that one vocabulary would reject correct official data for a
    competition-day segment/land-use category that happens to use a
    different system (Phase 1 REQ-006/007 confirms the segment WILL be
    different from Golden Case)."""

    def _seven_level_matrix(self):
        codes = ["1", "2", "3", "4", "5", "6", "7"]
        return {a: {b: (int(b) - int(a)) * 5 for b in codes} for a in codes}

    def test_seven_level_grades_produce_no_error(self):
        labels = ["極優", "優", "稍優", "普通", "稍劣", "劣", "極劣"]
        matrix = self._seven_level_matrix()
        rules = [_rule("測試因素7級", label, i + 1, matrix) for i, label in enumerate(labels)]
        issues = RuleTableValidator().validate(rules)
        errors = [i for i in issues if i.severity == "ERROR"]
        assert errors == [], "\n".join(str(e) for e in errors)

    def test_nine_level_grades_produce_no_error(self):
        codes = [str(i) for i in range(1, 10)]
        matrix = {a: {b: (int(b) - int(a)) * 3 for b in codes} for a in codes}
        labels = ["超極優", "極優", "優", "稍優", "普通", "稍劣", "劣", "極劣", "超極劣"]
        rules = [_rule("測試因素9級", label, i + 1, matrix) for i, label in enumerate(labels)]
        issues = RuleTableValidator().validate(rules)
        errors = [i for i in issues if i.severity == "ERROR"]
        assert errors == [], "\n".join(str(e) for e in errors)

    def test_severity_style_five_level_vocabulary_produces_no_error(self):
        """作業手冊同一頁5級制底下另一組用語：無/輕微/中度/嚴重/極嚴重
        （用於「有無及嚴重程度」類因素，非優劣類因素）。"""
        codes = ["1", "2", "3", "4", "5"]
        matrix = {a: {b: (int(b) - int(a)) * 4 for b in codes} for a in codes}
        labels = ["無", "輕微", "中度", "嚴重", "極嚴重"]
        rules = [_rule("測試嚴重程度因素", label, i + 1, matrix) for i, label in enumerate(labels)]
        issues = RuleTableValidator().validate(rules)
        errors = [i for i in issues if i.severity == "ERROR"]
        assert errors == [], "\n".join(str(e) for e in errors)

    def test_locally_decided_ten_plus_level_grade_is_warning_not_error(self):
        """9級以上由地方政府自行決定等級細項——用語不在任何已知清單內時，
        必須是WARNING（可能合法），絕不能是ERROR（會擋下合法的地方自訂資料）。"""
        matrix = {"1": {"1": 0, "2": 5}, "2": {"1": -5, "2": 0}}
        rules = [
            _rule("地方自訂因素", "特級優", 1, matrix),
            _rule("地方自訂因素", "特級劣", 2, matrix),
        ]
        issues = RuleTableValidator().validate(rules)
        errors = [i for i in issues if i.severity == "ERROR"]
        warnings = [i for i in issues if i.severity == "WARNING" and "特級" in str(i)]
        assert errors == []
        assert len(warnings) == 2  # one per unrecognized grade text

    def test_mixing_two_known_vocabularies_within_one_factor_is_flagged(self):
        """同一因素理論上應全部使用同一組用語；混用「優」（優劣組）與
        「嚴重」（嚴重程度組）在同一因素底下，是可疑的抄錄錯誤，應被標記
        （WARNING，因為不能100%排除刻意設計）。"""
        matrix = {"1": {"1": 0, "5": 20}, "5": {"1": -20, "5": 0}}
        rules = [
            _rule("混用因素", "優", 1, matrix),
            _rule("混用因素", "嚴重", 5, matrix),
        ]
        issues = RuleTableValidator().validate(rules)
        errors = [i for i in issues if i.severity == "ERROR"]
        mismatch_warnings = [i for i in issues if "橫跨了一組以上" in str(i)]
        assert errors == []
        assert len(mismatch_warnings) == 1
