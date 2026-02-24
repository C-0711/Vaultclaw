"""
E2E Encrypted Messaging routes
"""

from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid

from database import get_db, get_redis

router = APIRouter()


# ===========================================
# SCHEMAS
# ===========================================

class CreateThreadRequest(BaseModel):
    encrypted_metadata: str  # Encrypted thread name, etc.
    participant_ids: List[str]
    encrypted_keys: dict  # {user_id: encrypted_thread_key}


class SendMessageRequest(BaseModel):
    encrypted_content: str
    message_type: str = "text"
    attachment_ids: Optional[List[str]] = None


class MessageResponse(BaseModel):
    id: str
    thread_id: str
    sender_id: str
    encrypted_content: str
    message_type: str
    attachment_ids: Optional[List[str]]
    created_at: datetime


class ThreadResponse(BaseModel):
    id: str
    encrypted_metadata: str
    thread_type: str
    created_at: datetime
    last_message_at: Optional[datetime]


# ===========================================
# ENDPOINTS
# ===========================================

@router.post("/threads", response_model=ThreadResponse)
async def create_thread(request: CreateThreadRequest, db=Depends(get_db)):
    """
    Create a new message thread.
    
    E2E Encryption Flow:
    1. Creator generates thread key
    2. Creator encrypts thread key for each participant with their public key
    3. Server stores encrypted keys per participant
    4. Only participants can decrypt thread key
    """
    thread_id = str(uuid.uuid4())
    
    # Create thread
    await db.execute("""
        INSERT INTO message_threads (id, encrypted_metadata, thread_type)
        VALUES (:id, :encrypted_metadata, :thread_type)
    """, {
        "id": thread_id,
        "encrypted_metadata": request.encrypted_metadata,
        "thread_type": "group" if len(request.participant_ids) > 2 else "direct"
    })
    
    # Add participants with their encrypted keys
    for user_id in request.participant_ids:
        encrypted_key = request.encrypted_keys.get(user_id)
        await db.execute("""
            INSERT INTO thread_participants (thread_id, user_id, encrypted_thread_key)
            VALUES (:thread_id, :user_id, :encrypted_key)
        """, {
            "thread_id": thread_id,
            "user_id": user_id,
            "encrypted_key": encrypted_key
        })
    
    return ThreadResponse(
        id=thread_id,
        encrypted_metadata=request.encrypted_metadata,
        thread_type="group" if len(request.participant_ids) > 2 else "direct",
        created_at=datetime.utcnow(),
        last_message_at=None
    )


@router.get("/threads", response_model=List[ThreadResponse])
async def list_threads(user_id: str, db=Depends(get_db)):
    """
    List all threads for a user.
    """
    result = await db.execute("""
        SELECT mt.*, 
               (SELECT MAX(created_at) FROM messages WHERE thread_id = mt.id) as last_message_at
        FROM message_threads mt
        JOIN thread_participants tp ON mt.id = tp.thread_id
        WHERE tp.user_id = :user_id AND tp.left_at IS NULL
        ORDER BY last_message_at DESC NULLS LAST
    """, {"user_id": user_id})
    
    threads = result.fetchall()
    return [ThreadResponse(**t._mapping) for t in threads]


@router.get("/threads/{thread_id}/key")
async def get_thread_key(thread_id: str, user_id: str, db=Depends(get_db)):
    """
    Get encrypted thread key for user.
    User decrypts with their private key.
    """
    result = await db.execute("""
        SELECT encrypted_thread_key 
        FROM thread_participants 
        WHERE thread_id = :thread_id AND user_id = :user_id
    """, {"thread_id": thread_id, "user_id": user_id})
    
    participant = result.fetchone()
    if not participant:
        raise HTTPException(status_code=403, detail="Not a participant")
    
    return {"encrypted_thread_key": participant.encrypted_thread_key}


@router.get("/threads/{thread_id}/messages", response_model=List[MessageResponse])
async def get_messages(
    thread_id: str,
    before: Optional[datetime] = None,
    limit: int = 50,
    db=Depends(get_db)
):
    """
    Get messages in a thread.
    """
    query = """
        SELECT * FROM messages 
        WHERE thread_id = :thread_id
    """
    params = {"thread_id": thread_id, "limit": limit}
    
    if before:
        query += " AND created_at < :before"
        params["before"] = before
    
    query += " ORDER BY created_at DESC LIMIT :limit"
    
    result = await db.execute(query, params)
    messages = result.fetchall()
    
    return [MessageResponse(**m._mapping) for m in messages]


@router.post("/threads/{thread_id}/messages", response_model=MessageResponse)
async def send_message(
    thread_id: str,
    request: SendMessageRequest,
    sender_id: str,  # From auth
    db=Depends(get_db)
):
    """
    Send a message to a thread.
    """
    message_id = str(uuid.uuid4())
    
    await db.execute("""
        INSERT INTO messages (id, thread_id, sender_id, encrypted_content, message_type, attachment_ids)
        VALUES (:id, :thread_id, :sender_id, :encrypted_content, :message_type, :attachment_ids)
    """, {
        "id": message_id,
        "thread_id": thread_id,
        "sender_id": sender_id,
        "encrypted_content": request.encrypted_content,
        "message_type": request.message_type,
        "attachment_ids": request.attachment_ids
    })
    
    # Notify participants via WebSocket/Push
    redis = get_redis()
    if redis:
        await redis.publish(f"thread:{thread_id}", message_id)
    
    return MessageResponse(
        id=message_id,
        thread_id=thread_id,
        sender_id=sender_id,
        encrypted_content=request.encrypted_content,
        message_type=request.message_type,
        attachment_ids=request.attachment_ids,
        created_at=datetime.utcnow()
    )


# ===========================================
# WEBSOCKET FOR REALTIME
# ===========================================

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
    
    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.active_connections:
            self.active_connections[user_id].remove(websocket)
    
    async def send_to_user(self, user_id: str, message: dict):
        if user_id in self.active_connections:
            for connection in self.active_connections[user_id]:
                await connection.send_json(message)


manager = ConnectionManager()


@router.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    """
    WebSocket for realtime message delivery.
    """
    await manager.connect(websocket, user_id)
    
    try:
        while True:
            # Wait for messages (keep alive)
            data = await websocket.receive_text()
            # Handle incoming WebSocket messages (typing indicators, etc.)
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
