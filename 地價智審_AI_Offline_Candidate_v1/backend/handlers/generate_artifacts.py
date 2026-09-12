"""Generation completion publishes the same JSON/PDF snapshot to the case library."""
import base64
import json
import os
import sys
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
import runtime_paths
runtime_paths.bootstrap()

paths = ([Path(os.environ['LAMBDA_TASK_ROOT'])/'artifact_import'] if os.environ.get('LAMBDA_TASK_ROOT')
         else [Path(__file__).resolve().parents[3]/'services/artifact-import'])
for path in paths:
    if path.is_dir(): sys.path.insert(0,str(path))

from client import Client
from deliver_generated import deliver_generated
from common import response, error_response, parse_body


def _render_snapshot(case_no,case_id,group_id):
    from export.bundle_builder import build_case_export_bundle
    from export.pdf_export import render_official_pdf_from_bundle
    from export.page_layout import page_layout
    from domain.models import Table51Analysis, Table4Analysis
    bundle=build_case_export_bundle(case_no)
    pdf=render_official_pdf_from_bundle(bundle)
    _,_,pages=page_layout(bundle.table3,Table51Analysis.model_validate(bundle.table5_1),Table4Analysis.model_validate(bundle.table4))
    snapshot={'bundle':bundle.model_dump_json(), 'pdf':base64.b64encode(pdf).decode(),
              'pages':pages,'case_id':case_id,'group_id':group_id}
    return snapshot


def generate_and_publish(case_no, generation_id, *, case_id=None, group_id=None):
    # Caller reuses this UUID when retrying the same generation. A new UUID means a new run.
    generation_id=str(uuid.UUID(generation_id))
    if case_id: case_id=str(uuid.UUID(case_id))
    if group_id: group_id=str(uuid.UUID(group_id))
    if group_id and not case_id: raise ValueError('group_id requires case_id')
    endpoint=os.environ.get('ARTIFACT_API_ENDPOINT')
    bucket=os.environ.get('PDF_BUCKET_NAME')
    if not endpoint or not bucket:
        raise RuntimeError('ARTIFACT_API_ENDPOINT and PDF_BUCKET_NAME must be configured')
    import hashlib
    key='generation-snapshots/'+hashlib.sha256(case_no.encode()).hexdigest()+'/'+generation_id+'.json'
    s3=boto3.client('s3')
    try:
        snapshot=json.loads(s3.get_object(Bucket=bucket,Key=key)['Body'].read())
    except ClientError as exc:
        if exc.response['Error']['Code'] not in ('NoSuchKey','404'): raise
        snapshot=_render_snapshot(case_no,case_id,group_id)
        try:
            s3.put_object(Bucket=bucket,Key=key,Body=json.dumps(snapshot).encode(),ContentType='application/json',IfNoneMatch='*')
        except ClientError as exc:
            if exc.response['Error']['Code'] not in ('PreconditionFailed','412'): raise
            snapshot=json.loads(s3.get_object(Bucket=bucket,Key=key)['Body'].read())
    if snapshot['case_id']!=case_id or snapshot['group_id']!=group_id:
        raise ValueError('Generation ID already belongs to another destination')
    client=Client(endpoint,region=os.environ.get('AWS_REGION','us-west-2'))
    # One producer case maps to one group unless explicit library UUIDs are supplied.
    identity=hashlib.sha256(case_no.encode()).hexdigest()
    if not case_id:
        case_id=client.call('POST','/v1/cases',{'name':case_no,'case_no':case_no},'pipeline-case-'+identity)['case_id']
    if not group_id:
        group_id=client.call('POST','/v1/cases/'+case_id+'/groups',{'name':'估價書表'},'pipeline-group-'+identity)['group_id']
    return deliver_generated(client,snapshot['bundle'].encode(),base64.b64decode(snapshot['pdf']),
                             snapshot['pages'],generation_id,case_id=case_id,group_id=group_id)


def handler(event, context):
    try:
        body=parse_body(event)
        headers={k.lower():v for k,v in event.get('headers',{}).items()}
        generation_id=headers.get('idempotency-key')
        if not generation_id: return error_response(400,'GENERATION_ID_REQUIRED','請提供同一次生成使用的 Idempotency-Key UUID')
        result=generate_and_publish(event['pathParameters']['id'],generation_id,
                                    case_id=body.get('case_id'),group_id=body.get('group_id'))
        return response(200,{'generation_id':generation_id, 'artifacts':result})
    except (ValueError,KeyError,TypeError):
        return error_response(400,'INVALID_GENERATION','生成參數或目的案件不正確')
    except Exception:
        # No signed URLs or document contents in errors. Same job retries reuse its saved snapshot.
        return error_response(503,'GENERATION_OR_UPLOAD_FAILED','生成或存入案件庫未完成，請使用相同 Idempotency-Key 重試')
