"""
PROJEKT GENESIS Sprint 4: MCP API Routes
HTTP/WebSocket endpoints for MCP protocol
"""

from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
import asyncpg

# Import MCP server
import sys
sys.path.insert(0, "/home/christoph.bertsch/0711-Vault/backend/services/mcp")
from mcp_server import VaultMCPServer

router = APIRouter(prefix="/mcp", tags=["MCP"])

# Global db pool (set by init)
_db_pool: Optional[asyncpg.Pool] = None

def init_mcp_router(db_pool: asyncpg.Pool):
    """Initialize with database pool."""
    global _db_pool
    _db_pool = db_pool


# --- Pydantic Models ---

class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[int] = None
    method: str
    params: Dict[str, Any] = {}

class MCPConfigRequest(BaseModel):
    space_id: str
    branch: str = "main"
    name: Optional[str] = None

class MCPConfigResponse(BaseModel):
    config: Dict[str, Any]
    instructions: str


# --- HTTP Endpoints ---

@router.post("/{space_id}")
async def mcp_http_endpoint(
    space_id: str,
    request: MCPRequest,
    x_vault_token: str = Header(None, alias="X-Vault-Token"),
    x_branch: str = Header("main", alias="X-Branch")
):
    """
    HTTP endpoint for MCP protocol.
    
    Supports JSON-RPC over HTTP for simple integrations.
    """
    if not _db_pool:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    # Validate token (simplified - would check against vault_access_tokens)
    if not x_vault_token:
        raise HTTPException(status_code=401, detail="Missing X-Vault-Token header")
    
    # Create MCP server for this space
    server = VaultMCPServer(
        db_pool=_db_pool,
        space_id=space_id,
        branch=x_branch
    )
    
    # Handle message
    response = await server.handle_message(request.dict())
    
    return JSONResponse(content=response)


@router.websocket("/{space_id}/ws")
async def mcp_websocket_endpoint(
    websocket: WebSocket,
    space_id: str,
    token: str = None,
    branch: str = "main"
):
    """
    WebSocket endpoint for MCP protocol.
    
    Provides full bidirectional MCP communication.
    """
    await websocket.accept()
    
    if not _db_pool:
        await websocket.close(code=1011, reason="Database not initialized")
        return
    
    # Create MCP server
    server = VaultMCPServer(
        db_pool=_db_pool,
        space_id=space_id,
        branch=branch
    )
    
    try:
        while True:
            # Receive message
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # Handle message
            response = await server.handle_message(message)
            
            # Send response
            await websocket.send_text(json.dumps(response))
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.close(code=1011, reason=str(e))


@router.post("/config/generate", response_model=MCPConfigResponse)
async def generate_mcp_config(request: MCPConfigRequest):
    """
    Generate Claude Desktop config for a Vault space.
    
    Returns JSON config that can be added to claude_desktop_config.json
    """
    space_id = request.space_id
    branch = request.branch
    name = request.name or f"vault-{space_id[:8]}"
    
    # Generate config
    config = {
        "mcpServers": {
            name: {
                "command": "npx",
                "args": [
                    "-y",
                    "@anthropic/mcp-proxy",
                    "--url",
                    f"wss://api-vault.0711.io/mcp/{space_id}/ws?branch={branch}"
                ],
                "env": {
                    "VAULT_TOKEN": "${VAULT_TOKEN}"
                }
            }
        }
    }
    
    # Alternative: Direct HTTP config for MCP clients that support it
    http_config = {
        "mcpServers": {
            name: {
                "transport": "http",
                "url": f"https://api-vault.0711.io/mcp/{space_id}",
                "headers": {
                    "X-Vault-Token": "${VAULT_TOKEN}",
                    "X-Branch": branch
                }
            }
        }
    }
    
    instructions = f"""
## Claude Desktop Setup

1. Open Claude Desktop settings
2. Navigate to MCP Servers configuration
3. Add this config to your `claude_desktop_config.json`:

```json
{json.dumps(config, indent=2)}
```

4. Set the VAULT_TOKEN environment variable:
   - macOS/Linux: `export VAULT_TOKEN=your_token_here`
   - Windows: `set VAULT_TOKEN=your_token_here`

5. Restart Claude Desktop

## Alternative: HTTP Transport

If your MCP client supports HTTP transport:

```json
{json.dumps(http_config, indent=2)}
```

## Available Tools

Once connected, you'll have access to:
- `vault_commit` - Commit changes
- `vault_branch` - Create branches
- `vault_search` - Search files
- `vault_history` - View commit history
- `vault_diff` - Compare versions

## Available Resources

All files in your vault space will be available as MCP resources:
- `vault://{space_id}/{branch}/path/to/file`

## Available Prompts

- `analyze_document` - Analyze a document
- `summarize_changes` - Summarize recent changes
- `compare_versions` - Compare file versions
- `extract_data` - Extract structured data
"""
    
    return MCPConfigResponse(
        config=config,
        instructions=instructions
    )


@router.get("/{space_id}/info")
async def get_mcp_info(
    space_id: str,
    x_vault_token: str = Header(None, alias="X-Vault-Token"),
    x_branch: str = Header("main", alias="X-Branch")
):
    """
    Get MCP server info and capabilities for a space.
    """
    if not _db_pool:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    server = VaultMCPServer(
        db_pool=_db_pool,
        space_id=space_id,
        branch=x_branch
    )
    
    # Get resource count
    resources = await server.list_resources()
    tools = server.list_tools()
    prompts = await server.list_prompts()
    
    return {
        "serverInfo": server.server_info,
        "space_id": space_id,
        "branch": x_branch,
        "capabilities": {
            "resources": {
                "count": len(resources),
                "listChanged": True
            },
            "tools": {
                "count": len(tools),
                "available": [t.name for t in tools]
            },
            "prompts": {
                "count": len(prompts),
                "available": [p.name for p in prompts]
            }
        },
        "endpoints": {
            "http": f"/mcp/{space_id}",
            "websocket": f"/mcp/{space_id}/ws",
            "config": "/mcp/config/generate"
        }
    }


@router.get("/health")
async def mcp_health():
    """MCP service health check."""
    return {
        "status": "healthy" if _db_pool else "not_initialized",
        "protocol_version": "2024-11-05",
        "features": ["resources", "tools", "prompts", "websocket", "http"]
    }
