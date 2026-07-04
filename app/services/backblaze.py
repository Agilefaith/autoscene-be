import boto3
from botocore.config import Config
from app.core.config import get_settings
from functools import lru_cache

settings = get_settings()


@lru_cache
def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.backblaze_endpoint_url,
        aws_access_key_id=settings.backblaze_key_id,
        aws_secret_access_key=settings.backblaze_application_key,
        config=Config(signature_version="s3v4"),
        region_name=settings.backblaze_region,
    )


def upload_video(local_path: str, object_key: str) -> str:
    """Upload a local file to Backblaze B2. Returns the public URL."""
    client = _s3_client()
    client.upload_file(
        local_path,
        settings.backblaze_bucket_name,
        object_key,
        ExtraArgs={"ContentType": "video/mp4"},
    )
    return f"{settings.backblaze_endpoint_url}/{settings.backblaze_bucket_name}/{object_key}"


def upload_bytes(data: bytes, object_key: str, content_type: str = "application/octet-stream") -> str:
    """Upload raw bytes to Backblaze B2. Returns the public URL."""
    client = _s3_client()
    client.put_object(
        Bucket=settings.backblaze_bucket_name,
        Key=object_key,
        Body=data,
        ContentType=content_type,
    )
    return f"{settings.backblaze_endpoint_url}/{settings.backblaze_bucket_name}/{object_key}"


def generate_presigned_upload_url(object_key: str, content_type: str, expires_in: int = 3600) -> str:
    """Generate a presigned PUT URL for direct browser upload."""
    client = _s3_client()
    return client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.backblaze_bucket_name,
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=expires_in,
    )
