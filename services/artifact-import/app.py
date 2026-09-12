"""IAM-authenticated ingestion service. Never invokes an LLM or changes producer values."""
import base64
import hashlib
import io
import os
import re
import time
import uuid
import sys
import directory
from decimal import Decimal
from datetime import datetime, timezone

import boto3
import simplejson as json
from botocore.config import Config
from botocore.exceptions import ClientError
from pypdf import PdfReader
from splitter import InvalidBundle, decode, encode, split_bundle, select_fields, request_fingerprints

BUCKET = os.environ['BUCKET']
TABLE = os.environ['TABLE']
s3 = boto3.client('s3', config=Config(signature_version='s3v4', retries={'max_attempts': 2}))
db = boto3.resource('dynamodb').Table(TABLE)
MAX_JSON = 5 * 1024 * 1024
MAX_PDF = 20 * 1024 * 1024


class Error(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def fail(status, code, message):
    raise Error(status, code, message)


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


def get(key):
    return db.get_item(Key={'PK': key, 'SK': 'META'}, ConsistentRead=True).get('Item')


def owner_of(event):
    iam = event.get('requestContext', {}).get('authorizer', {}).get('iam', {})
    arn = iam.get('userArn', '')
    if not arn:
        fail(401, 'UNAUTHENTICATED', 'AWS IAM authentication is required')
    # Sessions of the same role share a service workspace, not separate end-user identities.
    if ':assumed-role/' in arn:
        arn = arn.rsplit('/', 1)[0]
    return arn


def owned(key, owner):
    item = get(key)
    if not item or item['owner'] != owner:
        fail(404, 'NOT_FOUND', 'Resource not found in this IAM-role workspace')
    return item


def request_json(event):
    raw = event.get('body') or '{}'
    if event.get('isBase64Encoded'):
        raw = base64.b64decode(raw)
    if len(raw) > 65536:
        fail(413, 'REQUEST_TOO_LARGE', 'Upload file bodies to S3; API metadata limit is 64 KiB')
    value = decode(raw)
    if not isinstance(value, dict):
        fail(400, 'INVALID_REQUEST', 'Request must be a JSON object')
    return value


def descriptor(value, kind):
    if not isinstance(value, dict):
        fail(400, 'INVALID_FILE', 'File descriptor must be an object')
    length, digest = value.get('size_bytes'), value.get('sha256')
    limit = MAX_JSON if kind == 'bundle' else MAX_PDF
    if type(length) is not int or not 0 < length <= limit:
        fail(400, 'INVALID_SIZE', f'{kind} must contain 1–{limit} bytes')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        fail(400, 'INVALID_CHECKSUM', 'sha256 must be lowercase hexadecimal')
    name = value.get('filename', 'bundle.json' if kind == 'bundle' else kind + '.pdf')
    if not isinstance(name, str) or not 1 <= len(name) <= 200 or any(ord(c) < 32 for c in name):
        fail(400, 'INVALID_FILENAME', 'Invalid filename')
    return {'document_id': uid(), 'kind': kind, 'filename': name, 'size_bytes': length,
            'sha256': digest, 'content_type': 'application/json' if kind == 'bundle' else 'application/pdf',
            'segments': value.get('segments', [])}


def upload_links(item):
    result = []
    for doc in item['uploads']:
        checksum = base64.b64encode(bytes.fromhex(doc['sha256'])).decode()
        headers = {'Content-Type': doc['content_type'], 'x-amz-checksum-sha256': checksum}
        params = {'Bucket': BUCKET, 'Key': doc['key'], 'ContentType': doc['content_type'],
                  'ContentLength': int(doc['size_bytes']), 'ChecksumSHA256': checksum}
        result.append({'document_id': doc['document_id'], 'filename': doc['filename'], 'kind': doc['kind'],
                       'url': s3.generate_presigned_url('put_object', Params=params, ExpiresIn=900),
                       'required_headers': headers, 'size_bytes': int(doc['size_bytes'])})
    return {'case_id': item['case_id'], 'group_id': item['group_id'], 'run_id': item['run_id'],
            'status': item['status'], 'expires_in': 900, 'uploads': result}


def create(body, owner, idem):
    if not idem or len(idem) > 200:
        fail(400, 'IDEMPOTENCY_KEY_REQUIRED', 'Provide an Idempotency-Key header (1–200 characters)')
    fingerprint, legacy_fingerprint = request_fingerprints(body)
    key = 'IMPORTKEY#' + hashlib.sha256((owner + '\n' + idem).encode()).hexdigest()
    prior = get(key)
    if prior:
        if prior['fingerprint'] not in (fingerprint, legacy_fingerprint):
            fail(409, 'IDEMPOTENCY_CONFLICT', 'Key already used with a different request')
        item = owned('RUN#' + prior['run_id'], owner)
        return upload_links(item) if item['status'] == 'pending' else public(item)
    files = [descriptor(body.get('bundle'), 'bundle')]
    pdfs = body.get('pdfs', [])
    if not isinstance(pdfs, list) or len(pdfs) > 20:
        fail(400, 'INVALID_PDFS', 'pdfs must be an array of up to 20 file descriptors')
    allowed = {'survey_pdf', 'comparison_pdf', 'regional_factors_pdf', 'source_criteria', 'source_survey'}
    for value in pdfs:
        if not isinstance(value, dict) or value.get('kind') not in allowed:
            fail(400, 'INVALID_KIND', 'Unsupported PDF kind')
        files.append(descriptor(value, value['kind']))
    if sum(d['size_bytes'] for d in files) > 40 * 1024 * 1024:
        fail(413, 'UPLOAD_TOTAL_TOO_LARGE', 'Maximum 40 MiB per import')
    case_id, group_id = body.get('case_id'), body.get('group_id')
    if group_id and not case_id:
        fail(400, 'CASE_REQUIRED', 'group_id requires case_id')
    case = owned('CASE#' + str(case_id), owner) if case_id else None
    group = owned('GROUP#' + str(group_id), owner) if group_id else None
    if group and group['case_id'] != case_id:
        fail(409, 'GROUP_MISMATCH', 'Group belongs to a different case')
    case_id, group_id, run_id = case_id or uid(), group_id or uid(), uid()
    prefix = f'cases/{case_id}/groups/{group_id}/runs/{run_id}/'
    for doc in files:
        doc['key'] = prefix + 'uploads/' + doc['document_id'] + ('.json' if doc['kind'] == 'bundle' else '.pdf')
    item = {'PK': 'RUN#' + run_id, 'SK': 'META', 'owner': owner, 'case_id': case_id,
            'group_id': group_id, 'run_id': run_id, 'status': 'pending', 'created_at': now(),
            'uploads': files, 'prefix': prefix}
    records = [item, {'PK': key, 'SK': 'META', 'owner': owner, 'run_id': run_id, 'fingerprint': fingerprint}]
    if not case:
        records.append({'PK': 'CASE#' + case_id, 'SK': 'META', 'owner': owner, 'case_id': case_id, 'created_at': item['created_at']})
    if not group:
        records.append({'PK': 'GROUP#' + group_id, 'SK': 'META', 'owner': owner,
                        'case_id': case_id, 'group_id': group_id, 'created_at': item['created_at']})
    records += [directory.indexes(r) for r in list(records) if r['PK'].startswith(('CASE#', 'GROUP#', 'RUN#'))]
    from boto3.dynamodb.types import TypeSerializer
    ser = TypeSerializer()
    try:
        boto3.client('dynamodb').transact_write_items(TransactItems=[{'Put': {'TableName': TABLE,
            'Item': {k: ser.serialize(v) for k, v in record.items()},
            'ConditionExpression': 'attribute_not_exists(PK)'}} for record in records])
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'TransactionCanceledException':
            prior = get(key)
            if prior and prior['fingerprint'] in (fingerprint, legacy_fingerprint):
                item = owned('RUN#' + prior['run_id'], owner)
                return upload_links(item) if item['status'] == 'pending' else public(item)
            fail(409, 'CREATE_CONFLICT', 'Retry with the same request and idempotency key')
        raise
    return upload_links(item)


def read_upload(doc):
    try:
        head = s3.head_object(Bucket=BUCKET, Key=doc['key'])
    except ClientError as exc:
        if exc.response['Error']['Code'] in ('404', 'NoSuchKey', 'NotFound'):
            fail(422, 'MISSING_UPLOAD', f"Upload missing: {doc['document_id']}")
        raise
    if head['ContentLength'] != doc['size_bytes']:
        fail(422, 'SIZE_MISMATCH', f"Wrong size: {doc['document_id']}")
    version = head['VersionId']
    raw = s3.get_object(Bucket=BUCKET, Key=doc['key'], VersionId=version)['Body'].read()
    if hashlib.sha256(raw).hexdigest() != doc['sha256']:
        fail(422, 'CHECKSUM_MISMATCH', f"Wrong checksum: {doc['document_id']}")
    return raw, {**doc, 'version_id': version}


def put_json(prefix, kind, payload):
    doc_id, raw = uid(), encode(payload)
    key = prefix + 'json/' + doc_id + '.json'
    result = s3.put_object(Bucket=BUCKET, Key=key, Body=raw, ContentType='application/json')
    return {'document_id': doc_id, 'kind': kind, 'key': key, 'version_id': result['VersionId'],
            'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'content_type': 'application/json'}


def public(item):
    return {k: v for k, v in item.items() if k in
            ('case_id', 'group_id', 'run_id', 'status', 'created_at', 'imported_at', 'forms',
             'documents', 'producer_case_no', 'producer_schema_version', 'pdf_complete', 'last_error')}


def complete(item):
    if item['status'] == 'imported':
        return public(item)
    token = uid()
    try:
        db.update_item(Key={'PK': item['PK'], 'SK': 'META'},
            UpdateExpression='SET #s=:processing, lease_until=:lease, lock_token=:token',
            ConditionExpression='#s=:pending OR (#s=:processing AND lease_until < :now)',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':processing': 'processing', ':pending': 'pending',
                                       ':lease': int(time.time()) + 120, ':now': int(time.time()), ':token': token})
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'ConditionalCheckFailedException':
            fail(409, 'IMPORT_IN_PROGRESS', 'Poll GET; retry after the processing lease expires if needed')
        raise
    try:
        raw, original = read_upload(item['uploads'][0])
        bundle = decode(raw)
        parts = split_bundle(bundle)
        forms, documents = [], [original]
        # Validate all PDFs and page mappings before writing any derived JSON.
        pdfs = []
        for doc in item['uploads'][1:]:
            pdf_raw, record = read_upload(doc)
            try:
                reader = PdfReader(io.BytesIO(pdf_raw), strict=True)
                if reader.is_encrypted:
                    fail(422, 'ENCRYPTED_PDF', 'Encrypted PDFs are not supported')
                page_count = len(reader.pages)
                if page_count < 1:
                    raise ValueError('No pages')
            except Error:
                raise
            except Exception:
                fail(422, 'INVALID_PDF', f"Cannot parse PDF: {doc['document_id']}")
            record['page_count'] = page_count
            pdfs.append(record)
        mappings = {}
        for record in pdfs:
            kind = record['kind']
            if kind.startswith('source_'):
                continue
            if kind == 'survey_pdf':
                refs = record['segments']
                if not isinstance(refs, list) or not refs:
                    fail(422, 'PAGE_MAP_REQUIRED', 'survey_pdf requires segments with page_start/page_end')
            else:
                refs = [{'segment_code': kind, 'page_start': 1, 'page_end': record['page_count']}]
            for ref in refs:
                if not isinstance(ref, dict):
                    fail(422, 'INVALID_PAGE_MAP', 'Page mapping must be an object')
                start, end, code = ref.get('page_start'), ref.get('page_end'), ref.get('segment_code')
                # DynamoDB reloads integer metadata as Decimal.
                if not isinstance(start, (int, Decimal)) or isinstance(start, bool) or not isinstance(end, (int, Decimal)) or isinstance(end, bool) or int(start) != start or int(end) != end or not 1 <= start <= end <= record['page_count']:
                    fail(422, 'INVALID_PAGE_RANGE', 'Page range must lie within the PDF (1-based inclusive)')
                if not isinstance(code, str) or (kind == 'survey_pdf' and code not in bundle['table3']):
                    fail(422, 'UNKNOWN_SEGMENT', 'PDF mapping references an unknown segment')
                if code in mappings:
                    fail(422, 'DUPLICATE_MAPPING', 'A form can have only one PDF mapping in this version')
                mappings[code] = {'document_id': record['document_id'], 'page_start': int(start), 'page_end': int(end)}
        for part in parts:
            record = put_json(item['prefix'], part['kind'], part['payload'])
            documents.append(record)
            if part['kind'] in ('survey', 'comparison', 'regional_factors'):
                form = {'form_id': uid(), 'form_type': part['kind'], 'json_document_id': record['document_id']}
                if 'segment_code' in part:
                    form['segment_code'] = part['segment_code']
                map_key = part.get('segment_code', part['kind'] + '_pdf')
                if map_key in mappings:
                    form['pdf'] = mappings[map_key]
                forms.append(form)
        documents.extend(pdfs)
        item.update(status='imported', imported_at=now(), forms=forms, documents=documents,
                    producer_case_no=bundle.get('case_no'), producer_schema_version=bundle['schema_version'],
                    pdf_complete=all('pdf' in f for f in forms))
        item.pop('last_error', None)
        item.pop('lock_token', None)
        item.pop('lease_until', None)
        if len(encode(item)) > 300000:
            fail(422, 'MANIFEST_TOO_LARGE', 'Import metadata exceeds supported size')
        from boto3.dynamodb.types import TypeSerializer
        ser = TypeSerializer()
        tx = [{'Put': {'TableName': TABLE, 'Item': {k: ser.serialize(v) for k, v in item.items()},
                       'ConditionExpression': 'lock_token=:token',
                       'ExpressionAttributeValues': {':token': ser.serialize(token)}}}]
        # Populate human metadata once; preserve user edits and original run provenance.
        producer_case = bundle.get('case') if isinstance(bundle.get('case'), dict) else {}
        defaults = {'case_no': bundle.get('case_no'), 'district': producer_case.get('district')}
        defaults = {k: v for k, v in defaults.items() if isinstance(v, str) and 0 < len(v) <= 200}
        if defaults:
            names = {f'#f{i}': k for i, k in enumerate(defaults)}
            names['#owner'] = 'owner'
            values = {f':v{i}': ser.serialize(v) for i, v in enumerate(defaults.values())}
            values[':owner'] = ser.serialize(item['owner'])
            tx.append({'Update': {'TableName': TABLE,
                'Key': {'PK': ser.serialize('CASE#' + item['case_id']), 'SK': ser.serialize('META')},
                'UpdateExpression': 'SET ' + ','.join(f'#f{i}=if_not_exists(#f{i},:v{i})' for i in range(len(defaults))),
                'ConditionExpression': '#owner=:owner', 'ExpressionAttributeNames': names,
                'ExpressionAttributeValues': values}})
        boto3.client('dynamodb').transact_write_items(TransactItems=tx)
        return public(item)
    except Exception as exc:
        error = exc.code if isinstance(exc, Error) else 'INVALID_BUNDLE' if isinstance(exc, InvalidBundle) else 'IMPORT_FAILED'
        try:
            db.update_item(Key={'PK': item['PK'], 'SK': 'META'},
                UpdateExpression='SET #s=:pending, last_error=:error REMOVE lease_until, lock_token',
                ConditionExpression='lock_token=:token', ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':pending': 'pending', ':error': error, ':token': token})
        except ClientError:
            pass
        raise


def route(event):
    owner = owner_of(event)
    method = event['requestContext']['http']['method']
    path = event.get('rawPath', '').strip('/').split('/')
    headers = {k.lower(): v for k, v in event.get('headers', {}).items()}
    if len(path) >= 2 and path[:1] == ['v1'] and path[1] in ('cases', 'groups'):
        return directory.dispatch(event, method, path, owner, headers.get('idempotency-key'), sys.modules[__name__])
    if method == 'GET' and path == ['v1', 'health']:
        return {'service': 'artifact-import', 'version': '1.0'}
    if method == 'POST' and path == ['v1', 'imports']:
        return create(request_json(event), owner, headers.get('idempotency-key'))
    if len(path) >= 3 and path[:2] == ['v1', 'imports']:
        item = owned('RUN#' + path[2], owner)
        if method == 'GET' and len(path) == 3:
            return public(item)
        if method == 'POST' and path[3:] == ['upload-access']:
            if item['status'] != 'pending':
                fail(409, 'NOT_PENDING', 'Upload links are only available for pending imports')
            return upload_links(item)
        if method == 'POST' and path[3:] == ['complete']:
            return complete(item)
        if item['status'] != 'imported':
            fail(409, 'NOT_IMPORTED', 'Files are only readable after successful import')
        query = event.get('queryStringParameters') or {}
        if method == 'GET' and len(path) == 5 and path[3] == 'forms':
            form = next((f for f in item['forms'] if f['form_id'] == path[4]), None)
            if not form:
                fail(404, 'FORM_NOT_FOUND', 'Unknown form')
            doc = next(d for d in item['documents'] if d['document_id'] == form['json_document_id'])
            payload = decode(s3.get_object(Bucket=BUCKET, Key=doc['key'], VersionId=doc['version_id'])['Body'].read())
            selected = select_fields(payload, query.get('segment_code'), query.get('field_id'))
            if len(encode(selected)) > 12000:
                fail(413, 'NARROW_SELECTION', 'Use segment_code and field_id, or download the complete JSON via document access')
            return {'run_id': item['run_id'], 'form_id': form['form_id'], 'data': selected}
        if method == 'GET' and len(path) == 6 and path[3] == 'documents' and path[5] == 'access':
            doc = next((d for d in item['documents'] if d['document_id'] == path[4]), None)
            if not doc:
                fail(404, 'DOCUMENT_NOT_FOUND', 'Unknown document')
            disposition = query.get('disposition', 'attachment')
            if disposition not in ('inline', 'attachment'):
                fail(400, 'INVALID_DISPOSITION', 'Choose inline or attachment')
            url = s3.generate_presigned_url('get_object', Params={'Bucket': BUCKET, 'Key': doc['key'],
                'VersionId': doc['version_id'], 'ResponseContentDisposition': disposition}, ExpiresIn=900)
            return {'document_id': doc['document_id'], 'url': url, 'expires_in': 900}
    fail(404, 'ROUTE_NOT_FOUND', 'Unknown endpoint')


def handler(event, context):
    try:
        payload, status = route(event), 200
    except InvalidBundle as exc:
        status, payload = 422, {'error': {'code': 'INVALID_BUNDLE', 'message': str(exc)}}
    except Error as exc:
        status, payload = exc.status, {'error': {'code': exc.code, 'message': exc.message}}
    except Exception as exc:
        # Do not log request bodies, file contents, credentials, or signed URLs.
        print({'request_id': getattr(context, 'aws_request_id', None), 'error_type': type(exc).__name__})
        status, payload = 500, {'error': {'code': 'INTERNAL_ERROR', 'message': 'Retry or inspect server request ID'}}
    return {'statusCode': status, 'headers': {'Content-Type': 'application/json', 'Cache-Control': 'no-store'},
            'body': encode(payload).decode()}
