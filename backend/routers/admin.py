"""
0711 Vault Admin Routes
User management, invitations, tenant administration
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
import uuid
import secrets

router = APIRouter(prefix="/admin", tags=["admin"])

# ===========================================
# SCHEMAS
# ===========================================

class UserInvite(BaseModel):
    email: EmailStr
    role: str = "member"
    tenant_id: str

class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None

class UserResponse(BaseModel):
    id: str
    email: str
    name: Optional[str]
    role: str
    status: str
    invited_at: Optional[datetime]
    joined_at: Optional[datetime]

# ===========================================
# AUTH HELPER
# ===========================================

async def get_admin_user(authorization: str = None) -> dict:
    """Validate admin token and return user info."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    
    from main import redis_client, db_pool
    token = authorization.split(" ")[1]
    
    if redis_client:
        user_id = await redis_client.get(f"token:{token}")
        if user_id:
            user_id = user_id.decode()
            # Check admin role
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT role FROM users WHERE id = \", 
                    uuid.UUID(user_id)
                )
                if row and row["role"] == "admin":
                    return {"user_id": user_id, "role": "admin"}
    
    raise HTTPException(status_code=403, detail="Admin access required")

# ===========================================
# USER MANAGEMENT
# ===========================================

@router.get("/users")
async def list_users(admin: dict = Depends(get_admin_user)):
    """List all users in the tenant."""
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, email, name, role, status, invited_at, joined_at
            FROM users
            ORDER BY created_at DESC
        """)
    
    return {
        "users": [{
            "id": str(r["id"]),
            "email": r["email"],
            "name": r["name"],
            "role": r["role"],
            "status": r["status"],
            "invited_at": r["invited_at"].isoformat() if r["invited_at"] else None,
            "joined_at": r["joined_at"].isoformat() if r["joined_at"] else None,
        } for r in rows]
    }


@router.post("/users/invite")
async def invite_user(
    data: UserInvite,
    background_tasks: BackgroundTasks,
    admin: dict = Depends(get_admin_user)
):
    """Invite a user via email."""
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    # Generate invite token
    invite_token = secrets.token_urlsafe(32)
    user_id = uuid.uuid4()
    
    async with db_pool.acquire() as conn:
        # Check if user already exists
        existing = await conn.fetchrow(
            "SELECT id FROM users WHERE email = \", data.email
        )
        if existing:
            raise HTTPException(400, "User already exists")
        
        # Create pending user
        await conn.execute("""
            INSERT INTO users (id, email, role, status, invite_token, invited_at, invited_by)
            VALUES (\, \, \, 'pending', \, NOW(), \)
        """, user_id, data.email, data.role, invite_token, uuid.UUID(admin["user_id"]))
    
    # Send invite email (background task)
    from email_service import send_invite_email
    background_tasks.add_task(
        send_invite_email,
        email=data.email,
        invite_token=invite_token,
        tenant_id=data.tenant_id
    )
    
    return {"status": "invited", "user_id": str(user_id)}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    data: UserUpdate,
    admin: dict = Depends(get_admin_user)
):
    """Update user role or status."""
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    updates = []
    values = []
    idx = 1
    
    if data.name is not None:
        updates.append(f"name = \")
        values.append(data.name)
        idx += 1
    if data.role is not None:
        updates.append(f"role = \")
        values.append(data.role)
        idx += 1
    if data.status is not None:
        updates.append(f"status = \")
        values.append(data.status)
        idx += 1
    
    if not updates:
        raise HTTPException(400, "No updates provided")
    
    values.append(uuid.UUID(user_id))
    
    async with db_pool.acquire() as conn:
        await conn.execute(
            f"UPDATE users SET {', '.join(updates)}, updated_at = NOW() WHERE id = \",
            *values
        )
    
    return {"status": "updated"}


@router.delete("/users/{user_id}")
async def remove_user(user_id: str, admin: dict = Depends(get_admin_user)):
    """Remove a user from the tenant."""
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET status = 'disabled', disabled_at = NOW() WHERE id = \",
            uuid.UUID(user_id)
        )
    
    return {"status": "removed"}


# ===========================================
# INVITE ACCEPTANCE
# ===========================================

@router.post("/accept-invite")
async def accept_invite(token: str, name: str, password: str):
    """Accept an invitation and set up account."""
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    import hashlib
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email FROM users WHERE invite_token = \ AND status = 'pending'",
            token
        )
        if not row:
            raise HTTPException(400, "Invalid or expired invite token")
        
        await conn.execute("""
            UPDATE users 
            SET name = \, password_hash = \, status = 'active', 
                joined_at = NOW(), invite_token = NULL
            WHERE id = \
        """, name, password_hash, row["id"])
    
    return {"status": "account_created", "email": row["email"]}
