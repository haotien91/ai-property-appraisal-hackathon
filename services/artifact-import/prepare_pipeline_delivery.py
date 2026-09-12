"""Prepare merged pipeline PDF + JSON for client.py; never uploads by itself."""
import argparse
import json
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from splitter import decode, split_bundle


def prepare(bundle_path, pdf_path, page_map_path, output_dir):
    bundle = decode(Path(bundle_path).read_bytes())
    split_bundle(bundle)
    pages = json.loads(Path(page_map_path).read_text())
    reader = PdfReader(pdf_path)
    if reader.is_encrypted:
        raise ValueError('Encrypted PDFs are unsupported')
    expected = {('survey', code) for code in bundle['table3']} | {('comparison', None), ('regional_factors', None)}
    seen, used = set(), set()
    for item in pages:
        identity = (item['kind'], item.get('segment_code'))
        start, end = item['page_start'], item['page_end']
        if identity not in expected or identity in seen:
            raise ValueError('Duplicate or unknown document identity')
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(reader.pages):
            raise ValueError('Invalid PDF page range')
        assigned = set(range(start, end + 1))
        if assigned & used:
            raise ValueError('Overlapping PDF pages')
        seen.add(identity); used.update(assigned)
    if seen != expected or used != set(range(1, len(reader.pages) + 1)):
        raise ValueError('Page map must cover every form and PDF page exactly once')
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for kind in ('survey', 'comparison', 'regional_factors'):
        writer = PdfWriter(); segments = []
        for item in sorted((x for x in pages if x['kind'] == kind), key=lambda x: x['page_start']):
            start = len(writer.pages) + 1
            for index in range(item['page_start'] - 1, item['page_end']):
                writer.add_page(reader.pages[index])
            if kind == 'survey':
                segments.append({'segment_code': item['segment_code'], 'page_start': start, 'page_end': len(writer.pages)})
        filename = kind + '.pdf'
        with (out / filename).open('wb') as stream:
            writer.write(stream)
        manifest.append({'path': filename, 'kind': kind, 'segments': segments})
    (out / 'bundle.json').write_bytes(Path(bundle_path).read_bytes())
    (out / 'pdf-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return out / 'pdf-manifest.json'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--page-map', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(prepare(args.bundle, args.pdf, args.page_map, args.output))
