"""
Calendar API for 0711 Vault (Tenant Version)
Works independently without init_calendar
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, Header
from pydantic import BaseModel
import redis.asyncio as aioredis
import asyncpg
import os

router = APIRouter(prefix="/calendar", tags=["calendar"])

# Get config from environment
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vault:vault@localhost:5432/vault")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Lazy-initialized connections
_db_pool = None
_redis = None


async def get_db():
    """Get or create database pool."""
    global _db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        await ensure_table()
    return _db_pool


async def get_redis():
    """Get or create redis client."""
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(REDIS_URL)
    return _redis


class EventCreate(BaseModel):
    title: str
    date: str  # ISO string
    end_date: Optional[str] = None
    all_day: bool = False
    color: str = "amber"
    description: Optional[str] = None
    location: Optional[str] = None
    encrypted_data: Optional[str] = None
    recurring: Optional[str] = None


class EventUpdate(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None
    end_date: Optional[str] = None
    all_day: Optional[bool] = None
    color: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    encrypted_data: Optional[str] = None
    recurring: Optional[str] = None


async def get_current_user(authorization: str = Header(None)):
    """Validate token and return user_id."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    
    token = authorization.split(" ")[1]
    redis = await get_redis()
    
    user_id = await redis.get(f"token:{token}")
    if user_id:
        return user_id.decode()
    
    raise HTTPException(status_code=401, detail="Invalid token")


async def ensure_table():
    """Create calendar_events table if not exists."""
    db = await get_db()
    async with db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                event_date TIMESTAMPTZ NOT NULL,
                end_date TIMESTAMPTZ,
                all_day BOOLEAN DEFAULT FALSE,
                color TEXT DEFAULT 'amber',
                description TEXT,
                location TEXT,
                encrypted_data TEXT,
                recurring TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_calendar_user ON calendar_events(user_id);
            CREATE INDEX IF NOT EXISTS idx_calendar_date ON calendar_events(event_date);
        """)


@router.get("/events")
async def list_events(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    user_id: str = Depends(get_current_user)
):
    """List all events for the current user."""
    db = await get_db()
    
    async with db.acquire() as conn:
        if start and end:
            events = await conn.fetch("""
                SELECT id, title, event_date as date, end_date, all_day, color,
                       description, location, encrypted_data, recurring,
                       created_at, updated_at
                FROM calendar_events
                WHERE user_id = $1 AND event_date >= $2 AND event_date <= $3
                ORDER BY event_date ASC
            """, user_id, start, end)
        else:
            events = await conn.fetch("""
                SELECT id, title, event_date as date, end_date, all_day, color,
                       description, location, encrypted_data, recurring,
                       created_at, updated_at
                FROM calendar_events
                WHERE user_id = $1
                ORDER BY event_date ASC
                LIMIT 100
            """, user_id)
        
        return {"events": [dict(e) for e in events], "total": len(events)}


@router.get("/events/{event_id}")
async def get_event(event_id: str, user_id: str = Depends(get_current_user)):
    """Get a specific event."""
    db = await get_db()
    
    async with db.acquire() as conn:
        event = await conn.fetchrow("""
            SELECT id, title, event_date as date, end_date, all_day, color,
                   description, location, encrypted_data, recurring,
                   created_at, updated_at
            FROM calendar_events
            WHERE id = $1 AND user_id = $2
        """, uuid.UUID(event_id), user_id)
        
        if not event:
            raise HTTPException(404, "Event not found")
        
        return dict(event)


@router.post("/events")
async def create_event(event: EventCreate, user_id: str = Depends(get_current_user)):
    """Create a new event."""
    db = await get_db()
    
    async with db.acquire() as conn:
        event_id = await conn.fetchval("""
            INSERT INTO calendar_events (
                user_id, title, event_date, end_date, all_day, color,
                description, location, encrypted_data, recurring
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id
        """, user_id, event.title, event.date, event.end_date, event.all_day,
             event.color, event.description, event.location, 
             event.encrypted_data, event.recurring)
        
        return {
            "id": str(event_id),
            "title": event.title,
            "date": event.date,
            "color": event.color
        }


@router.put("/events/{event_id}")
async def update_event(
    event_id: str, 
    event: EventUpdate, 
    user_id: str = Depends(get_current_user)
):
    """Update an existing event."""
    db = await get_db()
    
    updates = []
    params = [uuid.UUID(event_id), user_id]
    idx = 3
    
    for field, value in event.dict(exclude_unset=True).items():
        if value is not None:
            db_field = "event_date" if field == "date" else field
            updates.append(f"{db_field} = ${idx}")
            params.append(value)
            idx += 1
    
    if not updates:
        raise HTTPException(400, "No updates provided")
    
    updates.append("updated_at = NOW()")
    
    async with db.acquire() as conn:
        result = await conn.execute(f"""
            UPDATE calendar_events
            SET {', '.join(updates)}
            WHERE id = $1 AND user_id = $2
        """, *params)
        
        if result == "UPDATE 0":
            raise HTTPException(404, "Event not found")
        
        return {"updated": True}


@router.delete("/events/{event_id}")
async def delete_event(event_id: str, user_id: str = Depends(get_current_user)):
    """Delete an event."""
    db = await get_db()
    
    async with db.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM calendar_events WHERE id = $1 AND user_id = $2
        """, uuid.UUID(event_id), user_id)
        
        if result == "DELETE 0":
            raise HTTPException(404, "Event not found")
        
        return {"deleted": True}


@router.get("/today")
async def get_today_events(user_id: str = Depends(get_current_user)):
    """Get all events for today."""
    db = await get_db()
    
    today = datetime.utcnow().date()
    tomorrow = today + timedelta(days=1)
    
    async with db.acquire() as conn:
        events = await conn.fetch("""
            SELECT id, title, event_date as date, end_date, all_day, color,
                   description, location
            FROM calendar_events
            WHERE user_id = $1 AND event_date >= $2 AND event_date < $3
            ORDER BY event_date ASC
        """, user_id, today.isoformat(), tomorrow.isoformat())
        
        return {"events": [dict(e) for e in events], "date": today.isoformat()}


@router.get("/upcoming")
async def get_upcoming_events(
    days: int = Query(7, ge=1, le=90),
    user_id: str = Depends(get_current_user)
):
    """Get upcoming events for the next N days."""
    db = await get_db()
    
    now = datetime.utcnow()
    end = now + timedelta(days=days)
    
    async with db.acquire() as conn:
        events = await conn.fetch("""
            SELECT id, title, event_date as date, end_date, all_day, color,
                   description, location
            FROM calendar_events
            WHERE user_id = $1 AND event_date >= $2 AND event_date <= $3
            ORDER BY event_date ASC
            LIMIT 20
        """, user_id, now.isoformat(), end.isoformat())
        
        return {"events": [dict(e) for e in events], "days": days}


@router.get("/sync/status")
async def get_sync_status(user_id: str = Depends(get_current_user)):
    """Get calendar sync status."""
    return {
        "synced_calendars": [],
        "available_providers": [
            {"id": "apple", "name": "Apple Calendar", "available": True},
            {"id": "google", "name": "Google Calendar", "available": True},
            {"id": "outlook", "name": "Microsoft Outlook", "available": True},
            {"id": "caldav", "name": "CalDAV", "available": True}
        ],
        "last_sync": None
    }
