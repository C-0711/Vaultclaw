"""
PROJEKT GENESIS: Publishing API Router
GitBook-style documentation publishing
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
import hashlib
import json
import re

router = APIRouter(prefix="/publish", tags=["publishing"])

_db = None

def init_publish_router(db_pool):
    global _db
    _db = db_pool

def get_db():
    if _db is None:
        raise HTTPException(status_code=500, detail="Publish DB not initialized")
    return _db

# ============================================
# MODELS
# ============================================

class SiteCreate(BaseModel):
    space_id: str
    slug: str
    title: str
    description: Optional[str] = None
    branch: str = "main"
    root_path: str = "/"
    theme: str = "default"
    primary_color: str = "#2563eb"
    visibility: str = "public"

class SiteUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    branch: Optional[str] = None
    root_path: Optional[str] = None
    theme: Optional[str] = None
    primary_color: Optional[str] = None
    custom_css: Optional[str] = None
    custom_head: Optional[str] = None
    nav_config: Optional[dict] = None
    visibility: Optional[str] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None

class MemberCreate(BaseModel):
    principal_type: str = "user"
    principal_id: Optional[str] = None
    principal_email: Optional[str] = None
    role: str = "viewer"

class TokenCreate(BaseModel):
    name: str
    scopes: List[str] = ["read"]
    expires_days: Optional[int] = None

# ============================================
# HELPERS
# ============================================

def slugify(text: str) -> str:
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:100]

def generate_token() -> tuple:
    """Generate a secure token and return (token, hash, prefix)."""
    token = f"vlt_{uuid.uuid4().hex}{uuid.uuid4().hex[:16]}"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    prefix = token[:14]
    return token, token_hash, prefix

# ============================================
# SITE ENDPOINTS
# ============================================

@router.post("/sites")
async def create_site(site: SiteCreate, user_id: str = Query(default="system")):
    """Create a published site for a space."""
    db = get_db()
    
    async with db.acquire() as conn:
        # Check space exists
        space = await conn.fetchrow(
            "SELECT id FROM vault_spaces WHERE id = $1",
            uuid.UUID(site.space_id)
        )
        if not space:
            raise HTTPException(status_code=404, detail="Space not found")
        
        # Check slug available
        existing = await conn.fetchrow(
            "SELECT id FROM vault_published_sites WHERE slug = $1",
            site.slug
        )
        if existing:
            raise HTTPException(status_code=400, detail="Slug already taken")
        
        # Create site
        row = await conn.fetchrow("""
            INSERT INTO vault_published_sites 
            (space_id, slug, title, description, branch, root_path, theme, primary_color, visibility, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING *
        """, uuid.UUID(site.space_id), site.slug, site.title, site.description,
            site.branch, site.root_path, site.theme, site.primary_color, 
            site.visibility, uuid.UUID(user_id) if user_id != "system" else None)
        
        return {
            "id": str(row["id"]),
            "slug": row["slug"],
            "title": row["title"],
            "url": f"https://{row['slug']}.vault.0711.io",
            "status": row["status"],
            "created_at": row["created_at"].isoformat()
        }

@router.get("/sites")
async def list_sites(space_id: Optional[str] = None):
    """List published sites."""
    db = get_db()
    
    async with db.acquire() as conn:
        if space_id:
            rows = await conn.fetch(
                "SELECT * FROM vault_published_sites WHERE space_id = $1",
                uuid.UUID(space_id)
            )
        else:
            rows = await conn.fetch("SELECT * FROM vault_published_sites ORDER BY created_at DESC LIMIT 100")
        
        return {
            "sites": [{
                "id": str(r["id"]),
                "slug": r["slug"],
                "title": r["title"],
                "url": f"https://{r['slug']}.vault.0711.io",
                "status": r["status"],
                "visibility": r["visibility"],
                "last_published_at": r["last_published_at"].isoformat() if r["last_published_at"] else None
            } for r in rows]
        }

@router.get("/sites/{site_id}")
async def get_site(site_id: str):
    """Get site details."""
    db = get_db()
    
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM vault_published_sites WHERE id = $1",
            uuid.UUID(site_id)
        )
        if not row:
            raise HTTPException(status_code=404, detail="Site not found")
        
        return {
            "id": str(row["id"]),
            "space_id": str(row["space_id"]),
            "slug": row["slug"],
            "title": row["title"],
            "description": row["description"],
            "url": f"https://{row['slug']}.vault.0711.io",
            "custom_domain": row["custom_domain"],
            "branch": row["branch"],
            "root_path": row["root_path"],
            "theme": row["theme"],
            "primary_color": row["primary_color"],
            "visibility": row["visibility"],
            "status": row["status"],
            "nav_config": row["nav_config"],
            "meta_title": row["meta_title"],
            "meta_description": row["meta_description"],
            "last_published_at": row["last_published_at"].isoformat() if row["last_published_at"] else None
        }

@router.put("/sites/{site_id}")
async def update_site(site_id: str, update: SiteUpdate):
    """Update site settings."""
    db = get_db()
    
    updates = {k: v for k, v in update.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    
    async with db.acquire() as conn:
        # Build dynamic update
        set_clauses = []
        values = []
        for i, (key, value) in enumerate(updates.items(), 1):
            if key == "nav_config":
                set_clauses.append(f"{key} = ${i}::jsonb")
                values.append(json.dumps(value))
            else:
                set_clauses.append(f"{key} = ${i}")
                values.append(value)
        
        values.append(uuid.UUID(site_id))
        
        await conn.execute(f"""
            UPDATE vault_published_sites 
            SET {', '.join(set_clauses)}, updated_at = NOW()
            WHERE id = ${len(values)}
        """, *values)
        
        return {"updated": True}

@router.post("/sites/{site_id}/publish")
async def publish_site(site_id: str, user_id: str = Query(default="system")):
    """Publish the site (make live)."""
    db = get_db()
    
    async with db.acquire() as conn:
        # Get site and its space's latest snapshot
        site = await conn.fetchrow(
            "SELECT * FROM vault_published_sites WHERE id = $1",
            uuid.UUID(site_id)
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")
        
        # Get latest snapshot on the branch
        snapshot = await conn.fetchrow("""
            SELECT s.id FROM vault_snapshots s
            JOIN vault_branches b ON s.branch_id = b.id
            WHERE b.space_id = $1 AND b.name = $2
            ORDER BY s.created_at DESC LIMIT 1
        """, site["space_id"], site["branch"])
        
        snapshot_id = snapshot["id"] if snapshot else None
        
        # Update site status
        await conn.execute("""
            UPDATE vault_published_sites 
            SET status = 'published', 
                last_published_at = NOW(),
                last_published_snapshot_id = $1
            WHERE id = $2
        """, snapshot_id, uuid.UUID(site_id))
        
        return {
            "published": True,
            "url": f"https://{site['slug']}.vault.0711.io",
            "snapshot_id": str(snapshot_id) if snapshot_id else None,
            "published_at": datetime.utcnow().isoformat()
        }

@router.delete("/sites/{site_id}")
async def delete_site(site_id: str):
    """Delete a published site."""
    db = get_db()
    
    async with db.acquire() as conn:
        await conn.execute(
            "DELETE FROM vault_published_sites WHERE id = $1",
            uuid.UUID(site_id)
        )
        return {"deleted": True}

# ============================================
# MEMBERS (PERMISSIONS)
# ============================================

@router.post("/spaces/{space_id}/members")
async def add_member(space_id: str, member: MemberCreate, invited_by: str = Query(default="system")):
    """Add a member to a space."""
    db = get_db()
    
    async with db.acquire() as conn:
        principal_id = uuid.UUID(member.principal_id) if member.principal_id else uuid.uuid4()
        
        row = await conn.fetchrow("""
            INSERT INTO vault_space_members 
            (space_id, principal_type, principal_id, principal_email, role, invited_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (space_id, principal_type, principal_id) 
            DO UPDATE SET role = EXCLUDED.role
            RETURNING *
        """, uuid.UUID(space_id), member.principal_type, principal_id,
            member.principal_email, member.role, 
            uuid.UUID(invited_by) if invited_by != "system" else None)
        
        return {
            "id": str(row["id"]),
            "principal_type": row["principal_type"],
            "principal_id": str(row["principal_id"]),
            "email": row["principal_email"],
            "role": row["role"]
        }

@router.get("/spaces/{space_id}/members")
async def list_members(space_id: str):
    """List space members."""
    db = get_db()
    
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM vault_space_members WHERE space_id = $1",
            uuid.UUID(space_id)
        )
        
        return {
            "members": [{
                "id": str(r["id"]),
                "principal_type": r["principal_type"],
                "principal_id": str(r["principal_id"]),
                "email": r["principal_email"],
                "role": r["role"],
                "accepted": r["accepted_at"] is not None
            } for r in rows]
        }

@router.delete("/spaces/{space_id}/members/{member_id}")
async def remove_member(space_id: str, member_id: str):
    """Remove a member from a space."""
    db = get_db()
    
    async with db.acquire() as conn:
        await conn.execute(
            "DELETE FROM vault_space_members WHERE id = $1 AND space_id = $2",
            uuid.UUID(member_id), uuid.UUID(space_id)
        )
        return {"removed": True}

# ============================================
# ACCESS TOKENS
# ============================================

@router.post("/spaces/{space_id}/tokens")
async def create_token(space_id: str, token_req: TokenCreate, user_id: str = Query(default="system")):
    """Create an API access token."""
    db = get_db()
    
    token, token_hash, prefix = generate_token()
    
    expires_at = None
    if token_req.expires_days:
        from datetime import timedelta
        expires_at = datetime.utcnow() + timedelta(days=token_req.expires_days)
    
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO vault_access_tokens 
            (space_id, name, token_hash, token_prefix, scopes, expires_at, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
        """, uuid.UUID(space_id), token_req.name, token_hash, prefix,
            token_req.scopes, expires_at,
            uuid.UUID(user_id) if user_id != "system" else None)
        
        return {
            "id": str(row["id"]),
            "token": token,  # Only shown once!
            "prefix": prefix,
            "scopes": token_req.scopes,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "warning": "Save this token - it won't be shown again!"
        }

@router.get("/spaces/{space_id}/tokens")
async def list_tokens(space_id: str):
    """List access tokens (without the actual token values)."""
    db = get_db()
    
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM vault_access_tokens WHERE space_id = $1 AND revoked_at IS NULL",
            uuid.UUID(space_id)
        )
        
        return {
            "tokens": [{
                "id": str(r["id"]),
                "name": r["name"],
                "prefix": r["token_prefix"],
                "scopes": r["scopes"],
                "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
                "use_count": r["use_count"],
                "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None
            } for r in rows]
        }

@router.delete("/spaces/{space_id}/tokens/{token_id}")
async def revoke_token(space_id: str, token_id: str):
    """Revoke an access token."""
    db = get_db()
    
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE vault_access_tokens SET revoked_at = NOW() WHERE id = $1 AND space_id = $2",
            uuid.UUID(token_id), uuid.UUID(space_id)
        )
        return {"revoked": True}

# ============================================
# PERMISSION TEMPLATES
# ============================================

@router.get("/templates")
async def list_templates(tenant_id: str = Query(default="00000000-0000-0000-0000-000000000001")):
    """List permission templates."""
    db = get_db()
    
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM vault_permission_templates WHERE tenant_id = $1",
            uuid.UUID(tenant_id)
        )
        
        return {
            "templates": [{
                "id": str(r["id"]),
                "name": r["name"],
                "description": r["description"],
                "permissions": {
                    "read": r["can_read"],
                    "write": r["can_write"],
                    "delete": r["can_delete"],
                    "publish": r["can_publish"],
                    "manage_permissions": r["can_manage_permissions"],
                    "manage_branches": r["can_manage_branches"],
                    "approve_reviews": r["can_approve_reviews"]
                }
            } for r in rows]
        }
