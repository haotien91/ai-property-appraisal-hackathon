"""Publish already-generated pipeline ZIPs through the existing import API."""
import argparse
import io
import json
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from client import Client, file_descriptor, finish_import
from prepare_pipeline_delivery import prepare

MAX_ZIP_BYTES = 40 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024


def deliver_generated(client, archive, idempotency_key, *, case_id=None, group_id=None,
                      case_name=None, group_name=None):
    if len(archive) > MAX_ZIP_BYTES:
        raise ValueError('生成檔案包過大（上限 40 MB）')
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError('缺少有效的上傳識別碼')
    if group_id and not case_id:
        raise ValueError('組別必須指定所屬案件')
    for name in (case_name, group_name):
        if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or any(ord(c)<32 for c in name)):
            raise ValueError('名稱長度必須介於 1 到 120 字')
    with zipfile.ZipFile(io.BytesIO(archive)) as package, tempfile.TemporaryDirectory() as tmp:
        names = package.namelist()
        if len(names) > 500 or len(names) != len(set(names)):
            raise ValueError('檔案包內容重複或檔案數過多')
        bundles = [n for n in names if '/' not in n and n.endswith('_data.json')]
        if len(bundles) != 1 or 'official_6_page.pdf' not in names or 'artifact-pages.json' not in names:
            raise ValueError('請使用新版生成 ZIP，內含 *_data.json、official_6_page.pdf、artifact-pages.json')
        # Read only named files into fixed paths. Never extract arbitrary archive paths.
        root = Path(tmp)
        for source, target in [(bundles[0],'bundle.json'), ('official_6_page.pdf','source.pdf'), ('artifact-pages.json','pages.json')]:
            info = package.getinfo(source)
            if info.file_size > MAX_MEMBER_BYTES or info.flag_bits & 1:
                raise ValueError('檔案過大或已加密，無法匯入')
            (root/target).write_bytes(package.read(info))
        manifest = prepare(root/'bundle.json', root/'source.pdf', root/'pages.json', root/'delivery')
        bundle_path = root/'delivery/bundle.json'
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


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--zip',required=True);p.add_argument('--endpoint',required=True)
    p.add_argument('--idempotency-key',required=True);p.add_argument('--profile')
    p.add_argument('--case-id');p.add_argument('--group-id');p.add_argument('--case-name');p.add_argument('--group-name')
    a=p.parse_args()
    print(json.dumps(deliver_generated(Client(a.endpoint,a.profile),Path(a.zip).read_bytes(),a.idempotency_key,
        case_id=a.case_id,group_id=a.group_id,case_name=a.case_name,group_name=a.group_name),ensure_ascii=False))
