"""
Storage Quotas API for 0711-Vault
Per-tenant and per-user storage limits
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

router = APIRouter(prefix="/quotas", tags=["Storage Quotas"])

# Models
class QuotaConfig(BaseModel):
    tenant_id: str
    storage_limit_bytes: int  # -1 for unlimited
    file_count_limit: int  # -1 for unlimited
    max_file_size_bytes: int
    allowed_mime_types: list[str] = ['*/*']

class QuotaUsage(BaseModel):
    tenant_id: str
    used_bytes: int
    file_count: int
    percentage_used: float
    limit_bytes: int
    remaining_bytes: int

class UserQuota(BaseModel):
    user_id: str
    used_bytes: int
    file_count: int
    personal_limit_bytes: Optional[int] = None

# In-memory storage (replace with DB)
tenant_quotas: dict = {
    'default': QuotaConfig(
        tenant_id='default',
        storage_limit_bytes=10 * 1024**3,  # 10GB
        file_count_limit=10000,
        max_file_size_bytes=100 * 1024**2,  # 100MB
    ),
    'bosch': QuotaConfig(
        tenant_id='bosch',
        storage_limit_bytes=100 * 1024**3,  # 100GB
        file_count_limit=100000,
        max_file_size_bytes=500 * 1024**2,  # 500MB
    ),
}

usage_data: dict = {}


@router.get("/config/{tenant_id}", response_model=QuotaConfig)
async def get_quota_config(tenant_id: str):
    """Get quota configuration for tenant"""
    config = tenant_quotas.get(tenant_id) or tenant_quotas.get('default')
    if not config:
        raise HTTPException(status_code=404, detail="Quota config not found")
    return config


@router.put("/config/{tenant_id}", response_model=QuotaConfig)
async def update_quota_config(tenant_id: str, config: QuotaConfig):
    """Update quota configuration (admin only)"""
    config.tenant_id = tenant_id
    tenant_quotas[tenant_id] = config
    return config


@router.get("/usage/{tenant_id}", response_model=QuotaUsage)
async def get_usage(tenant_id: str):
    """Get current storage usage for tenant"""
    # TODO: Calculate from actual storage
    usage = usage_data.get(tenant_id, {'used_bytes': 0, 'file_count': 0})
    config = tenant_quotas.get(tenant_id) or tenant_quotas.get('default')
    
    limit = config.storage_limit_bytes if config else 10 * 1024**3
    used = usage.get('used_bytes', 0)
    
    return QuotaUsage(
        tenant_id=tenant_id,
        used_bytes=used,
        file_count=usage.get('file_count', 0),
        percentage_used=(used / limit * 100) if limit > 0 else 0,
        limit_bytes=limit,
        remaining_bytes=max(0, limit - used),
    )


@router.get("/check/{tenant_id}")
async def check_quota(tenant_id: str, file_size: int):
    """Check if upload would exceed quota"""
    usage = await get_usage(tenant_id)
    config = tenant_quotas.get(tenant_id) or tenant_quotas.get('default')
    
    errors = []
    
    if config.max_file_size_bytes > 0 and file_size > config.max_file_size_bytes:
        errors.append(f"File exceeds max size ({config.max_file_size_bytes} bytes)")
    
    if config.storage_limit_bytes > 0:
        if usage.used_bytes + file_size > config.storage_limit_bytes:
            errors.append("Upload would exceed storage quota")
    
    if config.file_count_limit > 0:
        if usage.file_count >= config.file_count_limit:
            errors.append("File count limit reached")
    
    return {
        "allowed": len(errors) == 0,
        "errors": errors,
        "remaining_bytes": usage.remaining_bytes,
    }


@router.post("/record/{tenant_id}")
async def record_usage(tenant_id: str, bytes_added: int, files_added: int = 1):
    """Record storage usage (called after upload)"""
    if tenant_id not in usage_data:
        usage_data[tenant_id] = {'used_bytes': 0, 'file_count': 0}
    
    usage_data[tenant_id]['used_bytes'] += bytes_added
    usage_data[tenant_id]['file_count'] += files_added
    
    return {"status": "recorded"}
