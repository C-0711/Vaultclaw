"""
Calendar API for 0711 Vault
Encrypted calendar events with PostgreSQL persistence
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, Header
from pydantic import BaseModel
import asyncpg

router = APIRouter(prefix="/calendar", tags=["calendar"])

# Database pool (will be set from main.py)
_db_pool = None
_redis_client = None


def init_calendar(db_pool, redis_client):
    """Initialize calendar with database connections."""
    global _db_pool, _redis_client
    _db_pool = db_pool
    _redis_client = redis_client


class EventCreate(BaseModel):
    title: str
    date: str  # ISO string
    end_date: Optional[str] = None
    all_day: bool = False
    color: str = "amber"  # amber, green, purple, red, blue
    description: Optional[str] = None
    location: Optional[str] = None
    encrypted_data: Optional[str] = None  # E2E encrypted extra data
    recurring: Optional[str] = None  # daily, weekly, monthly, yearly


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
    
    if _redis_client:
        user_id = await _redis_client.get(f"token:{token}")
        if user_id:
            return user_id.decode()
    
    raise HTTPException(status_code=401, detail="Invalid token")


async def ensure_table():
    """Create calendar_events table if not exists."""
    if not _db_pool:
        return
    async with _db_pool.acquire() as conn:
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
    start: Optional[str] = Query(None, description="Start date ISO string"),
    end: Optional[str] = Query(None, description="End date ISO string"),
    user_id: str = Depends(get_current_user)
):
    """List all events for the current user, optionally filtered by date range."""
    if not _db_pool:
        raise HTTPException(503, "Database unavailable")
    
    await ensure_table()
    
    async with _db_pool.acquire() as conn:
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
        
        return {
            "events": [dict(e) for e in events],
            "total": len(events)
        }


@router.get("/events/{event_id}")
async def get_event(event_id: str, user_id: str = Depends(get_current_user)):
    """Get a specific event."""
    if not _db_pool:
        raise HTTPException(503, "Database unavailable")
    
    await ensure_table()
    
    async with _db_pool.acquire() as conn:
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
    if not _db_pool:
        raise HTTPException(503, "Database unavailable")
    
    async with _db_pool.acquire() as conn:
        # Ensure table exists
        await ensure_table()
        
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
    if not _db_pool:
        raise HTTPException(503, "Database unavailable")
    
    # Build dynamic update
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
    
    updates.append(f"updated_at = NOW()")
    
    async with _db_pool.acquire() as conn:
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
    if not _db_pool:
        raise HTTPException(503, "Database unavailable")
    
    async with _db_pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM calendar_events WHERE id = $1 AND user_id = $2
        """, uuid.UUID(event_id), user_id)
        
        if result == "DELETE 0":
            raise HTTPException(404, "Event not found")
        
        return {"deleted": True}


@router.get("/today")
async def get_today_events(user_id: str = Depends(get_current_user)):
    """Get all events for today."""
    if not _db_pool:
        raise HTTPException(503, "Database unavailable")
    
    await ensure_table()
    today = datetime.utcnow().date()
    tomorrow = today + timedelta(days=1)
    
    async with _db_pool.acquire() as conn:
        events = await conn.fetch("""
            SELECT id, title, event_date as date, end_date, all_day, color,
                   description, location
            FROM calendar_events
            WHERE user_id = $1 AND event_date >= $2 AND event_date < $3
            ORDER BY event_date ASC
        """, user_id, today.isoformat(), tomorrow.isoformat())
        
        return {
            "events": [dict(e) for e in events],
            "date": today.isoformat()
        }


@router.get("/upcoming")
async def get_upcoming_events(
    days: int = Query(7, ge=1, le=90),
    user_id: str = Depends(get_current_user)
):
    """Get upcoming events for the next N days."""
    if not _db_pool:
        raise HTTPException(503, "Database unavailable")
    
    await ensure_table()
    now = datetime.utcnow()
    end = now + timedelta(days=days)
    
    async with _db_pool.acquire() as conn:
        events = await conn.fetch("""
            SELECT id, title, event_date as date, end_date, all_day, color,
                   description, location
            FROM calendar_events
            WHERE user_id = $1 AND event_date >= $2 AND event_date <= $3
            ORDER BY event_date ASC
            LIMIT 20
        """, user_id, now.isoformat(), end.isoformat())
        
        return {
            "events": [dict(e) for e in events],
            "days": days
        }


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
