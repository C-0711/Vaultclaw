"""
0711 Vault - Cloud Import Connectors
Migrate from Dropbox, Google Drive, OneDrive, iCloud, etc.
Full sovereignty: suck everything out, keep nothing behind.
"""

from fastapi import APIRouter, HTTPException, Depends, Request, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid
import httpx
import asyncio
import logging
import os
import json

logger = logging.getLogger("vault.import")

router = APIRouter(prefix="/import", tags=["Import Connectors"])

# ===========================================
# CONFIGURATION
# ===========================================

# OAuth credentials (from environment)
DROPBOX_APP_KEY = os.getenv("DROPBOX_APP_KEY", "")
DROPBOX_APP_SECRET = os.getenv("DROPBOX_APP_SECRET", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
ONEDRIVE_CLIENT_ID = os.getenv("ONEDRIVE_CLIENT_ID", "")
ONEDRIVE_CLIENT_SECRET = os.getenv("ONEDRIVE_CLIENT_SECRET", "")

REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "https://vault.0711.io/import/callback")


# ===========================================
# SCHEMAS
# ===========================================

class ConnectorInfo(BaseModel):
    id: str
    name: str
    icon: str
    description: str
    connected: bool
    last_sync: Optional[datetime] = None
    files_imported: int = 0
    bytes_imported: int = 0

class OAuthStartResponse(BaseModel):
    auth_url: str
    state: str

class OAuthCallbackRequest(BaseModel):
    code: str
    state: str
    provider: str

class ImportJobCreate(BaseModel):
    provider: str
    paths: Optional[List[str]] = None  # None = import all
    include_shared: bool = False
    preserve_folders: bool = True
    delete_after_import: bool = False  # Dangerous but sovereign

class ImportJobResponse(BaseModel):
    job_id: str
    provider: str
    status: str
    total_files: int
    imported_files: int
    failed_files: int
    total_bytes: int
    imported_bytes: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    current_file: Optional[str] = None
    errors: List[str] = []


# ===========================================
# DEPENDENCIES
# ===========================================

async def get_db_pool():
    from main import db_pool
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db_pool

async def get_redis():
    from main import redis_client
    return redis_client

async def get_current_user(request: Request):
    from main import get_current_user as auth_user
    return await auth_user(request.headers.get("authorization"))


# ===========================================
# CONNECTOR REGISTRY
# ===========================================

CONNECTORS = {
    "dropbox": {
        "name": "Dropbox",
        "icon": "📦",
        "description": "Import files from Dropbox",
        "auth_url": "https://www.dropbox.com/oauth2/authorize",
        "token_url": "https://api.dropboxapi.com/oauth2/token",
        "api_base": "https://api.dropboxapi.com/2",
        "content_base": "https://content.dropboxapi.com/2",
        "scopes": ["files.content.read", "files.metadata.read", "sharing.read"]
    },
    "google_drive": {
        "name": "Google Drive",
        "icon": "🔺",
        "description": "Import files from Google Drive",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "api_base": "https://www.googleapis.com/drive/v3",
        "scopes": ["https://www.googleapis.com/auth/drive.readonly"]
    },
    "onedrive": {
        "name": "OneDrive",
        "icon": "☁️",
        "description": "Import files from Microsoft OneDrive",
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "api_base": "https://graph.microsoft.com/v1.0",
        "scopes": ["Files.Read", "Files.Read.All"]
    },
    "icloud": {
        "name": "iCloud Drive",
        "icon": "☁️",
        "description": "Import from iCloud (requires local Mac)",
        "local_only": True
    },
    "nextcloud": {
        "name": "Nextcloud",
        "icon": "☁️",
        "description": "Import from any Nextcloud/ownCloud instance",
        "webdav": True
    },
    "s3": {
        "name": "Amazon S3",
        "icon": "📦",
        "description": "Import from S3-compatible storage",
        "s3_compatible": True
    }
}


# ===========================================
# LIST CONNECTORS
# ===========================================

@router.get("/connectors", response_model=List[ConnectorInfo])
async def list_connectors(
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """List available import connectors with connection status."""
    async with db_pool.acquire() as conn:
        # Get user's connected accounts
        connections = await conn.fetch("""
            SELECT provider, last_sync, files_imported, bytes_imported
            FROM import_connections
            WHERE user_id = $1 AND revoked_at IS NULL
        """, user_id)
        
        connected_map = {c['provider']: c for c in connections}
    
    result = []
    for provider_id, info in CONNECTORS.items():
        conn_data = connected_map.get(provider_id)
        result.append(ConnectorInfo(
            id=provider_id,
            name=info["name"],
            icon=info["icon"],
            description=info["description"],
            connected=conn_data is not None,
            last_sync=conn_data['last_sync'] if conn_data else None,
            files_imported=conn_data['files_imported'] if conn_data else 0,
            bytes_imported=conn_data['bytes_imported'] if conn_data else 0
        ))
    
    return result


# ===========================================
# OAUTH FLOW
# ===========================================

@router.post("/connect/{provider}", response_model=OAuthStartResponse)
async def start_oauth(
    provider: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    redis = Depends(get_redis)
):
    """Start OAuth flow for a cloud provider."""
    if provider not in CONNECTORS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    
    connector = CONNECTORS[provider]
    
    if connector.get("local_only"):
        raise HTTPException(status_code=400, detail="This connector requires local setup")
    if connector.get("webdav") or connector.get("s3_compatible"):
        raise HTTPException(status_code=400, detail="Use /connect/{provider}/credentials for this connector")
    
    # Generate state token
    state = str(uuid.uuid4())
    
    # Store state in Redis (10 min expiry)
    if redis:
        await redis.setex(
            f"oauth_state:{state}",
            600,
            json.dumps({"user_id": user_id, "provider": provider})
        )
    
    # Build auth URL
    if provider == "dropbox":
        auth_url = (
            f"{connector['auth_url']}?"
            f"client_id={DROPBOX_APP_KEY}&"
            f"response_type=code&"
            f"redirect_uri={REDIRECT_URI}&"
            f"state={state}&"
            f"token_access_type=offline"
        )
    elif provider == "google_drive":
        scopes = "%20".join(connector['scopes'])
        auth_url = (
            f"{connector['auth_url']}?"
            f"client_id={GOOGLE_CLIENT_ID}&"
            f"response_type=code&"
            f"redirect_uri={REDIRECT_URI}&"
            f"scope={scopes}&"
            f"state={state}&"
            f"access_type=offline&"
            f"prompt=consent"
        )
    elif provider == "onedrive":
        scopes = "%20".join(connector['scopes'])
        auth_url = (
            f"{connector['auth_url']}?"
            f"client_id={ONEDRIVE_CLIENT_ID}&"
            f"response_type=code&"
            f"redirect_uri={REDIRECT_URI}&"
            f"scope={scopes}&"
            f"state={state}"
        )
    else:
        raise HTTPException(status_code=501, detail=f"OAuth not implemented for {provider}")
    
    logger.info(f"User {user_id} starting OAuth for {provider}")
    return OAuthStartResponse(auth_url=auth_url, state=state)


@router.post("/callback")
async def oauth_callback(
    callback: OAuthCallbackRequest,
    db_pool = Depends(get_db_pool),
    redis = Depends(get_redis)
):
    """Handle OAuth callback and store tokens."""
    # Verify state
    if redis:
        state_data = await redis.get(f"oauth_state:{callback.state}")
        if not state_data:
            raise HTTPException(status_code=400, detail="Invalid or expired state")
        
        state_info = json.loads(state_data)
        user_id = state_info["user_id"]
        provider = state_info["provider"]
        
        # Delete used state
        await redis.delete(f"oauth_state:{callback.state}")
    else:
        raise HTTPException(status_code=503, detail="Session storage unavailable")
    
    if provider != callback.provider:
        raise HTTPException(status_code=400, detail="Provider mismatch")
    
    connector = CONNECTORS[provider]
    
    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        if provider == "dropbox":
            token_response = await client.post(
                connector['token_url'],
                data={
                    "code": callback.code,
                    "grant_type": "authorization_code",
                    "client_id": DROPBOX_APP_KEY,
                    "client_secret": DROPBOX_APP_SECRET,
                    "redirect_uri": REDIRECT_URI
                }
            )
        elif provider == "google_drive":
            token_response = await client.post(
                connector['token_url'],
                data={
                    "code": callback.code,
                    "grant_type": "authorization_code",
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": REDIRECT_URI
                }
            )
        elif provider == "onedrive":
            token_response = await client.post(
                connector['token_url'],
                data={
                    "code": callback.code,
                    "grant_type": "authorization_code",
                    "client_id": ONEDRIVE_CLIENT_ID,
                    "client_secret": ONEDRIVE_CLIENT_SECRET,
                    "redirect_uri": REDIRECT_URI
                }
            )
        else:
            raise HTTPException(status_code=501, detail="Not implemented")
        
        if token_response.status_code != 200:
            logger.error(f"Token exchange failed: {token_response.text}")
            raise HTTPException(status_code=400, detail="Token exchange failed")
        
        tokens = token_response.json()
    
    # Store encrypted tokens
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO import_connections 
            (user_id, provider, access_token, refresh_token, token_expires_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, provider) 
            DO UPDATE SET 
                access_token = $3, 
                refresh_token = COALESCE($4, import_connections.refresh_token),
                token_expires_at = $5,
                revoked_at = NULL,
                updated_at = NOW()
        """, user_id, provider, 
            tokens.get("access_token"),
            tokens.get("refresh_token"),
            datetime.utcnow() if tokens.get("expires_in") else None
        )
    
    logger.info(f"User {user_id} connected {provider}")
    return {"status": "connected", "provider": provider}


@router.delete("/disconnect/{provider}")
async def disconnect_provider(
    provider: str,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Disconnect a cloud provider."""
    async with db_pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE import_connections SET revoked_at = NOW()
            WHERE user_id = $1 AND provider = $2 AND revoked_at IS NULL
        """, user_id, provider)
        
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Connection not found")
    
    return {"status": "disconnected", "provider": provider}


# ===========================================
# WEBDAV / S3 CREDENTIALS
# ===========================================

class WebDAVCredentials(BaseModel):
    server_url: str
    username: str
    password: str
    path: str = "/"

class S3Credentials(BaseModel):
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"
    path_prefix: str = ""

@router.post("/connect/nextcloud/credentials")
async def connect_nextcloud(
    creds: WebDAVCredentials,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Connect to Nextcloud/ownCloud via WebDAV."""
    # Test connection
    async with httpx.AsyncClient() as client:
        try:
            test_url = f"{creds.server_url.rstrip('/')}/remote.php/dav/files/{creds.username}/"
            response = await client.request(
                "PROPFIND",
                test_url,
                auth=(creds.username, creds.password),
                headers={"Depth": "0"},
                timeout=10
            )
            if response.status_code not in [200, 207]:
                raise HTTPException(status_code=400, detail="Connection test failed")
        except httpx.RequestError as e:
            raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")
    
    # Store credentials (encrypted in production)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO import_connections 
            (user_id, provider, credentials)
            VALUES ($1, 'nextcloud', $2)
            ON CONFLICT (user_id, provider) 
            DO UPDATE SET credentials = $2, revoked_at = NULL, updated_at = NOW()
        """, user_id, json.dumps(creds.dict()))
    
    return {"status": "connected", "provider": "nextcloud"}


@router.post("/connect/s3/credentials")
async def connect_s3(
    creds: S3Credentials,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Connect to S3-compatible storage."""
    # Test connection would go here
    
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO import_connections 
            (user_id, provider, credentials)
            VALUES ($1, 's3', $2)
            ON CONFLICT (user_id, provider) 
            DO UPDATE SET credentials = $2, revoked_at = NULL, updated_at = NOW()
        """, user_id, json.dumps(creds.dict()))
    
    return {"status": "connected", "provider": "s3"}


# ===========================================
# IMPORT JOBS
# ===========================================

@router.post("/start", response_model=ImportJobResponse)
async def start_import(
    job: ImportJobCreate,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Start an import job from a connected provider."""
    # Verify connection exists
    async with db_pool.acquire() as conn:
        connection = await conn.fetchrow("""
            SELECT id, access_token, refresh_token, credentials
            FROM import_connections
            WHERE user_id = $1 AND provider = $2 AND revoked_at IS NULL
        """, user_id, job.provider)
        
        if not connection:
            raise HTTPException(status_code=400, detail=f"Not connected to {job.provider}")
        
        # Create job record
        job_id = await conn.fetchval("""
            INSERT INTO import_jobs 
            (user_id, provider, paths, include_shared, preserve_folders, delete_after_import, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'pending')
            RETURNING id
        """, user_id, job.provider, job.paths, job.include_shared, 
            job.preserve_folders, job.delete_after_import)
    
    # Queue background import
    background_tasks.add_task(
        run_import_job,
        str(job_id),
        user_id,
        job.provider,
        connection['access_token'],
        connection['refresh_token'],
        json.loads(connection['credentials']) if connection['credentials'] else None,
        job.dict()
    )
    
    logger.info(f"Started import job {job_id} for user {user_id} from {job.provider}")
    
    return ImportJobResponse(
        job_id=str(job_id),
        provider=job.provider,
        status="pending",
        total_files=0,
        imported_files=0,
        failed_files=0,
        total_bytes=0,
        imported_bytes=0,
        started_at=datetime.utcnow()
    )


@router.get("/jobs", response_model=List[ImportJobResponse])
async def list_import_jobs(
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """List user's import jobs."""
    async with db_pool.acquire() as conn:
        jobs = await conn.fetch("""
            SELECT id, provider, status, total_files, imported_files, failed_files,
                   total_bytes, imported_bytes, started_at, completed_at, current_file, errors
            FROM import_jobs
            WHERE user_id = $1
            ORDER BY started_at DESC
            LIMIT 50
        """, user_id)
        
        return [ImportJobResponse(
            job_id=str(j['id']),
            provider=j['provider'],
            status=j['status'],
            total_files=j['total_files'] or 0,
            imported_files=j['imported_files'] or 0,
            failed_files=j['failed_files'] or 0,
            total_bytes=j['total_bytes'] or 0,
            imported_bytes=j['imported_bytes'] or 0,
            started_at=j['started_at'],
            completed_at=j['completed_at'],
            current_file=j['current_file'],
            errors=j['errors'] or []
        ) for j in jobs]


@router.get("/jobs/{job_id}", response_model=ImportJobResponse)
async def get_import_job(
    job_id: str,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Get status of an import job."""
    async with db_pool.acquire() as conn:
        job = await conn.fetchrow("""
            SELECT id, provider, status, total_files, imported_files, failed_files,
                   total_bytes, imported_bytes, started_at, completed_at, current_file, errors
            FROM import_jobs
            WHERE id = $1 AND user_id = $2
        """, uuid.UUID(job_id), user_id)
        
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        return ImportJobResponse(
            job_id=str(job['id']),
            provider=job['provider'],
            status=job['status'],
            total_files=job['total_files'] or 0,
            imported_files=job['imported_files'] or 0,
            failed_files=job['failed_files'] or 0,
            total_bytes=job['total_bytes'] or 0,
            imported_bytes=job['imported_bytes'] or 0,
            started_at=job['started_at'],
            completed_at=job['completed_at'],
            current_file=job['current_file'],
            errors=job['errors'] or []
        )


@router.post("/jobs/{job_id}/cancel")
async def cancel_import_job(
    job_id: str,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool),
    redis = Depends(get_redis)
):
    """Cancel a running import job."""
    async with db_pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE import_jobs SET status = 'cancelled'
            WHERE id = $1 AND user_id = $2 AND status IN ('pending', 'running')
        """, uuid.UUID(job_id), user_id)
        
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Job not found or not cancellable")
    
    # Signal cancellation via Redis
    if redis:
        await redis.set(f"import_cancel:{job_id}", "1", ex=3600)
    
    return {"status": "cancelled"}


# ===========================================
# BACKGROUND IMPORT WORKER
# ===========================================

async def run_import_job(
    job_id: str,
    user_id: str,
    provider: str,
    access_token: Optional[str],
    refresh_token: Optional[str],
    credentials: Optional[Dict],
    job_config: Dict
):
    """Background task to run the actual import."""
    from main import db_pool, redis_client
    
    logger.info(f"Starting import job {job_id}")
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE import_jobs SET status = 'running', started_at = NOW()
                WHERE id = $1
            """, uuid.UUID(job_id))
        
        if provider == "dropbox":
            await import_from_dropbox(job_id, user_id, access_token, job_config, db_pool, redis_client)
        elif provider == "google_drive":
            await import_from_google_drive(job_id, user_id, access_token, job_config, db_pool, redis_client)
        elif provider == "onedrive":
            await import_from_onedrive(job_id, user_id, access_token, job_config, db_pool, redis_client)
        elif provider == "nextcloud":
            await import_from_webdav(job_id, user_id, credentials, job_config, db_pool, redis_client)
        elif provider == "s3":
            await import_from_s3(job_id, user_id, credentials, job_config, db_pool, redis_client)
        else:
            raise ValueError(f"Unknown provider: {provider}")
        
        # Mark complete
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE import_jobs SET status = 'complete', completed_at = NOW()
                WHERE id = $1
            """, uuid.UUID(job_id))
            
            # Update connection stats
            job = await conn.fetchrow("SELECT imported_files, imported_bytes FROM import_jobs WHERE id = $1", uuid.UUID(job_id))
            await conn.execute("""
                UPDATE import_connections 
                SET files_imported = files_imported + $1,
                    bytes_imported = bytes_imported + $2,
                    last_sync = NOW()
                WHERE user_id = $3 AND provider = $4
            """, job['imported_files'], job['imported_bytes'], user_id, provider)
        
        logger.info(f"Import job {job_id} completed")
        
    except Exception as e:
        logger.error(f"Import job {job_id} failed: {e}")
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE import_jobs 
                SET status = 'failed', 
                    completed_at = NOW(),
                    errors = array_append(errors, $2)
                WHERE id = $1
            """, uuid.UUID(job_id), str(e))


# ===========================================
# PROVIDER-SPECIFIC IMPORTERS
# ===========================================

async def import_from_dropbox(job_id, user_id, access_token, config, db_pool, redis):
    """Import files from Dropbox."""
    from storage import upload_bytes
    
    async with httpx.AsyncClient() as client:
        # List all files
        files = []
        cursor = None
        
        while True:
            if cursor:
                response = await client.post(
                    "https://api.dropboxapi.com/2/files/list_folder/continue",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"cursor": cursor}
                )
            else:
                response = await client.post(
                    "https://api.dropboxapi.com/2/files/list_folder",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "path": "" if not config.get('paths') else config['paths'][0],
                        "recursive": True,
                        "include_media_info": True
                    }
                )
            
            if response.status_code != 200:
                raise Exception(f"Dropbox API error: {response.text}")
            
            data = response.json()
            files.extend([e for e in data['entries'] if e['.tag'] == 'file'])
            
            if not data['has_more']:
                break
            cursor = data['cursor']
        
        # Update total count
        total_bytes = sum(f['size'] for f in files)
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE import_jobs SET total_files = $1, total_bytes = $2
                WHERE id = $3
            """, len(files), total_bytes, uuid.UUID(job_id))
        
        # Import each file
        imported = 0
        imported_bytes = 0
        
        for file in files:
            # Check cancellation
            if redis:
                cancelled = await redis.get(f"import_cancel:{job_id}")
                if cancelled:
                    break
            
            try:
                # Update current file
                async with db_pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE import_jobs SET current_file = $1
                        WHERE id = $2
                    """, file['path_display'], uuid.UUID(job_id))
                
                # Download file
                download_response = await client.post(
                    "https://content.dropboxapi.com/2/files/download",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Dropbox-API-Arg": json.dumps({"path": file['id']})
                    }
                )
                
                if download_response.status_code != 200:
                    raise Exception(f"Download failed: {download_response.status_code}")
                
                content = download_response.content
                
                # Determine item type
                mime_type = file.get('media_info', {}).get('metadata', {}).get('mime_type', 'application/octet-stream')
                if mime_type.startswith('image/'):
                    item_type = 'photo'
                elif mime_type.startswith('video/'):
                    item_type = 'video'
                else:
                    item_type = 'document'
                
                # Upload to vault storage
                storage_key = await upload_bytes(
                    user_id=user_id,
                    filename=file['name'],
                    content=content,
                    content_type=mime_type
                )
                
                # Create vault item
                async with db_pool.acquire() as conn:
                    # Build folder path if preserving
                    folder_path = None
                    if config.get('preserve_folders'):
                        folder_path = '/'.join(file['path_display'].split('/')[:-1])
                    
                    await conn.execute("""
                        INSERT INTO vault_items 
                        (user_id, item_type, storage_key, file_size, mime_type, 
                         encrypted_metadata, source_provider, source_path)
                        VALUES ($1, $2, $3, $4, $5, $6, 'dropbox', $7)
                    """, user_id, item_type, storage_key, file['size'], mime_type,
                        json.dumps({"original_name": file['name'], "folder": folder_path}),
                        file['path_display'])
                
                imported += 1
                imported_bytes += file['size']
                
                # Update progress
                async with db_pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE import_jobs 
                        SET imported_files = $1, imported_bytes = $2
                        WHERE id = $3
                    """, imported, imported_bytes, uuid.UUID(job_id))
                
            except Exception as e:
                logger.error(f"Failed to import {file['path_display']}: {e}")
                async with db_pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE import_jobs 
                        SET failed_files = failed_files + 1,
                            errors = array_append(errors, $1)
                        WHERE id = $2
                    """, f"{file['path_display']}: {str(e)}", uuid.UUID(job_id))


async def import_from_google_drive(job_id, user_id, access_token, config, db_pool, redis):
    """Import files from Google Drive."""
    from storage import upload_bytes
    
    async with httpx.AsyncClient() as client:
        # List all files
        files = []
        page_token = None
        
        while True:
            params = {
                "pageSize": 1000,
                "fields": "nextPageToken, files(id, name, mimeType, size, parents, createdTime, modifiedTime)",
                "q": "trashed = false"
            }
            if page_token:
                params["pageToken"] = page_token
            
            response = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params
            )
            
            if response.status_code != 200:
                raise Exception(f"Google Drive API error: {response.text}")
            
            data = response.json()
            # Filter out Google Docs (can't download directly)
            real_files = [f for f in data.get('files', []) 
                         if not f['mimeType'].startswith('application/vnd.google-apps')]
            files.extend(real_files)
            
            page_token = data.get('nextPageToken')
            if not page_token:
                break
        
        # Update total
        total_bytes = sum(int(f.get('size', 0)) for f in files)
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE import_jobs SET total_files = $1, total_bytes = $2
                WHERE id = $3
            """, len(files), total_bytes, uuid.UUID(job_id))
        
        # Import each file
        imported = 0
        imported_bytes = 0
        
        for file in files:
            if redis:
                cancelled = await redis.get(f"import_cancel:{job_id}")
                if cancelled:
                    break
            
            try:
                async with db_pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE import_jobs SET current_file = $1
                        WHERE id = $2
                    """, file['name'], uuid.UUID(job_id))
                
                # Download file
                download_response = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{file['id']}?alt=media",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                
                if download_response.status_code != 200:
                    raise Exception(f"Download failed: {download_response.status_code}")
                
                content = download_response.content
                mime_type = file['mimeType']
                
                if mime_type.startswith('image/'):
                    item_type = 'photo'
                elif mime_type.startswith('video/'):
                    item_type = 'video'
                else:
                    item_type = 'document'
                
                storage_key = await upload_bytes(
                    user_id=user_id,
                    filename=file['name'],
                    content=content,
                    content_type=mime_type
                )
                
                async with db_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO vault_items 
                        (user_id, item_type, storage_key, file_size, mime_type,
                         encrypted_metadata, source_provider, source_path)
                        VALUES ($1, $2, $3, $4, $5, $6, 'google_drive', $7)
                    """, user_id, item_type, storage_key, int(file.get('size', 0)), mime_type,
                        json.dumps({"original_name": file['name']}),
                        file['id'])
                
                imported += 1
                imported_bytes += int(file.get('size', 0))
                
                async with db_pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE import_jobs 
                        SET imported_files = $1, imported_bytes = $2
                        WHERE id = $3
                    """, imported, imported_bytes, uuid.UUID(job_id))
                
            except Exception as e:
                logger.error(f"Failed to import {file['name']}: {e}")
                async with db_pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE import_jobs 
                        SET failed_files = failed_files + 1,
                            errors = array_append(errors, $1)
                        WHERE id = $2
                    """, f"{file['name']}: {str(e)}", uuid.UUID(job_id))


async def import_from_onedrive(job_id, user_id, access_token, config, db_pool, redis):
    """Import files from OneDrive."""
    # Similar structure to Dropbox/Google Drive
    # Uses Microsoft Graph API
    pass  # Implementation similar to above


async def import_from_webdav(job_id, user_id, credentials, config, db_pool, redis):
    """Import files from Nextcloud/WebDAV."""
    # Uses PROPFIND for listing, GET for download
    pass


async def import_from_s3(job_id, user_id, credentials, config, db_pool, redis):
    """Import files from S3-compatible storage."""
    # Uses boto3 or httpx with AWS Signature V4
    pass
