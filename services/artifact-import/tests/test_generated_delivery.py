import io
import json
import unittest
import zipfile
from unittest.mock import patch, Mock
from moto import mock_aws
from pypdf import PdfWriter
import test_import
from test_import import fixture
from splitter import encode
from deliver_generated import deliver_generated


def package(include_map=True):
    pdf=io.BytesIO();writer=PdfWriter()
    for _ in range(4): writer.add_blank_page(width=100,height=200)
    writer.write(pdf)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('case_TEST_data.json',encode(fixture()))
        z.writestr('official_6_page.pdf',pdf.getvalue())
        if include_map:
            z.writestr('artifact-pages.json',json.dumps([
                {'kind':'survey','segment_code':'OTHER','page_start':1,'page_end':1},
                {'kind':'survey','segment_code':'BASE','page_start':2,'page_end':2},
                {'kind':'regional_factors','page_start':3,'page_end':3},
                {'kind':'comparison','page_start':4,'page_end':4}]))
    return out.getvalue()


@mock_aws
class GeneratedDeliveryTests(unittest.TestCase):
    def setUp(self): test_import.ApiTests.setUp(self)

    def test_full_delivery_and_retry_against_real_import_logic(self):
        urls={};outer=self
        class Client:
            def call(self,method,path,body=None,idempotency_key=None):
                if path=='/v1/imports':
                    result=outer.app.create(body,outer.owner,idempotency_key)
                    item=outer.app.owned('RUN#'+result['run_id'],outer.owner)
                    for link,record in zip(result.get('uploads',[]),item['uploads']):urls[link['url']]=record
                    return result
                if path.endswith('/complete'):
                    return outer.app.complete(outer.app.owned('RUN#'+path.split('/')[3],outer.owner))
                if method=='PATCH':
                    kind='CASE' if '/cases/' in path else 'GROUP'
                    item=outer.app.owned(kind+'#'+path.rsplit('/',1)[1],outer.owner)
                    return outer.app.directory.patch(item,body,outer.owner,outer.app)
                raise AssertionError((method,path))
        def upload(req,timeout):
            record=urls[req.full_url]
            outer.app.s3.put_object(Bucket=outer.app.BUCKET,Key=record['key'],Body=req.data)
            response=Mock();response.status=200
            context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
            return context
        with patch('deliver_generated.urllib.request.urlopen',side_effect=upload) as put:
            first=deliver_generated(Client(),package(),'same-job',case_name='測試案件',group_name='第一組')
            second=deliver_generated(Client(),package(),'same-job',case_name='測試案件',group_name='第一組')
        self.assertEqual(first,second)
        self.assertTrue(first['pdf_complete'])
        self.assertEqual(put.call_count,4)
        self.assertEqual(self.app.owned('CASE#'+first['case_id'],self.owner)['name'],'測試案件')
        run=self.app.owned('RUN#'+first['run_id'],self.owner)
        self.assertTrue(all(f.get('pdf') for f in run['forms']))

    def test_missing_manifest_never_starts_import(self):
        client=Mock()
        with self.assertRaisesRegex(ValueError,'新版生成 ZIP'):
            deliver_generated(client,package(False),'same-job')
        client.call.assert_not_called()
