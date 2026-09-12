import copy
import hashlib
import importlib
import io
import os
import unittest
from decimal import Decimal

import boto3
from moto import mock_aws
from pypdf import PdfWriter
from splitter import decode, encode, split_bundle, select_fields, InvalidBundle


def fixture():
    return {'schema_version': '1.0', 'case_no': 'TEST', 'base_segment_code': 'BASE',
            'comparable_segment_codes': ['OTHER'],
            'segments': {'BASE': {'segment_code': 'BASE', 'comparison_index': None},
                         'OTHER': {'segment_code': 'OTHER', 'comparison_index': 1}},
            'table3': {k: {'segment_code': k, 'factors': [{'field_id': 'width', 'raw_value': None}]}
                       for k in ['BASE', 'OTHER']},
            'table4': {'base_segment_code': 'BASE', 'comparisons': [
                {'comparable_segment_code': 'OTHER', 'comparison_index': 1,
                 'individual_factor_results': [{'field_id': 'width', 'value': Decimal('0.1234567890123456789012345')}]}]},
            'table5_1': {'base_segment_code': 'BASE', 'comparisons': [
                {'comparable_segment_code': 'OTHER', 'comparison_index': 1,
                 'factor_results': []}]}, 'future_metadata': {'preserved': True},
            'manual_review_items': [], 'review': None}


class SplitTests(unittest.TestCase):
    def test_lossless_decimal_null_and_extra_fields(self):
        x = fixture()
        result = split_bundle(decode(encode(x)))
        self.assertEqual(len(result), 6)
        self.assertEqual(next(p['payload'] for p in result if p['kind'] == 'comparison'), x['table4'])
        self.assertEqual(next(p['payload'] for p in result if p['kind'] == 'context')['future_metadata'], {'preserved': True})
        self.assertIsNone(result[0]['payload']['factors'][0]['raw_value'])

    def test_duplicate_keys_and_unknown_version_rejected(self):
        with self.assertRaises(InvalidBundle): decode('{"table3":{},"table3":{}}')
        x = fixture(); x['schema_version'] = '2.0'
        with self.assertRaises(InvalidBundle): split_bundle(x)

    def test_cross_table_mismatch_rejected(self):
        x = fixture(); x['table4']['comparisons'][0]['comparison_index'] = 2
        with self.assertRaises(InvalidBundle): split_bundle(x)

    def test_factor_cannot_claim_a_different_segment(self):
        x = fixture()
        x['table4']['comparisons'][0]['individual_factor_results'][0]['comparable_segment_code'] = 'WRONG'
        with self.assertRaises(InvalidBundle): split_bundle(x)

    def test_duplicate_factor_ids_rejected(self):
        x = fixture()
        x['table3']['BASE']['factors'].append(x['table3']['BASE']['factors'][0].copy())
        with self.assertRaises(InvalidBundle): split_bundle(x)

    def test_factor_filter_preserves_precision(self):
        x = fixture()['table4']
        out = select_fields(x, 'OTHER', 'width')
        self.assertEqual(out['matches'][0]['value'], Decimal('0.1234567890123456789012345'))
        self.assertEqual(select_fields(x, 'MISSING', 'width')['matches'], [])


@mock_aws
class ApiTests(unittest.TestCase):
    def setUp(self):
        os.environ.update(BUCKET='test-artifact-storage', TABLE='test-metadata', AWS_DEFAULT_REGION='us-west-2')
        s3 = boto3.client('s3')
        s3.create_bucket(Bucket=os.environ['BUCKET'], CreateBucketConfiguration={'LocationConstraint': 'us-west-2'})
        s3.put_bucket_versioning(Bucket=os.environ['BUCKET'], VersioningConfiguration={'Status': 'Enabled'})
        boto3.client('dynamodb').create_table(TableName=os.environ['TABLE'],
            AttributeDefinitions=[{'AttributeName': k, 'AttributeType': 'S'} for k in ['PK', 'SK']],
            KeySchema=[{'AttributeName': 'PK', 'KeyType': 'HASH'}, {'AttributeName': 'SK', 'KeyType': 'RANGE'}],
            BillingMode='PAY_PER_REQUEST')
        import app
        self.app = importlib.reload(app)
        self.owner = 'arn:aws:sts::123456789012:assumed-role/team'
        self.raw = encode(fixture())
        self.body = {'bundle': {'filename': 'case.json', 'sha256': hashlib.sha256(self.raw).hexdigest(), 'size_bytes': len(self.raw)}}

    def create(self):
        result = self.app.create(self.body, self.owner, 'test-key')
        return self.app.owned('RUN#' + result['run_id'], self.owner)

    def upload(self, item):
        self.app.s3.put_object(Bucket=os.environ['BUCKET'], Key=item['uploads'][0]['key'], Body=self.raw)

    def test_import_retry_version_pin_and_isolation(self):
        item = self.create(); self.upload(item)
        result = self.app.complete(item)
        self.assertEqual(result['status'], 'imported')
        self.assertEqual(len(result['forms']), 4)
        self.assertFalse(result['pdf_complete'])
        retry = self.app.create(self.body, self.owner, 'test-key')
        self.assertEqual(retry['run_id'], result['run_id'])
        self.assertEqual(self.app.complete(self.app.owned(item['PK'], self.owner))['forms'], result['forms'])
        original = result['documents'][0]
        self.app.s3.put_object(Bucket=os.environ['BUCKET'], Key=original['key'], Body=b'changed')
        data = self.app.s3.get_object(Bucket=os.environ['BUCKET'], Key=original['key'], VersionId=original['version_id'])['Body'].read()
        self.assertEqual(data, self.raw)
        with self.assertRaises(self.app.Error): self.app.owned(item['PK'], 'another-role')

    def test_idempotency_conflict(self):
        self.create()
        self.body['bundle']['filename'] = 'changed.json'
        with self.assertRaises(self.app.Error) as ctx: self.app.create(self.body, self.owner, 'test-key')
        self.assertEqual(ctx.exception.status, 409)

    def test_key_order_does_not_create_idempotency_conflict(self):
        original = self.create()
        reordered = {'bundle': dict(reversed(list(self.body['bundle'].items())))}
        result = self.app.create(reordered, self.owner, 'test-key')
        self.assertEqual(result['run_id'], original['run_id'])

    def test_missing_upload_does_not_publish(self):
        item = self.create()
        with self.assertRaises(self.app.Error) as ctx: self.app.complete(item)
        self.assertEqual(ctx.exception.code, 'MISSING_UPLOAD')
        self.assertEqual(self.app.get(item['PK'])['status'], 'pending')

    def test_pdf_page_mapping(self):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842); writer.add_blank_page(width=595, height=842)
        f = io.BytesIO(); writer.write(f); pdf = f.getvalue()
        self.body['pdfs'] = [{'kind': 'survey_pdf', 'filename': 'survey.pdf', 'size_bytes': len(pdf),
            'sha256': hashlib.sha256(pdf).hexdigest(), 'segments': [
                {'segment_code': 'BASE', 'page_start': 1, 'page_end': 1},
                {'segment_code': 'OTHER', 'page_start': 2, 'page_end': 2}]}]
        item = self.create(); self.upload(item)
        self.app.s3.put_object(Bucket=os.environ['BUCKET'], Key=item['uploads'][1]['key'], Body=pdf)
        result = self.app.complete(item)
        surveys = [f for f in result['forms'] if f['form_type'] == 'survey']
        self.assertEqual(surveys[1]['pdf']['page_start'], 2)

    def test_invalid_pdf_range_rejected(self):
        writer = PdfWriter(); writer.add_blank_page(width=595, height=842)
        f = io.BytesIO(); writer.write(f); pdf = f.getvalue()
        self.body['pdfs'] = [{'kind': 'survey_pdf', 'size_bytes': len(pdf), 'sha256': hashlib.sha256(pdf).hexdigest(),
                             'segments': [{'segment_code': 'BASE', 'page_start': 2, 'page_end': 2}]}]
        item = self.create(); self.upload(item)
        self.app.s3.put_object(Bucket=os.environ['BUCKET'], Key=item['uploads'][1]['key'], Body=pdf)
        with self.assertRaises(self.app.Error) as ctx: self.app.complete(item)
        self.assertEqual(ctx.exception.code, 'INVALID_PAGE_RANGE')
        self.assertEqual(self.app.get(item['PK'])['status'], 'pending')


if __name__ == '__main__': unittest.main()
