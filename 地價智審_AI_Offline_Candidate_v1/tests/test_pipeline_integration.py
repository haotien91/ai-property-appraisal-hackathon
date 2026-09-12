"""Regression coverage for branch integration boundaries."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT/'backend/handlers', ROOT/'providers', ROOT.parent/'services/artifact-import'):
    sys.path.insert(0, str(directory))


def test_container_data_path(monkeypatch, tmp_path):
    import runtime_paths
    (tmp_path/'data').mkdir()
    monkeypatch.setenv('LAMBDA_TASK_ROOT', str(tmp_path))
    assert runtime_paths.data_dir() == str(tmp_path/'data')


def test_dynamic_page_layout_and_identity():
    from export.page_layout import page_layout
    comparisons = [SimpleNamespace(comparable_segment_code='X'+str(i), comparison_index=i) for i in range(1, 6)]
    analysis = SimpleNamespace(base_segment_code='BASE', comparisons=comparisons)
    order, batches, pages = page_layout(['BASE', *['X'+str(i) for i in range(1, 6)]], analysis, analysis)
    assert order == ['X1','X2','X3','X4','X5','BASE']
    assert batches == 2
    assert pages[-2] == {'kind':'regional_factors','page_start':7,'page_end':8}
    assert pages[-1] == {'kind':'comparison','page_start':9,'page_end':10}
    with pytest.raises(ValueError): page_layout(['BASE'], analysis, analysis)


@pytest.mark.parametrize('stop,raw,status', [
    ('end_turn', {'confidence':'HIGH','reason':'test','candidate_factor_id':'道路'}, 'OK'),
    ('max_tokens', {'confidence':'HIGH','reason':'test'}, 'SCHEMA_INVALID'),
    ('end_turn', {'confidence':'HIGH','reason':'test','price':1}, 'SCOPE_VIOLATION'),
])
def test_converse_keeps_validation_gate(stop, raw, status):
    from semantic_rule_mapping_provider import BedrockSemanticRuleMappingProvider
    client=Mock()
    client.converse.return_value={'stopReason':stop, 'output':{'message':{'content':[{'text':json.dumps(raw)}]}}}
    result=BedrockSemanticRuleMappingProvider(client).propose_candidate('c','text',[],'regional','新北市','樹林','住宅',['道路'])
    assert result.status == status
    assert client.converse.call_args.kwargs['modelId'] == 'us.anthropic.claude-sonnet-4-6'


def test_pdf_delivery_manifest_preserves_identity(tmp_path):
    from prepare_pipeline_delivery import prepare
    from pypdf import PdfWriter, PdfReader
    bundle=ROOT/'frontend/mock/export_json_result.json'
    data=json.loads(bundle.read_text())
    codes=list(data['table3'])
    writer=PdfWriter()
    for _ in range(len(codes)+2): writer.add_blank_page(width=100,height=200)
    pdf=tmp_path/'source.pdf'
    with pdf.open('wb') as stream: writer.write(stream)
    pages=[{'kind':'survey','segment_code':code,'page_start':i+1,'page_end':i+1} for i,code in enumerate(codes)]
    pages += [{'kind':'regional_factors','page_start':len(codes)+1,'page_end':len(codes)+1},
              {'kind':'comparison','page_start':len(codes)+2,'page_end':len(codes)+2}]
    mapping=tmp_path/'pages.json'; mapping.write_text(json.dumps(pages))
    manifest=json.loads(prepare(bundle,pdf,mapping,tmp_path/'out').read_text())
    assert [x['kind'] for x in manifest] == ['survey_pdf','comparison_pdf','regional_factors_pdf']
    assert len(PdfReader(tmp_path/'out/survey.pdf').pages)==len(codes)
    assert [x['segment_code'] for x in manifest[0]['segments']]==codes
    pages[1]['page_start']=1;mapping.write_text(json.dumps(pages))
    with pytest.raises(ValueError, match='Overlapping'): prepare(bundle,pdf,mapping,tmp_path/'invalid')


def test_claude_json_fence_and_prose_rejection():
    from semantic_rule_mapping_provider import BedrockSemanticRuleMappingProvider as P
    raw={'confidence':'HIGH','reason':'test'}
    assert P._parse_bedrock_response({'content':[{'type':'text','text':'```json\n'+json.dumps(raw)+'\n```'}]})==raw
    with pytest.raises(ValueError):
        P._parse_bedrock_response({'content':[{'type':'text','text':'extra prose '+json.dumps(raw)}]})
