# Vaultclaw Project Handover for Bombas
**Date**: 2026-02-24
**From**: Claude Opus session on this Mac
**To**: Bombas (local Moltbot on this Mac)

---

## What Is Vaultclaw

Vaultclaw is a self-hosted, encrypted personal vault + AI assistant platform. Think "private iCloud + AI" running on our own H200V server. Each user (tenant) gets their own isolated stack: frontend (nginx), API (FastAPI/Python), PostgreSQL, Redis, Neo4j.

The AI assistant is called **Moltbot** — powered by Claude Sonnet via Anthropic API. It lives inside the frontend as a chat panel.

**Repo**: `/Users/m1/Desktop/Vaultclaw/`

---

## Architecture

```
Browser -> Cloudflare -> h200v-direct (SSH alias) -> Docker containers

Per tenant:
  vault-{name}-frontend  (nginx, serves index.html, proxies /api/ -> backend)
  vault-{name}-api       (FastAPI on port 8000, runs Moltbot)
  vault-{name}-postgres  (user data, vault items, embeddings)
  vault-{name}-redis     (sessions, cache)
  vault-{name}-neo4j     (graph relationships — not fully wired yet)
```

### Live Tenant Instances

| Tenant    | Frontend Container      | API Container      | Port  | URL                    |
|-----------|------------------------|--------------------|-------|------------------------|
| Main      | `vault-frontend`       | `vault-api`        | 9508  | `vault.0711.io`        |
| Denis     | `vault-denis-frontend`  | `vault-denis-api`  | 9908  | `denis-vault.0711.io`  |
| Alex      | `vault-alex-frontend`   | `vault-alex-api`   | 10008 | `alex-vault.0711.io`   |
| C         | `vault-c-frontend`      | `vault-c-api`      | 10108 | `c-vault.0711.io`      |

Other containers exist but are NOT active for end-users: `vault-bette-*`, `vault-isoled-*`, `vault-lightnet-*`.

### Credentials

| Instance | Email               | Password              |
|----------|--------------------|-----------------------|
| Main     | christoph@0711.io  | Vault0711Christoph!   |
| Denis    | denis@0711.io      | Stuttgart0711!        |
| Alex     | alex@0711.io       | Vault2026Sicher!      |
| C        | c@0711.io          | OpenClaw0711!         |

---

## Key Files in the Repo

| File | Purpose |
|------|---------|
| `frontend/public/index.html` | **THE frontend** — single ~140KB HTML file with all CSS+JS inline. Dual-panel UI: Vault (left) + OpenClaw/Moltbot (right). Login overlay on top. |
| `frontend/nginx.conf` | Nginx config — serves index.html, proxies `/api/` to `vault-api:8000` with SSE support |
| `backend/routers/assistant.py` | **Moltbot brain** — Claude Sonnet chat + streaming SSE + RAG context builder (Ollama embeddings) |
| `backend/routers/auth.py` | Login/register — bcrypt passwords, JWT tokens |
| `backend/database.py` | DB connections — PostgreSQL, Redis, Neo4j, Ollama client init |
| `backend/config.py` | Settings — OLLAMA_HOST, EMBEDDING_MODEL, JWT config |
| `backend/main.py` | FastAPI app — all routes, health endpoint, embedding/search endpoints |

---

## What Was Done in This Session

### 1. Deployed frontend to all 4 instances
- Copied `index.html` + `nginx.conf` into all 4 `vault-*-frontend` containers
- Changed nginx proxy from `api:8000` to `vault-api:8000` (Docker network alias)
- Added `X-Accel-Buffering: no` header for SSE streaming through Cloudflare
- Verified login + Moltbot chat works on all 3 tenant instances

### 2. Created christoph@0711.io password on main instance
- User already existed in DB but had no working password
- Generated bcrypt hash inside `vault-api` container, updated via SQL file in `vault-postgres`
- Password: `Vault0711Christoph!`

### 3. Fixed Moltbot streaming hang on main instance
- **Root cause**: `include_context: true` triggered Ollama embedding call, but Ollama is NOT running/reachable on the main instance's Docker network. The Ollama client had no timeout, so it hung forever.
- **Fix**: Added `asyncio.wait_for(..., timeout=5.0)` around the `ollama.embeddings()` call in `build_context()` in `assistant.py`. Now it times out after 5s and skips context gracefully.
- Deployed updated `assistant.py` to `vault-api` container and restarted it.
- **Note**: Only the main `vault-api` got this fix. The tenant instances (`vault-denis-api`, etc.) have Ollama connected so they don't hit this issue. But the fix should be deployed to all API containers for robustness.

### 4. Removed all mockup data from frontend
- Cleared fake Secure Chat conversations (Maria Schneider, Tom Kramer, Project Alpha, Family, Dr. Weber, OpenClaw Assistant)
- Cleared fake chat messages and thread replies
- Cleared all fake calendar events (Team Standup, Sprint Review, Doctor, etc.)
- Cleared Today's Schedule sidebar
- Zeroed out all file folder counts (Documents, Photos, Videos, Audio, Archives, Shared all show "0 files")
- Replaced Recent Files with "No files uploaded yet"
- Deployed clean `index.html` to all 4 frontend containers

---

## Current State — What Works

- **Login**: All 4 instances — user enters email/password, gets JWT, overlay hides, main UI shows
- **Moltbot chat**: Works on all 4 instances — non-streaming (fallback) and SSE streaming
- **Sign out**: Clears token, reloads to login screen
- **UI navigation**: Left sidebar nav buttons switch between Vault views (Dashboard, Files, Calendar, Settings, Intelligence, Secure Chat)
- **Right panel**: OpenClaw/Moltbot chat panel — send messages, get streaming responses

## Current State — What Does NOT Work / Is Not Wired

### Frontend (index.html)
1. **Secure Chat** — purely static HTML, no backend wiring. Shows "No conversations yet". No real messaging system exists in the backend.
2. **Calendar** — static HTML, no backend. Shows empty calendar grid. No event CRUD endpoints.
3. **Files** — static HTML, shows empty folders. Upload button is not wired. No file upload/download endpoints connected.
4. **Settings** — static HTML. LLM provider selection, embedding config, privacy toggles — none of these actually change backend config. Also shows outdated model names:
   - Shows "Claude 3.5 Sonnet" — should be "Claude Sonnet 4.6"
   - Shows "Mistral Large" as selected — Anthropic is the actual provider
   - Shows "Llama 3.3 70B" for local — should probably show actual Ollama models
5. **Vault Dashboard stats** — hardcoded to 0. Not fetching real counts from backend.
6. **AI Pipeline badges** ("Ingest", "Vision", "OCR", "Embeddings", "Search") — static, not reflecting real pipeline status.
7. **Storage bar** — hardcoded "0.00 GB of 5 GB". Not reading real storage usage.
8. **OpenClaw right panel** — the summary view, feature chips ("Analyze", "Search", "Organize"), quick actions — none wired to anything.

### Backend
1. **Ollama** — connected on tenant instances (`vault-denis-api`, `vault-alex-api`, `vault-c-api`) via `host.docker.internal:11434`. NOT connected on main instance (`vault-api`) which uses `172.17.0.1:11434`. The embedding timeout fix is only deployed to `vault-api`.
2. **Neo4j** — all instances show "Neo4j connection failed" in logs. Graph DB is running but connection config may be wrong (trying localhost:7687 instead of the container alias).
3. **RAG context** — `build_context()` in assistant.py does semantic search via Ollama embeddings + pgvector. Works when Ollama is up, gracefully skips when down. But no actual vault items exist yet for any user, so context is always empty anyway.
4. **Anthropic API key** — stored in `/app/.anthropic_key` inside API containers. Same key across all instances. Starts with `sk-ant-api03-wvQ-gkM...`
5. **No file upload pipeline** — backend has vault_items table but no wired upload endpoint from the frontend.

---

## What Needs to Be Done Next

### Priority 1: Core UX
1. **Wire the Vault Dashboard to real data** — fetch photo/document/event counts and storage usage from the API on login. Endpoints likely exist in `main.py` (health, vault stats).
2. **Wire file upload** — connect the Upload button to a real upload endpoint. Backend has storage infrastructure (`storage_albert.py` — ChaCha20-Poly1305 encrypted storage in PostgreSQL).
3. **Update Settings page model names** — change "Claude 3.5 Sonnet" to "Claude Sonnet 4.6", remove Mistral as selected default, reflect actual config.

### Priority 2: Deploy fix to all API containers
1. **Deploy the Ollama timeout fix** (`assistant.py` with `asyncio.wait_for`) to ALL API containers, not just `vault-api`. This prevents hangs if Ollama ever goes down.
2. **Fix Neo4j connection** — containers try `localhost:7687` but Neo4j runs in a separate container. Need to set `NEO4J_URI=bolt://neo4j:7687` or the appropriate Docker network alias.

### Priority 3: Real features
1. **File browser** — wire Files view to show actual vault_items from the API
2. **Calendar** — either wire to a backend calendar API or remove the view for now
3. **Secure Chat** — either build a real E2E messaging system or hide/disable the tab
4. **Moltbot memory** — Moltbot currently has no conversation persistence. Each page reload starts fresh. `conversation_id` is passed but may not be stored server-side.

### Priority 4: Infrastructure
1. **Cloudflare SSE** — streaming works from the server but Cloudflare may still buffer. If users report Moltbot "thinking" forever, the fix is a Cloudflare Page Rule to disable buffering on `*-vault.0711.io/api/assistant/*`
2. **Browser caching** — the nginx config has no cache-busting headers for `index.html`. Users may see stale versions. Add `Cache-Control: no-cache` or `ETag` headers for the HTML file.
3. **HTTPS** — Cloudflare handles SSL termination. The containers run HTTP internally. This is fine.

---

## How to Deploy Changes

### Frontend changes (index.html)
```bash
# 1. Edit locally
vim /Users/m1/Desktop/Vaultclaw/frontend/public/index.html

# 2. SCP to server
scp /Users/m1/Desktop/Vaultclaw/frontend/public/index.html h200v-direct:/tmp/vaultclaw-index.html

# 3. Deploy to all containers
ssh h200v-direct 'for c in vault-frontend vault-denis-frontend vault-alex-frontend vault-c-frontend; do
  docker cp /tmp/vaultclaw-index.html $c:/usr/share/nginx/html/index.html
done'
```
No nginx reload needed for static files — just hard refresh browser.

### Backend changes (Python)
```bash
# 1. Edit locally
vim /Users/m1/Desktop/Vaultclaw/backend/routers/assistant.py

# 2. SCP to server
scp /Users/m1/Desktop/Vaultclaw/backend/routers/assistant.py h200v-direct:/tmp/vaultclaw-assistant.py

# 3. Deploy + restart (must restart for Python changes)
ssh h200v-direct 'for c in vault-api vault-denis-api vault-alex-api vault-c-api; do
  docker cp /tmp/vaultclaw-assistant.py $c:/app/routers/assistant.py
  docker restart $c
done'
```
Wait ~5s after restart for FastAPI to boot.

### Nginx config changes
```bash
scp /Users/m1/Desktop/Vaultclaw/frontend/nginx.conf h200v-direct:/tmp/vaultclaw-nginx.conf
ssh h200v-direct 'for c in vault-frontend vault-denis-frontend vault-alex-frontend vault-c-frontend; do
  docker cp /tmp/vaultclaw-nginx.conf $c:/etc/nginx/conf.d/default.conf
  docker exec $c nginx -s reload
done'
```

---

## Server Access

- **SSH**: `ssh h200v-direct` (alias configured in ~/.ssh/config on this Mac)
- **Docker**: all containers run on h200v-direct, managed via `docker` CLI
- **No docker-compose on server** — containers were created individually. To see all: `docker ps | grep vault`
- **PostgreSQL access**: `docker exec vault-{name}-postgres psql -U vault_{name} -d vault_{name}` (main instance uses `vault`/`vault`)

---

## Gotchas

1. **Shell escaping hell** — passwords with `!` and bcrypt hashes with `$` get mangled in nested SSH+bash. Use Python scripts or write to files instead of inline curl/SQL.
2. **Browser cache** — after deploying frontend changes, users MUST hard-refresh (Cmd+Shift+R). Consider adding cache headers.
3. **index.html is huge** (~140KB, ~3600 lines) — all CSS, HTML, and JS in one file. Reading it requires offset/limit. Searching with grep is faster than reading sections.
4. **Ollama availability varies** — tenant instances can reach it via `host.docker.internal:11434`, main instance uses `172.17.0.1:11434` and it may not be running. The 5s timeout fix prevents hangs but context/RAG features are silently disabled when Ollama is down.
5. **The Anthropic API key** is stored in `/app/.anthropic_key` inside each API container, NOT in environment variables. The code reads the file first, falls back to `ANTHROPIC_API_KEY` env var.

---

*Good luck, Bombas. The foundation is deployed and working. Login + Moltbot chat are live for all users. The next step is wiring the frontend panels to real backend data.*

## Feb 24, 2026 - FV2 File Manager Implementation

### What was done
Implemented the complete FV2 (File Manager v2) design from VaultClawb.html reference:

1. **New CSS** (~600 lines):
   - `.fv2-topbar` - Breadcrumbs + action buttons
   - `.fv2-collections` - Horizontal scrollable smart collection cards
   - `.fv2-table` / `.fv2-file-row` - List view with proper columns
   - `.fv2-grid` / `.fv2-grid-item` - Grid view with thumbnails
   - `.fv2-storage-footer` - Bottom storage bar
   - `.fv2-context` - Right-click context menu
   - `.fv2-drop-zone` - Drag & drop overlay
   - Light theme support for all fv2 classes

2. **New HTML structure**:
   - Top bar with search, view toggle, new folder, upload buttons
   - Smart Collections: Recent, Documents, Images, Starred
   - List view (default) with Name, Type, Size, Modified columns
   - Grid view with thumbnail support
   - Empty state with upload CTA
   - Storage footer showing "X MB of 5 GB · Encrypted"

3. **New JavaScript** (~400 lines):
   - `FV2` state object
   - `initFv2FileManager()` - Drag/drop, context menu setup
   - `fv2LoadFiles()` - API calls with fallback
   - `fv2RenderFiles()` / `fv2RenderListItem()` / `fv2RenderGridItem()`
   - `fv2GetFileName()` - Proper filename extraction (original_filename > storage_key)
   - `fv2GetFileIcon()` - Type-based color coding
   - `fv2Upload()` / `fv2UploadFiles()`
   - `fv2DownloadFile()` / `fv2DeleteFile()` / `fv2RenameFile()`
   - `fv2ShowContext()` / `fv2ContextAction()`
   - View toggle, search, collection counts

### Key fixes
- Files now show original filenames instead of UUIDs
- Storage shows actual usage in footer
- Collections show item counts from file stats

### What's still TODO
- Smart Collections filtering (currently shows toast)
- Folder creation (API endpoint needed)
- File rename (needs PATCH /files/{id} endpoint)
- AI Insight bar (hidden by default, needs AI integration)

### Commit
`9f9be4d` - "Add FV2 File Manager - Dropbox-level design"
