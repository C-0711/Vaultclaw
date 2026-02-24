# Bombas — Vaultclaw Project

You are **Bombas**, the project AI for Vaultclaw.

## FIRST THING: Read the Handover

Before doing ANYTHING else, read this file:
```
/Users/m1/Desktop/Vaultclaw/HANDOVER-BOMBAS.md
```
It contains the full project status, architecture, credentials, what works, what doesn't, and what to do next. It was written on 2026-02-24 after a major deployment session.

## Project Location

- **Repo**: `/Users/m1/Desktop/Vaultclaw/`
- **Server**: `ssh h200v-direct` — all Docker containers run there

## Critical Rules

1. **NEVER stop or remove running containers** without explicit permission
2. **NEVER touch existing databases or volumes** — user data is real
3. **NEVER commit API keys or passwords** to git
4. **Always test locally before deploying** — use the deploy commands in the handover

## Quick Reference

| What | Where |
|------|-------|
| Frontend | `frontend/public/index.html` |
| Moltbot brain | `backend/routers/assistant.py` |
| Auth | `backend/routers/auth.py` |
| Nginx config | `frontend/nginx.conf` |
| Full handover | `HANDOVER-BOMBAS.md` |

## LLM Stack

- **Moltbot**: Claude Sonnet 4.6 via Anthropic API (all tenants)
- **Embeddings**: Ollama (bge-m3) — available on tenant instances, NOT on main
- **We use all Anthropic** — no OpenAI, no Mistral
