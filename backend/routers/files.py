"""
Vaultclaw File Manager API
Dropbox-level folder & file management
"""

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid

router = APIRouter(prefix="/files", tags=["files"])


# ===========================================
# SCHEMAS
# ===========================================

class FolderCreate(BaseModel):
    name: str
    parent_id: Optional[str] = None
    color: str = "default"
    icon: str = "folder"

class FolderUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None

class FolderResponse(BaseModel):
    id: str
    name: str
    parent_id: Optional[str]
    color: str
    icon: str
    file_count: int = 0
    created_at: datetime

class FileMove(BaseModel):
    file_ids: List[str]
    folder_id: Optional[str] = None  # None = root

class FileRename(BaseModel):
    name: str

class BulkDelete(BaseModel):
    file_ids: List[str]
    folder_ids: List[str] = []


# ===========================================
# DB POOL (injected from main)
# ===========================================

_db_pool = None
_redis_client = None

def init_files_router(db_pool, redis_client=None):
    global _db_pool, _redis_client
    _db_pool = db_pool
    _redis_client = redis_client

def get_db():
    if not _db_pool:
        raise HTTPException(503, "Database not initialized")
    return _db_pool


# ===========================================
# AUTH (simplified - uses main's auth)
# ===========================================

from fastapi import Header

async def get_current_user(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing authorization")
    token = authorization.split(" ")[1]
    
    # Use module-level redis client
    if _redis_client:
        user_id = await _redis_client.get(f"token:{token}")
        if user_id:
            return user_id.decode()
    
    # Fallback: try importing from main
    try:
        from main import redis_client
        if redis_client:
            user_id = await redis_client.get(f"token:{token}")
            if user_id:
                return user_id.decode()
    except:
        pass
    
    raise HTTPException(401, "Invalid token")


# ===========================================
# FOLDER ENDPOINTS
# ===========================================

@router.get("/folders")
async def list_folders(
    parent_id: Optional[str] = None,
    user_id: str = Depends(get_current_user)
):
    """List folders in a directory (or root if parent_id is None)."""
    db = get_db()
    
    async with db.acquire() as conn:
        if parent_id:
            rows = await conn.fetch("""
                SELECT f.*, 
                    (SELECT COUNT(*) FROM vault_items WHERE folder_id = f.id AND deleted_at IS NULL) as file_count,
                    (SELECT COUNT(*) FROM vault_folders WHERE parent_id = f.id) as subfolder_count
                FROM vault_folders f
                WHERE f.user_id = $1 AND f.parent_id = $2
                ORDER BY f.name
            """, uuid.UUID(user_id), uuid.UUID(parent_id))
        else:
            rows = await conn.fetch("""
                SELECT f.*,
                    (SELECT COUNT(*) FROM vault_items WHERE folder_id = f.id AND deleted_at IS NULL) as file_count,
                    (SELECT COUNT(*) FROM vault_folders WHERE parent_id = f.id) as subfolder_count
                FROM vault_folders f
                WHERE f.user_id = $1 AND f.parent_id IS NULL
                ORDER BY f.name
            """, uuid.UUID(user_id))
    
    return {"folders": [{
        "id": str(r["id"]),
        "name": r["name"],
        "parent_id": str(r["parent_id"]) if r["parent_id"] else None,
        "color": r["color"],
        "icon": r["icon"],
        "file_count": r["file_count"],
        "subfolder_count": r["subfolder_count"],
        "created_at": r["created_at"].isoformat()
    } for r in rows]}


@router.post("/folders")
async def create_folder(
    folder: FolderCreate,
    user_id: str = Depends(get_current_user)
):
    """Create a new folder."""
    db = get_db()
    
    async with db.acquire() as conn:
        # Check for duplicate name in same parent
        existing = await conn.fetchrow("""
            SELECT id FROM vault_folders 
            WHERE user_id = $1 AND name = $2 AND (parent_id = $3 OR (parent_id IS NULL AND $3 IS NULL))
        """, uuid.UUID(user_id), folder.name, 
            uuid.UUID(folder.parent_id) if folder.parent_id else None)
        
        if existing:
            raise HTTPException(400, "Folder with this name already exists")
        
        folder_id = await conn.fetchval("""
            INSERT INTO vault_folders (user_id, name, parent_id, color, icon)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
        """, uuid.UUID(user_id), folder.name,
            uuid.UUID(folder.parent_id) if folder.parent_id else None,
            folder.color, folder.icon)
    
    return {"id": str(folder_id), "name": folder.name, "status": "created"}


@router.get("/folders/{folder_id}")
async def get_folder(
    folder_id: str,
    user_id: str = Depends(get_current_user)
):
    """Get folder details with breadcrumb path."""
    db = get_db()
    
    async with db.acquire() as conn:
        folder = await conn.fetchrow("""
            SELECT * FROM vault_folders WHERE id = $1 AND user_id = $2
        """, uuid.UUID(folder_id), uuid.UUID(user_id))
        
        if not folder:
            raise HTTPException(404, "Folder not found")
        
        # Build breadcrumb path
        breadcrumbs = []
        current_id = folder["id"]
        while current_id:
            f = await conn.fetchrow(
                "SELECT id, name, parent_id FROM vault_folders WHERE id = $1",
                current_id
            )
            if f:
                breadcrumbs.insert(0, {"id": str(f["id"]), "name": f["name"]})
                current_id = f["parent_id"]
            else:
                break
    
    return {
        "id": str(folder["id"]),
        "name": folder["name"],
        "parent_id": str(folder["parent_id"]) if folder["parent_id"] else None,
        "color": folder["color"],
        "icon": folder["icon"],
        "breadcrumbs": breadcrumbs,
        "created_at": folder["created_at"].isoformat()
    }


@router.patch("/folders/{folder_id}")
async def update_folder(
    folder_id: str,
    update: FolderUpdate,
    user_id: str = Depends(get_current_user)
):
    """Update folder (rename, move, change color/icon)."""
    db = get_db()
    
    async with db.acquire() as conn:
        # Verify ownership
        folder = await conn.fetchrow(
            "SELECT * FROM vault_folders WHERE id = $1 AND user_id = $2",
            uuid.UUID(folder_id), uuid.UUID(user_id)
        )
        if not folder:
            raise HTTPException(404, "Folder not found")
        
        # Build update query
        updates = []
        params = [uuid.UUID(folder_id)]
        param_idx = 2
        
        if update.name is not None:
            updates.append(f"name = ${param_idx}")
            params.append(update.name)
            param_idx += 1
        
        if update.parent_id is not None:
            # Prevent moving folder into itself or its children
            if update.parent_id == folder_id:
                raise HTTPException(400, "Cannot move folder into itself")
            updates.append(f"parent_id = ${param_idx}")
            params.append(uuid.UUID(update.parent_id) if update.parent_id else None)
            param_idx += 1
        
        if update.color is not None:
            updates.append(f"color = ${param_idx}")
            params.append(update.color)
            param_idx += 1
        
        if update.icon is not None:
            updates.append(f"icon = ${param_idx}")
            params.append(update.icon)
            param_idx += 1
        
        if updates:
            updates.append("updated_at = NOW()")
            await conn.execute(
                f"UPDATE vault_folders SET {', '.join(updates)} WHERE id = $1",
                *params
            )
    
    return {"status": "updated"}


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    user_id: str = Depends(get_current_user)
):
    """Delete folder and optionally its contents."""
    db = get_db()
    
    async with db.acquire() as conn:
        # Verify ownership
        folder = await conn.fetchrow(
            "SELECT * FROM vault_folders WHERE id = $1 AND user_id = $2",
            uuid.UUID(folder_id), uuid.UUID(user_id)
        )
        if not folder:
            raise HTTPException(404, "Folder not found")
        
        # Move files to root (don't delete them)
        await conn.execute(
            "UPDATE vault_items SET folder_id = NULL WHERE folder_id = $1",
            uuid.UUID(folder_id)
        )
        
        # Delete folder (cascade deletes subfolders)
        await conn.execute(
            "DELETE FROM vault_folders WHERE id = $1",
            uuid.UUID(folder_id)
        )
    
    return {"status": "deleted"}


# ===========================================
# FILE BROWSING
# ===========================================

@router.get("/browse")
async def browse_files(
    folder_id: Optional[str] = None,
    search: Optional[str] = None,
    file_type: Optional[str] = None,
    sort: str = "name",  # name, date, size
    order: str = "asc",
    limit: int = Query(100, le=500),
    offset: int = 0,
    user_id: str = Depends(get_current_user)
):
    """
    Browse files and folders in a directory.
    Returns both folders and files for the current location.
    """
    db = get_db()
    
    async with db.acquire() as conn:
        # Get folders in this location
        if folder_id:
            folders = await conn.fetch("""
                SELECT f.*,
                    (SELECT COUNT(*) FROM vault_items WHERE folder_id = f.id AND deleted_at IS NULL) as file_count
                FROM vault_folders f
                WHERE f.user_id = $1 AND f.parent_id = $2
                ORDER BY f.name
            """, uuid.UUID(user_id), uuid.UUID(folder_id))
        else:
            folders = await conn.fetch("""
                SELECT f.*,
                    (SELECT COUNT(*) FROM vault_items WHERE folder_id = f.id AND deleted_at IS NULL) as file_count
                FROM vault_folders f
                WHERE f.user_id = $1 AND f.parent_id IS NULL
                ORDER BY f.name
            """, uuid.UUID(user_id))
        
        # Build file query
        conditions = ["user_id = $1", "deleted_at IS NULL"]
        params = [uuid.UUID(user_id)]
        param_idx = 2
        
        if folder_id:
            conditions.append(f"folder_id = ${param_idx}")
            params.append(uuid.UUID(folder_id))
            param_idx += 1
        else:
            conditions.append("folder_id IS NULL")
        
        if search:
            conditions.append(f"(original_filename ILIKE ${param_idx} OR storage_key ILIKE ${param_idx})")
            params.append(f"%{search}%")
            param_idx += 1
        
        if file_type and file_type != "all":
            conditions.append(f"item_type = ${param_idx}")
            params.append(file_type)
            param_idx += 1
        
        # Sort
        sort_col = {"name": "COALESCE(original_filename, storage_key)", "date": "created_at", "size": "file_size"}.get(sort, "created_at")
        sort_dir = "ASC" if order == "asc" else "DESC"
        
        params.extend([limit, offset])
        
        files = await conn.fetch(f"""
            SELECT id, item_type, storage_key, original_filename, file_size, mime_type, 
                   folder_id, created_at, processing_status
            FROM vault_items
            WHERE {' AND '.join(conditions)}
            ORDER BY {sort_col} {sort_dir}
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """, *params)
        
        # Get total count
        count_params = params[:-2]  # Remove limit/offset
        total = await conn.fetchval(f"""
            SELECT COUNT(*) FROM vault_items WHERE {' AND '.join(conditions)}
        """, *count_params)
        
        # Get breadcrumbs if in a folder
        breadcrumbs = [{"id": None, "name": "My Vault"}]
        if folder_id:
            current_id = uuid.UUID(folder_id)
            path = []
            while current_id:
                f = await conn.fetchrow(
                    "SELECT id, name, parent_id FROM vault_folders WHERE id = $1",
                    current_id
                )
                if f:
                    path.insert(0, {"id": str(f["id"]), "name": f["name"]})
                    current_id = f["parent_id"]
                else:
                    break
            breadcrumbs.extend(path)
    
    return {
        "breadcrumbs": breadcrumbs,
        "folders": [{
            "id": str(f["id"]),
            "name": f["name"],
            "type": "folder",
            "color": f["color"],
            "icon": f["icon"],
            "file_count": f["file_count"],
            "created_at": f["created_at"].isoformat()
        } for f in folders],
        "files": [{
            "id": str(f["id"]),
            "name": f["original_filename"] or f["storage_key"].split("/")[-1],
            "type": "file",
            "item_type": f["item_type"],
            "mime_type": f["mime_type"],
            "size": f["file_size"],
            "folder_id": str(f["folder_id"]) if f["folder_id"] else None,
            "created_at": f["created_at"].isoformat(),
            "status": f["processing_status"]
        } for f in files],
        "total_files": total,
        "limit": limit,
        "offset": offset
    }


# ===========================================
# FILE OPERATIONS
# ===========================================

@router.post("/move")
async def move_files(
    move: FileMove,
    user_id: str = Depends(get_current_user)
):
    """Move files to a folder (or root if folder_id is None)."""
    db = get_db()
    
    if not move.file_ids:
        raise HTTPException(400, "No files specified")
    
    async with db.acquire() as conn:
        # Verify folder ownership if moving to a folder
        if move.folder_id:
            folder = await conn.fetchrow(
                "SELECT id FROM vault_folders WHERE id = $1 AND user_id = $2",
                uuid.UUID(move.folder_id), uuid.UUID(user_id)
            )
            if not folder:
                raise HTTPException(404, "Destination folder not found")
        
        # Move files
        file_uuids = [uuid.UUID(fid) for fid in move.file_ids]
        await conn.execute("""
            UPDATE vault_items 
            SET folder_id = $1, updated_at = NOW()
            WHERE id = ANY($2) AND user_id = $3
        """, uuid.UUID(move.folder_id) if move.folder_id else None, 
            file_uuids, uuid.UUID(user_id))
    
    return {"status": "moved", "count": len(move.file_ids)}


@router.patch("/items/{file_id}/rename")
async def rename_file(
    file_id: str,
    rename: FileRename,
    user_id: str = Depends(get_current_user)
):
    """Rename a file."""
    db = get_db()
    
    async with db.acquire() as conn:
        result = await conn.execute("""
            UPDATE vault_items 
            SET original_filename = $1, updated_at = NOW()
            WHERE id = $2 AND user_id = $3
        """, rename.name, uuid.UUID(file_id), uuid.UUID(user_id))
        
        if result == "UPDATE 0":
            raise HTTPException(404, "File not found")
    
    return {"status": "renamed", "name": rename.name}


@router.post("/bulk-delete")
async def bulk_delete(
    delete: BulkDelete,
    user_id: str = Depends(get_current_user)
):
    """Delete multiple files and/or folders."""
    db = get_db()
    
    deleted_files = 0
    deleted_folders = 0
    
    async with db.acquire() as conn:
        # Soft delete files
        if delete.file_ids:
            file_uuids = [uuid.UUID(fid) for fid in delete.file_ids]
            result = await conn.execute("""
                UPDATE vault_items 
                SET deleted_at = NOW()
                WHERE id = ANY($1) AND user_id = $2 AND deleted_at IS NULL
            """, file_uuids, uuid.UUID(user_id))
            deleted_files = int(result.split()[-1]) if result else 0
        
        # Delete folders (files moved to root, not deleted)
        if delete.folder_ids:
            for folder_id in delete.folder_ids:
                # Move files to root
                await conn.execute(
                    "UPDATE vault_items SET folder_id = NULL WHERE folder_id = $1",
                    uuid.UUID(folder_id)
                )
                # Delete folder
                result = await conn.execute(
                    "DELETE FROM vault_folders WHERE id = $1 AND user_id = $2",
                    uuid.UUID(folder_id), uuid.UUID(user_id)
                )
                if "DELETE 1" in result:
                    deleted_folders += 1
    
    return {
        "status": "deleted",
        "deleted_files": deleted_files,
        "deleted_folders": deleted_folders
    }


@router.get("/storage")
async def get_storage_stats(user_id: str = Depends(get_current_user)):
    """Get storage usage statistics."""
    db = get_db()
    
    async with db.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total_files,
                COUNT(*) FILTER (WHERE item_type = 'photo') as photos,
                COUNT(*) FILTER (WHERE item_type = 'document') as documents,
                COUNT(*) FILTER (WHERE item_type = 'video') as videos,
                COUNT(*) FILTER (WHERE item_type = 'audio') as audio,
                COALESCE(SUM(file_size), 0) as total_bytes
            FROM vault_items
            WHERE user_id = $1 AND deleted_at IS NULL
        """, uuid.UUID(user_id))
        
        folder_count = await conn.fetchval(
            "SELECT COUNT(*) FROM vault_folders WHERE user_id = $1",
            uuid.UUID(user_id)
        )
    
    # Storage limit (could come from user plan)
    storage_limit = 10 * 1024 * 1024 * 1024  # 10 GB default
    
    return {
        "used_bytes": stats["total_bytes"],
        "limit_bytes": storage_limit,
        "used_percent": round((stats["total_bytes"] / storage_limit) * 100, 1),
        "total_files": stats["total_files"],
        "total_folders": folder_count,
        "by_type": {
            "photos": stats["photos"],
            "documents": stats["documents"],
            "videos": stats["videos"],
            "audio": stats["audio"]
        }
    }
