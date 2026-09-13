from unittest.mock import Mock, patch
import json
import os
import pytest
from backend import harness_chat as chat


def service():
    s=Mock();s.load.return_value={'created_at':'2026-09-13T00:00:00+00:00'}
    s.export_bundle.return_value.model_dump.return_value={'case_no':'A','generated_at':'changes','table4':{'price':'123.4500'}}
    return s


def test_snapshot_stable_and_precise():
    s=service();a=chat.snapshot(s,'A')
    s.export_bundle.return_value.model_dump.return_value['generated_at']='new-time'
    assert chat.snapshot(s,'A')==a
    assert json.loads(a[0])['table4']['price']=='123.4500'


def test_missing_case_never_publishes():
    s=service();s.load.return_value=None
    with patch.object(chat,'publish') as publish, pytest.raises(KeyError):chat.ask(s,{'case_no':'missing','question':'price?'})
    publish.assert_not_called()


def test_wrong_version_discards_history_and_disables_legacy_tools():
    s=service();runtime=Mock();runtime.invoke_harness.return_value={'stream':[{'contentBlockDelta':{'delta':{'text':'123.4500'}}}]}
    with patch.object(chat,'publish',return_value={'version':'v','run_id':'r'}), patch.object(chat.boto3,'client',return_value=runtime), patch.dict(os.environ,{'HARNESS_ARN':'test'}):
        result=chat.ask(s,{'case_no':'A','question':'price?','version':'old','history':[{'role':'assistant','text':'WRONG_CASE_VALUE'}]})
    args=runtime.invoke_harness.call_args.kwargs
    assert args['tools']==[] and args['allowedTools']==[]
    assert 'WRONG_CASE_VALUE' not in json.dumps(args['messages'])
    assert '123.4500' in args['messages'][-1]['content'][0]['text']
    assert result['answer']=='123.4500'
