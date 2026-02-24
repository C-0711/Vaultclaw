"""
User Settings API for 0711 Vault
Store and retrieve user preferences
"""

import json
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])

# Database pool (set from main.py)
_db_pool = None
_redis_client = None


def init_settings(db_pool, redis_client):
    """Initialize settings with database connections."""
    global _db_pool, _redis_client
    _db_pool = db_pool
    _redis_client = redis_client


class UserSettings(BaseModel):
    llm_provider: str = "local"
    llm_model: str = "llama3.3:70b"
    vision_model: str = "llama4:latest"
    embedding_provider: str = "local"
    embedding_model: str = "bge-m3:latest"
    vector_backend: str = "sqlite-vec"
    hybrid_search: bool = True
    temporal_decay: bool = True
    query_expansion: bool = True
    processing_mode: str = "hybrid"
    dark_mode: bool = True
    notifications: bool = True
    encryption_at_rest: bool = True


async def get_current_user(authorization: str = Header(None)):
    """Validate token and return user_id."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    
    token = authorization.split(" ")[1]
    
    if _redis_client:
        user_id = await _redis_client.get(f"token:{token}")
        if user_id:
            return user_id.decode()
    
    raise HTTPException(status_code=401, detail="Invalid token")


async def ensure_table():
    """Create user_settings table if not exists."""
    if not _db_pool:
        return
    async with _db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY,
                settings JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)


@router.get("")
async def get_settings(user_id: str = Depends(get_current_user)):
    """Get user settings."""
    if not _db_pool:
        raise HTTPException(503, "Database unavailable")
    
    await ensure_table()
    
    async with _db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT settings FROM user_settings WHERE user_id = $1",
            user_id
        )
        
        if not row:
            # Return defaults
            return UserSettings().dict()
        
        return json.loads(row['settings'])


@router.put("")
async def update_settings(settings: UserSettings, user_id: str = Depends(get_current_user)):
    """Update user settings."""
    if not _db_pool:
        raise HTTPException(503, "Database unavailable")
    
    await ensure_table()
    
    settings_json = json.dumps(settings.dict())
    
    async with _db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO user_settings (user_id, settings)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET 
                settings = $2,
                updated_at = NOW()
        """, user_id, settings_json)
    
    return {"updated": True}


@router.patch("")
async def patch_settings(updates: dict, user_id: str = Depends(get_current_user)):
    """Partially update user settings."""
    if not _db_pool:
        raise HTTPException(503, "Database unavailable")
    
    await ensure_table()
    
    async with _db_pool.acquire() as conn:
        # Get current settings
        row = await conn.fetchrow(
            "SELECT settings FROM user_settings WHERE user_id = $1",
            user_id
        )
        
        current = json.loads(row['settings']) if row else UserSettings().dict()
        
        # Merge updates
        current.update(updates)
        
        # Save
        await conn.execute("""
            INSERT INTO user_settings (user_id, settings)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET 
                settings = $2,
                updated_at = NOW()
        """, user_id, json.dumps(current))
    
    return current
