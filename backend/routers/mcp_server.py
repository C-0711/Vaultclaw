"""
PROJEKT GENESIS Sprint 4: Vault MCP Server
Model Context Protocol server for 0711 Vault spaces

Exposes Git spaces as:
- resources:// - versioned files
- tools:// - Git operations (commit, branch, merge)
- prompts:// - Container data templates
"""

import asyncio
import json
import hashlib
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime
import asyncpg


class MCPMessageType(Enum):
    """MCP message types."""
    INITIALIZE = "initialize"
    INITIALIZED = "initialized"
    RESOURCES_LIST = "resources/list"
    RESOURCES_READ = "resources/read"
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"
    PROMPTS_LIST = "prompts/list"
    PROMPTS_GET = "prompts/get"
    ERROR = "error"


@dataclass
class MCPResource:
    """MCP Resource definition."""
    uri: str
    name: str
    description: str
    mimeType: str = "text/plain"
    
    def to_dict(self):
        return asdict(self)


@dataclass
class MCPTool:
    """MCP Tool definition."""
    name: str
    description: str
    inputSchema: Dict[str, Any]
    
    def to_dict(self):
        return asdict(self)


@dataclass
class MCPPrompt:
    """MCP Prompt definition."""
    name: str
    description: str
    arguments: List[Dict[str, Any]]
    
    def to_dict(self):
        return asdict(self)


class VaultMCPServer:
    """
    MCP Server for a Vault space.
    
    Provides:
    - File resources (versioned)
    - Git operation tools
    - Container-based prompts
    """
    
    def __init__(
        self,
        db_pool: asyncpg.Pool,
        space_id: str,
        branch: str = "main"
    ):
        self.db_pool = db_pool
        self.space_id = space_id
        self.branch = branch
        self.server_info = {
            "name": f"vault-{space_id[:8]}",
            "version": "1.0.0",
            "protocolVersion": "2024-11-05"
        }
    
    # ==================== RESOURCES ====================
    
    async def list_resources(self) -> List[MCPResource]:
        """List all files in the space as MCP resources."""
        async with self.db_pool.acquire() as conn:
            # Get latest tree for branch
            rows = await conn.fetch('''
                SELECT 
                    t.path,
                    t.entry_type,
                    fv.content_type,
                    fv.size_bytes,
                    s.message as last_commit
                FROM vault_trees t
                JOIN vault_snapshots s ON t.snapshot_id = s.id
                JOIN vault_branches b ON s.branch_id = b.id
                LEFT JOIN vault_file_versions fv ON t.file_version_id = fv.id
                WHERE b.space_id = $1 
                AND b.name = $2
                AND t.entry_type = 'file'
                ORDER BY t.path
            ''', self.space_id, self.branch)
            
            resources = []
            for row in rows:
                mime = row['content_type'] or 'application/octet-stream'
                resources.append(MCPResource(
                    uri=f"vault://{self.space_id}/{self.branch}/{row['path']}",
                    name=row['path'],
                    description=f"Size: {row['size_bytes'] or 0} bytes. Last: {row['last_commit'] or 'N/A'}",
                    mimeType=mime
                ))
            
            return resources
    
    async def read_resource(self, uri: str) -> Dict[str, Any]:
        """Read a resource by URI."""
        # Parse URI: vault://{space_id}/{branch}/{path}
        parts = uri.replace("vault://", "").split("/", 2)
        if len(parts) < 3:
            return {"error": f"Invalid URI: {uri}"}
        
        space_id, branch, path = parts[0], parts[1], parts[2]
        
        async with self.db_pool.acquire() as conn:
            # Get file content
            row = await conn.fetchrow('''
                SELECT 
                    fv.content,
                    fv.content_type,
                    fv.content_hash
                FROM vault_trees t
                JOIN vault_snapshots s ON t.snapshot_id = s.id
                JOIN vault_branches b ON s.branch_id = b.id
                JOIN vault_file_versions fv ON t.file_version_id = fv.id
                WHERE b.space_id = $1 
                AND b.name = $2
                AND t.path = $3
                AND t.entry_type = 'file'
            ''', space_id, branch, path)
            
            if not row:
                return {"error": f"Resource not found: {uri}"}
            
            content = row['content']
            if isinstance(content, bytes):
                try:
                    content = content.decode('utf-8')
                except UnicodeDecodeError:
                    import base64
                    content = base64.b64encode(content).decode()
            
            return {
                "uri": uri,
                "mimeType": row['content_type'] or "text/plain",
                "text": content
            }
    
    # ==================== TOOLS ====================
    
    def list_tools(self) -> List[MCPTool]:
        """List available Git operation tools."""
        return [
            MCPTool(
                name="vault_commit",
                description="Commit changes to the vault space",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "Commit message"},
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "content": {"type": "string"},
                                    "action": {"type": "string", "enum": ["add", "modify", "delete"]}
                                }
                            },
                            "description": "Files to commit"
                        }
                    },
                    "required": ["message", "files"]
                }
            ),
            MCPTool(
                name="vault_branch",
                description="Create a new branch",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Branch name"},
                        "from_branch": {"type": "string", "description": "Source branch", "default": "main"}
                    },
                    "required": ["name"]
                }
            ),
            MCPTool(
                name="vault_search",
                description="Search files in the vault",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "path_filter": {"type": "string", "description": "Filter by path pattern"}
                    },
                    "required": ["query"]
                }
            ),
            MCPTool(
                name="vault_history",
                description="Get commit history",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path (optional)"},
                        "limit": {"type": "integer", "description": "Max commits", "default": 10}
                    }
                }
            ),
            MCPTool(
                name="vault_diff",
                description="Compare two commits or branches",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "from_ref": {"type": "string", "description": "Source ref (commit/branch)"},
                        "to_ref": {"type": "string", "description": "Target ref (commit/branch)"}
                    },
                    "required": ["from_ref", "to_ref"]
                }
            )
        ]
    
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool call."""
        try:
            if name == "vault_commit":
                return await self._tool_commit(arguments)
            elif name == "vault_branch":
                return await self._tool_branch(arguments)
            elif name == "vault_search":
                return await self._tool_search(arguments)
            elif name == "vault_history":
                return await self._tool_history(arguments)
            elif name == "vault_diff":
                return await self._tool_diff(arguments)
            else:
                return {"error": f"Unknown tool: {name}"}
        except Exception as e:
            return {"error": str(e)}
    
    async def _tool_commit(self, args: Dict) -> Dict:
        """Create a commit."""
        message = args.get("message", "MCP commit")
        files = args.get("files", [])
        
        async with self.db_pool.acquire() as conn:
            # Get branch
            branch = await conn.fetchrow(
                'SELECT id FROM vault_branches WHERE space_id = $1 AND name = $2',
                self.space_id, self.branch
            )
            if not branch:
                return {"error": f"Branch not found: {self.branch}"}
            
            # Get parent snapshot
            parent = await conn.fetchrow('''
                SELECT id FROM vault_snapshots 
                WHERE branch_id = $1 
                ORDER BY created_at DESC LIMIT 1
            ''', branch['id'])
            
            # Create snapshot
            snapshot_id = await conn.fetchval('''
                INSERT INTO vault_snapshots (branch_id, parent_id, message, author)
                VALUES ($1, $2, $3, 'mcp-client')
                RETURNING id
            ''', branch['id'], parent['id'] if parent else None, message)
            
            # Process files
            for file in files:
                path = file.get("path")
                content = file.get("content", "")
                action = file.get("action", "add")
                
                if action == "delete":
                    # Mark as deleted in tree
                    continue
                
                # Create file version
                content_bytes = content.encode('utf-8') if isinstance(content, str) else content
                content_hash = hashlib.sha256(content_bytes).hexdigest()
                
                fv_id = await conn.fetchval('''
                    INSERT INTO vault_file_versions (space_id, path, content, content_hash, size_bytes)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id
                ''', self.space_id, path, content_bytes, content_hash, len(content_bytes))
                
                # Add to tree
                await conn.execute('''
                    INSERT INTO vault_trees (snapshot_id, path, entry_type, file_version_id)
                    VALUES ($1, $2, 'file', $3)
                ''', snapshot_id, path, fv_id)
            
            return {
                "success": True,
                "snapshot_id": str(snapshot_id),
                "message": message,
                "files_changed": len(files)
            }
    
    async def _tool_branch(self, args: Dict) -> Dict:
        """Create a branch."""
        name = args.get("name")
        from_branch = args.get("from_branch", "main")
        
        async with self.db_pool.acquire() as conn:
            # Get source branch HEAD
            source = await conn.fetchrow('''
                SELECT b.id, s.id as head_id
                FROM vault_branches b
                LEFT JOIN vault_snapshots s ON s.branch_id = b.id
                WHERE b.space_id = $1 AND b.name = $2
                ORDER BY s.created_at DESC
                LIMIT 1
            ''', self.space_id, from_branch)
            
            if not source:
                return {"error": f"Source branch not found: {from_branch}"}
            
            # Create new branch
            branch_id = await conn.fetchval('''
                INSERT INTO vault_branches (space_id, name, head_snapshot_id)
                VALUES ($1, $2, $3)
                RETURNING id
            ''', self.space_id, name, source['head_id'])
            
            return {
                "success": True,
                "branch_id": str(branch_id),
                "name": name,
                "from": from_branch
            }
    
    async def _tool_search(self, args: Dict) -> Dict:
        """Search files."""
        query = args.get("query", "")
        path_filter = args.get("path_filter", "")
        
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT DISTINCT t.path, fv.content_type
                FROM vault_trees t
                JOIN vault_snapshots s ON t.snapshot_id = s.id
                JOIN vault_branches b ON s.branch_id = b.id
                LEFT JOIN vault_file_versions fv ON t.file_version_id = fv.id
                WHERE b.space_id = $1 
                AND b.name = $2
                AND (t.path ILIKE $3 OR $3 = '')
                LIMIT 50
            ''', self.space_id, self.branch, f"%{path_filter}%" if path_filter else "")
            
            return {
                "results": [
                    {"path": row['path'], "type": row['content_type']}
                    for row in rows
                ],
                "count": len(rows)
            }
    
    async def _tool_history(self, args: Dict) -> Dict:
        """Get commit history."""
        path = args.get("path")
        limit = args.get("limit", 10)
        
        async with self.db_pool.acquire() as conn:
            if path:
                rows = await conn.fetch('''
                    SELECT s.id, s.message, s.author, s.created_at
                    FROM vault_snapshots s
                    JOIN vault_branches b ON s.branch_id = b.id
                    JOIN vault_trees t ON t.snapshot_id = s.id
                    WHERE b.space_id = $1 AND b.name = $2 AND t.path = $3
                    ORDER BY s.created_at DESC
                    LIMIT $4
                ''', self.space_id, self.branch, path, limit)
            else:
                rows = await conn.fetch('''
                    SELECT s.id, s.message, s.author, s.created_at
                    FROM vault_snapshots s
                    JOIN vault_branches b ON s.branch_id = b.id
                    WHERE b.space_id = $1 AND b.name = $2
                    ORDER BY s.created_at DESC
                    LIMIT $3
                ''', self.space_id, self.branch, limit)
            
            return {
                "commits": [
                    {
                        "id": str(row['id']),
                        "message": row['message'],
                        "author": row['author'],
                        "date": row['created_at'].isoformat() if row['created_at'] else None
                    }
                    for row in rows
                ]
            }
    
    async def _tool_diff(self, args: Dict) -> Dict:
        """Compare refs."""
        from_ref = args.get("from_ref")
        to_ref = args.get("to_ref")
        
        # Simplified diff - just list changed files
        return {
            "from": from_ref,
            "to": to_ref,
            "changes": [],
            "note": "Full diff implementation pending"
        }
    
    # ==================== PROMPTS ====================
    
    async def list_prompts(self) -> List[MCPPrompt]:
        """List available prompts from Container data."""
        return [
            MCPPrompt(
                name="analyze_document",
                description="Analyze a document in the vault",
                arguments=[
                    {"name": "path", "description": "File path to analyze", "required": True}
                ]
            ),
            MCPPrompt(
                name="summarize_changes",
                description="Summarize recent changes in the vault",
                arguments=[
                    {"name": "since", "description": "Date or commit to start from", "required": False}
                ]
            ),
            MCPPrompt(
                name="compare_versions",
                description="Compare two versions of a file",
                arguments=[
                    {"name": "path", "description": "File path", "required": True},
                    {"name": "version1", "description": "First version/commit", "required": True},
                    {"name": "version2", "description": "Second version/commit", "required": True}
                ]
            ),
            MCPPrompt(
                name="extract_data",
                description="Extract structured data from documents",
                arguments=[
                    {"name": "schema", "description": "Target schema (e.g., ETIM-9.0)", "required": True},
                    {"name": "path_pattern", "description": "Files to process", "required": False}
                ]
            )
        ]
    
    async def get_prompt(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Get a prompt with filled arguments."""
        if name == "analyze_document":
            path = arguments.get("path", "")
            return {
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"Please analyze the document at vault://{self.space_id}/{self.branch}/{path}. Provide a summary, key points, and any structured data you can extract."
                        }
                    }
                ]
            }
        
        elif name == "summarize_changes":
            since = arguments.get("since", "last week")
            return {
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"Summarize all changes in the vault since {since}. Group by file type and highlight important modifications."
                        }
                    }
                ]
            }
        
        elif name == "compare_versions":
            path = arguments.get("path", "")
            v1 = arguments.get("version1", "")
            v2 = arguments.get("version2", "")
            return {
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"Compare versions {v1} and {v2} of the file {path}. Highlight additions, deletions, and modifications."
                        }
                    }
                ]
            }
        
        elif name == "extract_data":
            schema = arguments.get("schema", "generic")
            pattern = arguments.get("path_pattern", "*")
            return {
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"Extract structured data from files matching '{pattern}' according to the {schema} schema. Return JSON with all extracted fields."
                        }
                    }
                ]
            }
        
        return {"error": f"Unknown prompt: {name}"}
    
    # ==================== MESSAGE HANDLER ====================
    
    async def handle_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Handle an MCP message."""
        method = message.get("method", "")
        params = message.get("params", {})
        msg_id = message.get("id")
        
        try:
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "serverInfo": self.server_info,
                        "capabilities": {
                            "resources": {"listChanged": True},
                            "tools": {},
                            "prompts": {"listChanged": True}
                        }
                    }
                }
            
            elif method == "resources/list":
                resources = await self.list_resources()
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"resources": [r.to_dict() for r in resources]}
                }
            
            elif method == "resources/read":
                uri = params.get("uri", "")
                result = await self.read_resource(uri)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"contents": [result]}
                }
            
            elif method == "tools/list":
                tools = self.list_tools()
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": [t.to_dict() for t in tools]}
                }
            
            elif method == "tools/call":
                name = params.get("name", "")
                arguments = params.get("arguments", {})
                result = await self.call_tool(name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(result)}]}
                }
            
            elif method == "prompts/list":
                prompts = await self.list_prompts()
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"prompts": [p.to_dict() for p in prompts]}
                }
            
            elif method == "prompts/get":
                name = params.get("name", "")
                arguments = params.get("arguments", {})
                result = await self.get_prompt(name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": result
                }
            
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                }
                
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(e)}
            }
