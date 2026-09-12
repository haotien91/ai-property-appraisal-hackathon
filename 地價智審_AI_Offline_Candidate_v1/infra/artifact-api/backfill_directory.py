"""Idempotently index existing imports; preserve all existing metadata and file contents."""
import argparse
import sys
from pathlib import Path
import boto3
from botocore.exceptions import ClientError
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'services/artifact-import'))
from directory import indexes
from splitter import decode

p=argparse.ArgumentParser()
p.add_argument('--profile',required=True)
a=p.parse_args()
s=boto3.Session(profile_name=a.profile,region_name='us-west-2')
assert s.client('sts').get_caller_identity()['Account']=='137336531963'
t=s.resource('dynamodb').Table('ntpc-appraisal-metadata-v1')
records=[];args={'ConsistentRead':True}
while True:
 page=t.scan(**args)
 records += [r for r in page['Items'] if r.get('SK')=='META' and r['PK'].startswith(('CASE#','GROUP#','RUN#'))]
 if 'LastEvaluatedKey' not in page:break
 args['ExclusiveStartKey']=page['LastEvaluatedKey']
for r in records:
 index=indexes(r)
 try:t.put_item(Item=index,ConditionExpression='attribute_not_exists(PK)')
 except ClientError as exc:
  if exc.response['Error']['Code']!='ConditionalCheckFailedException':raise
  old=t.get_item(Key={'PK':index['PK'],'SK':index['SK']},ConsistentRead=True)['Item']
  assert old['owner']==index['owner'] and old['target']==index['target']
for r in sorted(records,key=lambda x:x.get('created_at','')):
 if not r['PK'].startswith('RUN#') or r.get('status')!='imported':continue
 defaults={'case_no':r.get('producer_case_no')}
 context=next((d for d in r.get('documents',[]) if d['kind']=='context'),None)
 if context:
  raw=s.client('s3').get_object(Bucket='ntpc-appraisal-artifacts-137336531963-us-west-2',Key=context['key'],VersionId=context['version_id'])['Body'].read()
  case=decode(raw).get('case',{})
  if isinstance(case,dict):defaults['district']=case.get('district')
 for key,val in defaults.items():
  if isinstance(val,str) and 0<len(val)<=200:
   t.update_item(Key={'PK':'CASE#'+r['case_id'],'SK':'META'},
     UpdateExpression='SET #field=if_not_exists(#field,:value)',ConditionExpression='#owner=:owner',
     ExpressionAttributeNames={'#field':key,'#owner':'owner'},
     ExpressionAttributeValues={':value':val,':owner':r['owner']})
print('Directory index records verified:',len(records))
