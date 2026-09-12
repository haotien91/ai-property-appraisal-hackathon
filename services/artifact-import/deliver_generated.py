"""Publish generated JSON/PDF bytes through the existing artifact API; no archive input."""
import json
import tempfile
import urllib.request
from pathlib import Path
from client import file_descriptor, finish_import
from prepare_pipeline_delivery import prepare


def deliver_generated(client, bundle_bytes, pdf_bytes, pages, idempotency_key, *,
                      case_id=None, group_id=None, case_name=None, group_name=None):
    if not idempotency_key:
        raise ValueError('generation_id is required for retry safety')
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        (root/'bundle.json').write_bytes(bundle_bytes)
        (root/'source.pdf').write_bytes(pdf_bytes)
        (root/'pages.json').write_text(json.dumps(pages))
        manifest=prepare(root/'bundle.json',root/'source.pdf',root/'pages.json',root/'delivery')
        bundle_path=root/'delivery/bundle.json'
        entries = json.loads(manifest.read_text())
        paths = [bundle_path] + [manifest.parent / entry['path'] for entry in entries]
        body = {'bundle':file_descriptor(bundle_path), 'pdfs':[
            dict(file_descriptor(path), kind=entry['kind'], segments=entry['segments'])
            for entry,path in zip(entries, paths[1:], strict=True)]}
        if case_id: body['case_id'] = case_id
        if group_id: body['group_id'] = group_id
        result = client.call('POST','/v1/imports',body,idempotency_key)
        if result['status'] != 'imported':
            uploads = result.get('uploads', [])
            if uploads:
                for upload,path in zip(uploads,paths,strict=True):
                    request = urllib.request.Request(upload['url'],data=path.read_bytes(),
                        headers=upload['required_headers'],method='PUT')
                    with urllib.request.urlopen(request,timeout=120) as response:
                        if response.status != 200: raise RuntimeError('檔案上傳失敗，請重試')
            result = finish_import(client,result['run_id'])
        if not result.get('pdf_complete'):
            raise RuntimeError('匯入完成但 PDF 未齊，請檢查生成內容')
        # Only name containers created by this request; never rename an existing one implicitly.
        if not case_id and case_name:
            client.call('PATCH','/v1/cases/'+result['case_id'],{'name':case_name.strip()})
        if not group_id and group_name:
            client.call('PATCH','/v1/groups/'+result['group_id'],{'name':group_name.strip()})
        return {k:result[k] for k in ('case_id','group_id','run_id','status','pdf_complete')}


def main(argv=None):
    """Producer command: upload existing outputs without re-running generation."""
    import argparse
    import uuid
    from client import Client
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--profile')
    parser.add_argument('--region', default='us-west-2')
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--page-map', required=True)
    parser.add_argument('--generation-id', required=True, type=uuid.UUID)
    parser.add_argument('--case-id', type=uuid.UUID)
    parser.add_argument('--group-id', type=uuid.UUID)
    parser.add_argument('--result', required=True)
    args = parser.parse_args(argv)
    if args.group_id and not args.case_id:
        parser.error('--group-id requires --case-id')
    # Read all producer files before making any AWS request.
    bundle = Path(args.bundle).read_bytes()
    pdf = Path(args.pdf).read_bytes()
    pages = json.loads(Path(args.page_map).read_text())
    result = deliver_generated(
        Client(args.endpoint, args.profile, args.region), bundle, pdf, pages,
        str(args.generation_id),
        case_id=str(args.case_id) if args.case_id else None,
        group_id=str(args.group_id) if args.group_id else None,
    )
    result['generation_id'] = str(args.generation_id)
    Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    main()
