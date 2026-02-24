"""
iMessage Import Service for 0711 Vault
Imports photos and attachments from macOS iMessage database
"""

import os
import sqlite3
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel

# iMessage database path on macOS
IMESSAGE_DB = os.path.expanduser("~/Library/Messages/chat.db")
ATTACHMENTS_PATH = os.path.expanduser("~/Library/Messages/Attachments")

router = APIRouter(prefix="/import/imessage", tags=["import"])


@dataclass
class IMMessageAttachment:
    id: int
    filename: str
    mime_type: str
    path: str
    size: int
    created_at: datetime
    sender: str
    is_from_me: bool


class ImportRequest(BaseModel):
    since_days: int = 30
    include_sent: bool = True
    include_received: bool = True
    contacts: Optional[List[str]] = None  # Filter by contact


class ImportStatus(BaseModel):
    status: str
    total: int
    imported: int
    failed: int
    current_file: Optional[str] = None


# In-memory status tracking (use Redis in production)
import_status = {}


def get_imessage_db():
    """Get connection to iMessage database."""
    if not os.path.exists(IMESSAGE_DB):
        raise HTTPException(
            status_code=400,
            detail="iMessage database not found. Make sure you're on macOS with Full Disk Access."
        )
    
    # Copy database to avoid locking issues
    temp_db = "/tmp/chat_copy.db"
    shutil.copy2(IMESSAGE_DB, temp_db)
    
    conn = sqlite3.connect(temp_db)
    conn.row_factory = sqlite3.Row
    return conn


def get_attachments(
    since_days: int = 30,
    include_sent: bool = True,
    include_received: bool = True,
    contacts: Optional[List[str]] = None
) -> List[IMMessageAttachment]:
    """Query iMessage attachments from database."""
    
    conn = get_imessage_db()
    cursor = conn.cursor()
    
    # Calculate timestamp (Apple uses nanoseconds since 2001-01-01)
    from_date = datetime.now().timestamp() - (since_days * 86400)
    apple_epoch = datetime(2001, 1, 1).timestamp()
    from_timestamp = (from_date - apple_epoch) * 1_000_000_000
    
    # Build query
    direction_filter = []
    if include_sent:
        direction_filter.append("m.is_from_me = 1")
    if include_received:
        direction_filter.append("m.is_from_me = 0")
    
    if not direction_filter:
        return []
    
    direction_clause = f"({' OR '.join(direction_filter)})"
    
    query = f"""
        SELECT 
            a.ROWID as id,
            a.filename,
            a.mime_type,
            a.total_bytes as size,
            a.created_date,
            m.is_from_me,
            h.id as sender
        FROM attachment a
        JOIN message_attachment_join maj ON a.ROWID = maj.attachment_id
        JOIN message m ON maj.message_id = m.ROWID
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE a.created_date > ?
        AND {direction_clause}
        AND a.mime_type LIKE 'image/%' OR a.mime_type LIKE 'video/%'
        ORDER BY a.created_date DESC
    """
    
    cursor.execute(query, (from_timestamp,))
    rows = cursor.fetchall()
    
    attachments = []
    for row in rows:
        # Filter by contacts if specified
        if contacts and row['sender'] not in contacts:
            continue
        
        # Convert Apple timestamp to datetime
        if row['created_date']:
            created_at = datetime(2001, 1, 1) + timedelta(
                seconds=row['created_date'] / 1_000_000_000
            )
        else:
            created_at = datetime.now()
        
        # Resolve attachment path
        filename = row['filename']
        if filename and filename.startswith('~'):
            filename = os.path.expanduser(filename)
        
        attachments.append(IMMessageAttachment(
            id=row['id'],
            filename=os.path.basename(filename) if filename else f"attachment_{row['id']}",
            mime_type=row['mime_type'] or 'application/octet-stream',
            path=filename,
            size=row['size'] or 0,
            created_at=created_at,
            sender=row['sender'] or 'unknown',
            is_from_me=bool(row['is_from_me'])
        ))
    
    conn.close()
    return attachments


@router.get("/status")
async def check_imessage_access():
    """Check if iMessage database is accessible."""
    try:
        if not os.path.exists(IMESSAGE_DB):
            return {
                "accessible": False,
                "error": "iMessage database not found",
                "hint": "Make sure you're on macOS and have granted Full Disk Access to Terminal/IDE"
            }
        
        conn = get_imessage_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM attachment WHERE mime_type LIKE 'image/%'")
        count = cursor.fetchone()[0]
        conn.close()
        
        return {
            "accessible": True,
            "total_images": count,
            "database_path": IMESSAGE_DB
        }
    except Exception as e:
        return {
            "accessible": False,
            "error": str(e)
        }


@router.get("/preview")
async def preview_import(
    since_days: int = 30,
    include_sent: bool = True,
    include_received: bool = True
):
    """Preview what would be imported without actually importing."""
    try:
        attachments = get_attachments(since_days, include_sent, include_received)
        
        # Group by type
        by_type = {}
        total_size = 0
        for a in attachments:
            mime = a.mime_type.split('/')[0] if a.mime_type else 'unknown'
            by_type[mime] = by_type.get(mime, 0) + 1
            total_size += a.size
        
        return {
            "total_attachments": len(attachments),
            "by_type": by_type,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "date_range": {
                "from": attachments[-1].created_at.isoformat() if attachments else None,
                "to": attachments[0].created_at.isoformat() if attachments else None
            },
            "sample": [
                {
                    "filename": a.filename,
                    "type": a.mime_type,
                    "size_kb": round(a.size / 1024, 1),
                    "from": "me" if a.is_from_me else a.sender,
                    "date": a.created_at.isoformat()
                }
                for a in attachments[:10]
            ]
        }
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/contacts")
async def list_contacts():
    """List contacts with attachments."""
    try:
        conn = get_imessage_db()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                h.id as contact,
                COUNT(a.ROWID) as attachment_count
            FROM handle h
            JOIN message m ON h.ROWID = m.handle_id
            JOIN message_attachment_join maj ON m.ROWID = maj.message_id
            JOIN attachment a ON maj.attachment_id = a.ROWID
            WHERE a.mime_type LIKE 'image/%' OR a.mime_type LIKE 'video/%'
            GROUP BY h.id
            ORDER BY attachment_count DESC
            LIMIT 50
        """)
        
        contacts = [
            {"contact": row['contact'], "attachments": row['attachment_count']}
            for row in cursor.fetchall()
        ]
        
        conn.close()
        return {"contacts": contacts}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/start")
async def start_import(
    request: ImportRequest,
    background_tasks: BackgroundTasks,
    # user=Depends(get_current_user)  # Add auth in production
):
    """Start background import of iMessage attachments."""
    import uuid
    
    job_id = str(uuid.uuid4())
    import_status[job_id] = ImportStatus(
        status="starting",
        total=0,
        imported=0,
        failed=0
    )
    
    # Start background task
    background_tasks.add_task(
        run_import,
        job_id,
        request.since_days,
        request.include_sent,
        request.include_received,
        request.contacts
    )
    
    return {"job_id": job_id, "status": "started"}


@router.get("/job/{job_id}")
async def get_import_status(job_id: str):
    """Get status of an import job."""
    if job_id not in import_status:
        raise HTTPException(404, "Job not found")
    
    return import_status[job_id]


async def run_import(
    job_id: str,
    since_days: int,
    include_sent: bool,
    include_received: bool,
    contacts: Optional[List[str]]
):
    """Background task to import attachments."""
    from datetime import timedelta
    
    try:
        attachments = get_attachments(since_days, include_sent, include_received, contacts)
        import_status[job_id].total = len(attachments)
        import_status[job_id].status = "importing"
        
        for attachment in attachments:
            try:
                import_status[job_id].current_file = attachment.filename
                
                # Check if file exists
                if not attachment.path or not os.path.exists(attachment.path):
                    import_status[job_id].failed += 1
                    continue
                
                # TODO: Upload to vault
                # 1. Encrypt file
                # 2. Upload to MinIO
                # 3. Create vault item
                
                import_status[job_id].imported += 1
                
            except Exception as e:
                print(f"Failed to import {attachment.filename}: {e}")
                import_status[job_id].failed += 1
        
        import_status[job_id].status = "completed"
        import_status[job_id].current_file = None
        
    except Exception as e:
        import_status[job_id].status = f"error: {str(e)}"
