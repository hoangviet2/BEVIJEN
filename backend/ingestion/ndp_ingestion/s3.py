"""Raw archive adapter. Bucket, encryption and IAM policy are owned by team IaC."""
from urllib.parse import quote
from .core import RawPayload, archive_key
import hashlib


class S3RawStore:
    def __init__(self, bucket: str, prefix: str = 'ingestion/raw', client=None):
        if not bucket:
            raise ValueError('S3 bucket is required')
        if client is None:
            import boto3
            client = boto3.client('s3')  # IAM role / approved AWS SDK environment chain.
        self.client, self.bucket, self.prefix = client, bucket, prefix.strip('/')

    def put(self, payload: RawPayload, source_name: str, run_id: str, page: int) -> str:
        key = '/'.join(filter(None, [self.prefix, archive_key(source_name, run_id, page)]))
        response = self.client.put_object(
            Bucket=self.bucket, Key=key, Body=payload.body,
            ContentType=payload.content_type,
            Metadata={'source-name': quote(source_name, safe=''), 'retrieved-at': payload.retrieved_at,
                      'sha256': hashlib.sha256(payload.body).hexdigest()},
        )
        ref = f's3://{self.bucket}/{key}'
        version = response.get('VersionId')
        return ref + '?versionId=' + quote(version, safe='') if version else ref
