"""
Vault item routes (photos, documents, etc.)
"""

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
import hashlib

from config import settings
from database import get_db, get_minio

router = APIRouter()


# ===========================================
# SCHEMAS
# ===========================================

class VaultItemCreate(BaseModel):
    item_type: str  # photo, video, document, note
    encrypted_metadata: str
    mime_type: Optional[str] = None
    captured_at: Optional[datetime] = None


class VaultItemResponse(BaseModel):
    id: str
    item_type: str
    encrypted_metadata: str
    storage_path: str
    file_size: Optional[int]
    mime_type: Optional[str]
    created_at: datetime
    sync_version: int


class UploadResponse(BaseModel):
    id: str
    upload_url: str  # Presigned URL for direct upload to MinIO


# ===========================================
# ENDPOINTS
# ===========================================

@router.post("/items", response_model=UploadResponse)
async def create_item(item: VaultItemCreate, db=Depends(get_db)):
    """
    Create a new vault item and get presigned upload URL.
    Client uploads encrypted file directly to MinIO.
    """
    item_id = str(uuid.uuid4())
    storage_path = f"items/{item_id}"
    
    # Store metadata
    await db.execute("""
        INSERT INTO vault_items (id, item_type, encrypted_metadata, storage_path, mime_type, captured_at)
        VALUES (:id, :item_type, :encrypted_metadata, :storage_path, :mime_type, :captured_at)
    """, {
        "id": item_id,
        "item_type": item.item_type,
        "encrypted_metadata": item.encrypted_metadata,
        "storage_path": storage_path,
        "mime_type": item.mime_type,
        "captured_at": item.captured_at
    })
    
    # Generate presigned upload URL
    minio = get_minio()
    upload_url = minio.presigned_put_object(
        settings.MINIO_BUCKET,
        storage_path,
        expires=timedelta(hours=1)
    )
    
    return UploadResponse(id=item_id, upload_url=upload_url)


@router.get("/items", response_model=List[VaultItemResponse])
async def list_items(
    item_type: Optional[str] = None,
    since_version: Optional[int] = None,
    limit: int = 100,
    db=Depends(get_db)
):
    """
    List vault items with optional filtering.
    """
    query = "SELECT * FROM vault_items WHERE deleted_at IS NULL"
    params = {}
    
    if item_type:
        query += " AND item_type = :item_type"
        params["item_type"] = item_type
    
    if since_version:
        query += " AND sync_version > :since_version"
        params["since_version"] = since_version
    
    query += " ORDER BY created_at DESC LIMIT :limit"
    params["limit"] = limit
    
    result = await db.execute(query, params)
    items = result.fetchall()
    
    return [VaultItemResponse(**item._mapping) for item in items]


@router.get("/items/{item_id}", response_model=VaultItemResponse)
async def get_item(item_id: str, db=Depends(get_db)):
    """
    Get vault item metadata.
    """
    result = await db.execute(
        "SELECT * FROM vault_items WHERE id = :id AND deleted_at IS NULL",
        {"id": item_id}
    )
    item = result.fetchone()
    
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    return VaultItemResponse(**item._mapping)


@router.get("/items/{item_id}/download")
async def get_download_url(item_id: str, db=Depends(get_db)):
    """
    Get presigned download URL for encrypted file.
    """
    result = await db.execute(
        "SELECT storage_path FROM vault_items WHERE id = :id AND deleted_at IS NULL",
        {"id": item_id}
    )
    item = result.fetchone()
    
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    minio = get_minio()
    download_url = minio.presigned_get_object(
        settings.MINIO_BUCKET,
        item.storage_path,
        expires=timedelta(hours=1)
    )
    
    return {"download_url": download_url}


@router.delete("/items/{item_id}")
async def delete_item(item_id: str, permanent: bool = False, db=Depends(get_db)):
    """
    Delete vault item.
    Soft delete by default, permanent delete if requested.
    """
    if permanent:
        # Actually delete from MinIO and database
        result = await db.execute(
            "SELECT storage_path FROM vault_items WHERE id = :id",
            {"id": item_id}
        )
        item = result.fetchone()
        
        if item:
            minio = get_minio()
            minio.remove_object(settings.MINIO_BUCKET, item.storage_path)
            await db.execute("DELETE FROM vault_items WHERE id = :id", {"id": item_id})
    else:
        # Soft delete (recoverable for 30 days)
        await db.execute("""
            UPDATE vault_items 
            SET deleted_at = NOW(), 
                permanently_delete_at = NOW() + INTERVAL '30 days',
                sync_version = sync_version + 1
            WHERE id = :id
        """, {"id": item_id})
    
    return {"status": "deleted"}


from datetime import timedelta
