"""Upload a producer bundle and optional PDFs using AWS SigV4. No LLM calls."""
import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


class ApiError(RuntimeError):
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload
        error = payload.get('error', {}) if isinstance(payload, dict) else {}
        self.code = error.get('code')
        super().__init__(f'API HTTP {status}: {payload}')


class NetworkError(RuntimeError):
    pass


def finish_import(client, run_id, timeout=180, poll_interval=3):
    """Recover uncertain completion without generating another run."""
    deadline = time.monotonic() + timeout
    retry_complete_at = 0
    while time.monotonic() < deadline:
        if time.monotonic() >= retry_complete_at:
            try:
                return client.call('POST', f'/v1/imports/{run_id}/complete', {})
            except ApiError as exc:
                if not (exc.status >= 500 or exc.status == 429 or
                        (exc.status == 409 and exc.code == 'IMPORT_IN_PROGRESS')):
                    raise
            except NetworkError:
                pass
            retry_complete_at = time.monotonic() + 15
        try:
            result = client.call('GET', f'/v1/imports/{run_id}')
            if result['status'] == 'imported':
                return result
            if result['status'] == 'pending':
                if result.get('last_error'):
                    raise RuntimeError(f"Import {run_id} failed: {result['last_error']}; retry with the same idempotency key")
                retry_complete_at = 0
        except ApiError as exc:
            if exc.status < 500 and exc.status != 429:
                raise
        except NetworkError:
            pass
        time.sleep(poll_interval)
    raise RuntimeError(f'Import {run_id} still processing; poll GET or retry with the same idempotency key')


class Client:
    def __init__(self, endpoint, profile=None, region='us-west-2'):
        self.endpoint = endpoint.rstrip('/')
        self.session = boto3.Session(profile_name=profile, region_name=region)
        self.region = region

    def call(self, method, path, body=None, idempotency_key=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = {'Content-Type': 'application/json'}
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        req = AWSRequest(method=method, url=self.endpoint + path, data=data, headers=headers)
        credentials = self.session.get_credentials()
        if credentials is None:
            raise RuntimeError('Configure AWS credentials or use --profile')
        SigV4Auth(credentials.get_frozen_credentials(), 'execute-api', self.region).add_auth(req)
        http = urllib.request.Request(req.url, data=data, headers=dict(req.headers.items()), method=method)
        try:
            with urllib.request.urlopen(http, timeout=70) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # API response bodies do not include credentials or presigned URLs.
            detail = exc.read().decode()[:2000]
            try:
                payload = json.loads(detail)
            except ValueError:
                payload = {'message': detail}
            raise ApiError(exc.code, payload) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise NetworkError('API network failure; retry with the same idempotency key') from None


def file_descriptor(path):
    p = Path(path)
    data = p.read_bytes()
    return {'filename': p.name, 'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--profile', help='AWS profile; omit to use the default credential chain')
    parser.add_argument('--region', default='us-west-2')
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--pdf-manifest', help='JSON array: path, kind, optional segments page map')
    parser.add_argument('--case-id')
    parser.add_argument('--group-id')
    parser.add_argument('--idempotency-key', help='Reuse this key for a network retry; change for a new version')
    parser.add_argument('--result', default='/tmp/ntpc-import-result.json')
    args = parser.parse_args()
    client = Client(args.endpoint, args.profile, args.region)
    body = {'bundle': file_descriptor(args.bundle), 'pdfs': []}
    paths = [Path(args.bundle)]
    if args.pdf_manifest:
        manifest_path = Path(args.pdf_manifest).resolve()
        for value in json.loads(manifest_path.read_text()):
            path = Path(value['path'])
            if not path.is_absolute(): path = manifest_path.parent / path
            body['pdfs'].append({**file_descriptor(path), 'kind': value['kind'], 'segments': value.get('segments', [])})
            paths.append(path)
    if args.case_id: body['case_id'] = args.case_id
    if args.group_id: body['group_id'] = args.group_id
    idem = args.idempotency_key or str(uuid.uuid4())
    print('Idempotency-Key:', idem)
    result = client.call('POST', '/v1/imports', body, idem)
    run_id = result['run_id']
    saved = {'idempotency_key': idem, 'endpoint': args.endpoint,
             **{k: result[k] for k in ('case_id', 'group_id', 'run_id', 'status')}}
    Path(args.result).write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n')
    if result['status'] != 'imported':
        for entry, path in zip(result.get('uploads', []), paths if result.get('uploads') else [], strict=True):
            request = urllib.request.Request(entry['url'], data=path.read_bytes(),
                headers=entry['required_headers'], method='PUT')
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    assert response.status == 200
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f'S3 upload failed ({entry["kind"]}), HTTP {exc.code}; rerun with the same idempotency key') from None
        result = finish_import(client, run_id)
    saved['status'] = result['status']
    saved['result'] = result
    Path(args.result).write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('case_id', 'group_id', 'run_id', 'status', 'pdf_complete')}, indent=2))
    print('Saved:', args.result)


if __name__ == '__main__': main()
