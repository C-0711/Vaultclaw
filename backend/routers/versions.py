"""
V-12: File Versioning System for 0711-Vault
Tracks file versions with restore capability

Path: backend/services/vault-api/routers/versions.py
"""

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import hashlib
import uuid

router = APIRouter(prefix="/versions", tags=["File Versioning"])

class FileVersion(BaseModel):
    id: str
    file_id: str
    version_number: int
    size_bytes: int
    checksum: str
    created_at: datetime
    created_by: Optional[str]
    comment: Optional[str]
    storage_path: str

class VersionDiff(BaseModel):
    version_a: int
    version_b: int
    size_diff: int
    changed_at: datetime

# In-memory storage (replace with DB)
versions_db: dict[str, List[FileVersion]] = {}
max_versions_per_file = 50

def compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

@router.get("/{file_id}", response_model=List[FileVersion])
async def list_versions(file_id: str, limit: int = 20):
    """Get version history for a file"""
    versions = versions_db.get(file_id, [])
    return sorted(versions, key=lambda v: v.version_number, reverse=True)[:limit]

@router.get("/{file_id}/{version_number}", response_model=FileVersion)
async def get_version(file_id: str, version_number: int):
    """Get specific version details"""
    versions = versions_db.get(file_id, [])
    for v in versions:
        if v.version_number == version_number:
            return v
    raise HTTPException(status_code=404, detail="Version not found")

@router.post("/{file_id}", response_model=FileVersion)
async def create_version(
    file_id: str,
    file: UploadFile = File(...),
    comment: Optional[str] = None,
    user_id: Optional[str] = None,
):
    """Upload new version of a file"""
    content = await file.read()
    checksum = compute_checksum(content)
    
    # Get current versions
    versions = versions_db.get(file_id, [])
    
    # Check if content actually changed
    if versions and versions[-1].checksum == checksum:
        raise HTTPException(status_code=400, detail="Content unchanged, no new version created")
    
    # Calculate version number
    version_number = (versions[-1].version_number + 1) if versions else 1
    
    # Create storage path (in production, save to actual storage)
    storage_path = f"/versions/{file_id}/{version_number}/{file.filename}"
    
    new_version = FileVersion(
        id=str(uuid.uuid4()),
        file_id=file_id,
        version_number=version_number,
        size_bytes=len(content),
        checksum=checksum,
        created_at=datetime.now(),
        created_by=user_id,
        comment=comment,
        storage_path=storage_path,
    )
    
    # Add to versions (enforce max versions)
    versions.append(new_version)
    if len(versions) > max_versions_per_file:
        versions = versions[-max_versions_per_file:]
    
    versions_db[file_id] = versions
    return new_version

@router.post("/{file_id}/{version_number}/restore")
async def restore_version(file_id: str, version_number: int, user_id: Optional[str] = None):
    """Restore a previous version as the current version"""
    versions = versions_db.get(file_id, [])
    
    target_version = None
    for v in versions:
        if v.version_number == version_number:
            target_version = v
            break
    
    if not target_version:
        raise HTTPException(status_code=404, detail="Version not found")
    
    # Create new version from restored content
    new_version_number = versions[-1].version_number + 1
    
    restored_version = FileVersion(
        id=str(uuid.uuid4()),
        file_id=file_id,
        version_number=new_version_number,
        size_bytes=target_version.size_bytes,
        checksum=target_version.checksum,
        created_at=datetime.now(),
        created_by=user_id,
        comment=f"Restored from version {version_number}",
        storage_path=f"/versions/{file_id}/{new_version_number}/restored",
    )
    
    versions.append(restored_version)
    versions_db[file_id] = versions
    
    return {"status": "restored", "new_version": new_version_number}

@router.delete("/{file_id}/{version_number}")
async def delete_version(file_id: str, version_number: int):
    """Delete a specific version (cannot delete latest)"""
    versions = versions_db.get(file_id, [])
    
    if not versions:
        raise HTTPException(status_code=404, detail="File not found")
    
    if versions[-1].version_number == version_number:
        raise HTTPException(status_code=400, detail="Cannot delete the latest version")
    
    versions_db[file_id] = [v for v in versions if v.version_number != version_number]
    return {"status": "deleted"}

@router.get("/{file_id}/diff/{version_a}/{version_b}", response_model=VersionDiff)
async def compare_versions(file_id: str, version_a: int, version_b: int):
    """Compare two versions"""
    versions = versions_db.get(file_id, [])
    
    va = vb = None
    for v in versions:
        if v.version_number == version_a:
            va = v
        if v.version_number == version_b:
            vb = v
    
    if not va or not vb:
        raise HTTPException(status_code=404, detail="One or both versions not found")
    
    return VersionDiff(
        version_a=version_a,
        version_b=version_b,
        size_diff=vb.size_bytes - va.size_bytes,
        changed_at=vb.created_at,
    )
