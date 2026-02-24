"""
Chunked Upload Router - Large file uploads for 0711-Vault
Bypasses Cloudflare's 100MB limit by splitting files into 95MB chunks.

Integration with existing vault-api infrastructure.
"""
import os
import json
import uuid
import hashlib
import shutil
import aiofiles
import aiofiles.os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Set
from fastapi import APIRouter, HTTPException, Request, Depends, Header
from pydantic import BaseModel, Field

# ============================================================
# Configuration
# ============================================================

CHUNK_SIZE = 95 * 1024 * 1024  # 95MB - under Cloudflare's 100MB limit
SESSION_TTL = 3600  # 1 hour
TEMP_DIR = "/tmp/vault-chunked-uploads"

router = APIRouter(prefix="/vault/upload", tags=["Chunked Upload"])


# ============================================================
# Request/Response Models
# ============================================================

class InitUploadRequest(BaseModel):
    filename: str = Field(..., description="Original filename")
    file_size: int = Field(..., gt=0, description="Total file size in bytes")
    total_chunks: int = Field(..., gt=0, description="Number of chunks")
    mime_type: str = Field(default="application/octet-stream")
    encrypted_metadata: Optional[str] = Field(None, description="Encrypted metadata blob")
    item_type: str = Field(default="document", description="photo, video, or document")
    captured_at: Optional[datetime] = None


class InitUploadResponse(BaseModel):
    upload_session_id: str
    chunk_size: int
    expires_at: str
    total_chunks: int


class ChunkResponse(BaseModel):
    chunk_number: int
    received_bytes: int
    status: str
    total_received: int
    total_chunks: int


class CompleteUploadRequest(BaseModel):
    checksum: Optional[str] = Field(None, description="SHA256 checksum")


class CompleteUploadResponse(BaseModel):
    item_id: str
    storage_key: str
    status: str
    file_size: int
    checksum: str


class SessionStatusResponse(BaseModel):
    session_id: str
    filename: str
    file_size: int
    total_chunks: int
    received_chunks: int
    missing_chunks: list
    progress_percent: float
    expires_at: str


# ============================================================
# Session Management (Redis-backed)
# ============================================================

def _session_key(session_id: str) -> str:
    return f"chunked_upload:{session_id}"


def _chunk_dir(session_id: str) -> Path:
    return Path(TEMP_DIR) / session_id


def _chunk_path(session_id: str, chunk_number: int) -> Path:
    return _chunk_dir(session_id) / f"chunk_{chunk_number:05d}"


async def _save_session(redis_client, session_data: dict, ttl: int = SESSION_TTL):
    """Save session to Redis."""
    await redis_client.setex(
        _session_key(session_data["session_id"]),
        ttl,
        json.dumps(session_data)
    )


async def _get_session(redis_client, session_id: str) -> Optional[dict]:
    """Get session from Redis."""
    data = await redis_client.get(_session_key(session_id))
    if not data:
        return None
    
    if isinstance(data, bytes):
        data = data.decode()
    
    session = json.loads(data)
    
    # Check expiration
    expires_at = datetime.fromisoformat(session["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        await _cleanup_session(redis_client, session_id)
        return None
    
    return session


async def _cleanup_session(redis_client, session_id: str):
    """Clean up session data and temp files."""
    await redis_client.delete(_session_key(session_id))
    
    chunk_dir = _chunk_dir(session_id)
    if chunk_dir.exists():
        shutil.rmtree(str(chunk_dir))


# ============================================================
# Endpoints
# ============================================================

@router.post("/init", response_model=InitUploadResponse)
async def init_upload(
    request: InitUploadRequest,
    req: Request,
):
    """
    Initialize a chunked upload session.
    
    Client should:
    1. Call this endpoint with file metadata
    2. Split file into chunks of `chunk_size` bytes
    3. Upload each chunk with PUT /vault/upload/{session_id}/chunk/{n}
    4. Call POST /vault/upload/{session_id}/complete
    """
    # Get user from request state (set by dependency in main.py)
    user_id = req.state.user_id
    redis_client = req.app.state.redis_client
    
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    
    # Validate chunk calculation
    expected_chunks = (request.file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
    if request.total_chunks != expected_chunks:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid total_chunks: expected {expected_chunks} for {request.file_size} bytes"
        )
    
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=SESSION_TTL)
    
    session_data = {
        "session_id": session_id,
        "user_id": user_id,
        "filename": request.filename,
        "file_size": request.file_size,
        "total_chunks": request.total_chunks,
        "mime_type": request.mime_type,
        "encrypted_metadata": request.encrypted_metadata,
        "item_type": request.item_type,
        "captured_at": request.captured_at.isoformat() if request.captured_at else None,
        "chunk_size": CHUNK_SIZE,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "received_chunks": [],
    }
    
    # Create chunk directory
    chunk_dir = _chunk_dir(session_id)
    await aiofiles.os.makedirs(chunk_dir, exist_ok=True)
    
    # Save session
    await _save_session(redis_client, session_data)
    
    return InitUploadResponse(
        upload_session_id=session_id,
        chunk_size=CHUNK_SIZE,
        expires_at=expires_at.isoformat(),
        total_chunks=request.total_chunks,
    )


@router.put("/{session_id}/chunk/{chunk_number}", response_model=ChunkResponse)
async def upload_chunk(
    session_id: str,
    chunk_number: int,
    request: Request,
):
    """
    Upload a single chunk.
    
    - Request body: raw binary data
    - Content-Type: application/octet-stream
    - chunk_number: 0-indexed
    """
    user_id = request.state.user_id
    redis_client = request.app.state.redis_client
    
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    
    session = await _get_session(redis_client, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    
    if session["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Session belongs to another user")
    
    if chunk_number < 0 or chunk_number >= session["total_chunks"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid chunk number {chunk_number}. Expected 0-{session['total_chunks'] - 1}"
        )
    
    # Read body
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty chunk data")
    
    # Validate size (last chunk may be smaller)
    is_last = chunk_number == session["total_chunks"] - 1
    if not is_last and len(body) != CHUNK_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid chunk size {len(body)}. Expected {CHUNK_SIZE}"
        )
    
    # Write chunk
    chunk_path = _chunk_path(session_id, chunk_number)
    async with aiofiles.open(chunk_path, "wb") as f:
        await f.write(body)
    
    # Update session
    received = set(session["received_chunks"])
    received.add(chunk_number)
    session["received_chunks"] = list(received)
    await _save_session(redis_client, session)
    
    return ChunkResponse(
        chunk_number=chunk_number,
        received_bytes=len(body),
        status="received",
        total_received=len(received),
        total_chunks=session["total_chunks"],
    )


@router.post("/{session_id}/complete", response_model=CompleteUploadResponse)
async def complete_upload(
    session_id: str,
    request_body: CompleteUploadRequest,
    request: Request,
):
    """
    Complete the upload - reassemble chunks and store.
    
    All chunks must be uploaded before calling this.
    """
    user_id = request.state.user_id
    redis_client = request.app.state.redis_client
    db_pool = request.app.state.db_pool
    
    if not redis_client or not db_pool:
        raise HTTPException(status_code=503, detail="Service unavailable")
    
    session = await _get_session(redis_client, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    
    if session["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Session belongs to another user")
    
    # Verify all chunks received
    expected = set(range(session["total_chunks"]))
    received = set(session["received_chunks"])
    if received != expected:
        missing = sorted(expected - received)
        raise HTTPException(status_code=400, detail=f"Missing chunks: {missing}")
    
    # Reassemble file
    chunk_dir = _chunk_dir(session_id)
    assembled_path = chunk_dir / "assembled"
    
    hasher = hashlib.sha256()
    total_bytes = 0
    
    async with aiofiles.open(assembled_path, "wb") as out_file:
        for i in range(session["total_chunks"]):
            chunk_path = _chunk_path(session_id, i)
            async with aiofiles.open(chunk_path, "rb") as chunk_file:
                chunk_data = await chunk_file.read()
                hasher.update(chunk_data)
                total_bytes += len(chunk_data)
                await out_file.write(chunk_data)
    
    # Verify checksum
    computed_checksum = hasher.hexdigest()
    if request_body.checksum and request_body.checksum.lower() != computed_checksum:
        await _cleanup_session(redis_client, session_id)
        raise HTTPException(
            status_code=400,
            detail=f"Checksum mismatch. Expected: {request_body.checksum}, Got: {computed_checksum}"
        )
    
    # Verify size
    if total_bytes != session["file_size"]:
        await _cleanup_session(redis_client, session_id)
        raise HTTPException(
            status_code=400,
            detail=f"Size mismatch. Expected: {session['file_size']}, Got: {total_bytes}"
        )
    
    # Upload to storage service
    from storage import upload_bytes
    
    async with aiofiles.open(assembled_path, "rb") as f:
        content = await f.read()
    
    storage_key = await upload_bytes(
        user_id=user_id,
        filename=session["filename"],
        content=content,
        content_type=session["mime_type"]
    )
    
    # Create database record
    item_id = None
    captured_at = None
    if session.get("captured_at"):
        captured_at = datetime.fromisoformat(session["captured_at"])
    
    async with db_pool.acquire() as conn:
        item_id = await conn.fetchval("""
            INSERT INTO vault_items (
                user_id, item_type, encrypted_metadata, storage_key, 
                file_size, mime_type, captured_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
        """, 
            user_id, 
            session["item_type"],
            session.get("encrypted_metadata"),
            storage_key,
            total_bytes,
            session["mime_type"],
            captured_at
        )
        
        # Add to processing queue
        await conn.execute("""
            INSERT INTO processing_queue (item_id, user_id, task_type, priority)
            VALUES ($1, $2, 'full_process', 5)
        """, item_id, user_id)
    
    # Cleanup
    await _cleanup_session(redis_client, session_id)
    
    return CompleteUploadResponse(
        item_id=str(item_id),
        storage_key=storage_key,
        status="complete",
        file_size=total_bytes,
        checksum=computed_checksum,
    )


@router.get("/{session_id}/status", response_model=SessionStatusResponse)
async def get_upload_status(
    session_id: str,
    request: Request,
):
    """Get current status of an upload session."""
    user_id = request.state.user_id
    redis_client = request.app.state.redis_client
    
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    
    session = await _get_session(redis_client, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    
    if session["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Session belongs to another user")
    
    received = set(session["received_chunks"])
    expected = set(range(session["total_chunks"]))
    
    return SessionStatusResponse(
        session_id=session["session_id"],
        filename=session["filename"],
        file_size=session["file_size"],
        total_chunks=session["total_chunks"],
        received_chunks=len(received),
        missing_chunks=sorted(expected - received),
        progress_percent=round(100 * len(received) / session["total_chunks"], 1),
        expires_at=session["expires_at"],
    )


@router.delete("/{session_id}")
async def cancel_upload(
    session_id: str,
    request: Request,
):
    """Cancel an upload session and clean up."""
    user_id = request.state.user_id
    redis_client = request.app.state.redis_client
    
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    
    session = await _get_session(redis_client, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    
    if session["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Session belongs to another user")
    
    await _cleanup_session(redis_client, session_id)
    
    return {"status": "cancelled", "session_id": session_id}


# ============================================================
# Auth Dependency Middleware
# ============================================================

async def chunked_upload_auth(request: Request, authorization: str = Header(None)):
    """
    Validate auth and set user_id on request.state.
    To be used as a dependency or middleware.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    
    token = authorization.split(" ")[1]
    redis_client = request.app.state.redis_client
    
    if redis_client:
        user_id = await redis_client.get(f"token:{token}")
        if user_id:
            if isinstance(user_id, bytes):
                user_id = user_id.decode()
            request.state.user_id = user_id
            return user_id
    
    raise HTTPException(status_code=401, detail="Invalid token")


# Apply auth to all routes
for route in router.routes:
    if hasattr(route, "dependencies"):
        route.dependencies.append(Depends(chunked_upload_auth))
    else:
        route.dependencies = [Depends(chunked_upload_auth)]
