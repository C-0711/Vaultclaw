"""
Moltbot AI — The brain of 0711 Vault
Powered by Claude Sonnet 4.6 via Anthropic API
Answers questions about YOUR photos, documents, and memories.
Also helps with vault setup and onboarding.

OpenClaw H200V · Stuttgart · 2026
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy import text
import json
import asyncio
import traceback

from config import settings
from database import get_db, get_neo4j, get_ollama, get_redis
from auth import get_current_user

router = APIRouter()

# ===========================================
# ANTHROPIC CLIENT (lazy init)
# ===========================================

_anthropic_client = None
CLAUDE_MODEL = "claude-4-sonnet-20250514"


def _get_anthropic():
    """Lazy-init Anthropic client. Reads API key from file or env."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client

    api_key = None

    # Try file first (more secure in containers)
    try:
        with open("/app/.anthropic_key", "r") as f:
            api_key = f.read().strip()
    except FileNotFoundError:
        pass

    # Fallback to env
    if not api_key:
        import os
        api_key = os.getenv("ANTHROPIC_API_KEY", "")

    if not api_key:
        print("[Moltbot] WARNING: No Anthropic API key found!")
        return None

    try:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
        print(f"[Moltbot] Anthropic client initialized (model: {CLAUDE_MODEL})")
        return _anthropic_client
    except Exception as e:
        print(f"[Moltbot] Failed to init Anthropic client: {e}")
        return None


# ===========================================
# MOLTBOT SYSTEM PROMPT
# ===========================================

MOLTBOT_SYSTEM = """Du bist **Moltbot**, der KI-Assistent im 0711 Vault — entwickelt von OpenClaw, betrieben auf einem H200V Server in Stuttgart.

## Deine Persoenlichkeit
- Freundlich, hilfsbereit, und ein bisschen witzig
- Stolz auf Stuttgart und die 0711-Community
- Du sprichst Deutsch und Englisch — antworte in der Sprache des Users
- Du bist technisch kompetent aber erklaerst Dinge einfach
- Wenn du etwas nicht weisst, sagst du das ehrlich

## Was du kannst
1. **Vault-Fragen beantworten**: Fotos finden, Dokumente suchen, Erinnerungen durchstoebern
2. **Setup-Hilfe**: Neuen Usern erklaeren wie der Vault funktioniert
3. **Vault-Features erklaeren**:
   - Foto-Upload und -Verwaltung (verschluesselt)
   - Dokumenten-Speicher
   - Gesichtserkennung und Personen-Tagging
   - Orte und Zeitlinien
   - KI-gestuetzte Suche ueber alle Inhalte
   - "On This Day" Erinnerungen
4. **Allgemeine Fragen**: Du kannst auch allgemeine Fragen beantworten

## Wichtige Regeln
- Wenn Vault-Kontext mitgegeben wird, beziehe dich darauf
- Erfinde KEINE Daten — wenn du nichts findest, sag das
- Der Vault ist 100% privat — Daten bleiben auf dem H200V Server
- Erwaehne bei Setup-Fragen, dass alles lokal auf dem OpenClaw H200V laeuft

## Ueber den Vault
- **0711 Vault** ist ein privater digitaler Tresor fuer Fotos, Dokumente und Erinnerungen
- Laeuft auf **OpenClaw H200V** Hardware in Stuttgart
- Ende-zu-Ende verschluesselt
- KI-Features (Gesichtserkennung, Suche) laufen lokal
- Kein Big Tech, keine Cloud — deine Daten gehoeren dir

Sei hilfreich, sei ehrlich, sei Moltbot! 🐾"""


# ===========================================
# SCHEMAS
# ===========================================

class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: Optional[datetime] = None


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    include_context: bool = True


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    sources: List[Dict[str, Any]]
    thinking: Optional[str] = None


class MemoryRequest(BaseModel):
    memory_type: str
    date: Optional[datetime] = None


class MemoryResponse(BaseModel):
    memories: List[Dict[str, Any]]
    message: str


# ===========================================
# CONTEXT BUILDER — RAG Engine (Ollama embeddings)
# ===========================================

async def build_context(query: str, user_id: str, db, neo4j) -> Dict[str, Any]:
    """
    Build rich context from the user's vault.
    Uses Ollama for embeddings (semantic search), Claude for chat.
    """
    context = {
        "photos": [],
        "documents": [],
        "people": [],
        "places": [],
        "events": [],
        "timeline": []
    }

    ollama = get_ollama()

    # 1. Generate query embedding via Ollama
    if not ollama:
        print("[Moltbot] Ollama not available — skipping semantic search")
        return context

    try:
        embedding_response = await ollama.embeddings(
            model=settings.EMBEDDING_MODEL,
            prompt=query
        )
        query_embedding = embedding_response['embedding']
    except Exception as e:
        print(f"[Moltbot] Embedding error: {e}")
        return context

    # 2. Semantic search in PostgreSQL (pgvector)
    try:
        embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
        result = await db.execute(text("""
            SELECT
                vi.id,
                vi.item_type,
                vi.encrypted_metadata,
                vi.captured_at,
                vi.storage_key,
                1 - (e.embedding <=> CAST(:query_embedding AS vector)) as score
            FROM embeddings e
            JOIN vault_items vi ON e.item_id = vi.id
            WHERE vi.user_id = :user_id
              AND vi.deleted_at IS NULL
            ORDER BY e.embedding <=> CAST(:query_embedding AS vector)
            LIMIT 10
        """), {
            "query_embedding": embedding_str,
            "user_id": user_id
        })

        items = result.fetchall()
        for item in items:
            if item.item_type == 'photo':
                context["photos"].append({
                    "id": str(item.id),
                    "captured_at": item.captured_at.isoformat() if item.captured_at else None,
                    "score": item.score,
                    "path": item.storage_key
                })
            elif item.item_type == 'document':
                context["documents"].append({
                    "id": str(item.id),
                    "score": item.score,
                    "path": item.storage_key
                })
    except Exception as e:
        print(f"[Moltbot] Semantic search error: {e}")
        traceback.print_exc()

    # 3. Graph search (Neo4j)
    if neo4j:
        try:
            async with neo4j.session() as session:
                result = await session.run("""
                    MATCH (p:Person)-[:APPEARS_IN]->(i:VaultItem)
                    WHERE i.user_id = $user_id
                    WITH p, count(i) as appearances
                    RETURN p.id as id, p.name as name, appearances
                    ORDER BY appearances DESC
                    LIMIT 20
                """, user_id=user_id)
                context["people"] = await result.data()

                result = await session.run("""
                    MATCH (l:Location)<-[:TAKEN_AT]-(i:VaultItem)
                    WHERE i.user_id = $user_id
                    WITH l, count(i) as photo_count
                    RETURN l.id as id, l.name as name, l.coordinates as coords, photo_count
                    ORDER BY photo_count DESC
                    LIMIT 10
                """, user_id=user_id)
                context["places"] = await result.data()
        except Exception as e:
            print(f"[Moltbot] Graph search error: {e}")

    return context


# ===========================================
# CLAUDE CHAT (with Ollama fallback)
# ===========================================

async def _chat_claude(messages: list, stream: bool = False):
    """Call Claude Sonnet 4.6 via Anthropic API."""
    client = _get_anthropic()
    if not client:
        return None

    # Separate system prompt from messages
    system_text = ""
    chat_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
        else:
            chat_messages.append({"role": msg["role"], "content": msg["content"]})

    if stream:
        return client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=system_text,
            messages=chat_messages,
        )
    else:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=system_text,
            messages=chat_messages,
        )
        return response.content[0].text


async def _chat_ollama_fallback(messages: list):
    """Fallback to Ollama if Claude is unavailable."""
    ollama = get_ollama()
    if not ollama:
        return None
    try:
        response = await ollama.chat(
            model=settings.VISION_MODEL,
            messages=messages,
            stream=False
        )
        return response['message']['content']
    except Exception as e:
        print(f"[Moltbot] Ollama fallback also failed: {e}")
        return None


# ===========================================
# MAIN CHAT ENDPOINT
# ===========================================

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user_id: str = Depends(get_current_user), db=Depends(get_db)):
    """
    Chat with Moltbot — your vault's AI assistant.
    Powered by Claude Sonnet 4.6.
    """
    neo4j = get_neo4j()
    redis = get_redis()

    # Get or create conversation
    conversation_id = request.conversation_id or f"conv_{user_id}_{datetime.utcnow().timestamp()}"

    # Load conversation history
    history = []
    if redis and request.conversation_id:
        try:
            cached = await redis.get(f"conversation:{conversation_id}")
            if cached:
                history = json.loads(cached)
        except Exception:
            pass

    # Build vault context (RAG)
    sources = []
    vault_context = ""

    if request.include_context:
        context = await build_context(request.message, user_id, db, neo4j)

        if context["photos"]:
            vault_context += f"\n\nRelevant photos found: {len(context['photos'])}"
            for p in context["photos"][:5]:
                vault_context += f"\n- Photo from {p['captured_at'] or 'unknown date'} (relevance: {p['score']:.2f})"
                sources.append({"type": "photo", "id": p["id"], "date": p["captured_at"]})

        if context["documents"]:
            vault_context += f"\n\nRelevant documents found: {len(context['documents'])}"
            for d in context["documents"][:3]:
                sources.append({"type": "document", "id": d["id"]})

        if context["people"]:
            vault_context += f"\n\nPeople in your vault: {', '.join([p['name'] for p in context['people'][:10] if p['name']])}"

        if context["places"]:
            vault_context += f"\n\nPlaces you've been: {', '.join([p['name'] for p in context['places'][:10] if p['name']])}"

    # Build messages
    messages = [{"role": "system", "content": MOLTBOT_SYSTEM}]

    for msg in history[-10:]:
        messages.append(msg)

    user_message = request.message
    if vault_context:
        user_message += f"\n\n[VAULT CONTEXT]{vault_context}\n[/VAULT CONTEXT]"

    messages.append({"role": "user", "content": user_message})

    # Generate response — Claude first, Ollama fallback
    assistant_response = None

    try:
        assistant_response = await _chat_claude(messages, stream=False)
    except Exception as e:
        print(f"[Moltbot] Claude error: {e}")

    if not assistant_response:
        assistant_response = await _chat_ollama_fallback(messages)

    if not assistant_response:
        assistant_response = (
            "Entschuldigung, ich habe gerade technische Schwierigkeiten. "
            "Bitte versuche es in ein paar Sekunden nochmal! 🐾"
        )

    # Save conversation
    history.append({"role": "user", "content": request.message})
    history.append({"role": "assistant", "content": assistant_response})

    if redis:
        try:
            await redis.setex(
                f"conversation:{conversation_id}",
                3600 * 24,
                json.dumps(history)
            )
        except Exception:
            pass

    return ChatResponse(
        response=assistant_response,
        conversation_id=conversation_id,
        sources=sources
    )


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, user_id: str = Depends(get_current_user), db=Depends(get_db)):
    """
    Streaming chat with Moltbot via Server-Sent Events.
    Claude Sonnet 4.6 with SSE streaming.
    """
    neo4j = get_neo4j()

    # Build context
    context = await build_context(request.message, user_id, db, neo4j) if request.include_context else {}

    vault_context = ""
    if context.get("photos"):
        vault_context += f"\nFound {len(context['photos'])} relevant photos."
    if context.get("people"):
        vault_context += f"\nPeople: {', '.join([p['name'] for p in context['people'][:5] if p.get('name')])}"

    messages = [
        {"role": "system", "content": MOLTBOT_SYSTEM},
        {"role": "user", "content": request.message + (f"\n\n[Context]{vault_context}[/Context]" if vault_context else "")}
    ]

    async def generate_claude():
        """Stream from Claude via SSE."""
        try:
            client = _get_anthropic()
            if not client:
                raise Exception("No Anthropic client")

            system_text = ""
            chat_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_text = msg["content"]
                else:
                    chat_messages.append({"role": msg["role"], "content": msg["content"]})

            with client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=2048,
                system=system_text,
                messages=chat_messages,
            ) as stream:
                for text_chunk in stream.text_stream:
                    yield f"data: {json.dumps({'content': text_chunk})}\n\n"

            yield f"data: {json.dumps({'done': True})}\n\n"

        except Exception as e:
            print(f"[Moltbot] Stream error: {e}")
            # Fallback: non-streaming Ollama response
            fallback = await _chat_ollama_fallback(messages)
            if fallback:
                yield f"data: {json.dumps({'content': fallback})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            else:
                yield f"data: {json.dumps({'error': 'Moltbot ist gerade nicht erreichbar. Bitte versuche es nochmal!'})}\n\n"

    return StreamingResponse(
        generate_claude(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# ===========================================
# MEMORY FEATURES (unchanged from original)
# ===========================================

@router.get("/memories/on-this-day")
async def on_this_day(user_id: str = Depends(get_current_user), db=Depends(get_db)):
    """Get photos from this day in previous years."""
    today = datetime.utcnow()
    memories = []

    for years_ago in range(1, 11):
        target_date = today - timedelta(days=365 * years_ago)
        start = target_date.replace(hour=0, minute=0, second=0)
        end = target_date.replace(hour=23, minute=59, second=59)

        result = await db.execute(text("""
            SELECT id, storage_key, captured_at, encrypted_metadata
            FROM vault_items
            WHERE user_id = :user_id
              AND item_type = 'photo'
              AND captured_at BETWEEN :start AND :end
              AND deleted_at IS NULL
            ORDER BY captured_at
            LIMIT 10
        """), {
            "user_id": user_id,
            "start": start,
            "end": end
        })

        photos = result.fetchall()
        if photos:
            memories.append({
                "year": target_date.year,
                "years_ago": years_ago,
                "photos": [
                    {
                        "id": str(p.id),
                        "path": p.storage_key,
                        "captured_at": p.captured_at.isoformat()
                    }
                    for p in photos
                ]
            })

    message = f"Found memories from {len(memories)} previous years!" if memories else "No memories from this day yet. Keep capturing moments!"

    return MemoryResponse(memories=memories, message=message)


@router.get("/memories/highlights")
async def weekly_highlights(user_id: str = Depends(get_current_user), days: int = 7, db=Depends(get_db)):
    """Get photo highlights from the past week."""
    since = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(text("""
        SELECT id, storage_key, captured_at, encrypted_metadata
        FROM vault_items
        WHERE user_id = :user_id
          AND item_type = 'photo'
          AND created_at >= :since
          AND deleted_at IS NULL
        ORDER BY created_at DESC
        LIMIT 50
    """), {
        "user_id": user_id,
        "since": since
    })

    photos = result.fetchall()

    return {
        "period": f"Last {days} days",
        "total_photos": len(photos),
        "highlights": [
            {
                "id": str(p.id),
                "path": p.storage_key,
                "captured_at": p.captured_at.isoformat() if p.captured_at else None
            }
            for p in photos[:10]
        ]
    }


@router.get("/memories/people/{person_id}")
async def person_memories(person_id: str, user_id: str = Depends(get_current_user), db=Depends(get_db)):
    """Get all memories featuring a specific person."""
    neo4j = get_neo4j()
    if not neo4j:
        raise HTTPException(status_code=503, detail="Graph database not available")

    async with neo4j.session() as session:
        result = await session.run("""
            MATCH (p:Person {id: $person_id})
            RETURN p.name as name, p.first_seen as first_seen, p.last_seen as last_seen
        """, person_id=person_id)

        person = await result.single()
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        result = await session.run("""
            MATCH (p:Person {id: $person_id})-[:APPEARS_IN]->(i:VaultItem)
            WHERE i.user_id = $user_id
            RETURN i.id as id, i.captured_at as date, i.storage_key as path
            ORDER BY i.captured_at DESC
            LIMIT 100
        """, person_id=person_id, user_id=user_id)

        photos = await result.data()

    return {
        "person": {
            "id": person_id,
            "name": person["name"],
            "first_seen": person["first_seen"],
            "last_seen": person["last_seen"]
        },
        "photo_count": len(photos),
        "photos": photos
    }


@router.post("/albums/generate")
async def generate_smart_album(
    album_type: str,
    params: Dict[str, Any] = {},
    user_id: str = Depends(get_current_user),
    db=Depends(get_db)
):
    """Generate a smart album using AI."""
    return {
        "status": "coming_soon",
        "message": "Smart album generation is being built!"
    }
