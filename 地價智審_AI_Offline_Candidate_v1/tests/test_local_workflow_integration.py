"""Exercise persisted local output without public-network dependencies."""
import json
from pathlib import Path
import sys
import zipfile

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.local_workflow_service import LocalWorkflowService
from providers.public_http import PublicHttpClient


def test_local_creation_pdf_exports_and_reload(tmp_path):
    source = ROOT / 'data/sources/competition/shulin_residential_2026'
    service = LocalWorkflowService(tmp_path / 'cases', PublicHttpClient(tmp_path / 'cache', offline=True))
    created = service.create(
        {'case_no': 'integration-test', 'district': '樹林區', 'segment_code': 'P001-00'},
        [('題目.pdf', (source / '題目.pdf').read_bytes())],
        (source / '評價基準明細表.pdf').read_bytes(),
    )
    assert created['status'] == 'MANUAL_REVIEW_REQUIRED'
    restored = LocalWorkflowService(tmp_path / 'cases')
    assert restored.list_cases()['cases'][0]['case_no'] == 'integration-test'
    pdf = restored.pdf_response('integration-test')
    with fitz.open(restored.file_path(Path(pdf['official_pdf_url']).name)) as document:
        assert len(document) == 6
        assert all(page.get_text().strip() for page in document)
    with zipfile.ZipFile(restored._zip_path('integration-test')) as archive:
        assert 'official_6_page.pdf' in archive.namelist()
        assert any(name.endswith('.xlsx') for name in archive.namelist())
        record = json.loads(archive.read('public-data-record.json'))
        assert record['documents'][0]['sha256']
        assert record['status'] == 'MANUAL_REVIEW_REQUIRED'
    result = restored.result('integration-test')
    assert result['status'] == 'MANUAL_REVIEW_REQUIRED'
    assert result['review_summary']['missing'] > 0
    assert result['review_summary']['passed'] is None
