# Vaultclaw

**0711 Vault x OpenClaw** — Privacy-first personal data fortress with AI.

Dual-panel interface: encrypted vault (files, chat, calendar) on the left, Moltbot AI assistant (Claude Sonnet) on the right. All data stays 100% local.

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY

# 2. Start all services
docker compose -f infrastructure/docker-compose.yml up --build

# 3. Open
# Frontend: http://localhost:3000
# API Docs: http://localhost:8000/docs
# Neo4j UI: http://localhost:7474
```

## Architecture

```
Frontend (Nginx :3000)
    |
    |--- /api/* ---> Backend API (FastAPI :8000)
    |                    |
    |                    |--- PostgreSQL + pgvector (embeddings, users, vault items)
    |                    |--- Redis (session tokens, job queue)
    |                    |--- Neo4j (knowledge graph: people, locations, items)
    |                    |--- Anthropic API (Claude Sonnet — Moltbot chat)
    |                    |--- Ollama (local embeddings, vision — fallback)
    |
    Worker (background document processing)
```

## Stack

| Layer | Tech |
|---|---|
| Frontend | Dual-panel HTML prototype (React port in progress) |
| Backend | FastAPI, Python 3.11 |
| Database | PostgreSQL 16 + pgvector |
| Cache/Queue | Redis 7 |
| Graph | Neo4j 5 |
| AI Chat | Claude Sonnet via Anthropic API |
| AI Local | Ollama (bge-m3 embeddings, llama4 vision) |
| Storage | Albert (ChaCha20-Poly1305 encrypted, PostgreSQL-backed) |

## Repo Structure

```
backend/          FastAPI application
frontend/         Nginx + prototype HTML (→ React)
infrastructure/   Docker Compose, deploy scripts, DB schema
docs/             Architecture docs, task checklist
```

## Credentials (Dev)

| User | Email | Password |
|---|---|---|
| Denis | denis@0711.io | Stuttgart0711! |
| Alex | alex@0711.io | Vault2026Sicher! |
| C | c@0711.io | OpenClaw0711! |

## Production

Deployed on Hetzner H200V via Cloudflare Tunnels:
- `vault.0711.io` — Main instance
- `denis-vault.0711.io` / `alex-vault.0711.io` / `c-vault.0711.io` — Tenant instances

See `infrastructure/deploy/` for deployment scripts.
