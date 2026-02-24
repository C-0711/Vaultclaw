"""
0711 Vault API
Zero-knowledge encrypted storage backend
"""

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Header, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
import os
import asyncpg
import redis.asyncio as aioredis
import httpx
import secrets
import hashlib
import json
import uuid

# Storage service (Albert - replaces MinIO)
from storage_albert import (
    init_storage, get_storage, VaultCrypto,
    generate_upload_url, generate_download_url,
    store_content, retrieve_content, delete_content
)

# Database module (for assistant routes)
from database import init_db as init_assistant_db

# Stripe billing
from stripe_routes import router as stripe_router

# iMessage import (macOS only)
try:
    from imessage_import import router as imessage_router
    IMESSAGE_AVAILABLE = True
except ImportError:
    IMESSAGE_AVAILABLE = False

# Calendar
from calendar_routes import router as calendar_router, init_calendar

# Personal AI Assistant
from routers.assistant import router as assistant_router
from routers.s3 import router as s3_router
from routers.folders import router as folders_router
from routers.webhooks import router as webhooks_router
from routers.quotas import router as quotas_router
from routers.sharing import router as sharing_router
from routers.versions import router as versions_router
from routers.git import router as git_router, init_git_router
from routers.publish import router as publish_router, init_publish_router
from routers.pipeline import router as pipeline_router
from routers.mcp import router as mcp_router, init_mcp_router
from routers.docs_routes import router as docs_router, init_docs_router
from routers.settings import router as settings_router, init_settings
from routers.chat import router as chat_router
from routers.files import router as files_router, init_files_router

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vault:vault@localhost:5432/vault")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "bge-m3:latest")
VISION_MODEL = os.getenv("VISION_MODEL", "llama4:latest")
# MinIO removed - Albert Storage uses PostgreSQL + ChaCha20-Poly1305
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")

# Global connections
db_pool = None
redis_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    global db_pool, redis_client
    
    print("🚀 Starting 0711 Vault API...")
    
    # Connect to PostgreSQL
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        print("✅ PostgreSQL connected")
        init_git_router(db_pool)
        init_publish_router(db_pool)
        init_mcp_router(db_pool)
        init_docs_router(db_pool)
    except Exception as e:
        print(f"⚠️ PostgreSQL connection failed: {e}")
    
    # Connect to Redis
    try:
        redis_client = await aioredis.from_url(REDIS_URL)
        await redis_client.ping()
        print("✅ Redis connected")
        # Initialize calendar and settings after redis is available
        init_calendar(db_pool, redis_client)
        init_settings(db_pool, redis_client)
        init_files_router(db_pool, redis_client)
    except Exception as e:
        print(f"⚠️ Redis connection failed: {e}")
    
    # Initialize assistant module connections (Neo4j, Ollama)
    try:
        await init_assistant_db()
        print("✅ Assistant module initialized")
    except Exception as e:
        print(f"⚠️ Assistant module init failed: {e}")
    
    # Initialize Albert storage (replaces MinIO)
    try:
        if db_pool:
            storage = init_storage(db_pool)
            await storage.ensure_table()
            print("✅ Albert storage initialized (MinIO replaced)")
    except Exception as e:
        print(f"⚠️ Albert storage init failed: {e}")
    
    yield
    
    # Cleanup
    if db_pool:
        await db_pool.close()
    if redis_client:
        await redis_client.close()
    print("👋 Vault API shutdown complete")


app = FastAPI(
    title="0711 Vault API",
    description="Zero-knowledge encrypted storage",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(stripe_router)
app.include_router(calendar_router)
app.include_router(assistant_router, prefix="/assistant", tags=["AI Assistant"])
app.include_router(s3_router, tags=["S3 Compatible"])
app.include_router(folders_router)
app.include_router(webhooks_router)
app.include_router(quotas_router)
app.include_router(sharing_router)
app.include_router(git_router)
app.include_router(publish_router)
app.include_router(pipeline_router)
app.include_router(mcp_router)
app.include_router(docs_router)
app.include_router(versions_router)
app.include_router(settings_router)
app.include_router(chat_router, prefix="/chat", tags=["Secure Chat"])
app.include_router(files_router, tags=["File Management"])
if IMESSAGE_AVAILABLE:
    app.include_router(imessage_router)


# ===========================================
# AUTH HELPERS
# ===========================================

async def get_current_user(authorization: str = Header(None)):
    """Validate token and return user_id."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    
    token = authorization.split(" ")[1]
    
    if redis_client:
        user_id = await redis_client.get(f"token:{token}")
        if user_id:
            return user_id.decode()
    
    raise HTTPException(status_code=401, detail="Invalid token")


# ===========================================
# MODELS
# ===========================================

class RegisterRequest(BaseModel):
    email: EmailStr
    auth_hash: str
    salt: str
    encrypted_master_key: str

class LoginRequest(BaseModel):
    email: EmailStr
    auth_hash: Optional[str] = None
    password: Optional[str] = None

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    encrypted_master_key: str

class VaultItemCreate(BaseModel):
    item_type: str  # photo, document, video
    encrypted_metadata: Optional[str] = None
    file_size: int
    mime_type: Optional[str] = None
    captured_at: Optional[datetime] = None

class FaceClusterCreate(BaseModel):
    encrypted_name: str
    relationship: Optional[str] = None

class FaceClusterUpdate(BaseModel):
    encrypted_name: Optional[str] = None
    relationship: Optional[str] = None

class PlaceClusterCreate(BaseModel):
    encrypted_name: str
    place_type: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class SearchRequest(BaseModel):
    query: str
    limit: int = 20

class TrainFaceRequest(BaseModel):
    face_ids: List[str]
    cluster_id: Optional[str] = None
    encrypted_name: Optional[str] = None
    relationship: Optional[str] = None


# ===========================================
# HEALTH ENDPOINTS
# ===========================================

@app.get("/")
async def root():
    return {"service": "0711 Vault API", "version": "1.0.0", "status": "running"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    services = {"api": "healthy", "postgres": "unknown", "redis": "unknown", "ollama": "unknown"}
    
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            services["postgres"] = "healthy"
        except:
            services["postgres"] = "unhealthy"
    
    if redis_client:
        try:
            await redis_client.ping()
            services["redis"] = "healthy"
        except:
            services["redis"] = "unhealthy"
    
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
            if r.status_code == 200:
                services["ollama"] = "healthy"
    except:
        services["ollama"] = "unavailable"
    
    overall = "healthy" if services["postgres"] == "healthy" else "degraded"
    return {"status": overall, "timestamp": datetime.utcnow().isoformat(), "services": services}


# ===========================================
# AUTH ENDPOINTS
# ===========================================

@app.post("/auth/register", response_model=dict)
async def register(request: RegisterRequest):
    """Register a new user with zero-knowledge auth."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM users WHERE email = $1", request.email)
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        user_id = await conn.fetchval("""
            INSERT INTO users (email, auth_hash, salt, encrypted_master_key)
            VALUES ($1, $2, $3, $4)
            RETURNING id
        """, request.email, request.auth_hash, request.salt, request.encrypted_master_key)
        
        return {"user_id": str(user_id), "message": "User registered successfully"}


@app.get("/auth/salt/{email}")
async def get_salt(email: str):
    """Get user's salt for client-side key derivation."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        salt = await conn.fetchval("SELECT salt FROM users WHERE email = $1", email)
        if not salt:
            # Return random salt to prevent email enumeration
            return {"salt": secrets.token_hex(32)}
        return {"salt": salt}


@app.post("/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Login and get access token."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id, auth_hash, encrypted_master_key FROM users WHERE email = $1",
            request.email
        )
        
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        stored = user["auth_hash"]
        _valid = False
        if request.auth_hash:
            _valid = (stored == request.auth_hash)
        if request.password:
            _valid = (stored == request.password)
            if not _valid:
                try:
                    import bcrypt as _bc
                    _valid = _bc.checkpw(request.password.encode("utf-8"), stored.encode("utf-8"))
                except Exception:
                    pass
        if not _valid:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        # Generate token
        token = secrets.token_urlsafe(32)
        
        # Store in Redis (24 hour expiry)
        if redis_client:
            await redis_client.setex(f"token:{token}", 86400, str(user["id"]))
        
        # Update last login
        await conn.execute("UPDATE users SET last_login = NOW() WHERE id = $1", user["id"])
        
        return LoginResponse(
            access_token=token,
            user_id=str(user["id"]),
            encrypted_master_key=user["encrypted_master_key"]
        )


@app.post("/auth/logout")
async def logout(user_id: str = Depends(get_current_user), authorization: str = Header(None)):
    """Logout and invalidate token."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        if redis_client:
            await redis_client.delete(f"token:{token}")
    return {"message": "Logged out"}


# ===========================================
# VAULT ITEM ENDPOINTS
# ===========================================

@app.post("/vault/items")
async def create_item(item: VaultItemCreate, user_id: str = Depends(get_current_user)):
    """Create a vault item and get presigned upload URL."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    # Generate presigned upload URL
    filename = f"file.{item.mime_type.split('/')[-1]}" if item.mime_type else "file"
    upload_url, storage_key = generate_upload_url(
        user_id=user_id,
        filename=filename,
        content_type=item.mime_type or "application/octet-stream",
        expires=timedelta(hours=1)
    )
    
    async with db_pool.acquire() as conn:
        item_id = await conn.fetchval("""
            INSERT INTO vault_items (user_id, item_type, encrypted_metadata, storage_key, file_size, mime_type, captured_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
        """, user_id, item.item_type, item.encrypted_metadata, storage_key, item.file_size, item.mime_type, item.captured_at)
        
        # Add to processing queue
        await conn.execute("""
            INSERT INTO processing_queue (item_id, user_id, task_type, priority)
            VALUES ($1, $2, 'full_process', 5)
        """, item_id, user_id)
    
    return {
        "item_id": str(item_id),
        "storage_key": storage_key,
        "upload_url": upload_url,
        "upload_method": "PUT",
        "expires_in": 3600
    }


@app.get("/vault/items")
async def list_items(
    user_id: str = Depends(get_current_user),
    item_type: Optional[str] = None,
    limit: int = Query(100, le=500),
    offset: int = 0
):
    """List user's vault items."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        if item_type:
            items = await conn.fetch("""
                SELECT id, item_type, encrypted_metadata, file_size, mime_type, 
                       captured_at, created_at, processing_status
                FROM vault_items
                WHERE user_id = $1 AND item_type = $2 AND deleted_at IS NULL
                ORDER BY COALESCE(captured_at, created_at) DESC
                LIMIT $3 OFFSET $4
            """, user_id, item_type, limit, offset)
        else:
            items = await conn.fetch("""
                SELECT id, item_type, encrypted_metadata, file_size, mime_type,
                       captured_at, created_at, processing_status
                FROM vault_items
                WHERE user_id = $1 AND deleted_at IS NULL
                ORDER BY COALESCE(captured_at, created_at) DESC
                LIMIT $2 OFFSET $3
            """, user_id, limit, offset)
        
        return {"items": [dict(item) for item in items], "count": len(items)}


# ===========================================
# DIRECT UPLOAD/DOWNLOAD (Albert Storage)
# ===========================================

@app.post("/vault/items/{item_id}/upload")
async def upload_item_content(
    item_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user)
):
    """
    Upload encrypted content directly (replaces presigned URL upload).
    Content is encrypted with ChaCha20-Poly1305 and stored in PostgreSQL.
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    # Verify item exists and belongs to user
    async with db_pool.acquire() as conn:
        item = await conn.fetchrow(
            "SELECT storage_key FROM vault_items WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
            uuid.UUID(item_id), user_id
        )
        
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        
        storage_key = item['storage_key']
    
    # Read content
    content = await file.read()
    
    # Store encrypted content
    try:
        await store_content(user_id, storage_key, content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Storage error: {str(e)}")
    
    # Update item status
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE vault_items SET processing_status = 'uploaded' WHERE id = $1",
            uuid.UUID(item_id)
        )
    
    return {"message": "Content uploaded", "item_id": item_id, "size": len(content)}


@app.get("/vault/items/{item_id}/download")
async def download_item_content(
    item_id: str,
    user_id: str = Depends(get_current_user)
):
    """
    Download decrypted content directly (replaces presigned URL download).
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    # Get item info
    async with db_pool.acquire() as conn:
        item = await conn.fetchrow(
            "SELECT storage_key, mime_type, encrypted_metadata FROM vault_items WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
            uuid.UUID(item_id), user_id
        )
        
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
    
    # Retrieve and decrypt content
    try:
        content = await retrieve_content(item['storage_key'], user_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Content not found")
    except Exception as e:
        import traceback
        print(f"[Download Error] item_id={item_id}, storage_key={item['storage_key']}, user_id={user_id}")
        print(f"[Download Error] Exception type: {type(e).__name__}, message: {repr(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Storage error: {type(e).__name__}: {str(e)}")
    
    return Response(
        content=content,
        media_type=item['mime_type'] or "application/octet-stream"
    )


@app.post("/vault/upload")
async def direct_upload(
    file: UploadFile = File(...),
    item_type: str = Query("document"),
    user_id: str = Depends(get_current_user)
):
    """
    Combined create + upload in single request.
    Creates item and stores content in one call.
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    # Read content
    content = await file.read()
    
    # Generate storage key
    _, storage_key = generate_upload_url(user_id, file.filename or "file")
    
    # Create item with original filename
    original_filename = file.filename or "file"
    async with db_pool.acquire() as conn:
        item_id = await conn.fetchval("""
            INSERT INTO vault_items (user_id, item_type, storage_key, file_size, mime_type, processing_status, original_filename)
            VALUES ($1, $2, $3, $4, $5, 'uploaded', $6)
            RETURNING id
        """, user_id, item_type, storage_key, len(content), file.content_type, original_filename)
        
        # Add to processing queue
        await conn.execute("""
            INSERT INTO processing_queue (item_id, user_id, task_type, priority)
            VALUES ($1, $2, 'full_process', 5)
        """, item_id, user_id)
    
    # Store encrypted content
    try:
        await store_content(user_id, storage_key, content)
    except Exception as e:
        # Rollback: delete the item
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM vault_items WHERE id = $1", item_id)
        raise HTTPException(status_code=500, detail=f"Storage error: {str(e)}")
    
    return {
        "item_id": str(item_id),
        "storage_key": storage_key,
        "size": len(content),
        "message": "Content uploaded and encrypted"
    }


@app.get("/vault/items/{item_id}")
async def get_item(item_id: str, user_id: str = Depends(get_current_user)):
    """Get single vault item with presigned download URL."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        item = await conn.fetchrow("""
            SELECT id, item_type, encrypted_metadata, storage_key, file_size, 
                   mime_type, captured_at, created_at, processing_status
            FROM vault_items
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
        """, uuid.UUID(item_id), user_id)
        
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        
        # Generate presigned download URL
        download_url = generate_download_url(
            storage_key=item['storage_key'],
            expires=timedelta(hours=1)
        )
        
        result = dict(item)
        result["download_url"] = download_url
        result["download_expires_in"] = 3600
        return result


@app.delete("/vault/items/{item_id}")
async def delete_item(item_id: str, user_id: str = Depends(get_current_user)):
    """Soft delete a vault item."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE vault_items SET deleted_at = NOW()
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
        """, uuid.UUID(item_id), user_id)
        
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Item not found")
        
        return {"message": "Item deleted"}


# ===========================================
# FACE ENDPOINTS
# ===========================================

@app.get("/faces/clusters")
async def list_face_clusters(user_id: str = Depends(get_current_user)):
    """List user's face clusters."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        clusters = await conn.fetch("""
            SELECT id, encrypted_name, relationship, photo_count, created_at
            FROM face_clusters
            WHERE user_id = $1
            ORDER BY photo_count DESC
        """, user_id)
        
        return {"clusters": [dict(c) for c in clusters]}


@app.get("/faces/unlabeled")
async def get_unlabeled_faces(user_id: str = Depends(get_current_user), limit: int = 50):
    """Get faces that haven't been labeled yet, grouped by similarity."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        # Get faces without clusters, with their items
        faces = await conn.fetch("""
            SELECT f.id, f.item_id, f.bbox_x, f.bbox_y, f.bbox_width, f.bbox_height,
                   f.detection_confidence, v.storage_key
            FROM faces f
            JOIN vault_items v ON f.item_id = v.id
            WHERE f.user_id = $1 AND f.cluster_id IS NULL
            ORDER BY f.created_at DESC
            LIMIT $2
        """, user_id, limit)
        
        return {"faces": [dict(f) for f in faces]}


@app.post("/faces/train")
async def train_faces(request: TrainFaceRequest, user_id: str = Depends(get_current_user)):
    """Assign faces to a cluster (new or existing)."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            cluster_id = request.cluster_id
            
            # Create new cluster if needed
            if not cluster_id and request.encrypted_name:
                cluster_id = await conn.fetchval("""
                    INSERT INTO face_clusters (user_id, encrypted_name, relationship)
                    VALUES ($1, $2, $3)
                    RETURNING id
                """, user_id, request.encrypted_name, request.relationship)
            
            if not cluster_id:
                raise HTTPException(status_code=400, detail="Must provide cluster_id or encrypted_name")
            
            # Update faces
            face_uuids = [uuid.UUID(fid) for fid in request.face_ids]
            await conn.execute("""
                UPDATE faces SET cluster_id = $1
                WHERE id = ANY($2) AND user_id = $3
            """, uuid.UUID(str(cluster_id)), face_uuids, user_id)
            
            # Update cluster photo count
            count = await conn.fetchval("""
                SELECT COUNT(DISTINCT item_id) FROM faces WHERE cluster_id = $1
            """, uuid.UUID(str(cluster_id)))
            
            await conn.execute("""
                UPDATE face_clusters SET photo_count = $1 WHERE id = $2
            """, count, uuid.UUID(str(cluster_id)))
            
            return {"cluster_id": str(cluster_id), "faces_updated": len(request.face_ids)}


@app.put("/faces/clusters/{cluster_id}")
async def update_face_cluster(
    cluster_id: str, 
    update: FaceClusterUpdate, 
    user_id: str = Depends(get_current_user)
):
    """Update a face cluster's name/relationship."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        updates = []
        params = [uuid.UUID(cluster_id), user_id]
        param_idx = 3
        
        if update.encrypted_name is not None:
            updates.append(f"encrypted_name = ${param_idx}")
            params.append(update.encrypted_name)
            param_idx += 1
        
        if update.relationship is not None:
            updates.append(f"relationship = ${param_idx}")
            params.append(update.relationship)
            param_idx += 1
        
        if not updates:
            raise HTTPException(status_code=400, detail="No updates provided")
        
        await conn.execute(f"""
            UPDATE face_clusters SET {', '.join(updates)}
            WHERE id = $1 AND user_id = $2
        """, *params)
        
        return {"message": "Cluster updated"}


# ===========================================
# PLACE ENDPOINTS
# ===========================================

@app.get("/places/clusters")
async def list_place_clusters(user_id: str = Depends(get_current_user)):
    """List user's place clusters."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        clusters = await conn.fetch("""
            SELECT id, encrypted_name, place_type, latitude, longitude, 
                   city, country, photo_count, created_at
            FROM place_clusters
            WHERE user_id = $1
            ORDER BY photo_count DESC
        """, user_id)
        
        return {"clusters": [dict(c) for c in clusters]}


@app.post("/places/clusters")
async def create_place_cluster(
    cluster: PlaceClusterCreate, 
    user_id: str = Depends(get_current_user)
):
    """Create a new place cluster."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        cluster_id = await conn.fetchval("""
            INSERT INTO place_clusters (user_id, encrypted_name, place_type, latitude, longitude)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
        """, user_id, cluster.encrypted_name, cluster.place_type, cluster.latitude, cluster.longitude)
        
        return {"cluster_id": str(cluster_id)}


# ===========================================
# SEARCH ENDPOINTS
# ===========================================

@app.post("/search/semantic")
async def semantic_search(request: SearchRequest, user_id: str = Depends(get_current_user)):
    """Search vault using natural language."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    # Get embedding for query
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{OLLAMA_HOST}/api/embeddings",
                json={"model": EMBEDDING_MODEL, "prompt": request.query},
                timeout=30
            )
            if r.status_code != 200:
                raise HTTPException(status_code=500, detail="Embedding generation failed")
            
            embedding = r.json().get("embedding", [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama error: {str(e)}")
    
    # Search in database
    async with db_pool.acquire() as conn:
        # Convert embedding to pgvector format
        embedding_str = "[" + ",".join(map(str, embedding)) + "]"
        
        results = await conn.fetch("""
            SELECT v.id, v.item_type, v.encrypted_metadata, v.storage_key,
                   1 - (e.embedding <=> $1::vector) as similarity
            FROM embeddings e
            JOIN vault_items v ON e.item_id = v.id
            WHERE e.user_id = $2 AND v.deleted_at IS NULL
            ORDER BY e.embedding <=> $1::vector
            LIMIT $3
        """, embedding_str, user_id, request.limit)
        
        return {
            "query": request.query,
            "results": [dict(r) for r in results]
        }


@app.get("/search/faces/{cluster_id}")
async def search_by_face(cluster_id: str, user_id: str = Depends(get_current_user)):
    """Get all photos containing a specific person."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        items = await conn.fetch("""
            SELECT DISTINCT v.id, v.item_type, v.encrypted_metadata, v.storage_key, v.captured_at
            FROM faces f
            JOIN vault_items v ON f.item_id = v.id
            WHERE f.cluster_id = $1 AND f.user_id = $2 AND v.deleted_at IS NULL
            ORDER BY v.captured_at DESC
        """, uuid.UUID(cluster_id), user_id)
        
        return {"items": [dict(i) for i in items]}


@app.get("/search/places/{cluster_id}")
async def search_by_place(cluster_id: str, user_id: str = Depends(get_current_user)):
    """Get all photos from a specific place."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        items = await conn.fetch("""
            SELECT DISTINCT v.id, v.item_type, v.encrypted_metadata, v.storage_key, v.captured_at
            FROM item_places p
            JOIN vault_items v ON p.item_id = v.id
            WHERE p.cluster_id = $1 AND v.user_id = $2 AND v.deleted_at IS NULL
            ORDER BY v.captured_at DESC
        """, uuid.UUID(cluster_id), user_id)
        
        return {"items": [dict(i) for i in items]}


# ===========================================
# STATS ENDPOINTS
# ===========================================

@app.get("/vault/stats")
async def vault_stats(user_id: str = Depends(get_current_user)):
    """Get user's vault statistics."""
    if not db_pool:
        return {"error": "Database unavailable"}
    
    async with db_pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) FILTER (WHERE item_type = 'photo') as photos,
                COUNT(*) FILTER (WHERE item_type = 'document') as documents,
                COUNT(*) FILTER (WHERE item_type = 'video') as videos,
                COALESCE(SUM(file_size), 0) as total_bytes,
                COUNT(*) FILTER (WHERE processing_status = 'complete') as processed,
                COUNT(*) FILTER (WHERE processing_status = 'pending') as pending
            FROM vault_items
            WHERE user_id = $1 AND deleted_at IS NULL
        """, user_id)
        
        face_clusters = await conn.fetchval(
            "SELECT COUNT(*) FROM face_clusters WHERE user_id = $1", user_id
        )
        place_clusters = await conn.fetchval(
            "SELECT COUNT(*) FROM place_clusters WHERE user_id = $1", user_id
        )
        
        return {
            "photos": stats["photos"],
            "documents": stats["documents"],
            "videos": stats["videos"],
            "total_bytes": stats["total_bytes"],
            "total_gb": round(stats["total_bytes"] / (1024**3), 2),
            "processed": stats["processed"],
            "pending": stats["pending"],
            "face_clusters": face_clusters,
            "place_clusters": place_clusters
        }


# ===========================================
# PROCESSING STATUS
# ===========================================

@app.get("/processing/status")
async def processing_status(user_id: str = Depends(get_current_user)):
    """Get processing queue status."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with db_pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) FILTER (WHERE status = 'pending') as pending,
                COUNT(*) FILTER (WHERE status = 'processing') as processing,
                COUNT(*) FILTER (WHERE status = 'complete') as complete,
                COUNT(*) FILTER (WHERE status = 'failed') as failed
            FROM processing_queue
            WHERE user_id = $1
        """, user_id)
        
        return dict(stats)


# ===========================================
# AI ENDPOINTS
# ===========================================

@app.post("/ai/embed")
async def create_embedding(text: str, user_id: str = Depends(get_current_user)):
    """Create text embedding using Ollama."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{OLLAMA_HOST}/api/embeddings",
                json={"model": EMBEDDING_MODEL, "prompt": text},
                timeout=30
            )
            if r.status_code == 200:
                return r.json()
            raise HTTPException(status_code=r.status_code, detail="Embedding failed")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Ollama timeout")


@app.get("/ai/models")
async def list_models():
    """List available Ollama models."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{OLLAMA_HOST}/api/tags", timeout=10)
            if r.status_code == 200:
                return r.json()
    except:
        pass
    return {"models": []}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# OpenAPI Documentation Configuration
app.title = "0711-Vault API"
app.description = "Privacy-first personal vault with AI-powered organization"
app.version = "2.0.0"
app.docs_url = "/docs"
app.redoc_url = "/redoc"
app.openapi_url = "/openapi.json"
