"""
0711 Vault - Albums/Folders API
Hierarchical organization for vault items
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
import logging

# Configure logging
logger = logging.getLogger("vault.albums")

router = APIRouter(prefix="/albums", tags=["Albums"])


# ===========================================
# SCHEMAS
# ===========================================

class AlbumCreate(BaseModel):
    encrypted_name: str
    encrypted_description: Optional[str] = None
    parent_id: Optional[str] = None

class AlbumUpdate(BaseModel):
    encrypted_name: Optional[str] = None
    encrypted_description: Optional[str] = None
    cover_item_id: Optional[str] = None
    sort_order: Optional[int] = None

class AlbumResponse(BaseModel):
    id: str
    encrypted_name: str
    encrypted_description: Optional[str]
    parent_id: Optional[str]
    cover_item_id: Optional[str]
    item_count: int
    is_smart_album: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime

class AddItemsRequest(BaseModel):
    item_ids: List[str]

class MoveItemsRequest(BaseModel):
    item_ids: List[str]
    target_album_id: Optional[str] = None  # None = remove from album


# ===========================================
# DEPENDENCIES
# ===========================================

async def get_db_pool():
    """Get database pool from app state."""
    from main import db_pool
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db_pool

async def get_current_user(request: Request):
    """Get current user from auth header."""
    from main import get_current_user as auth_user
    return await auth_user(request.headers.get("authorization"))

async def log_audit(db_pool, user_id: str, action: str, resource_type: str, 
                    resource_id: str, request: Request, details: dict = None):
    """Log action to audit table."""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO audit_log (user_id, action, resource_type, resource_id, 
                                       ip_address, user_agent, details)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, user_id, action, resource_type, uuid.UUID(resource_id) if resource_id else None,
                request.client.host if request.client else None,
                request.headers.get("user-agent"),
                details)
    except Exception as e:
        logger.error(f"Audit log failed: {e}")


# ===========================================
# ENDPOINTS
# ===========================================

@router.post("", response_model=AlbumResponse)
async def create_album(
    album: AlbumCreate,
    request: Request,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Create a new album/folder."""
    logger.info(f"User {user_id} creating album")
    
    async with db_pool.acquire() as conn:
        # Verify parent exists if specified
        if album.parent_id:
            parent = await conn.fetchrow(
                "SELECT id FROM albums WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
                uuid.UUID(album.parent_id), user_id
            )
            if not parent:
                raise HTTPException(status_code=404, detail="Parent album not found")
        
        # Create album
        row = await conn.fetchrow("""
            INSERT INTO albums (user_id, parent_id, encrypted_name, encrypted_description)
            VALUES ($1, $2, $3, $4)
            RETURNING id, encrypted_name, encrypted_description, parent_id, cover_item_id,
                      item_count, is_smart_album, sort_order, created_at, updated_at
        """, user_id, 
            uuid.UUID(album.parent_id) if album.parent_id else None,
            album.encrypted_name, 
            album.encrypted_description)
        
        result = dict(row)
        result['id'] = str(result['id'])
        result['parent_id'] = str(result['parent_id']) if result['parent_id'] else None
        result['cover_item_id'] = str(result['cover_item_id']) if result['cover_item_id'] else None
        
        # Audit log
        await log_audit(db_pool, user_id, "album.create", "album", result['id'], request)
        
        logger.info(f"Album {result['id']} created by user {user_id}")
        return result


@router.get("", response_model=List[AlbumResponse])
async def list_albums(
    parent_id: Optional[str] = None,
    include_smart: bool = True,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """List user's albums, optionally filtered by parent."""
    logger.info(f"User {user_id} listing albums (parent={parent_id})")
    
    async with db_pool.acquire() as conn:
        if parent_id:
            albums = await conn.fetch("""
                SELECT id, encrypted_name, encrypted_description, parent_id, cover_item_id,
                       item_count, is_smart_album, sort_order, created_at, updated_at
                FROM albums
                WHERE user_id = $1 AND parent_id = $2 AND deleted_at IS NULL
                ORDER BY sort_order, created_at
            """, user_id, uuid.UUID(parent_id))
        else:
            # Root level albums (no parent)
            query = """
                SELECT id, encrypted_name, encrypted_description, parent_id, cover_item_id,
                       item_count, is_smart_album, sort_order, created_at, updated_at
                FROM albums
                WHERE user_id = $1 AND parent_id IS NULL AND deleted_at IS NULL
            """
            if not include_smart:
                query += " AND is_smart_album = FALSE"
            query += " ORDER BY sort_order, created_at"
            albums = await conn.fetch(query, user_id)
        
        results = []
        for row in albums:
            r = dict(row)
            r['id'] = str(r['id'])
            r['parent_id'] = str(r['parent_id']) if r['parent_id'] else None
            r['cover_item_id'] = str(r['cover_item_id']) if r['cover_item_id'] else None
            results.append(r)
        
        return results


@router.get("/{album_id}", response_model=AlbumResponse)
async def get_album(
    album_id: str,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Get album details."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, encrypted_name, encrypted_description, parent_id, cover_item_id,
                   item_count, is_smart_album, sort_order, created_at, updated_at
            FROM albums
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
        """, uuid.UUID(album_id), user_id)
        
        if not row:
            raise HTTPException(status_code=404, detail="Album not found")
        
        result = dict(row)
        result['id'] = str(result['id'])
        result['parent_id'] = str(result['parent_id']) if result['parent_id'] else None
        result['cover_item_id'] = str(result['cover_item_id']) if result['cover_item_id'] else None
        
        return result


@router.put("/{album_id}", response_model=AlbumResponse)
async def update_album(
    album_id: str,
    update: AlbumUpdate,
    request: Request,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Update album details."""
    logger.info(f"User {user_id} updating album {album_id}")
    
    async with db_pool.acquire() as conn:
        # Build dynamic update
        updates = []
        params = [uuid.UUID(album_id), user_id]
        param_idx = 3
        
        if update.encrypted_name is not None:
            updates.append(f"encrypted_name = ${param_idx}")
            params.append(update.encrypted_name)
            param_idx += 1
        
        if update.encrypted_description is not None:
            updates.append(f"encrypted_description = ${param_idx}")
            params.append(update.encrypted_description)
            param_idx += 1
        
        if update.cover_item_id is not None:
            updates.append(f"cover_item_id = ${param_idx}")
            params.append(uuid.UUID(update.cover_item_id))
            param_idx += 1
        
        if update.sort_order is not None:
            updates.append(f"sort_order = ${param_idx}")
            params.append(update.sort_order)
            param_idx += 1
        
        if not updates:
            raise HTTPException(status_code=400, detail="No updates provided")
        
        updates.append("updated_at = NOW()")
        
        row = await conn.fetchrow(f"""
            UPDATE albums SET {', '.join(updates)}
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
            RETURNING id, encrypted_name, encrypted_description, parent_id, cover_item_id,
                      item_count, is_smart_album, sort_order, created_at, updated_at
        """, *params)
        
        if not row:
            raise HTTPException(status_code=404, detail="Album not found")
        
        result = dict(row)
        result['id'] = str(result['id'])
        result['parent_id'] = str(result['parent_id']) if result['parent_id'] else None
        result['cover_item_id'] = str(result['cover_item_id']) if result['cover_item_id'] else None
        
        await log_audit(db_pool, user_id, "album.update", "album", album_id, request)
        
        return result


@router.delete("/{album_id}")
async def delete_album(
    album_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Soft delete an album (items are NOT deleted, just unlinked)."""
    logger.info(f"User {user_id} deleting album {album_id}")
    
    async with db_pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE albums SET deleted_at = NOW()
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
        """, uuid.UUID(album_id), user_id)
        
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Album not found")
        
        await log_audit(db_pool, user_id, "album.delete", "album", album_id, request)
        
        return {"message": "Album deleted"}


@router.get("/{album_id}/items")
async def list_album_items(
    album_id: str,
    limit: int = Query(100, le=500),
    offset: int = 0,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """List items in an album."""
    async with db_pool.acquire() as conn:
        # Verify album access
        album = await conn.fetchrow(
            "SELECT id FROM albums WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
            uuid.UUID(album_id), user_id
        )
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
        
        items = await conn.fetch("""
            SELECT v.id, v.item_type, v.encrypted_metadata, v.storage_key, v.file_size,
                   v.mime_type, v.captured_at, v.created_at, ai.sort_order
            FROM album_items ai
            JOIN vault_items v ON ai.item_id = v.id
            WHERE ai.album_id = $1 AND v.deleted_at IS NULL
            ORDER BY ai.sort_order, v.captured_at DESC
            LIMIT $2 OFFSET $3
        """, uuid.UUID(album_id), limit, offset)
        
        return {"items": [dict(i) for i in items], "count": len(items)}


@router.post("/{album_id}/items")
async def add_items_to_album(
    album_id: str,
    request_body: AddItemsRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Add items to an album."""
    logger.info(f"User {user_id} adding {len(request_body.item_ids)} items to album {album_id}")
    
    async with db_pool.acquire() as conn:
        # Verify album access
        album = await conn.fetchrow(
            "SELECT id FROM albums WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
            uuid.UUID(album_id), user_id
        )
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
        
        # Add items (ignore duplicates)
        added = 0
        for item_id in request_body.item_ids:
            try:
                await conn.execute("""
                    INSERT INTO album_items (album_id, item_id)
                    VALUES ($1, $2)
                    ON CONFLICT (album_id, item_id) DO NOTHING
                """, uuid.UUID(album_id), uuid.UUID(item_id))
                added += 1
            except Exception as e:
                logger.warning(f"Failed to add item {item_id}: {e}")
        
        await log_audit(db_pool, user_id, "album.add_items", "album", album_id, request,
                       {"item_count": added})
        
        return {"message": f"Added {added} items to album"}


@router.delete("/{album_id}/items/{item_id}")
async def remove_item_from_album(
    album_id: str,
    item_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Remove an item from an album (doesn't delete the item)."""
    logger.info(f"User {user_id} removing item {item_id} from album {album_id}")
    
    async with db_pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM album_items 
            WHERE album_id = $1 AND item_id = $2
            AND EXISTS (SELECT 1 FROM albums WHERE id = $1 AND user_id = $3)
        """, uuid.UUID(album_id), uuid.UUID(item_id), user_id)
        
        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Item not in album")
        
        await log_audit(db_pool, user_id, "album.remove_item", "album", album_id, request,
                       {"item_id": item_id})
        
        return {"message": "Item removed from album"}
