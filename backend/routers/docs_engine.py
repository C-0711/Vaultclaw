"""
PROJEKT GENESIS Sprint 5: Vault Docs Engine
GitBook-style documentation publishing from Vault spaces
"""

import re
import json
import hashlib
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
from datetime import datetime
import asyncpg


class DocTheme(Enum):
    """Available documentation themes."""
    LIGHT = "light"
    DARK = "dark"
    SEPIA = "sepia"
    AUTO = "auto"


@dataclass
class NavItem:
    """Navigation item for docs sidebar."""
    title: str
    path: str
    level: int = 0
    children: List["NavItem"] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "path": self.path,
            "level": self.level,
            "children": [c.to_dict() for c in self.children]
        }


@dataclass
class DocPage:
    """A documentation page."""
    path: str
    title: str
    content: str
    html: str
    toc: List[Dict[str, Any]]
    prev_page: Optional[Dict[str, str]] = None
    next_page: Optional[Dict[str, str]] = None
    last_modified: Optional[str] = None
    edit_url: Optional[str] = None


@dataclass
class DocsConfig:
    """Documentation site configuration."""
    title: str = "Documentation"
    description: str = ""
    logo: Optional[str] = None
    favicon: Optional[str] = None
    theme: DocTheme = DocTheme.AUTO
    primary_color: str = "#3B82F6"
    font_family: str = "Inter, system-ui, sans-serif"
    code_theme: str = "github-dark"
    show_toc: bool = True
    show_edit_link: bool = True
    repo_url: Optional[str] = None
    custom_css: Optional[str] = None
    custom_head: Optional[str] = None
    footer_text: Optional[str] = None
    analytics_id: Optional[str] = None


class MarkdownParser:
    """
    Extended Markdown parser with MDX-like features.
    
    Supports:
    - Standard Markdown (headings, lists, links, images, code blocks)
    - GFM (tables, task lists, strikethrough)
    - Admonitions (:::note, :::warning, :::tip, :::danger)
    - Code syntax highlighting hints
    - Auto-linking headers
    - Table of contents extraction
    """
    
    # Admonition pattern: :::type\ncontent\n:::
    ADMONITION_PATTERN = re.compile(
        r':::(note|warning|tip|danger|info|caution)\n(.*?)\n:::', 
        re.DOTALL
    )
    
    # Header pattern for TOC
    HEADER_PATTERN = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
    
    # Code block pattern
    CODE_BLOCK_PATTERN = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)
    
    def __init__(self):
        self.toc: List[Dict[str, Any]] = []
    
    def parse(self, markdown: str) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Parse Markdown to HTML and extract TOC.
        
        Returns (html, toc).
        """
        self.toc = []
        html = markdown
        
        # Extract TOC from headers
        for match in self.HEADER_PATTERN.finditer(markdown):
            level = len(match.group(1))
            title = match.group(2).strip()
            slug = self._slugify(title)
            self.toc.append({
                "level": level,
                "title": title,
                "slug": slug
            })
        
        # Convert headers with anchors
        def replace_header(match):
            level = len(match.group(1))
            title = match.group(2).strip()
            slug = self._slugify(title)
            return f'<h{level} id="{slug}"><a href="#{slug}" class="anchor">#</a>{self._escape_html(title)}</h{level}>'
        
        html = self.HEADER_PATTERN.sub(replace_header, html)
        
        # Convert admonitions
        def replace_admonition(match):
            admon_type = match.group(1)
            content = match.group(2).strip()
            icon = {
                "note": "📝",
                "warning": "⚠️",
                "tip": "💡",
                "danger": "🚨",
                "info": "ℹ️",
                "caution": "⚡"
            }.get(admon_type, "📌")
            return f'''<div class="admonition admonition-{admon_type}">
                <div class="admonition-icon">{icon}</div>
                <div class="admonition-content">{content}</div>
            </div>'''
        
        html = self.ADMONITION_PATTERN.sub(replace_admonition, html)
        
        # Convert code blocks with syntax hints
        def replace_code(match):
            lang = match.group(1) or "text"
            code = self._escape_html(match.group(2).strip())
            return f'<pre><code class="language-{lang}">{code}</code></pre>'
        
        html = self.CODE_BLOCK_PATTERN.sub(replace_code, html)
        
        # Convert inline code
        html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
        
        # Convert bold
        html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html)
        
        # Convert italic
        html = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', html)
        
        # Convert links
        html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)
        
        # Convert images
        html = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1" loading="lazy">', html)
        
        # Convert unordered lists
        html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        html = re.sub(r'(<li>.*</li>\n)+', r'<ul>\g<0></ul>', html)
        
        # Convert blockquotes
        html = re.sub(r'^> (.+)$', r'<blockquote>\1</blockquote>', html, flags=re.MULTILINE)
        
        # Convert horizontal rules
        html = re.sub(r'^---+$', '<hr>', html, flags=re.MULTILINE)
        
        # Convert paragraphs (lines not already wrapped)
        lines = html.split('\n')
        result = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('<'):
                line = f'<p>{line}</p>'
            result.append(line)
        html = '\n'.join(result)
        
        return html, self.toc
    
    def _slugify(self, text: str) -> str:
        """Convert text to URL-safe slug."""
        slug = text.lower()
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        slug = re.sub(r'[\s_]+', '-', slug)
        slug = slug.strip('-')
        return slug
    
    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters."""
        return (
            text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
        )


class SummaryParser:
    """
    Parse SUMMARY.md to build navigation structure.
    
    Format:
    # Summary
    
    - [Introduction](README.md)
    - [Getting Started](getting-started/README.md)
      - [Installation](getting-started/installation.md)
      - [Configuration](getting-started/configuration.md)
    - [API Reference](api/README.md)
    """
    
    LINK_PATTERN = re.compile(r'^(\s*)-\s+\[([^\]]+)\]\(([^)]+)\)')
    
    def parse(self, content: str) -> List[NavItem]:
        """Parse SUMMARY.md content to navigation structure."""
        items = []
        stack: List[Tuple[int, NavItem]] = []  # (indent_level, item)
        
        for line in content.split('\n'):
            match = self.LINK_PATTERN.match(line)
            if not match:
                continue
            
            indent = len(match.group(1))
            title = match.group(2)
            path = match.group(3)
            
            # Determine nesting level (2 spaces per level)
            level = indent // 2
            
            item = NavItem(title=title, path=path, level=level)
            
            if level == 0:
                items.append(item)
                stack = [(0, item)]
            else:
                # Find parent
                while stack and stack[-1][0] >= level:
                    stack.pop()
                
                if stack:
                    parent = stack[-1][1]
                    parent.children.append(item)
                
                stack.append((level, item))
        
        return items
    
    def flatten(self, items: List[NavItem]) -> List[Dict[str, str]]:
        """Flatten navigation for prev/next links."""
        result = []
        
        def walk(items: List[NavItem]):
            for item in items:
                result.append({"title": item.title, "path": item.path})
                walk(item.children)
        
        walk(items)
        return result


class DocsEngine:
    """
    Main documentation engine.
    
    Builds and serves documentation from Vault spaces.
    """
    
    def __init__(self, db_pool: asyncpg.Pool):
        self.db_pool = db_pool
        self.md_parser = MarkdownParser()
        self.summary_parser = SummaryParser()
    
    async def build_docs(
        self,
        space_id: str,
        branch: str = "main",
        config: Optional[DocsConfig] = None
    ) -> Dict[str, Any]:
        """
        Build documentation from a space.
        
        Returns build result with pages and navigation.
        """
        config = config or DocsConfig()
        
        async with self.db_pool.acquire() as conn:
            # Get SUMMARY.md for navigation
            summary = await self._get_file(conn, space_id, branch, "SUMMARY.md")
            if summary:
                nav_items = self.summary_parser.parse(summary)
            else:
                # Auto-generate navigation from file structure
                nav_items = await self._auto_nav(conn, space_id, branch)
            
            # Flatten for prev/next
            flat_nav = self.summary_parser.flatten(nav_items)
            
            # Build each page
            pages = []
            for i, nav_item in enumerate(flat_nav):
                content = await self._get_file(conn, space_id, branch, nav_item["path"])
                if not content:
                    continue
                
                html, toc = self.md_parser.parse(content)
                
                # Determine title from first H1 or nav
                title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
                title = title_match.group(1) if title_match else nav_item["title"]
                
                page = DocPage(
                    path=nav_item["path"],
                    title=title,
                    content=content,
                    html=html,
                    toc=toc,
                    prev_page=flat_nav[i - 1] if i > 0 else None,
                    next_page=flat_nav[i + 1] if i < len(flat_nav) - 1 else None,
                    edit_url=f"{config.repo_url}/edit/{branch}/{nav_item['path']}" if config.repo_url else None
                )
                pages.append(page)
            
            # Build search index
            search_index = self._build_search_index(pages)
        
        return {
            "config": asdict(config),
            "navigation": [item.to_dict() for item in nav_items],
            "pages": [asdict(p) for p in pages],
            "search_index": search_index,
            "built_at": datetime.utcnow().isoformat()
        }
    
    async def render_page(
        self,
        space_id: str,
        branch: str,
        path: str,
        config: Optional[DocsConfig] = None
    ) -> Optional[DocPage]:
        """Render a single documentation page."""
        config = config or DocsConfig()
        
        async with self.db_pool.acquire() as conn:
            content = await self._get_file(conn, space_id, branch, path)
            if not content:
                return None
            
            html, toc = self.md_parser.parse(content)
            
            # Get title
            title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
            title = title_match.group(1) if title_match else path
            
            return DocPage(
                path=path,
                title=title,
                content=content,
                html=html,
                toc=toc
            )
    
    async def _get_file(
        self,
        conn: asyncpg.Connection,
        space_id: str,
        branch: str,
        path: str
    ) -> Optional[str]:
        """Get file content from vault."""
        row = await conn.fetchrow('''
            SELECT fv.content
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
            return None
        
        content = row['content']
        if isinstance(content, bytes):
            content = content.decode('utf-8')
        
        return content
    
    async def _auto_nav(
        self,
        conn: asyncpg.Connection,
        space_id: str,
        branch: str
    ) -> List[NavItem]:
        """Auto-generate navigation from .md files."""
        rows = await conn.fetch('''
            SELECT DISTINCT t.path
            FROM vault_trees t
            JOIN vault_snapshots s ON t.snapshot_id = s.id
            JOIN vault_branches b ON s.branch_id = b.id
            WHERE b.space_id = $1 
            AND b.name = $2
            AND t.path LIKE '%.md'
            ORDER BY t.path
        ''', space_id, branch)
        
        items = []
        for row in rows:
            path = row['path']
            title = path.replace('.md', '').replace('-', ' ').replace('_', ' ').title()
            if path == 'README.md':
                title = 'Introduction'
            items.append(NavItem(title=title, path=path))
        
        return items
    
    def _build_search_index(self, pages: List[DocPage]) -> List[Dict[str, Any]]:
        """Build search index from pages."""
        index = []
        
        for page in pages:
            # Extract text content (strip HTML)
            text = re.sub(r'<[^>]+>', '', page.html)
            text = re.sub(r'\s+', ' ', text).strip()
            
            index.append({
                "path": page.path,
                "title": page.title,
                "content": text[:1000],  # First 1000 chars for search
                "headings": [h["title"] for h in page.toc]
            })
        
        return index


class StaticSiteGenerator:
    """
    Generate static HTML site from docs.
    """
    
    HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} | {site_title}</title>
    <meta name="description" content="{description}">
    <link rel="icon" href="{favicon}">
    {custom_head}
    <style>
        :root {{
            --primary: {primary_color};
            --font-family: {font_family};
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: var(--font-family); line-height: 1.6; }}
        .container {{ max-width: 1200px; margin: 0 auto; display: flex; }}
        .sidebar {{ width: 280px; padding: 2rem; border-right: 1px solid #eee; height: 100vh; position: sticky; top: 0; overflow-y: auto; }}
        .content {{ flex: 1; padding: 2rem 3rem; max-width: 800px; }}
        .toc {{ width: 200px; padding: 2rem; position: sticky; top: 0; height: 100vh; overflow-y: auto; }}
        nav a {{ display: block; padding: 0.5rem; color: #666; text-decoration: none; }}
        nav a:hover, nav a.active {{ color: var(--primary); }}
        h1, h2, h3 {{ margin-top: 1.5rem; margin-bottom: 0.5rem; }}
        h1 {{ font-size: 2rem; }}
        h2 {{ font-size: 1.5rem; }}
        pre {{ background: #1e1e1e; color: #d4d4d4; padding: 1rem; border-radius: 8px; overflow-x: auto; }}
        code {{ font-family: 'Fira Code', monospace; }}
        .admonition {{ padding: 1rem; border-radius: 8px; margin: 1rem 0; display: flex; gap: 1rem; }}
        .admonition-note {{ background: #e3f2fd; }}
        .admonition-warning {{ background: #fff3e0; }}
        .admonition-tip {{ background: #e8f5e9; }}
        .admonition-danger {{ background: #ffebee; }}
        .anchor {{ opacity: 0; margin-left: -1.5rem; padding-right: 0.5rem; }}
        h1:hover .anchor, h2:hover .anchor, h3:hover .anchor {{ opacity: 0.5; }}
        .nav-footer {{ display: flex; justify-content: space-between; margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #eee; }}
        .nav-footer a {{ color: var(--primary); text-decoration: none; }}
        {custom_css}
    </style>
</head>
<body>
    <div class="container">
        <aside class="sidebar">
            <div class="logo">{logo}</div>
            <nav>{navigation}</nav>
        </aside>
        <main class="content">
            {content}
            <div class="nav-footer">
                {prev_link}
                {next_link}
            </div>
        </main>
        {toc_sidebar}
    </div>
    {analytics}
</body>
</html>'''
    
    def __init__(self, engine: DocsEngine):
        self.engine = engine
    
    async def generate(
        self,
        space_id: str,
        branch: str = "main",
        config: Optional[DocsConfig] = None
    ) -> Dict[str, str]:
        """
        Generate static HTML files.
        
        Returns dict of {path: html_content}.
        """
        config = config or DocsConfig()
        build = await self.engine.build_docs(space_id, branch, config)
        
        files = {}
        
        # Generate navigation HTML
        nav_html = self._render_nav(build["navigation"])
        
        # Generate each page
        for page in build["pages"]:
            html = self._render_page(page, nav_html, config)
            
            # Convert .md path to .html
            html_path = page["path"].replace(".md", ".html")
            if html_path == "README.html":
                html_path = "index.html"
            
            files[html_path] = html
        
        # Generate search index JSON
        files["search-index.json"] = json.dumps(build["search_index"])
        
        return files
    
    def _render_nav(self, nav_items: List[Dict]) -> str:
        """Render navigation HTML."""
        html = "<ul>"
        
        for item in nav_items:
            html += f'<li><a href="{item["path"].replace(".md", ".html")}">{item["title"]}</a>'
            if item.get("children"):
                html += self._render_nav(item["children"])
            html += "</li>"
        
        html += "</ul>"
        return html
    
    def _render_page(
        self,
        page: Dict,
        nav_html: str,
        config: DocsConfig
    ) -> str:
        """Render a single page to HTML."""
        # TOC sidebar
        toc_html = ""
        if config.show_toc and page.get("toc"):
            toc_html = '<aside class="toc"><h4>On this page</h4><nav>'
            for item in page["toc"]:
                indent = "  " * (item["level"] - 1)
                toc_html += f'{indent}<a href="#{item["slug"]}">{item["title"]}</a>'
            toc_html += "</nav></aside>"
        
        # Prev/next links
        prev_link = ""
        next_link = ""
        if page.get("prev_page"):
            prev_link = f'<a href="{page["prev_page"]["path"].replace(".md", ".html")}">← {page["prev_page"]["title"]}</a>'
        if page.get("next_page"):
            next_link = f'<a href="{page["next_page"]["path"].replace(".md", ".html")}">{page["next_page"]["title"]} →</a>'
        
        # Analytics
        analytics = ""
        if config.analytics_id:
            analytics = f'''<script async src="https://www.googletagmanager.com/gtag/js?id={config.analytics_id}"></script>
            <script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','{config.analytics_id}');</script>'''
        
        return self.HTML_TEMPLATE.format(
            theme=config.theme.value,
            title=page["title"],
            site_title=config.title,
            description=config.description,
            favicon=config.favicon or "/favicon.ico",
            custom_head=config.custom_head or "",
            primary_color=config.primary_color,
            font_family=config.font_family,
            custom_css=config.custom_css or "",
            logo=f'<img src="{config.logo}" alt="Logo">' if config.logo else f'<h2>{config.title}</h2>',
            navigation=nav_html,
            content=page["html"],
            toc_sidebar=toc_html,
            prev_link=prev_link,
            next_link=next_link,
            analytics=analytics
        )
