"""Text + image via Bedrock Converse. AWS uses execution-role credentials."""
import argparse
import json
import os
from pathlib import Path
import boto3
from botocore.config import Config

DEFAULT_MODEL = 'us.anthropic.claude-sonnet-4-6'


def describe_image(image_bytes, prompt, *, image_format='png', model_id=None,
                   region=None, profile=None, max_tokens=2048):
    if image_format not in ('png', 'jpeg', 'gif', 'webp'):
        raise ValueError('Use png, jpeg, gif or webp')
    if not image_bytes or len(image_bytes) > 3750000:
        raise ValueError('Image must contain 1–3,750,000 bytes; resize larger images first')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('A text prompt is required')
    if type(max_tokens) is not int or not 1 <= max_tokens <= 8192:
        raise ValueError('max_tokens must be 1–8192 for this helper')
    client = boto3.Session(profile_name=profile, region_name=region or os.getenv('AWS_REGION', 'us-west-2')).client(
        'bedrock-runtime', config=Config(connect_timeout=10, read_timeout=180,
                                        retries={'mode':'standard', 'total_max_attempts':2}))
    response = client.converse(
        modelId=model_id or os.getenv('BEDROCK_MODEL_ID', DEFAULT_MODEL),
        messages=[{'role':'user','content':[
            {'image':{'format':image_format,'source':{'bytes':image_bytes}}}, {'text':prompt}]}],
        inferenceConfig={'maxTokens':max_tokens})
    return {'text':'\n'.join(b['text'] for b in response['output']['message']['content'] if 'text' in b),
            'stop_reason':response.get('stopReason'), 'usage':response.get('usage', {}),
            'request_id':response['ResponseMetadata'].get('RequestId')}


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--image',required=True)
    p.add_argument('--prompt',required=True)
    p.add_argument('--profile')
    p.add_argument('--model-id')
    p.add_argument('--max-tokens',type=int,default=2048)
    a=p.parse_args()
    image=Path(a.image)
    fmt=image.suffix.lower().lstrip('.')
    if fmt=='jpg':fmt='jpeg'
    print(json.dumps(describe_image(image.read_bytes(), a.prompt, image_format=fmt,
        model_id=a.model_id,profile=a.profile,max_tokens=a.max_tokens),ensure_ascii=False,indent=2))
