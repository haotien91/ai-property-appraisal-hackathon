import os
import io
import shutil
import zipfile
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from backend import competition_fixtures as loader


class FixtureLoadingTests(unittest.TestCase):
    def tearDown(self):
        loader._load.cache_clear()

    def test_ignores_conflicting_data_namespace(self):
        fake = types.ModuleType("data.competition_cases.shulin_residential_2026")
        fake.__path__ = []
        with patch.dict("sys.modules", {"data.competition_cases.shulin_residential_2026": fake}):
            from backend.public_data_service import presets, prepare_request, PROFILE
            self.assertEqual(len(presets()["segments"]), 4)
            request = prepare_request({"case_no": "fixture-loading-test", "segment_code": "P002-00",
                                       "use_project_source": True, "profile_id": PROFILE})
            self.assertEqual(request["district"], loader.load_table3().DISTRICT)
            self.assertEqual(request["transaction"], loader.load_table4().SEGMENT_TABLE4_TRANSACTION["P002-00"])

    def test_works_outside_project_working_directory(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                loader.check_required_fixtures()
            finally:
                os.chdir(original)

    def test_missing_file_reports_concrete_path(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(loader, "FIXTURE_DIR", Path(directory)):
            with self.assertRaises(ImportError) as error:
                loader.load_table3()
            self.assertIn("segment_table3_fixtures.py", str(error.exception))
            self.assertIn(directory, str(error.exception))

    def test_download_style_long_directory(self):
        inputs = {name: (loader.FIXTURE_DIR / (name + ".py")).read_bytes()
                  for name in ("segment_table3_fixtures", "segment_table4_fixtures")}
        with tempfile.TemporaryDirectory() as directory:
            long_dir = Path(directory) / ("downloaded-project-" * 6) / ("nested-project-" * 6) / "fixtures"
            disk_dir = Path("\\\\?\\" + str(long_dir)) if os.name == "nt" else long_dir
            disk_dir.mkdir(parents=True)
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, content in inputs.items():
                    bundle.writestr(name + ".py", content)
            archive.seek(0)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(disk_dir)
            self.assertGreater(len(str(long_dir / "segment_table3_fixtures.py")), 260)
            try:
                with patch.object(loader, "FIXTURE_DIR", long_dir):
                    loader.check_required_fixtures()
            finally:
                # Cleanup needs the extended path too on Windows.
                cleanup_root = long_dir.parents[1]
                assert cleanup_root.is_relative_to(Path(directory)) and cleanup_root != Path(directory)
                shutil.rmtree(disk_dir.parents[1])


if __name__ == "__main__":
    unittest.main()
