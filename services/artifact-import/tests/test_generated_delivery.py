import io
import json
import unittest
from unittest.mock import patch, Mock
from moto import mock_aws
from pypdf import PdfWriter
import test_import
from test_import import fixture
from splitter import encode
from deliver_generated import deliver_generated
from deliver_generated import main
import tempfile
from pathlib import Path


def generated_files():
    pdf=io.BytesIO();writer=PdfWriter()
    for _ in range(4): writer.add_blank_page(width=100,height=200)
    writer.write(pdf)
    pages=[{'kind':'survey','segment_code':'OTHER','page_start':1,'page_end':1},
           {'kind':'survey','segment_code':'BASE','page_start':2,'page_end':2},
           {'kind':'regional_factors','page_start':3,'page_end':3},
           {'kind':'comparison','page_start':4,'page_end':4}]
    return encode(fixture()),pdf.getvalue(),pages


class ProducerCommandTests(unittest.TestCase):
    def test_command_passes_original_outputs_and_saves_identifiers(self):
        bundle,pdf,pages=generated_files()
        job='22222222-2222-4222-8222-222222222222'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'bundle.json').write_bytes(bundle)
            (root/'forms.pdf').write_bytes(pdf)
            (root/'pages.json').write_text(json.dumps(pages))
            result={'case_id':'case','group_id':'group','run_id':'run','status':'imported','pdf_complete':True}
            with patch('deliver_generated.deliver_generated',return_value=result) as delivery, \
                 patch('client.Client') as client, patch('builtins.print'):
                main(['--endpoint','https://example.invalid','--bundle',str(root/'bundle.json'),
                      '--pdf',str(root/'forms.pdf'),'--page-map',str(root/'pages.json'),
                      '--generation-id',job,'--result',str(root/'result.json')])
            self.assertEqual(delivery.call_args.args,(client.return_value,bundle,pdf,pages,job))
            saved=json.loads((root/'result.json').read_text())
            self.assertEqual(saved['generation_id'],job)
            self.assertEqual(saved['run_id'],'run')
            self.assertNotIn('url',saved)


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
            first=deliver_generated(Client(),*generated_files(),'same-job',case_name='測試案件',group_name='第一組')
            second=deliver_generated(Client(),*generated_files(),'same-job',case_name='測試案件',group_name='第一組')
        self.assertEqual(first,second)
        self.assertTrue(first['pdf_complete'])
        self.assertEqual(put.call_count,4)
        self.assertEqual(self.app.owned('CASE#'+first['case_id'],self.owner)['name'],'測試案件')
        run=self.app.owned('RUN#'+first['run_id'],self.owner)
        self.assertTrue(all(f.get('pdf') for f in run['forms']))

    def test_missing_manifest_never_starts_import(self):
        client=Mock()
        bundle,pdf,_=generated_files()
        with self.assertRaises(ValueError):
            deliver_generated(client,bundle,pdf,[],'same-job')
        client.call.assert_not_called()

    def test_worker_reuses_snapshot_after_upload_failure(self):
        import sys,os,base64
        from pathlib import Path
        root=Path(__file__).resolve().parents[3]/'地價智審_AI_Offline_Candidate_v1'
        sys.path.insert(0,str(root/'backend/handlers'));sys.path.insert(0,str(root))
        import generate_artifacts as worker
        os.environ['PDF_BUCKET_NAME']=self.app.BUCKET
        os.environ['ARTIFACT_API_ENDPOINT']='https://example.invalid'
        bundle,pdf,pages=generated_files()
        snapshot={'bundle':bundle.decode(),'pdf':base64.b64encode(pdf).decode(),'pages':pages,
                  'case_id':None,'group_id':None}
        client=Mock()
        client.call.side_effect=lambda method,path,body,key: ({'case_id':'case'} if path=='/v1/cases' else {'group_id':'group'})
        with patch.object(worker,'_render_snapshot',return_value=snapshot) as render, \
             patch.object(worker,'Client',return_value=client), \
             patch.object(worker,'deliver_generated',side_effect=[RuntimeError('offline'),{'status':'imported','pdf_complete':True}]) as delivery:
            job='22222222-2222-4222-8222-222222222222'
            with self.assertRaises(RuntimeError):worker.generate_and_publish('TEST',job)
            result=worker.generate_and_publish('TEST',job)
            self.assertTrue(result['pdf_complete'])
            self.assertEqual(render.call_count,1)
            self.assertEqual(delivery.call_args_list[0],delivery.call_args_list[1])
            with self.assertRaises(ValueError):worker.generate_and_publish('TEST',job,case_id='33333333-3333-4333-8333-333333333333')
