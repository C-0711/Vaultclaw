"""
0711 Vault Storage Service
MinIO/S3 presigned URLs for secure file upload/download
"""

import os
from datetime import timedelta
from minio import Minio
from minio.error import S3Error

# Configuration
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_EXTERNAL_ENDPOINT = os.getenv("MINIO_EXTERNAL_ENDPOINT", "localhost:9000")
# For Docker: use host.docker.internal to reach MinIO for signing, but URL will show external endpoint
MINIO_SIGNING_ENDPOINT = os.getenv("MINIO_SIGNING_ENDPOINT", MINIO_EXTERNAL_ENDPOINT)
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
BUCKET_NAME = os.getenv("MINIO_BUCKET", "vault")

# Initialize clients
_internal_client = None  # For internal operations (bucket management, etc.)
_signing_client = None   # For generating presigned URLs (uses signing endpoint)


def get_minio_client() -> Minio:
    """Get or create MinIO client (internal, for operations)."""
    global _internal_client
    if _internal_client is None:
        _internal_client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE
        )
        # Ensure bucket exists
        try:
            if not _internal_client.bucket_exists(BUCKET_NAME):
                _internal_client.make_bucket(BUCKET_NAME)
        except S3Error as e:
            print(f"MinIO bucket error: {e}")
    return _internal_client


def get_signing_client() -> Minio:
    """Get or create MinIO client for generating presigned URLs.
    
    Uses external endpoint for signing so URLs work from browser.
    Sets region explicitly to avoid network calls to MinIO.
    """
    global _signing_client
    if _signing_client is None:
        # Use the external endpoint directly for signing - the signature will be valid
        # when accessed from browser via the same endpoint
        _signing_client = Minio(
            MINIO_EXTERNAL_ENDPOINT,  # Sign with external endpoint so signature matches
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
            region="us-east-1"  # Set region explicitly to avoid network call
        )
    return _signing_client


# Keep old function name for compatibility
def get_external_client() -> Minio:
    """Deprecated: use get_signing_client instead."""
    return get_signing_client()


def generate_upload_url(
    user_id: str,
    filename: str,
    content_type: str = "application/octet-stream",
    expires: timedelta = timedelta(hours=1)
) -> tuple[str, str]:
    """
    Generate a presigned URL for uploading a file.
    
    Returns:
        tuple: (presigned_url, storage_key)
    """
    import uuid
    
    # Ensure bucket exists (via internal client)
    get_minio_client()
    
    # Generate unique storage key
    file_ext = filename.split('.')[-1] if '.' in filename else ''
    storage_key = f"{user_id}/{uuid.uuid4()}"
    if file_ext:
        storage_key += f".{file_ext}"
    
    # Use EXTERNAL client for presigned URL generation
    # This ensures the signature matches the hostname the browser will use
    client = get_external_client()
    
    url = client.presigned_put_object(
        BUCKET_NAME,
        storage_key,
        expires=expires
    )
    
    return url, storage_key


def generate_download_url(
    storage_key: str,
    expires: timedelta = timedelta(hours=1),
    filename: str = None
) -> str:
    """
    Generate a presigned URL for downloading a file.
    
    Args:
        storage_key: The object key in MinIO
        expires: URL expiration time
        filename: Optional filename for Content-Disposition header
    
    Returns:
        Presigned download URL
    """
    # Use SIGNING client (external endpoint) so signature matches browser access
    client = get_signing_client()
    
    # Response headers for download
    response_headers = {}
    if filename:
        response_headers["response-content-disposition"] = f'attachment; filename="{filename}"'
    
    url = client.presigned_get_object(
        BUCKET_NAME,
        storage_key,
        expires=expires,
        response_headers=response_headers if response_headers else None
    )
    
    return url


def delete_object(storage_key: str) -> bool:
    """Delete an object from storage."""
    try:
        client = get_minio_client()
        client.remove_object(BUCKET_NAME, storage_key)
        return True
    except S3Error as e:
        print(f"Delete error: {e}")
        return False


def get_object_info(storage_key: str) -> dict:
    """Get object metadata."""
    try:
        client = get_minio_client()
        stat = client.stat_object(BUCKET_NAME, storage_key)
        return {
            "size": stat.size,
            "content_type": stat.content_type,
            "last_modified": stat.last_modified,
            "etag": stat.etag
        }
    except S3Error:
        return None


def copy_object(source_key: str, dest_key: str) -> bool:
    """Copy an object within the bucket."""
    try:
        from minio.commonconfig import CopySource
        client = get_minio_client()
        client.copy_object(
            BUCKET_NAME,
            dest_key,
            CopySource(BUCKET_NAME, source_key)
        )
        return True
    except S3Error as e:
        print(f"Copy error: {e}")
        return False


def list_user_objects(user_id: str, prefix: str = "") -> list:
    """List all objects for a user."""
    client = get_minio_client()
    objects = []
    
    full_prefix = f"{user_id}/{prefix}"
    for obj in client.list_objects(BUCKET_NAME, prefix=full_prefix, recursive=True):
        objects.append({
            "key": obj.object_name,
            "size": obj.size,
            "last_modified": obj.last_modified
        })
    
    return objects


def get_user_storage_used(user_id: str) -> int:
    """Calculate total storage used by a user in bytes."""
    objects = list_user_objects(user_id)
    return sum(obj["size"] for obj in objects)


# Thumbnail generation helper
def generate_thumbnail_key(storage_key: str, size: str = "thumb") -> str:
    """Generate storage key for a thumbnail."""
    parts = storage_key.rsplit('.', 1)
    if len(parts) == 2:
        return f"{parts[0]}_{size}.{parts[1]}"
    return f"{storage_key}_{size}"
