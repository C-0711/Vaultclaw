"""
S3-Compatible API Layer for 0711-Vault
Implements AWS S3 API subset for programmatic access
"""

from fastapi import APIRouter, HTTPException, Request, Response, Depends, Header
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime
import hashlib
import hmac
import base64
from pydantic import BaseModel

router = APIRouter(prefix="/s3", tags=["S3 Compatible API"])

# Models
class Bucket(BaseModel):
    name: str
    creation_date: datetime

class S3Object(BaseModel):
    key: str
    last_modified: datetime
    size: int
    etag: str
    storage_class: str = "STANDARD"

class ListBucketsResponse(BaseModel):
    owner: dict
    buckets: list[Bucket]

class ListObjectsResponse(BaseModel):
    name: str
    prefix: str
    max_keys: int
    is_truncated: bool
    contents: list[S3Object]

# AWS Signature V4 verification (simplified)
def verify_aws_signature(
    authorization: Optional[str] = Header(None),
    x_amz_date: Optional[str] = Header(None, alias="x-amz-date")
):
    """Verify AWS Signature V4 authentication"""
    if not authorization:
        # Allow unauthenticated for now, add auth later
        return {"user_id": "anonymous", "tenant": "default"}
    
    # Parse authorization header
    # AWS4-HMAC-SHA256 Credential=.../.../.../s3/aws4_request, SignedHeaders=..., Signature=...
    try:
        parts = authorization.split(", ")
        credential = parts[0].split("=")[1]
        access_key = credential.split("/")[0]
        # In production, validate signature against stored secret key
        return {"user_id": access_key, "tenant": "default"}
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid signature")


@router.get("/")
async def list_buckets(auth: dict = Depends(verify_aws_signature)):
    """
    GET / - List all buckets (folders at root level)
    Compatible with: aws s3 ls
    """
    # TODO: Fetch from database
    buckets = [
        Bucket(name="photos", creation_date=datetime.now()),
        Bucket(name="documents", creation_date=datetime.now()),
        Bucket(name="media", creation_date=datetime.now()),
    ]
    
    return {
        "ListAllMyBucketsResult": {
            "Owner": {"ID": auth["user_id"], "DisplayName": auth["user_id"]},
            "Buckets": {"Bucket": [b.dict() for b in buckets]}
        }
    }


@router.get("/{bucket}")
async def list_objects(
    bucket: str,
    prefix: str = "",
    delimiter: str = "",
    max_keys: int = 1000,
    continuation_token: Optional[str] = None,
    auth: dict = Depends(verify_aws_signature)
):
    """
    GET /{bucket} - List objects in bucket
    Compatible with: aws s3 ls s3://bucket/
    """
    # TODO: Fetch from database based on bucket/prefix
    objects = [
        S3Object(
            key=f"{prefix}example.jpg",
            last_modified=datetime.now(),
            size=1024,
            etag="\"d41d8cd98f00b204e9800998ecf8427e\""
        )
    ]
    
    return {
        "ListBucketResult": {
            "Name": bucket,
            "Prefix": prefix,
            "MaxKeys": max_keys,
            "IsTruncated": False,
            "Contents": [o.dict() for o in objects]
        }
    }


@router.head("/{bucket}/{key:path}")
@router.get("/{bucket}/{key:path}")
async def get_object(
    bucket: str,
    key: str,
    request: Request,
    auth: dict = Depends(verify_aws_signature)
):
    """
    GET /{bucket}/{key} - Download object
    HEAD /{bucket}/{key} - Get object metadata
    Compatible with: aws s3 cp s3://bucket/key ./local
    """
    # TODO: Fetch from MinIO/storage
    
    if request.method == "HEAD":
        return Response(
            headers={
                "Content-Length": "1024",
                "Content-Type": "application/octet-stream",
                "ETag": "\"d41d8cd98f00b204e9800998ecf8427e\"",
                "Last-Modified": datetime.now().strftime("%a, %d %b %Y %H:%M:%S GMT")
            }
        )
    
    # TODO: Stream actual file content
    async def file_iterator():
        yield b"placeholder content"
    
    return StreamingResponse(
        file_iterator(),
        media_type="application/octet-stream",
        headers={"ETag": "\"d41d8cd98f00b204e9800998ecf8427e\""}
    )


@router.put("/{bucket}/{key:path}")
async def put_object(
    bucket: str,
    key: str,
    request: Request,
    content_type: str = Header("application/octet-stream"),
    auth: dict = Depends(verify_aws_signature)
):
    """
    PUT /{bucket}/{key} - Upload object
    Compatible with: aws s3 cp ./local s3://bucket/key
    """
    body = await request.body()
    
    # Calculate ETag (MD5)
    etag = hashlib.md5(body).hexdigest()
    
    # TODO: Store in MinIO/database
    
    return Response(
        status_code=200,
        headers={
            "ETag": f"\"{etag}\"",
            "x-amz-request-id": "0711-vault-request"
        }
    )


@router.delete("/{bucket}/{key:path}")
async def delete_object(
    bucket: str,
    key: str,
    auth: dict = Depends(verify_aws_signature)
):
    """
    DELETE /{bucket}/{key} - Delete object
    Compatible with: aws s3 rm s3://bucket/key
    """
    # TODO: Delete from storage
    
    return Response(status_code=204)


@router.put("/{bucket}")
async def create_bucket(
    bucket: str,
    auth: dict = Depends(verify_aws_signature)
):
    """
    PUT /{bucket} - Create bucket
    Compatible with: aws s3 mb s3://bucket
    """
    # TODO: Create folder in database
    
    return Response(
        status_code=200,
        headers={"Location": f"/{bucket}"}
    )


@router.delete("/{bucket}")
async def delete_bucket(
    bucket: str,
    auth: dict = Depends(verify_aws_signature)
):
    """
    DELETE /{bucket} - Delete bucket
    Compatible with: aws s3 rb s3://bucket
    """
    # TODO: Delete folder (must be empty)
    
    return Response(status_code=204)
