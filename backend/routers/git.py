"""
PROJEKT GENESIS: Vault-Git API Router
Created: 2026-02-21
Author: Fleet Admiral Bombas

Git-like version control for Vault spaces.
Integrated with GitDB for full database operations.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime
import uuid
import hashlib
import json
import re

router = APIRouter(prefix="/git", tags=["git"])

# Global db reference - set by init_git_router
_db = None

def init_git_router(db_pool):
    """Initialize router with database pool."""
    global _db
    from .git_db import GitDB
    _db = GitDB(db_pool)

def get_db():
    if _db is None:
        raise HTTPException(status_code=500, detail="Git DB not initialized")
    return _db

# ============================================
# MODELS
# ============================================

class SpaceCreate(BaseModel):
    name: str
    description: Optional[str] = None
    visibility: str = "private"

class SpaceResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: Optional[str]
    default_branch: str
    visibility: str
    branch_count: Optional[int] = 0
    snapshot_count: Optional[int] = 0
    created_at: datetime

class BranchCreate(BaseModel):
    name: str
    from_branch: str = "main"

class BranchResponse(BaseModel):
    id: str
    name: str
    head_snapshot_id: Optional[str]
    protected: bool
    created_at: datetime

class SnapshotCreate(BaseModel):
    message: str
    files: List[dict]  # [{path, content_hash, size_bytes, mime_type, action}]

class ReviewCreate(BaseModel):
    title: str
    source_branch: str
    target_branch: str = "main"
    description: Optional[str] = None

# ============================================
# HELPERS
# ============================================

def slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")

def compute_tree_hash(files: List[dict]) -> str:
    sorted_files = sorted(files, key=lambda f: f.get("path", ""))
    content = json.dumps(sorted_files, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()

# ============================================
# SPACE ENDPOINTS
# ============================================

@router.post("/spaces")
async def create_space(space: SpaceCreate, tenant_id: str = Query(...), user_id: str = Query(default="system")):
    """Create a new versioned space (like git init)."""
    db = get_db()
    slug = slugify(space.name)
    result = await db.create_space(
        tenant_id=tenant_id,
        name=space.name,
        slug=slug,
        description=space.description,
        visibility=space.visibility,
        created_by=user_id
    )
    return {
        "id": str(result["id"]),
        "name": result["name"],
        "slug": result["slug"],
        "description": result.get("description"),
        "default_branch": result.get("default_branch", "main"),
        "visibility": result.get("visibility", "private"),
        "created_at": result["created_at"].isoformat()
    }

@router.get("/spaces")
async def list_spaces(
    tenant_id: str = Query(...),
    limit: int = Query(default=50, le=100),
    offset: int = 0
):
    """List all spaces for a tenant."""
    db = get_db()
    spaces = await db.list_spaces(tenant_id, limit, offset)
    return {
        "spaces": [{
            "id": str(s["id"]),
            "name": s["name"],
            "slug": s["slug"],
            "description": s.get("description"),
            "default_branch": s.get("default_branch", "main"),
            "visibility": s.get("visibility", "private"),
            "branch_count": s.get("branch_count", 0),
            "snapshot_count": s.get("snapshot_count", 0),
            "created_at": s["created_at"].isoformat(),
            "updated_at": s["updated_at"].isoformat() if s.get("updated_at") else None
        } for s in spaces],
        "total": len(spaces),
        "limit": limit,
        "offset": offset
    }

@router.get("/spaces/{space_id}")
async def get_space(space_id: str):
    """Get space details."""
    db = get_db()
    space = await db.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    return {
        "id": str(space["id"]),
        "name": space["name"],
        "slug": space["slug"],
        "description": space.get("description"),
        "default_branch": space.get("default_branch", "main"),
        "visibility": space.get("visibility", "private"),
        "created_at": space["created_at"].isoformat()
    }

# ============================================
# BRANCH ENDPOINTS
# ============================================

@router.post("/spaces/{space_id}/branches")
async def create_branch(space_id: str, branch: BranchCreate, user_id: str = Query(default="system")):
    """Create a new branch (like git checkout -b)."""
    db = get_db()
    result = await db.create_branch(
        space_id=space_id,
        name=branch.name,
        from_branch=branch.from_branch,
        created_by=user_id
    )
    return {
        "id": str(result["id"]),
        "name": result["name"],
        "head_snapshot_id": str(result["head_snapshot_id"]) if result.get("head_snapshot_id") else None,
        "protected": result.get("protected", False),
        "created_at": result["created_at"].isoformat()
    }

@router.get("/spaces/{space_id}/branches")
async def list_branches(space_id: str):
    """List all branches in a space."""
    db = get_db()
    branches = await db.list_branches(space_id)
    return {
        "branches": [{
            "id": str(b["id"]),
            "name": b["name"],
            "head_snapshot_id": str(b["head_snapshot_id"]) if b.get("head_snapshot_id") else None,
            "protected": b.get("protected", False),
            "created_at": b["created_at"].isoformat()
        } for b in branches]
    }

@router.delete("/spaces/{space_id}/branches/{branch_name}")
async def delete_branch(space_id: str, branch_name: str):
    """Delete a branch."""
    if branch_name == "main":
        raise HTTPException(status_code=400, detail="Cannot delete main branch")
    db = get_db()
    await db.delete_branch(space_id, branch_name)
    return {"deleted": True}

# ============================================
# SNAPSHOT (COMMIT) ENDPOINTS
# ============================================

@router.post("/spaces/{space_id}/snapshots")
async def create_snapshot(
    space_id: str,
    snapshot: SnapshotCreate,
    branch: str = Query(default="main"),
    user_id: str = Query(default="system"),
    user_name: str = Query(default="System"),
    user_email: str = Query(default="system@0711.io")
):
    """Create a snapshot (commit) on a branch."""
    db = get_db()
    result = await db.create_snapshot(
        space_id=space_id,
        branch_name=branch,
        message=snapshot.message,
        files=snapshot.files,
        author_id=user_id,
        author_name=user_name,
        author_email=user_email
    )
    return {
        "id": str(result["id"]),
        "message": result["message"],
        "tree_hash": result["tree_hash"],
        "author_name": result.get("author_name", user_name),
        "branch": branch,
        "created_at": result["created_at"].isoformat()
    }

@router.get("/spaces/{space_id}/history")
async def get_history(
    space_id: str,
    branch: str = Query(default="main"),
    path: Optional[str] = None,
    limit: int = Query(default=50, le=100)
):
    """Get commit history (like git log)."""
    db = get_db()
    commits = await db.get_history(space_id, branch, path, limit)
    return {
        "commits": [{
            "id": str(c["id"]),
            "message": c["message"],
            "author_name": c.get("author_name"),
            "author_email": c.get("author_email"),
            "tree_hash": c["tree_hash"],
            "created_at": c["created_at"].isoformat()
        } for c in commits],
        "branch": branch,
        "path": path
    }

@router.get("/spaces/{space_id}/snapshots/{snapshot_id}")
async def get_snapshot(space_id: str, snapshot_id: str):
    """Get snapshot details."""
    db = get_db()
    snapshot = await db.get_snapshot(snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {
        "id": str(snapshot["id"]),
        "message": snapshot["message"],
        "author_name": snapshot.get("author_name"),
        "author_email": snapshot.get("author_email"),
        "tree_hash": snapshot["tree_hash"],
        "created_at": snapshot["created_at"].isoformat()
    }

# ============================================
# TREE ENDPOINTS
# ============================================

@router.get("/spaces/{space_id}/tree")
async def get_tree(
    space_id: str,
    ref: str = Query(default="main"),
    path: str = Query(default="/")
):
    """Get file tree at a specific ref."""
    db = get_db()
    entries = await db.get_tree(space_id, ref, path)
    return {
        "ref": ref,
        "path": path,
        "entries": [{
            "path": e["path"],
            "type": e["type"],
            "file_version_id": str(e["file_version_id"]) if e.get("file_version_id") else None,
            "mode": e.get("mode", "644")
        } for e in entries]
    }

@router.get("/spaces/{space_id}/blob/{path:path}")
async def get_blob(
    space_id: str,
    path: str,
    ref: str = Query(default="main")
):
    """Get file content reference at a specific ref."""
    db = get_db()
    blob = await db.get_blob(space_id, ref, "/" + path if not path.startswith("/") else path)
    if not blob:
        raise HTTPException(status_code=404, detail="File not found")
    return {
        "path": path,
        "ref": ref,
        "file_version_id": str(blob["id"]),
        "content_hash": blob["content_hash"],
        "size_bytes": blob["size_bytes"],
        "mime_type": blob.get("mime_type")
    }

# ============================================
# DIFF ENDPOINTS
# ============================================

@router.get("/spaces/{space_id}/diff")
async def get_diff(
    space_id: str,
    from_ref: str = Query(...),
    to_ref: str = Query(...)
):
    """Compare two refs (like git diff)."""
    db = get_db()
    diff = await db.compute_diff(space_id, from_ref, to_ref)
    return {
        "from_ref": from_ref,
        "to_ref": to_ref,
        "files_changed": diff.get("files_changed", 0),
        "additions": diff.get("additions", 0),
        "deletions": diff.get("deletions", 0),
        "changes": diff.get("changes", [])
    }

# ============================================
# REVIEW (PR) ENDPOINTS  
# ============================================

@router.post("/spaces/{space_id}/reviews")
async def create_review(
    space_id: str,
    review: ReviewCreate,
    user_id: str = Query(default="system")
):
    """Create a review request (like a PR)."""
    db = get_db()
    result = await db.create_review(
        space_id=space_id,
        title=review.title,
        source_branch=review.source_branch,
        target_branch=review.target_branch,
        description=review.description,
        created_by=user_id
    )
    return {
        "id": str(result["id"]),
        "number": result["number"],
        "title": result["title"],
        "source_branch": review.source_branch,
        "target_branch": review.target_branch,
        "status": result.get("status", "open"),
        "created_at": result["created_at"].isoformat()
    }

@router.get("/spaces/{space_id}/reviews")
async def list_reviews(space_id: str, status: Optional[str] = None):
    """List review requests."""
    db = get_db()
    reviews = await db.list_reviews(space_id, status)
    return {
        "reviews": [{
            "id": str(r["id"]),
            "number": r["number"],
            "title": r["title"],
            "status": r.get("status", "open"),
            "created_at": r["created_at"].isoformat()
        } for r in reviews]
    }

@router.post("/spaces/{space_id}/reviews/{review_id}/merge")
async def merge_review(space_id: str, review_id: str, user_id: str = Query(default="system")):
    """Merge a review into target branch."""
    db = get_db()
    await db.merge_review(space_id, review_id, user_id)
    return {"status": "merged"}
