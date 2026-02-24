"""
Calendar API for 0711 Vault
Encrypted calendar events with optional sync
"""

import os
import uuid
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

router = APIRouter(prefix="/calendar", tags=["calendar"])

# In-memory storage (use database in production)
events_store = {}


class EventCreate(BaseModel):
    title: str
    date: str  # ISO string
    end_date: Optional[str] = None
    all_day: bool = False
    color: str = "blue"
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


class Event(BaseModel):
    id: str
    user_id: str
    title: str
    date: str
    end_date: Optional[str] = None
    all_day: bool = False
    color: str = "blue"
    description: Optional[str] = None
    location: Optional[str] = None
    encrypted_data: Optional[str] = None
    recurring: Optional[str] = None
    created_at: str
    updated_at: str


def get_user_events(user_id: str) -> dict:
    """Get or create user's event store."""
    if user_id not in events_store:
        events_store[user_id] = {}
    return events_store[user_id]


# Placeholder for auth dependency
async def get_current_user():
    return {"id": "demo-user", "email": "demo@example.com"}


@router.get("/events")
async def list_events(
    start: Optional[str] = Query(None, description="Start date ISO string"),
    end: Optional[str] = Query(None, description="End date ISO string"),
    user=Depends(get_current_user)
):
    """List all events for the current user, optionally filtered by date range."""
    user_events = get_user_events(user["id"])
    
    events = list(user_events.values())
    
    # Filter by date range
    if start:
        start_date = datetime.fromisoformat(start.replace('Z', '+00:00'))
        events = [e for e in events if datetime.fromisoformat(e["date"].replace('Z', '+00:00')) >= start_date]
    
    if end:
        end_date = datetime.fromisoformat(end.replace('Z', '+00:00'))
        events = [e for e in events if datetime.fromisoformat(e["date"].replace('Z', '+00:00')) <= end_date]
    
    # Sort by date
    events.sort(key=lambda e: e["date"])
    
    return {"events": events, "total": len(events)}


@router.get("/events/{event_id}")
async def get_event(event_id: str, user=Depends(get_current_user)):
    """Get a specific event."""
    user_events = get_user_events(user["id"])
    
    if event_id not in user_events:
        raise HTTPException(404, "Event not found")
    
    return user_events[event_id]


@router.post("/events")
async def create_event(event: EventCreate, user=Depends(get_current_user)):
    """Create a new event."""
    user_events = get_user_events(user["id"])
    
    event_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    
    new_event = {
        "id": event_id,
        "user_id": user["id"],
        "title": event.title,
        "date": event.date,
        "end_date": event.end_date,
        "all_day": event.all_day,
        "color": event.color,
        "description": event.description,
        "location": event.location,
        "encrypted_data": event.encrypted_data,
        "recurring": event.recurring,
        "created_at": now,
        "updated_at": now
    }
    
    user_events[event_id] = new_event
    
    return new_event


@router.put("/events/{event_id}")
async def update_event(
    event_id: str, 
    event: EventUpdate, 
    user=Depends(get_current_user)
):
    """Update an existing event."""
    user_events = get_user_events(user["id"])
    
    if event_id not in user_events:
        raise HTTPException(404, "Event not found")
    
    existing = user_events[event_id]
    
    # Update fields
    update_data = event.dict(exclude_unset=True)
    for key, value in update_data.items():
        if value is not None:
            existing[key] = value
    
    existing["updated_at"] = datetime.utcnow().isoformat() + "Z"
    
    return existing


@router.delete("/events/{event_id}")
async def delete_event(event_id: str, user=Depends(get_current_user)):
    """Delete an event."""
    user_events = get_user_events(user["id"])
    
    if event_id not in user_events:
        raise HTTPException(404, "Event not found")
    
    del user_events[event_id]
    
    return {"deleted": True}


@router.get("/sync/status")
async def get_sync_status(user=Depends(get_current_user)):
    """Get calendar sync status."""
    return {
        "synced_calendars": [],
        "available_providers": [
            {"id": "apple", "name": "Apple Kalender", "available": True},
            {"id": "google", "name": "Google Calendar", "available": True},
            {"id": "outlook", "name": "Microsoft Outlook", "available": True},
            {"id": "caldav", "name": "CalDAV", "available": True}
        ],
        "last_sync": None
    }


@router.post("/sync/{provider}")
async def start_sync(provider: str, user=Depends(get_current_user)):
    """Start syncing with an external calendar provider."""
    providers = ["apple", "google", "outlook", "caldav"]
    
    if provider not in providers:
        raise HTTPException(400, f"Unknown provider. Available: {providers}")
    
    # In production, this would initiate OAuth flow or CalDAV setup
    return {
        "status": "pending",
        "provider": provider,
        "message": f"Sync mit {provider} wird eingerichtet...",
        "next_step": "authorize"  # OAuth authorization URL would go here
    }


@router.get("/upcoming")
async def get_upcoming_events(
    days: int = Query(7, ge=1, le=90),
    user=Depends(get_current_user)
):
    """Get upcoming events for the next N days."""
    user_events = get_user_events(user["id"])
    
    now = datetime.utcnow()
    end = datetime(now.year, now.month, now.day + days)
    
    upcoming = []
    for event in user_events.values():
        event_date = datetime.fromisoformat(event["date"].replace('Z', '+00:00').replace('+00:00', ''))
        if now <= event_date <= end:
            upcoming.append(event)
    
    upcoming.sort(key=lambda e: e["date"])
    
    return {"events": upcoming[:20], "days": days}


@router.get("/today")
async def get_today_events(user=Depends(get_current_user)):
    """Get all events for today."""
    user_events = get_user_events(user["id"])
    
    today = datetime.utcnow().date()
    
    events = []
    for event in user_events.values():
        event_date = datetime.fromisoformat(event["date"].replace('Z', '+00:00').replace('+00:00', '')).date()
        if event_date == today:
            events.append(event)
    
    events.sort(key=lambda e: e["date"])
    
    return {"events": events, "date": today.isoformat()}
