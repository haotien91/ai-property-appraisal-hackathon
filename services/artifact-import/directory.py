"""Human-facing metadata and query indexes, independent of producer JSON."""
import base64
import hashlib
import uuid
import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from splitter import encode, decode, request_fingerprints


def workspace(owner):
    return 'DIRECTORY#' + hashlib.sha256(owner.encode()).hexdigest()


def indexes(item):
    if item['PK'].startswith('CASE#'):
        pk, sk = workspace(item['owner']), item['case_id']
    elif item['PK'].startswith('GROUP#'):
        pk, sk = 'CASEGROUPS#' + item['case_id'], item['group_id']
    else:
        pk, sk = 'GROUPRUNS#' + item['group_id'], item['created_at'] + '#' + item['run_id']
    return {'PK': pk, 'SK': sk, 'target': item['PK'], 'owner': item['owner']}


def view(item):
    names = ('case_id','case_no','name','district','group_id','run_id','created_at','updated_at',
             'selected_run_id','status','pdf_complete','producer_case_no')
    out = {k:item[k] for k in names if k in item}
    if item['PK'].startswith('CASE#'):
        out.setdefault('name', item.get('case_no') or '未命名案件')
    if item['PK'].startswith('GROUP#'):
        out.setdefault('name', '未命名估價組')
    return out


def clean(body, allowed, api):
    if set(body) - set(allowed):
        api.fail(400,'UNKNOWN_FIELD','Unsupported metadata field')
    out = {}
    for k,v in body.items():
        if not isinstance(v,str) or not v.strip() or len(v)>200 or any(ord(c)<32 for c in v):
            api.fail(400,'INVALID_METADATA',f'{k} must be a nonempty string up to 200 characters')
        out[k]=v.strip()
    return out


def list_items(pk, query, owner, api, descending=False):
    try:
        limit=int(query.get('limit','50'))
        if not 1<=limit<=100:raise ValueError()
    except (ValueError,TypeError):api.fail(400,'INVALID_LIMIT','limit must be 1–100')
    args={'KeyConditionExpression':'PK=:pk','ExpressionAttributeValues':{':pk':pk},
          'Limit':limit,'ScanIndexForward':not descending,'ConsistentRead':True}
    if query.get('cursor'):
        try:
            if len(query['cursor'])>2048:raise ValueError()
            key=decode(base64.urlsafe_b64decode(query['cursor']))
            if not isinstance(key,dict) or set(key)!={'PK','SK'} or key['PK']!=pk or not isinstance(key['SK'],str):raise ValueError()
            args['ExclusiveStartKey']=key
        except Exception:api.fail(400,'INVALID_CURSOR','Cursor does not belong to this listing')
    page=api.db.query(**args)
    values=[]
    for index in page.get('Items',[]):
        if index['owner']!=owner:continue
        item=api.owned(index['target'],owner)
        values.append(view(item))
    cursor=base64.urlsafe_b64encode(encode(page['LastEvaluatedKey'])).decode() if page.get('LastEvaluatedKey') else None
    return {'items':values,'next_cursor':cursor}


def create_entity(body, owner, idem, parent, api):
    if not idem or len(idem)>200:api.fail(400,'IDEMPOTENCY_KEY_REQUIRED','Provide Idempotency-Key')
    kind='GROUP' if parent else 'CASE'
    attrs=clean(body,('name',) if parent else ('name','case_no','district'),api)
    if 'name' not in attrs:api.fail(400,'NAME_REQUIRED','Provide a display name')
    key='ENTITYKEY#'+hashlib.sha256((owner+'\n'+kind+'\n'+(parent or '')+'\n'+idem).encode()).hexdigest()
    fingerprint,legacy_fingerprint=request_fingerprints(attrs)
    prior=api.get(key)
    if prior:
        if prior['fingerprint'] not in (fingerprint,legacy_fingerprint):api.fail(409,'IDEMPOTENCY_CONFLICT','Key used with different metadata')
        return view(api.owned(prior['target'],owner))
    ident=str(uuid.uuid4())
    item={'PK':kind+'#'+ident,'SK':'META','owner':owner,'created_at':api.now(),**attrs}
    if parent:item.update(case_id=parent,group_id=ident)
    else:item['case_id']=ident
    records=[item,indexes(item),{'PK':key,'SK':'META','owner':owner,'fingerprint':fingerprint,'target':item['PK']}]
    ser=TypeSerializer()
    try:
        boto3.client('dynamodb').transact_write_items(TransactItems=[{'Put':{'TableName':api.TABLE,
            'Item':{k:ser.serialize(v) for k,v in r.items()},'ConditionExpression':'attribute_not_exists(PK)'}} for r in records])
    except ClientError as exc:
        if exc.response['Error']['Code']=='TransactionCanceledException':
            prior=api.get(key)
            if prior and prior['fingerprint'] in (fingerprint,legacy_fingerprint):return view(api.owned(prior['target'],owner))
            api.fail(409,'CREATE_CONFLICT','Retry using the same idempotency key')
        raise
    return view(item)


def patch(item,body,owner,api):
    is_group=item['PK'].startswith('GROUP#')
    attrs=clean(body,('name','selected_run_id') if is_group else ('name','case_no','district'),api)
    if not attrs:api.fail(400,'EMPTY_PATCH','Provide metadata to update')
    if 'selected_run_id' in attrs:
        run=api.owned('RUN#'+attrs['selected_run_id'],owner)
        if run['group_id']!=item['group_id'] or run['status']!='imported':
            api.fail(409,'INVALID_SELECTION','Selected version must be imported and belong to this group')
    attrs['updated_at']=api.now()
    names={f'#f{i}':k for i,k in enumerate(attrs)}
    values={f':v{i}':v for i,v in enumerate(attrs.values())}
    values[':owner']=owner;names['#owner']='owner'
    result=api.db.update_item(Key={'PK':item['PK'],'SK':'META'},
        UpdateExpression='SET '+','.join(f'#f{i}=:v{i}' for i in range(len(attrs))),
        ConditionExpression='#owner=:owner',ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,ReturnValues='ALL_NEW')
    return view(result['Attributes'])


def dispatch(event,method,path,owner,idem,api):
    query=event.get('queryStringParameters') or {}
    if path==['v1','cases']:
        if method=='GET':return list_items(workspace(owner),query,owner,api)
        if method=='POST':return create_entity(api.request_json(event),owner,idem,None,api)
    if len(path)>=3 and path[:2]==['v1','cases']:
        case=api.owned('CASE#'+path[2],owner)
        if len(path)==3:
            if method=='GET':return view(case)
            if method=='PATCH':return patch(case,api.request_json(event),owner,api)
        if path[3:]==['groups']:
            if method=='GET':return list_items('CASEGROUPS#'+path[2],query,owner,api)
            if method=='POST':return create_entity(api.request_json(event),owner,idem,path[2],api)
    if len(path)>=3 and path[:2]==['v1','groups']:
        group=api.owned('GROUP#'+path[2],owner)
        if len(path)==3:
            if method=='GET':return view(group)
            if method=='PATCH':return patch(group,api.request_json(event),owner,api)
        if method=='GET' and path[3:]==['runs']:
            return list_items('GROUPRUNS#'+path[2],query,owner,api,descending=True)
    api.fail(404,'ROUTE_NOT_FOUND','Unknown directory endpoint')
