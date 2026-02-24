"""
0711 Vault Secure Chat - API Routes
Phase 1: Core Messaging (Fixed Auth)
"""

from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect, Header
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime
import uuid

router = APIRouter(prefix="/chat", tags=["chat"])


# ===========================================
# AUTH HELPER (avoids circular import)
# ===========================================

async def get_chat_user(authorization: str = Header(None)) -> str:
    """Validate token and return user_id for chat routes."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    
    token = authorization.split(" ")[1]
    
    # Import redis client from main at runtime
    from main import redis_client
    
    if redis_client:
        user_id = await redis_client.get(f"token:{token}")
        if user_id:
            return user_id.decode()
    
    raise HTTPException(status_code=401, detail="Invalid token")


# ===========================================
# CONNECTION MANAGER
# ===========================================

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
    
    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
    
    async def send_to_user(self, user_id: str, message: dict):
        if user_id in self.active_connections:
            for ws in self.active_connections[user_id]:
                try:
                    await ws.send_json(message)
                except:
                    pass

manager = ConnectionManager()


# ===========================================
# SCHEMAS
# ===========================================

class ConversationCreate(BaseModel):
    type: str = "direct"
    encrypted_name: Optional[str] = None
    member_ids: List[str]
    encrypted_keys: Dict[str, str]

class MessageCreate(BaseModel):
    encrypted_content: str
    message_type: str = "text"
    encrypted_media_ref: Optional[str] = None
    reply_to_id: Optional[str] = None

class KeyBundle(BaseModel):
    identity_public_key: str
    signed_prekey: str
    signed_prekey_signature: str
    one_time_prekeys: List[str] = []


# ===========================================
# KEY EXCHANGE
# ===========================================

@router.post("/keys")
async def upload_keys(keys: KeyBundle, user_id: str = Depends(get_chat_user)):
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO user_keys (user_id, identity_public_key, signed_prekey, 
                                   signed_prekey_signature, one_time_prekeys)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id) DO UPDATE SET
                identity_public_key = $2, signed_prekey = $3,
                signed_prekey_signature = $4, one_time_prekeys = $5, updated_at = NOW()
        """, uuid.UUID(user_id),
            bytes.fromhex(keys.identity_public_key),
            bytes.fromhex(keys.signed_prekey),
            bytes.fromhex(keys.signed_prekey_signature),
            [bytes.fromhex(k) for k in keys.one_time_prekeys])
    
    return {"status": "keys_uploaded"}


@router.get("/keys/{target_user_id}")
async def get_user_keys(target_user_id: str, user_id: str = Depends(get_chat_user)):
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT identity_public_key, signed_prekey, signed_prekey_signature, one_time_prekeys
            FROM user_keys WHERE user_id = $1
        """, uuid.UUID(target_user_id))
    
    if not row:
        raise HTTPException(404, "User keys not found")
    
    otp_key = None
    if row["one_time_prekeys"] and len(row["one_time_prekeys"]) > 0:
        otp_key = row["one_time_prekeys"][0].hex()
    
    return {
        "identity_public_key": row["identity_public_key"].hex(),
        "signed_prekey": row["signed_prekey"].hex(),
        "signed_prekey_signature": row["signed_prekey_signature"].hex(),
        "one_time_prekey": otp_key
    }


# ===========================================
# CONVERSATIONS
# ===========================================

@router.get("/conversations")
async def list_conversations(user_id: str = Depends(get_chat_user)):
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.id, c.type, c.encrypted_name, c.created_at,
                   cm.encrypted_conversation_key, cm.last_read_at,
                   (SELECT COUNT(*) FROM chat_messages m 
                    WHERE m.conversation_id = c.id 
                    AND m.created_at > COALESCE(cm.last_read_at, TIMESTAMP '1970-01-01')) as unread
            FROM chat_conversations c
            JOIN chat_members cm ON c.id = cm.conversation_id
            WHERE cm.user_id = $1
            ORDER BY c.updated_at DESC
        """, uuid.UUID(user_id))
    
    return {"conversations": [{
        "id": str(r["id"]),
        "type": r["type"],
        "encrypted_name": r["encrypted_name"].hex() if r["encrypted_name"] else None,
        "created_at": r["created_at"].isoformat(),
        "encrypted_key": r["encrypted_conversation_key"].hex() if r["encrypted_conversation_key"] else None,
        "unread_count": r["unread"]
    } for r in rows]}


@router.post("/conversations")
async def create_conversation(data: ConversationCreate, user_id: str = Depends(get_chat_user)):
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    conversation_id = uuid.uuid4()
    
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO chat_conversations (id, type, encrypted_name, created_by)
            VALUES ($1, $2, $3, $4)
        """, conversation_id, data.type,
            bytes.fromhex(data.encrypted_name) if data.encrypted_name else None,
            uuid.UUID(user_id))
        
        for member_id in data.member_ids:
            encrypted_key = data.encrypted_keys.get(member_id, "")
            await conn.execute("""
                INSERT INTO chat_members (conversation_id, user_id, role, encrypted_conversation_key)
                VALUES ($1, $2, $3, $4)
            """, conversation_id, uuid.UUID(member_id),
                "admin" if member_id == user_id else "member",
                bytes.fromhex(encrypted_key) if encrypted_key else b"")
    
    return {"conversation_id": str(conversation_id), "status": "created"}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user_id: str = Depends(get_chat_user)):
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    async with db_pool.acquire() as conn:
        member = await conn.fetchrow("""
            SELECT encrypted_conversation_key FROM chat_members
            WHERE conversation_id = $1 AND user_id = $2
        """, uuid.UUID(conversation_id), uuid.UUID(user_id))
        
        if not member:
            raise HTTPException(403, "Not a member")
        
        conv = await conn.fetchrow("""
            SELECT id, type, encrypted_name, created_at FROM chat_conversations WHERE id = $1
        """, uuid.UUID(conversation_id))
        
        members = await conn.fetch("""
            SELECT user_id, role FROM chat_members WHERE conversation_id = $1
        """, uuid.UUID(conversation_id))
    
    return {
        "id": str(conv["id"]),
        "type": conv["type"],
        "encrypted_name": conv["encrypted_name"].hex() if conv["encrypted_name"] else None,
        "encrypted_key": member["encrypted_conversation_key"].hex() if member["encrypted_conversation_key"] else None,
        "created_at": conv["created_at"].isoformat(),
        "members": [{"user_id": str(m["user_id"]), "role": m["role"]} for m in members]
    }


# ===========================================
# MESSAGES
# ===========================================

@router.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, limit: int = 50, user_id: str = Depends(get_chat_user)):
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    async with db_pool.acquire() as conn:
        member = await conn.fetchrow("""
            SELECT 1 FROM chat_members WHERE conversation_id = $1 AND user_id = $2
        """, uuid.UUID(conversation_id), uuid.UUID(user_id))
        
        if not member:
            raise HTTPException(403, "Not a member")
        
        rows = await conn.fetch("""
            SELECT id, sender_id, encrypted_content, message_type, encrypted_media_ref,
                   reply_to_id, created_at, edited_at
            FROM chat_messages
            WHERE conversation_id = $1 AND deleted_at IS NULL
            ORDER BY created_at DESC LIMIT $2
        """, uuid.UUID(conversation_id), limit)
    
    return {"messages": [{
        "id": str(r["id"]),
        "sender_id": str(r["sender_id"]) if r["sender_id"] else None,
        "encrypted_content": r["encrypted_content"].hex(),
        "message_type": r["message_type"],
        "encrypted_media_ref": r["encrypted_media_ref"].hex() if r["encrypted_media_ref"] else None,
        "reply_to_id": str(r["reply_to_id"]) if r["reply_to_id"] else None,
        "created_at": r["created_at"].isoformat(),
        "edited_at": r["edited_at"].isoformat() if r["edited_at"] else None
    } for r in reversed(rows)]}


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, message: MessageCreate, user_id: str = Depends(get_chat_user)):
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    message_id = uuid.uuid4()
    
    async with db_pool.acquire() as conn:
        member = await conn.fetchrow("""
            SELECT 1 FROM chat_members WHERE conversation_id = $1 AND user_id = $2
        """, uuid.UUID(conversation_id), uuid.UUID(user_id))
        
        if not member:
            raise HTTPException(403, "Not a member")
        
        await conn.execute("""
            INSERT INTO chat_messages (id, conversation_id, sender_id, encrypted_content,
                                       message_type, encrypted_media_ref, reply_to_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, message_id, uuid.UUID(conversation_id), uuid.UUID(user_id),
            bytes.fromhex(message.encrypted_content), message.message_type,
            bytes.fromhex(message.encrypted_media_ref) if message.encrypted_media_ref else None,
            uuid.UUID(message.reply_to_id) if message.reply_to_id else None)
        
        await conn.execute("""
            UPDATE chat_conversations SET updated_at = NOW() WHERE id = $1
        """, uuid.UUID(conversation_id))
        
        members = await conn.fetch("""
            SELECT user_id FROM chat_members WHERE conversation_id = $1 AND user_id != $2
        """, uuid.UUID(conversation_id), uuid.UUID(user_id))
    
    # Notify via WebSocket
    for m in members:
        await manager.send_to_user(str(m["user_id"]), {
            "type": "message:new",
            "conversation_id": conversation_id,
            "message": {
                "id": str(message_id),
                "sender_id": user_id,
                "encrypted_content": message.encrypted_content,
                "message_type": message.message_type,
                "created_at": datetime.utcnow().isoformat()
            }
        })
    
    return {"message_id": str(message_id), "status": "sent"}


@router.post("/conversations/{conversation_id}/read")
async def mark_as_read(conversation_id: str, message_id: str, user_id: str = Depends(get_chat_user)):
    from main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO chat_read_receipts (conversation_id, user_id, last_read_message_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (conversation_id, user_id) DO UPDATE SET
                last_read_message_id = $3, read_at = NOW()
        """, uuid.UUID(conversation_id), uuid.UUID(user_id), uuid.UUID(message_id))
        
        await conn.execute("""
            UPDATE chat_members SET last_read_at = NOW()
            WHERE conversation_id = $1 AND user_id = $2
        """, uuid.UUID(conversation_id), uuid.UUID(user_id))
    
    return {"status": "read"}


# ===========================================
# WEBSOCKET
# ===========================================

@router.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    from main import redis_client
    
    # Validate token
    if not redis_client:
        await websocket.close(code=1008)
        return
    
    user_id = await redis_client.get(f"token:{token}")
    if not user_id:
        await websocket.close(code=1008)
        return
    
    user_id = user_id.decode()
    await manager.connect(websocket, user_id)
    
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)


print("✅ Chat routes v2 loaded")
