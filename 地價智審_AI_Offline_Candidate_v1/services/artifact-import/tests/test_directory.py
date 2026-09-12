import json
import unittest
import test_import
from moto import mock_aws


@mock_aws
class DirectoryTests(unittest.TestCase):
    setUp = test_import.ApiTests.setUp
    create = test_import.ApiTests.create
    upload = test_import.ApiTests.upload
    def call(self, method, path, body=None, key='directory-test', query=None, role='team'):
        arn=f'arn:aws:sts::123456789012:assumed-role/{role}/session'
        event={'requestContext':{'http':{'method':method},'authorizer':{'iam':{'userArn':arn}}},
               'rawPath':path,'headers':{'idempotency-key':key},'body':json.dumps(body or {}),
               'queryStringParameters':query}
        response=self.app.handler(event,None)
        return response['statusCode'],json.loads(response['body'])

    def test_create_list_patch_and_owner_isolation(self):
        status,case=self.call('POST','/v1/cases',{'name':'測試案件','case_no':'114-001','district':'樹林區'})
        self.assertEqual(status,200)
        self.assertEqual(self.call('POST','/v1/cases',{'name':'測試案件','case_no':'114-001','district':'樹林區'})[1],case)
        _,group=self.call('POST',f"/v1/cases/{case['case_id']}/groups",{'name':'第一組'})
        status,changed=self.call('PATCH',f"/v1/groups/{group['group_id']}",{'name':'另一組名稱'})
        self.assertEqual(status,200);self.assertEqual(changed['name'],'另一組名稱')
        self.assertEqual(self.call('GET','/v1/cases')[1]['items'][0]['case_id'],case['case_id'])
        self.assertEqual(self.call('GET','/v1/cases',role='other')[1]['items'],[])
        self.assertEqual(self.call('GET',f"/v1/cases/{case['case_id']}",role='other')[0],404)

    def test_auto_import_index_selection_and_case_number(self):
        item=self.create();self.upload(item);self.app.complete(item)
        _,cases=self.call('GET','/v1/cases')
        self.assertEqual(cases['items'][0]['case_no'],'TEST')
        _,runs=self.call('GET',f"/v1/groups/{item['group_id']}/runs")
        self.assertEqual(runs['items'][0]['run_id'],item['run_id'])
        self.assertEqual(self.call('PATCH',f"/v1/groups/{item['group_id']}",{'selected_run_id':item['run_id']})[0],200)
        body={**self.body,'case_id':item['case_id']}
        another=self.app.create(body,self.owner,'another-group')
        self.assertEqual(self.call('PATCH',f"/v1/groups/{another['group_id']}",{'selected_run_id':item['run_id']})[0],409)

    def test_cursor_partition_binding(self):
        for i in range(2):self.call('POST','/v1/cases',{'name':str(i)},key=str(i))
        _,first=self.call('GET','/v1/cases',query={'limit':'1'})
        self.assertIsNotNone(first['next_cursor'])
        _,second=self.call('GET','/v1/cases',query={'limit':'1','cursor':first['next_cursor']})
        self.assertNotEqual(first['items'][0]['case_id'],second['items'][0]['case_id'])
        self.assertEqual(self.call('GET','/v1/cases',query={'cursor':first['next_cursor']},role='other')[0],400)
