"""
Sync routes for multi-device synchronization
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from database import get_db

router = APIRouter()


# ===========================================
# SCHEMAS
# ===========================================

class SyncRequest(BaseModel):
    device_id: str
    last_sync_version: int
    changes: Optional[List[dict]] = None  # Local changes to push


class SyncResponse(BaseModel):
    current_version: int
    changes: List[dict]
    conflicts: List[dict]


class DeviceRegisterRequest(BaseModel):
    name: str
    device_type: str  # ios, android, web, mac
    device_model: Optional[str] = None
    encrypted_device_key: str


# ===========================================
# ENDPOINTS
# ===========================================

@router.post("/devices")
async def register_device(request: DeviceRegisterRequest, user_id: str, db=Depends(get_db)):
    """
    Register a new device for sync.
    """
    import uuid
    device_id = str(uuid.uuid4())
    
    await db.execute("""
        INSERT INTO devices (id, user_id, name, device_type, device_model, encrypted_device_key)
        VALUES (:id, :user_id, :name, :device_type, :device_model, :encrypted_device_key)
    """, {
        "id": device_id,
        "user_id": user_id,
        "name": request.name,
        "device_type": request.device_type,
        "device_model": request.device_model,
        "encrypted_device_key": request.encrypted_device_key
    })
    
    return {"device_id": device_id}


@router.get("/devices")
async def list_devices(user_id: str, db=Depends(get_db)):
    """
    List all devices for user.
    """
    result = await db.execute("""
        SELECT id, name, device_type, device_model, last_sync_at, created_at, is_active
        FROM devices 
        WHERE user_id = :user_id AND is_active = TRUE
        ORDER BY last_sync_at DESC
    """, {"user_id": user_id})
    
    devices = result.fetchall()
    return {"devices": [dict(d._mapping) for d in devices]}


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: str, user_id: str, db=Depends(get_db)):
    """
    Revoke a device (logout remotely).
    """
    await db.execute("""
        UPDATE devices 
        SET is_active = FALSE 
        WHERE id = :device_id AND user_id = :user_id
    """, {"device_id": device_id, "user_id": user_id})
    
    return {"status": "revoked"}


@router.post("/pull", response_model=SyncResponse)
async def sync_pull(request: SyncRequest, user_id: str, db=Depends(get_db)):
    """
    Pull changes from server since last sync.
    
    Sync Algorithm:
    1. Get all items with sync_version > last_sync_version
    2. Return encrypted items
    3. Client decrypts and applies locally
    """
    # Get vault item changes
    vault_result = await db.execute("""
        SELECT id, item_type, encrypted_metadata, storage_path, file_size, mime_type,
               created_at, updated_at, captured_at, sync_version, deleted_at
        FROM vault_items
        WHERE user_id = :user_id AND sync_version > :last_version
        ORDER BY sync_version ASC
    """, {"user_id": user_id, "last_version": request.last_sync_version})
    
    vault_changes = [dict(r._mapping) for r in vault_result.fetchall()]
    
    # Get message changes
    message_result = await db.execute("""
        SELECT m.* FROM messages m
        JOIN thread_participants tp ON m.thread_id = tp.thread_id
        WHERE tp.user_id = :user_id AND m.sync_version > :last_version
        ORDER BY m.sync_version ASC
    """, {"user_id": user_id, "last_version": request.last_sync_version})
    
    message_changes = [dict(r._mapping) for r in message_result.fetchall()]
    
    # Get contact changes
    contact_result = await db.execute("""
        SELECT * FROM contacts
        WHERE user_id = :user_id AND sync_version > :last_version
        ORDER BY sync_version ASC
    """, {"user_id": user_id, "last_version": request.last_sync_version})
    
    contact_changes = [dict(r._mapping) for r in contact_result.fetchall()]
    
    # Get current max version
    version_result = await db.execute("""
        SELECT COALESCE(MAX(sync_version), 0) as max_version FROM (
            SELECT sync_version FROM vault_items WHERE user_id = :user_id
            UNION ALL
            SELECT m.sync_version FROM messages m
            JOIN thread_participants tp ON m.thread_id = tp.thread_id
            WHERE tp.user_id = :user_id
        ) versions
    """, {"user_id": user_id})
    
    current_version = version_result.fetchone().max_version
    
    # Update device last sync
    await db.execute("""
        UPDATE devices SET last_sync_at = NOW() WHERE id = :device_id
    """, {"device_id": request.device_id})
    
    # Combine all changes
    all_changes = [
        {"type": "vault_item", **c} for c in vault_changes
    ] + [
        {"type": "message", **c} for c in message_changes
    ] + [
        {"type": "contact", **c} for c in contact_changes
    ]
    
    return SyncResponse(
        current_version=current_version,
        changes=all_changes,
        conflicts=[]
    )


@router.post("/push")
async def sync_push(request: SyncRequest, user_id: str, db=Depends(get_db)):
    """
    Push local changes to server.
    
    Conflict Resolution:
    - Last-write-wins for most items
    - Merge for messages (append)
    - Client resolves conflicts for edits
    """
    if not request.changes:
        return {"status": "no_changes"}
    
    conflicts = []
    
    for change in request.changes:
        change_type = change.get("type")
        
        if change_type == "vault_item":
            # Check for conflicts
            result = await db.execute("""
                SELECT sync_version FROM vault_items WHERE id = :id
            """, {"id": change["id"]})
            existing = result.fetchone()
            
            if existing and existing.sync_version > change.get("base_version", 0):
                # Conflict detected
                conflicts.append(change)
                continue
            
            # Apply change
            if change.get("deleted"):
                await db.execute("""
                    UPDATE vault_items 
                    SET deleted_at = NOW(), sync_version = sync_version + 1
                    WHERE id = :id AND user_id = :user_id
                """, {"id": change["id"], "user_id": user_id})
            else:
                await db.execute("""
                    INSERT INTO vault_items (id, user_id, item_type, encrypted_metadata, storage_path, sync_version)
                    VALUES (:id, :user_id, :item_type, :encrypted_metadata, :storage_path, 1)
                    ON CONFLICT (id) DO UPDATE SET
                        encrypted_metadata = EXCLUDED.encrypted_metadata,
                        sync_version = vault_items.sync_version + 1
                """, {**change, "user_id": user_id})
    
    return {
        "status": "synced",
        "conflicts": conflicts
    }


@router.get("/status")
async def sync_status(device_id: str, user_id: str, db=Depends(get_db)):
    """
    Get sync status for device.
    """
    # Get device info
    device_result = await db.execute("""
        SELECT last_sync_at FROM devices WHERE id = :device_id AND user_id = :user_id
    """, {"device_id": device_id, "user_id": user_id})
    
    device = device_result.fetchone()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Count pending changes
    pending_result = await db.execute("""
        SELECT COUNT(*) as pending FROM vault_items 
        WHERE user_id = :user_id AND updated_at > :last_sync
    """, {"user_id": user_id, "last_sync": device.last_sync_at or datetime.min})
    
    pending = pending_result.fetchone().pending
    
    return {
        "last_sync_at": device.last_sync_at,
        "pending_changes": pending,
        "needs_sync": pending > 0
    }
