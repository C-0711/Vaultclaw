"""
Folder Management API for 0711-Vault
Hierarchical folder structure for organizing files
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid

router = APIRouter(prefix="/folders", tags=["Folders"])

# Models
class FolderCreate(BaseModel):
    name: str
    parent_id: Optional[str] = None
    description: Optional[str] = None

class FolderUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None
    description: Optional[str] = None

class FolderResponse(BaseModel):
    id: str
    name: str
    parent_id: Optional[str]
    path: str
    description: Optional[str]
    item_count: int
    subfolder_count: int
    created_at: datetime
    updated_at: datetime

class FolderTree(BaseModel):
    id: str
    name: str
    path: str
    children: List['FolderTree'] = []
    item_count: int = 0

FolderTree.model_rebuild()

# In-memory storage (replace with DB)
folders_db: dict = {}


def get_folder_path(folder_id: str, folders: dict) -> str:
    """Build full path for a folder"""
    parts = []
    current_id = folder_id
    while current_id:
        folder = folders.get(current_id)
        if not folder:
            break
        parts.insert(0, folder['name'])
        current_id = folder.get('parent_id')
    return '/' + '/'.join(parts)


@router.post("", response_model=FolderResponse)
async def create_folder(folder: FolderCreate):
    """Create a new folder"""
    folder_id = str(uuid.uuid4())
    now = datetime.now()
    
    # Verify parent exists if specified
    if folder.parent_id and folder.parent_id not in folders_db:
        raise HTTPException(status_code=404, detail="Parent folder not found")
    
    new_folder = {
        "id": folder_id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "description": folder.description,
        "item_count": 0,
        "subfolder_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    
    new_folder["path"] = get_folder_path(folder_id, {**folders_db, folder_id: new_folder})
    folders_db[folder_id] = new_folder
    
    # Update parent's subfolder count
    if folder.parent_id:
        folders_db[folder.parent_id]["subfolder_count"] += 1
    
    return FolderResponse(**new_folder)


@router.get("", response_model=List[FolderResponse])
async def list_folders(parent_id: Optional[str] = None):
    """List folders, optionally filtered by parent"""
    result = []
    for folder in folders_db.values():
        if folder.get("parent_id") == parent_id:
            result.append(FolderResponse(**folder))
    return result


@router.get("/tree", response_model=List[FolderTree])
async def get_folder_tree():
    """Get complete folder tree structure"""
    def build_tree(parent_id: Optional[str] = None) -> List[FolderTree]:
        children = []
        for folder in folders_db.values():
            if folder.get("parent_id") == parent_id:
                tree_node = FolderTree(
                    id=folder["id"],
                    name=folder["name"],
                    path=folder["path"],
                    item_count=folder["item_count"],
                    children=build_tree(folder["id"])
                )
                children.append(tree_node)
        return children
    
    return build_tree(None)


@router.get("/{folder_id}", response_model=FolderResponse)
async def get_folder(folder_id: str):
    """Get folder by ID"""
    folder = folders_db.get(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    return FolderResponse(**folder)


@router.put("/{folder_id}", response_model=FolderResponse)
async def update_folder(folder_id: str, update: FolderUpdate):
    """Update folder"""
    folder = folders_db.get(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    
    if update.name is not None:
        folder["name"] = update.name
    if update.description is not None:
        folder["description"] = update.description
    if update.parent_id is not None:
        # Prevent circular reference
        if update.parent_id == folder_id:
            raise HTTPException(status_code=400, detail="Cannot set folder as its own parent")
        folder["parent_id"] = update.parent_id
    
    folder["updated_at"] = datetime.now()
    folder["path"] = get_folder_path(folder_id, folders_db)
    
    return FolderResponse(**folder)


@router.delete("/{folder_id}")
async def delete_folder(folder_id: str, recursive: bool = False):
    """Delete folder (optionally recursive)"""
    folder = folders_db.get(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    
    # Check for children
    has_children = any(f.get("parent_id") == folder_id for f in folders_db.values())
    
    if has_children and not recursive:
        raise HTTPException(
            status_code=400, 
            detail="Folder has subfolders. Use recursive=true to delete"
        )
    
    if recursive:
        # Delete all descendants
        to_delete = [folder_id]
        for fid, f in list(folders_db.items()):
            if f.get("parent_id") in to_delete:
                to_delete.append(fid)
        for fid in to_delete:
            del folders_db[fid]
    else:
        del folders_db[folder_id]
    
    return {"status": "deleted", "folder_id": folder_id}


@router.post("/{folder_id}/move")
async def move_folder(folder_id: str, new_parent_id: Optional[str] = None):
    """Move folder to new parent"""
    folder = folders_db.get(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    
    if new_parent_id and new_parent_id not in folders_db:
        raise HTTPException(status_code=404, detail="Target parent not found")
    
    # Prevent moving to descendant
    if new_parent_id:
        current = new_parent_id
        while current:
            if current == folder_id:
                raise HTTPException(status_code=400, detail="Cannot move folder to its descendant")
            parent_folder = folders_db.get(current)
            current = parent_folder.get("parent_id") if parent_folder else None
    
    folder["parent_id"] = new_parent_id
    folder["updated_at"] = datetime.now()
    folder["path"] = get_folder_path(folder_id, folders_db)
    
    return FolderResponse(**folder)
