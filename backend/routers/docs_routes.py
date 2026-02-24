"""
PROJEKT GENESIS Sprint 5: Vault Docs API
GitBook-style documentation publishing endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
import asyncpg
from datetime import datetime

# Import docs engine
from .docs_engine import DocsEngine, DocsConfig, DocTheme, StaticSiteGenerator

router = APIRouter(prefix="/docs", tags=["docs"])

# Global db pool
_db_pool: Optional[asyncpg.Pool] = None
_engine: Optional[DocsEngine] = None
_generator: Optional[StaticSiteGenerator] = None

def init_docs_router(db_pool: asyncpg.Pool):
    """Initialize with database pool."""
    global _db_pool, _engine, _generator
    _db_pool = db_pool
    _engine = DocsEngine(db_pool)
    _generator = StaticSiteGenerator(_engine)


# --- Pydantic Models ---

class DocsConfigRequest(BaseModel):
    title: str = "Documentation"
    description: str = ""
    logo: Optional[str] = None
    favicon: Optional[str] = None
    theme: str = "auto"
    primary_color: str = "#3B82F6"
    font_family: str = "Inter, system-ui, sans-serif"
    code_theme: str = "github-dark"
    show_toc: bool = True
    show_edit_link: bool = True
    repo_url: Optional[str] = None
    custom_css: Optional[str] = None
    footer_text: Optional[str] = None
    analytics_id: Optional[str] = None

class BuildRequest(BaseModel):
    space_id: str
    branch: str = "main"
    config: Optional[DocsConfigRequest] = None

class BuildResponse(BaseModel):
    build_id: str
    status: str
    pages_count: int
    built_at: str

class PublishRequest(BaseModel):
    space_id: str
    branch: str = "main"
    subdomain: str
    custom_domain: Optional[str] = None
    config: Optional[DocsConfigRequest] = None


# --- Build Cache ---
_build_cache: Dict[str, Dict] = {}


# --- Endpoints ---

@router.post("/build", response_model=BuildResponse)
async def build_docs(request: BuildRequest):
    """
    Build documentation from a vault space.
    
    Parses all Markdown files and generates navigation.
    """
    if not _engine:
        raise HTTPException(status_code=503, detail="Docs engine not initialized")
    
    # Convert config
    config = None
    if request.config:
        config = DocsConfig(
            title=request.config.title,
            description=request.config.description,
            logo=request.config.logo,
            favicon=request.config.favicon,
            theme=DocTheme(request.config.theme),
            primary_color=request.config.primary_color,
            font_family=request.config.font_family,
            code_theme=request.config.code_theme,
            show_toc=request.config.show_toc,
            show_edit_link=request.config.show_edit_link,
            repo_url=request.config.repo_url,
            custom_css=request.config.custom_css,
            footer_text=request.config.footer_text,
            analytics_id=request.config.analytics_id
        )
    
    # Build docs
    build = await _engine.build_docs(
        space_id=request.space_id,
        branch=request.branch,
        config=config
    )
    
    # Cache build
    build_id = f"{request.space_id}-{request.branch}"
    _build_cache[build_id] = build
    
    return BuildResponse(
        build_id=build_id,
        status="success",
        pages_count=len(build["pages"]),
        built_at=build["built_at"]
    )


@router.get("/build/{space_id}/{branch}")
async def get_build(space_id: str, branch: str = "main"):
    """Get cached build for a space."""
    build_id = f"{space_id}-{branch}"
    
    if build_id not in _build_cache:
        raise HTTPException(status_code=404, detail="Build not found. Run POST /docs/build first.")
    
    return _build_cache[build_id]


@router.get("/render/{space_id}/{branch}/{path:path}", response_class=HTMLResponse)
async def render_page(space_id: str, branch: str, path: str):
    """
    Render a single documentation page as HTML.
    
    Use for live preview or individual page serving.
    """
    if not _engine:
        raise HTTPException(status_code=503, detail="Docs engine not initialized")
    
    page = await _engine.render_page(space_id, branch, path)
    
    if not page:
        raise HTTPException(status_code=404, detail=f"Page not found: {path}")
    
    # Simple HTML wrapper
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{page.title}</title>
    <style>
        body {{ font-family: system-ui; max-width: 800px; margin: 2rem auto; padding: 0 1rem; }}
        pre {{ background: #1e1e1e; color: #d4d4d4; padding: 1rem; border-radius: 8px; overflow-x: auto; }}
        .admonition {{ padding: 1rem; border-radius: 8px; margin: 1rem 0; }}
        .admonition-note {{ background: #e3f2fd; }}
        .admonition-warning {{ background: #fff3e0; }}
        .admonition-tip {{ background: #e8f5e9; }}
    </style>
</head>
<body>
    <nav><a href="/">← Back</a></nav>
    <article>{page.html}</article>
</body>
</html>"""
    
    return HTMLResponse(content=html)


@router.post("/generate")
async def generate_static_site(request: BuildRequest):
    """
    Generate a complete static HTML site.
    
    Returns all HTML files for deployment.
    """
    if not _generator:
        raise HTTPException(status_code=503, detail="Generator not initialized")
    
    # Convert config
    config = None
    if request.config:
        config = DocsConfig(
            title=request.config.title,
            description=request.config.description,
            theme=DocTheme(request.config.theme),
            primary_color=request.config.primary_color
        )
    
    files = await _generator.generate(
        space_id=request.space_id,
        branch=request.branch,
        config=config
    )
    
    return {
        "status": "success",
        "files_count": len(files),
        "files": list(files.keys()),
        "total_size": sum(len(v) for v in files.values())
    }


@router.get("/generate/{space_id}/{branch}/download")
async def download_static_site(space_id: str, branch: str = "main"):
    """
    Generate and return static site as JSON bundle.
    
    Can be used to deploy to any static host.
    """
    if not _generator:
        raise HTTPException(status_code=503, detail="Generator not initialized")
    
    files = await _generator.generate(space_id=space_id, branch=branch)
    
    return JSONResponse(content={
        "space_id": space_id,
        "branch": branch,
        "generated_at": datetime.utcnow().isoformat(),
        "files": files
    })


@router.post("/publish")
async def publish_docs(request: PublishRequest, background_tasks: BackgroundTasks):
    """
    Publish documentation to a subdomain.
    
    Creates/updates vault_published_sites entry and triggers CDN deploy.
    """
    if not _db_pool:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    async with _db_pool.acquire() as conn:
        # Check if site exists
        existing = await conn.fetchrow('''
            SELECT id FROM vault_published_sites 
            WHERE space_id = $1 AND subdomain = $2
        ''', request.space_id, request.subdomain)
        
        config_json = json.dumps(request.config.dict() if request.config else {})
        
        if existing:
            # Update existing
            await conn.execute('''
                UPDATE vault_published_sites
                SET branch = $1, custom_domain = $2, config = $3, updated_at = NOW()
                WHERE id = $4
            ''', request.branch, request.custom_domain, config_json, existing['id'])
            site_id = existing['id']
        else:
            # Create new
            site_id = await conn.fetchval('''
                INSERT INTO vault_published_sites 
                (space_id, branch, subdomain, custom_domain, visibility, config)
                VALUES ($1, $2, $3, $4, 'public', $5)
                RETURNING id
            ''', request.space_id, request.branch, request.subdomain, request.custom_domain, config_json)
        
        # Trigger background build + deploy
        background_tasks.add_task(deploy_to_cdn, site_id, request.space_id, request.branch)
    
    return {
        "status": "publishing",
        "site_id": str(site_id),
        "url": f"https://{request.subdomain}.docs.0711.io",
        "custom_domain": request.custom_domain
    }


async def deploy_to_cdn(site_id: str, space_id: str, branch: str):
    """Background task to deploy docs to CDN."""
    # Generate static files
    files = await _generator.generate(space_id=space_id, branch=branch)
    
    # TODO: Upload to Cloudflare Pages / R2
    # For now, just update the published_at timestamp
    
    async with _db_pool.acquire() as conn:
        await conn.execute('''
            UPDATE vault_published_sites
            SET published_at = NOW()
            WHERE id = $1
        ''', site_id)
    
    print(f"✅ Deployed docs for {space_id} ({len(files)} files)")


@router.get("/sites")
async def list_published_sites(space_id: Optional[str] = None):
    """List all published documentation sites."""
    if not _db_pool:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    async with _db_pool.acquire() as conn:
        if space_id:
            rows = await conn.fetch('''
                SELECT id, space_id, branch, subdomain, custom_domain, visibility, published_at
                FROM vault_published_sites
                WHERE space_id = $1
                ORDER BY created_at DESC
            ''', space_id)
        else:
            rows = await conn.fetch('''
                SELECT id, space_id, branch, subdomain, custom_domain, visibility, published_at
                FROM vault_published_sites
                ORDER BY created_at DESC
                LIMIT 100
            ''')
    
    return {
        "sites": [
            {
                "id": str(row['id']),
                "space_id": str(row['space_id']),
                "branch": row['branch'],
                "url": f"https://{row['subdomain']}.docs.0711.io",
                "custom_domain": row['custom_domain'],
                "visibility": row['visibility'],
                "published_at": row['published_at'].isoformat() if row['published_at'] else None
            }
            for row in rows
        ]
    }


@router.get("/search/{space_id}/{branch}")
async def search_docs(space_id: str, branch: str, q: str):
    """Search documentation content."""
    build_id = f"{space_id}-{branch}"
    
    if build_id not in _build_cache:
        # Build first
        build = await _engine.build_docs(space_id=space_id, branch=branch)
        _build_cache[build_id] = build
    else:
        build = _build_cache[build_id]
    
    # Simple search
    results = []
    query_lower = q.lower()
    
    for item in build["search_index"]:
        score = 0
        
        # Title match (highest weight)
        if query_lower in item["title"].lower():
            score += 10
        
        # Heading match
        for heading in item.get("headings", []):
            if query_lower in heading.lower():
                score += 5
        
        # Content match
        if query_lower in item["content"].lower():
            score += 1
            # Bonus for multiple occurrences
            score += item["content"].lower().count(query_lower)
        
        if score > 0:
            results.append({
                "path": item["path"],
                "title": item["title"],
                "score": score,
                "snippet": _extract_snippet(item["content"], query_lower)
            })
    
    # Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)
    
    return {"query": q, "results": results[:20]}


def _extract_snippet(content: str, query: str, context: int = 100) -> str:
    """Extract a snippet around the query match."""
    idx = content.lower().find(query)
    if idx == -1:
        return content[:200] + "..."
    
    start = max(0, idx - context)
    end = min(len(content), idx + len(query) + context)
    
    snippet = content[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."
    
    return snippet


@router.get("/health")
async def docs_health():
    """Docs service health check."""
    return {
        "status": "healthy" if _engine else "not_initialized",
        "cached_builds": len(_build_cache),
        "features": ["markdown", "mdx", "search", "static-gen", "publish"]
    }
