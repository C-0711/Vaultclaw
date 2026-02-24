"""
PROJEKT GENESIS Sprint 4: MCP Authentication
Token-based authentication for MCP endpoints
"""

from fastapi import HTTPException, Header, Depends
from typing import Optional
import asyncpg
import hashlib
import secrets
from datetime import datetime, timedelta

# Global db pool
_db_pool: Optional[asyncpg.Pool] = None

def init_mcp_auth(db_pool: asyncpg.Pool):
    """Initialize auth with database pool."""
    global _db_pool
    _db_pool = db_pool


class MCPToken:
    """MCP access token."""
    
    def __init__(
        self,
        token_id: str,
        user_id: str,
        space_id: str,
        scopes: list,
        expires_at: Optional[datetime] = None
    ):
        self.token_id = token_id
        self.user_id = user_id
        self.space_id = space_id
        self.scopes = scopes
        self.expires_at = expires_at
    
    def has_scope(self, scope: str) -> bool:
        """Check if token has a scope."""
        return "*" in self.scopes or scope in self.scopes
    
    @property
    def is_expired(self) -> bool:
        """Check if token is expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at


async def create_mcp_token(
    user_id: str,
    space_id: str,
    scopes: list = None,
    expires_days: int = 30,
    name: str = "MCP Token"
) -> tuple[str, str]:
    """
    Create a new MCP access token.
    
    Returns (token_id, raw_token).
    The raw_token is only returned once - store it securely!
    """
    if not _db_pool:
        raise Exception("Database not initialized")
    
    # Generate token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    
    scopes = scopes or ["read", "write"]
    expires_at = datetime.utcnow() + timedelta(days=expires_days) if expires_days else None
    
    async with _db_pool.acquire() as conn:
        token_id = await conn.fetchval('''
            INSERT INTO vault_access_tokens 
            (user_id, space_id, token_hash, name, scopes, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        ''', user_id, space_id, token_hash, name, scopes, expires_at)
    
    return str(token_id), raw_token


async def validate_mcp_token(
    raw_token: str,
    space_id: str
) -> MCPToken:
    """
    Validate an MCP token.
    
    Raises HTTPException if invalid.
    """
    if not _db_pool:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    
    async with _db_pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT id, user_id, space_id, scopes, expires_at, revoked_at
            FROM vault_access_tokens
            WHERE token_hash = $1
        ''', token_hash)
    
    if not row:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    if row['revoked_at']:
        raise HTTPException(status_code=401, detail="Token revoked")
    
    if row['expires_at'] and datetime.utcnow() > row['expires_at']:
        raise HTTPException(status_code=401, detail="Token expired")
    
    if row['space_id'] and row['space_id'] != space_id:
        raise HTTPException(status_code=403, detail="Token not valid for this space")
    
    return MCPToken(
        token_id=str(row['id']),
        user_id=str(row['user_id']),
        space_id=row['space_id'],
        scopes=row['scopes'] or [],
        expires_at=row['expires_at']
    )


async def revoke_mcp_token(token_id: str, user_id: str):
    """Revoke an MCP token."""
    if not _db_pool:
        raise Exception("Database not initialized")
    
    async with _db_pool.acquire() as conn:
        result = await conn.execute('''
            UPDATE vault_access_tokens
            SET revoked_at = NOW()
            WHERE id = $1 AND user_id = $2 AND revoked_at IS NULL
        ''', token_id, user_id)
    
    return "UPDATE" in result


# FastAPI dependency
async def require_mcp_token(
    space_id: str,
    x_vault_token: str = Header(None, alias="X-Vault-Token")
) -> MCPToken:
    """
    FastAPI dependency to require valid MCP token.
    
    Usage:
        @router.get("/protected")
        async def protected_route(token: MCPToken = Depends(require_mcp_token)):
            ...
    """
    if not x_vault_token:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Vault-Token header",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    return await validate_mcp_token(x_vault_token, space_id)


def require_scope(scope: str):
    """
    Create a dependency that requires a specific scope.
    
    Usage:
        @router.post("/write")
        async def write_route(token: MCPToken = Depends(require_scope("write"))):
            ...
    """
    async def check_scope(token: MCPToken = Depends(require_mcp_token)):
        if not token.has_scope(scope):
            raise HTTPException(
                status_code=403,
                detail=f"Token missing required scope: {scope}"
            )
        return token
    
    return check_scope
