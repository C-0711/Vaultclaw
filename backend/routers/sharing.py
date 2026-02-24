"""
Sharing & Permissions API for 0711-Vault
File and folder sharing with granular permissions
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
from enum import Enum
import uuid
import secrets

router = APIRouter(prefix="/sharing", tags=["Sharing"])

class Permission(str, Enum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"

class ShareCreate(BaseModel):
    resource_type: str  # "file" or "folder"
    resource_id: str
    recipient_email: Optional[str] = None
    permission: Permission = Permission.READ
    expires_in_days: Optional[int] = None
    public: bool = False
    password: Optional[str] = None

class ShareResponse(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    permission: Permission
    recipient_email: Optional[str]
    public: bool
    public_link: Optional[str]
    expires_at: Optional[datetime]
    created_at: datetime

# Storage
shares_db: dict = {}

@router.post("", response_model=ShareResponse)
async def create_share(share: ShareCreate):
    share_id = str(uuid.uuid4())
    now = datetime.now()
    expires_at = now + timedelta(days=share.expires_in_days) if share.expires_in_days else None
    
    public_link = None
    if share.public:
        token = secrets.token_urlsafe(32)
        public_link = f"https://vault.0711.io/s/{token}"
    
    new_share = {
        "id": share_id,
        "resource_type": share.resource_type,
        "resource_id": share.resource_id,
        "permission": share.permission,
        "recipient_email": share.recipient_email,
        "public": share.public,
        "public_link": public_link,
        "password_hash": share.password,  # TODO: hash it
        "expires_at": expires_at,
        "created_at": now,
    }
    shares_db[share_id] = new_share
    return ShareResponse(**new_share)

@router.get("", response_model=List[ShareResponse])
async def list_shares(resource_id: Optional[str] = None):
    result = []
    for share in shares_db.values():
        if resource_id is None or share["resource_id"] == resource_id:
            result.append(ShareResponse(**share))
    return result

@router.delete("/{share_id}")
async def revoke_share(share_id: str):
    if share_id not in shares_db:
        raise HTTPException(status_code=404, detail="Share not found")
    del shares_db[share_id]
    return {"status": "revoked"}

@router.get("/public/{token}")
async def access_public_share(token: str, password: Optional[str] = None):
    for share in shares_db.values():
        if share.get("public_link", "").endswith(token):
            if share.get("expires_at") and datetime.now() > share["expires_at"]:
                raise HTTPException(status_code=410, detail="Link expired")
            if share.get("password_hash") and share["password_hash"] != password:
                raise HTTPException(status_code=401, detail="Password required")
            return {"resource_type": share["resource_type"], "resource_id": share["resource_id"], "permission": share["permission"]}
    raise HTTPException(status_code=404, detail="Share not found")
