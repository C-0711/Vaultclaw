"""
Moltbot AI — The brain of 0711 Vault
Powered by Claude Sonnet 4.6 via Anthropic API with TOOL CALLING
Can create calendar events, search vault, analyze images, and more!

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
import httpx
import uuid

from config import settings
from database import get_db, get_neo4j, get_ollama, get_redis
from auth import get_current_user

router = APIRouter()

# ===========================================
# ANTHROPIC CLIENT (lazy init)
# ===========================================

_anthropic_client = None
CLAUDE_MODEL = "claude-sonnet-4-20250514"


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
# TOOL DEFINITIONS
# ===========================================

MOLTBOT_TOOLS = [
    {
        "name": "create_calendar_event",
        "description": "Create a new calendar event in the user's vault calendar. Use this when the user wants to schedule something, create a reminder, or add an event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The title/name of the event"
                },
                "date": {
                    "type": "string",
                    "description": "The date of the event in YYYY-MM-DD format"
                },
                "time": {
                    "type": "string",
                    "description": "The time of the event in HH:MM format (24h). Optional, defaults to 09:00"
                },
                "description": {
                    "type": "string",
                    "description": "Optional description or notes for the event"
                },
                "color": {
                    "type": "string",
                    "enum": ["amber", "green", "purple", "red", "blue"],
                    "description": "Color tag for the event. Defaults to amber."
                }
            },
            "required": ["title", "date"]
        }
    },
    {
        "name": "list_calendar_events",
        "description": "List upcoming calendar events. Use this when the user asks about their schedule, upcoming events, or what's on their calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look ahead. Default is 7."
                }
            },
            "required": []
        }
    },
    {
        "name": "get_vault_stats",
        "description": "Get statistics about the user's vault - number of photos, documents, storage used, etc. Use when user asks about their vault contents or storage.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "search_vault",
        "description": "Search for photos or documents in the vault. Use when user wants to find specific files, photos from a date, or documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query - can be a description, date, person name, or location"
                },
                "item_type": {
                    "type": "string",
                    "enum": ["photo", "document", "all"],
                    "description": "Type of items to search. Default is 'all'."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return. Default is 10."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "delete_calendar_event",
        "description": "Delete a calendar event by its ID. Use when user wants to remove or cancel an event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "The UUID of the event to delete"
                }
            },
            "required": ["event_id"]
        }
    },
    {
        "name": "analyze_vault_file",
        "description": "Analyze an image or document from the user's vault using AI vision. Use when user wants to know what's in a photo, understand a document, or get insights about a file. Can describe images, read text in photos, analyze documents, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "The UUID of the vault file to analyze"
                },
                "question": {
                    "type": "string",
                    "description": "Optional specific question about the file. If not provided, gives general analysis."
                }
            },
            "required": ["file_id"]
        }
    },
    {
        "name": "list_vault_files",
        "description": "List recent files in the user's vault. Use this to see what files are available before analyzing them, or when user asks to see their files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_type": {
                    "type": "string",
                    "enum": ["photo", "document", "video", "all"],
                    "description": "Filter by file type. Default is 'all'."
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of files to return. Default is 10."
                }
            },
            "required": []
        }
    },
    {
        "name": "share_file_to_chat",
        "description": "Share a vault file to a secure chat conversation. Use when user wants to send a photo or document to someone in their secure chat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "The UUID of the vault file to share"
                },
                "conversation_id": {
                    "type": "string",
                    "description": "The UUID of the chat conversation to share to"
                },
                "message": {
                    "type": "string",
                    "description": "Optional message to include with the file"
                }
            },
            "required": ["file_id", "conversation_id"]
        }
    }
]


# ===========================================
# TOOL HANDLERS
# ===========================================

async def execute_tool(tool_name: str, tool_input: dict, user_id: str, db) -> str:
    """Execute a tool and return the result as a string."""
    
    if tool_name == "create_calendar_event":
        return await tool_create_calendar_event(tool_input, user_id, db)
    
    elif tool_name == "list_calendar_events":
        return await tool_list_calendar_events(tool_input, user_id, db)
    
    elif tool_name == "get_vault_stats":
        return await tool_get_vault_stats(user_id, db)
    
    elif tool_name == "search_vault":
        return await tool_search_vault(tool_input, user_id, db)
    
    elif tool_name == "delete_calendar_event":
        return await tool_delete_calendar_event(tool_input, user_id, db)
    
    elif tool_name == "analyze_vault_file":
        return await tool_analyze_vault_file(tool_input, user_id, db)
    
    elif tool_name == "list_vault_files":
        return await tool_list_vault_files(tool_input, user_id, db)
    
    elif tool_name == "share_file_to_chat":
        return await tool_share_file_to_chat(tool_input, user_id, db)
    
    else:
        return f"Unknown tool: {tool_name}"


async def tool_create_calendar_event(params: dict, user_id: str, db) -> str:
    """Create a calendar event."""
    try:
        title = params.get("title", "Untitled Event")
        date_str = params.get("date")
        time_str = params.get("time", "09:00")
        description = params.get("description", "")
        color = params.get("color", "amber")
        
        # Parse date and time
        if 'T' in date_str:
            event_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        else:
            event_date = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        
        # Insert into database
        result = await db.execute(text("""
            INSERT INTO calendar_events (id, user_id, title, event_date, description, color, created_at, updated_at)
            VALUES (:id, :user_id, :title, :event_date, :description, :color, NOW(), NOW())
            RETURNING id
        """), {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "title": title,
            "event_date": event_date,
            "description": description,
            "color": color
        })
        await db.commit()
        
        event_id = result.scalar()
        formatted_date = event_date.strftime("%A, %B %d, %Y at %H:%M")
        
        return f"✅ Created event: **{title}** on {formatted_date}\nEvent ID: {event_id}"
        
    except Exception as e:
        print(f"[Moltbot] Calendar create error: {e}")
        traceback.print_exc()
        return f"❌ Failed to create event: {str(e)}"


async def tool_list_calendar_events(params: dict, user_id: str, db) -> str:
    """List upcoming calendar events."""
    try:
        days = params.get("days", 7)
        now = datetime.utcnow()
        end = now + timedelta(days=days)
        
        result = await db.execute(text("""
            SELECT id, title, event_date, description, color
            FROM calendar_events
            WHERE user_id = :user_id 
              AND event_date >= :now 
              AND event_date <= :end
            ORDER BY event_date ASC
            LIMIT 20
        """), {
            "user_id": user_id,
            "now": now,
            "end": end
        })
        
        events = result.fetchall()
        
        if not events:
            return f"📅 No events scheduled for the next {days} days."
        
        output = f"📅 **Upcoming events (next {days} days):**\n\n"
        for e in events:
            date_str = e.event_date.strftime("%a %b %d, %H:%M")
            output += f"• **{e.title}** — {date_str}\n"
            if e.description:
                output += f"  _{e.description}_\n"
        
        return output
        
    except Exception as e:
        print(f"[Moltbot] Calendar list error: {e}")
        return f"❌ Failed to list events: {str(e)}"


async def tool_get_vault_stats(user_id: str, db) -> str:
    """Get vault statistics."""
    try:
        # Get counts by type
        result = await db.execute(text("""
            SELECT 
                COUNT(*) FILTER (WHERE item_type = 'photo') as photos,
                COUNT(*) FILTER (WHERE item_type = 'document') as documents,
                COUNT(*) FILTER (WHERE item_type = 'video') as videos,
                COALESCE(SUM(file_size), 0) as total_bytes
            FROM vault_items
            WHERE user_id = :user_id AND deleted_at IS NULL
        """), {"user_id": user_id})
        
        stats = result.fetchone()
        
        # Get calendar event count
        cal_result = await db.execute(text("""
            SELECT COUNT(*) as count FROM calendar_events WHERE user_id = :user_id
        """), {"user_id": user_id})
        events = cal_result.scalar()
        
        total_gb = (stats.total_bytes or 0) / (1024 * 1024 * 1024)
        
        return f"""📊 **Your Vault Stats:**

• 📷 Photos: {stats.photos or 0}
• 📄 Documents: {stats.documents or 0}
• 🎥 Videos: {stats.videos or 0}
• 📅 Calendar Events: {events or 0}
• 💾 Storage Used: {total_gb:.2f} GB of 5 GB

Everything is encrypted and stored locally on the H200V! 🔐"""
        
    except Exception as e:
        print(f"[Moltbot] Stats error: {e}")
        return f"❌ Failed to get stats: {str(e)}"


async def tool_search_vault(params: dict, user_id: str, db) -> str:
    """Search vault items."""
    try:
        query = params.get("query", "")
        item_type = params.get("item_type", "all")
        limit = params.get("limit", 10)
        
        type_filter = ""
        if item_type == "photo":
            type_filter = "AND item_type = 'photo'"
        elif item_type == "document":
            type_filter = "AND item_type = 'document'"
        
        # Simple search by filename/path for now
        result = await db.execute(text(f"""
            SELECT id, item_type, storage_key, file_size, captured_at, created_at
            FROM vault_items
            WHERE user_id = :user_id 
              AND deleted_at IS NULL
              AND (storage_key ILIKE :query OR item_type ILIKE :query)
              {type_filter}
            ORDER BY created_at DESC
            LIMIT :limit
        """), {
            "user_id": user_id,
            "query": f"%{query}%",
            "limit": limit
        })
        
        items = result.fetchall()
        
        if not items:
            return f"🔍 No results found for '{query}'"
        
        output = f"🔍 **Found {len(items)} items for '{query}':**\n\n"
        for item in items:
            date_str = (item.captured_at or item.created_at).strftime("%Y-%m-%d") if (item.captured_at or item.created_at) else "Unknown"
            size_kb = (item.file_size or 0) / 1024
            icon = "📷" if item.item_type == "photo" else "📄" if item.item_type == "document" else "📁"
            output += f"• {icon} {item.storage_key.split('/')[-1]} — {date_str} ({size_kb:.0f} KB)\n"
        
        return output
        
    except Exception as e:
        print(f"[Moltbot] Search error: {e}")
        return f"❌ Search failed: {str(e)}"


async def tool_delete_calendar_event(params: dict, user_id: str, db) -> str:
    """Delete a calendar event."""
    try:
        event_id = params.get("event_id")
        
        # Check ownership first
        result = await db.execute(text("""
            SELECT title FROM calendar_events WHERE id = :event_id AND user_id = :user_id
        """), {"event_id": event_id, "user_id": user_id})
        
        event = result.fetchone()
        if not event:
            return "❌ Event not found or you don't have permission to delete it."
        
        await db.execute(text("""
            DELETE FROM calendar_events WHERE id = :event_id AND user_id = :user_id
        """), {"event_id": event_id, "user_id": user_id})
        await db.commit()
        
        return f"✅ Deleted event: **{event.title}**"
        
    except Exception as e:
        print(f"[Moltbot] Delete error: {e}")
        return f"❌ Failed to delete event: {str(e)}"


async def tool_list_vault_files(params: dict, user_id: str, db) -> str:
    """List recent vault files."""
    try:
        file_type = params.get("file_type", "all")
        limit = params.get("limit", 10)
        
        type_filter = ""
        if file_type in ["photo", "document", "video"]:
            type_filter = f"AND item_type = '{file_type}'"
        
        result = await db.execute(text(f"""
            SELECT id, item_type, storage_key, file_size, mime_type, created_at
            FROM vault_items
            WHERE user_id = :user_id AND deleted_at IS NULL {type_filter}
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"user_id": user_id, "limit": limit})
        
        items = result.fetchall()
        
        if not items:
            return "📁 No files in your vault yet. Upload some photos or documents!"
        
        output = f"📁 **Your recent files ({len(items)}):**\n\n"
        for item in items:
            date_str = item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "Unknown"
            size_kb = (item.file_size or 0) / 1024
            filename = item.storage_key.split('/')[-1] if item.storage_key else "Unknown"
            icon = "📷" if item.item_type == "photo" else "📄" if item.item_type == "document" else "🎥" if item.item_type == "video" else "📁"
            # Include file ID so user/Moltbot can reference it
            output += f"• {icon} **{filename}** — {date_str} ({size_kb:.0f} KB)\n"
            output += f"  ID: `{item.id}`\n"
        
        output += "\n💡 *Tip: Use the file ID to analyze or share a file!*"
        return output
        
    except Exception as e:
        print(f"[Moltbot] List files error: {e}")
        traceback.print_exc()
        return f"❌ Failed to list files: {str(e)}"


async def tool_analyze_vault_file(params: dict, user_id: str, db) -> str:
    """Analyze a vault file using Claude's vision."""
    try:
        file_id = params.get("file_id")
        question = params.get("question", "")
        
        # Get file metadata
        result = await db.execute(text("""
            SELECT id, item_type, storage_key, mime_type, file_size
            FROM vault_items
            WHERE id = :file_id AND user_id = :user_id AND deleted_at IS NULL
        """), {"file_id": file_id, "user_id": user_id})
        
        item = result.fetchone()
        if not item:
            return f"❌ File not found. Use `list_vault_files` to see available files."
        
        # Check if it's an image (we can analyze)
        mime = item.mime_type or ""
        if not mime.startswith("image/"):
            if mime.startswith("text/") or mime == "application/pdf":
                return f"📄 This is a document ({mime}). Document analysis coming soon! For now, I can only analyze images."
            return f"❌ Cannot analyze this file type ({mime}). I can analyze images (photos, screenshots, etc.)"
        
        # Retrieve file content from Albert Storage
        try:
            from storage_albert import retrieve_content
            content = await retrieve_content(item.storage_key, user_id)
        except Exception as e:
            print(f"[Moltbot] Failed to retrieve file: {e}")
            return f"❌ Could not retrieve file from storage: {str(e)}"
        
        # Encode as base64
        import base64
        image_data = base64.standard_b64encode(content).decode("utf-8")
        
        # Determine media type
        media_type = mime if mime in ["image/jpeg", "image/png", "image/gif", "image/webp"] else "image/jpeg"
        
        # Build prompt
        if question:
            analysis_prompt = f"Analyze this image and answer the following question: {question}"
        else:
            analysis_prompt = "Analyze this image in detail. Describe what you see, any text visible, people, objects, location, mood, and anything interesting or notable."
        
        # Call Claude with vision
        client = _get_anthropic()
        if not client:
            return "❌ AI service unavailable. Please try again later."
        
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1500,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": analysis_prompt
                        }
                    ]
                }]
            )
            
            analysis = response.content[0].text
            filename = item.storage_key.split('/')[-1] if item.storage_key else "file"
            
            return f"🔍 **Analysis of {filename}:**\n\n{analysis}"
            
        except Exception as e:
            print(f"[Moltbot] Vision API error: {e}")
            traceback.print_exc()
            return f"❌ Failed to analyze image: {str(e)}"
        
    except Exception as e:
        print(f"[Moltbot] Analyze file error: {e}")
        traceback.print_exc()
        return f"❌ Failed to analyze file: {str(e)}"


async def tool_share_file_to_chat(params: dict, user_id: str, db) -> str:
    """Share a vault file to a secure chat conversation."""
    try:
        file_id = params.get("file_id")
        conversation_id = params.get("conversation_id")
        message_text = params.get("message", "")
        
        # Verify file exists and belongs to user
        result = await db.execute(text("""
            SELECT id, item_type, storage_key, mime_type, file_size
            FROM vault_items
            WHERE id = :file_id AND user_id = :user_id AND deleted_at IS NULL
        """), {"file_id": file_id, "user_id": user_id})
        
        item = result.fetchone()
        if not item:
            return f"❌ File not found in your vault."
        
        # Verify user is member of conversation
        member_check = await db.execute(text("""
            SELECT 1 FROM chat_members WHERE conversation_id = CAST(:conv_id AS UUID) AND user_id = CAST(:user_id AS UUID)
        """), {"conv_id": conversation_id, "user_id": user_id})
        
        if not member_check.fetchone():
            return f"❌ You're not a member of that conversation."
        
        # Create message with file attachment
        # The encrypted_media_ref will contain file reference info
        # In real E2E encryption, this would be encrypted, but for now we use a simple JSON reference
        import json as json_mod
        media_ref = json_mod.dumps({
            "vault_file_id": str(item.id),
            "filename": item.storage_key.split('/')[-1] if item.storage_key else "file",
            "mime_type": item.mime_type,
            "file_size": item.file_size,
            "item_type": item.item_type
        })
        
        # Determine message type based on file
        msg_type = "image" if item.item_type == "photo" else "file"
        
        # Insert message - use bytes for bytea columns
        message_id = str(uuid.uuid4())
        content_bytes = message_text.encode('utf-8') if message_text else b'\x00'
        media_ref_bytes = media_ref.encode('utf-8')
        
        await db.execute(text("""
            INSERT INTO chat_messages (id, conversation_id, sender_id, encrypted_content, message_type, encrypted_media_ref, created_at)
            VALUES (:msg_id, CAST(:conv_id AS UUID), CAST(:user_id AS UUID), :content, :msg_type, :media_ref, NOW())
        """), {
            "msg_id": message_id,
            "conv_id": conversation_id,
            "user_id": user_id,
            "content": content_bytes,
            "msg_type": msg_type,
            "media_ref": media_ref_bytes
        })
        
        # Update conversation timestamp
        await db.execute(text("""
            UPDATE chat_conversations SET updated_at = NOW() WHERE id = CAST(:conv_id AS UUID)
        """), {"conv_id": conversation_id})
        
        await db.commit()
        
        filename = item.storage_key.split('/')[-1] if item.storage_key else "file"
        icon = "📷" if item.item_type == "photo" else "📄"
        return f"✅ Shared {icon} **{filename}** to the conversation!"
        
    except Exception as e:
        print(f"[Moltbot] Share file error: {e}")
        traceback.print_exc()
        return f"❌ Failed to share file: {str(e)}"


# ===========================================
# MOLTBOT SYSTEM PROMPT
# ===========================================

def get_system_prompt():
    """Generate system prompt with current date."""
    today = datetime.utcnow().strftime("%A, %B %d, %Y")
    return f"""Du bist **Moltbot**, der KI-Assistent im 0711 Vault — entwickelt von OpenClaw, betrieben auf einem H200V Server in Stuttgart.

**Aktuelles Datum: {today}**

## Deine Persoenlichkeit
- Freundlich, hilfsbereit, und ein bisschen witzig
- Stolz auf Stuttgart und die 0711-Community
- Du sprichst Deutsch und Englisch — antworte in der Sprache des Users
- Du bist technisch kompetent aber erklaerst Dinge einfach
- Wenn du etwas nicht weisst, sagst du das ehrlich

## Was du kannst (mit Tools!)
1. **Kalender-Events erstellen**: "Erstell einen Termin für morgen um 14 Uhr"
2. **Kalender anzeigen**: "Was steht diese Woche an?"
3. **Vault durchsuchen**: "Zeig mir Fotos vom Urlaub"
4. **Vault-Statistiken**: "Wie viele Fotos habe ich?"
5. **Events loeschen**: "Loesch den Meeting-Termin"
6. **Dateien auflisten**: "Zeig mir meine Dateien" oder "Was hab ich hochgeladen?"
7. **Bilder analysieren**: "Analysier dieses Foto" oder "Was ist auf dem Bild?"
8. **Dateien im Chat teilen**: "Schick das Foto an [Person]"

## Wichtige Regeln
- Nutze die Tools wenn der User eine Aktion ausfuehren will
- Bei Kalender-Events: Frag nach wenn Datum oder Titel fehlt
- Bestaettige ausgefuehrte Aktionen immer
- Der Vault ist 100% privat — Daten bleiben auf dem H200V Server

## Ueber den Vault
- **0711 Vault** ist ein privater digitaler Tresor fuer Fotos, Dokumente und Erinnerungen
- Laeuft auf **OpenClaw H200V** Hardware in Stuttgart
- Ende-zu-Ende verschluesselt
- KI-Features (Gesichtserkennung, Suche) laufen lokal

Sei hilfreich, sei proaktiv mit Tools, sei Moltbot! 🐾"""


MOLTBOT_SYSTEM = get_system_prompt()  # For backwards compat


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
    tool_calls: Optional[List[Dict[str, Any]]] = None


class MemoryRequest(BaseModel):
    memory_type: str
    date: Optional[datetime] = None


class MemoryResponse(BaseModel):
    memories: List[Dict[str, Any]]
    message: str


# ===========================================
# CLAUDE CHAT WITH TOOLS
# ===========================================

async def _chat_claude_with_tools(messages: list, user_id: str, db):
    """Call Claude with tool definitions and execute any tool calls."""
    client = _get_anthropic()
    if not client:
        return None, []

    # Separate system prompt from messages
    system_text = ""
    chat_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
        else:
            chat_messages.append({"role": msg["role"], "content": msg["content"]})

    tool_results = []
    max_iterations = 5
    
    for iteration in range(max_iterations):
        # Call Claude with tools
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=system_text,
            messages=chat_messages,
            tools=MOLTBOT_TOOLS
        )
        
        # Check if Claude wants to use tools
        if response.stop_reason == "tool_use":
            # Process tool calls
            assistant_content = response.content
            tool_use_blocks = [block for block in assistant_content if block.type == "tool_use"]
            
            # Add assistant message with tool use
            chat_messages.append({
                "role": "assistant",
                "content": assistant_content
            })
            
            # Execute each tool and collect results
            tool_results_content = []
            for tool_block in tool_use_blocks:
                print(f"[Moltbot] Executing tool: {tool_block.name}")
                result = await execute_tool(
                    tool_block.name,
                    tool_block.input,
                    user_id,
                    db
                )
                tool_results.append({
                    "tool": tool_block.name,
                    "input": tool_block.input,
                    "result": result
                })
                tool_results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": result
                })
            
            # Add tool results to messages
            chat_messages.append({
                "role": "user",
                "content": tool_results_content
            })
            
            # Continue loop to get Claude's response after tool execution
            continue
        
        else:
            # Claude is done, extract final text response
            text_response = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text_response += block.text
            
            return text_response, tool_results
    
    return "Ich habe zu viele Tool-Aufrufe gemacht. Bitte versuche es nochmal mit einer einfacheren Anfrage.", tool_results


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
    Powered by Claude Sonnet with TOOL CALLING!
    """
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

    # Build messages
    messages = [{"role": "system", "content": get_system_prompt()}]

    # Add history (skip tool-related messages for simplicity)
    for msg in history[-10:]:
        if msg.get("role") in ["user", "assistant"] and isinstance(msg.get("content"), str):
            messages.append(msg)

    messages.append({"role": "user", "content": request.message})

    # Generate response with tools
    assistant_response = None
    tool_calls = []

    try:
        assistant_response, tool_calls = await _chat_claude_with_tools(messages, user_id, db)
    except Exception as e:
        print(f"[Moltbot] Claude error: {e}")
        traceback.print_exc()

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
        sources=[],
        tool_calls=tool_calls if tool_calls else None
    )


# ===========================================
# STREAMING ENDPOINT (simplified, no tools)
# ===========================================

@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, user_id: str = Depends(get_current_user), db=Depends(get_db)):
    """
    Streaming chat with Moltbot via Server-Sent Events.
    Note: Streaming doesn't support tool calling - use /chat for tools.
    """
    messages = [
        {"role": "system", "content": get_system_prompt()},
        {"role": "user", "content": request.message}
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
            fallback = await _chat_ollama_fallback(messages)
            if fallback:
                yield f"data: {json.dumps({'content': fallback})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            else:
                yield f"data: {json.dumps({'error': 'Moltbot ist gerade nicht erreichbar.'})}\n\n"

    return StreamingResponse(
        generate_claude(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


# ===========================================
# MEMORY FEATURES
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
        """), {"user_id": user_id, "start": start, "end": end})

        photos = result.fetchall()
        if photos:
            memories.append({
                "year": target_date.year,
                "years_ago": years_ago,
                "photos": [{"id": str(p.id), "path": p.storage_key, "captured_at": p.captured_at.isoformat()} for p in photos]
            })

    message = f"Found memories from {len(memories)} previous years!" if memories else "No memories from this day yet."
    return MemoryResponse(memories=memories, message=message)


@router.get("/memories/highlights")
async def weekly_highlights(user_id: str = Depends(get_current_user), days: int = 7, db=Depends(get_db)):
    """Get photo highlights from the past week."""
    since = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(text("""
        SELECT id, storage_key, captured_at FROM vault_items
        WHERE user_id = :user_id AND item_type = 'photo' AND created_at >= :since AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT 50
    """), {"user_id": user_id, "since": since})

    photos = result.fetchall()
    return {
        "period": f"Last {days} days",
        "total_photos": len(photos),
        "highlights": [{"id": str(p.id), "path": p.storage_key, "captured_at": p.captured_at.isoformat() if p.captured_at else None} for p in photos[:10]]
    }


@router.get("/conversation/{conversation_id}")
async def get_conversation(conversation_id: str, user_id: str = Depends(get_current_user)):
    """Get conversation history."""
    redis = get_redis()
    if not redis:
        return {"messages": []}
    
    try:
        cached = await redis.get(f"conversation:{conversation_id}")
        if cached:
            return {"conversation_id": conversation_id, "messages": json.loads(cached)}
    except Exception:
        pass
    
    return {"conversation_id": conversation_id, "messages": []}


@router.delete("/conversation/{conversation_id}")
async def clear_conversation(conversation_id: str, user_id: str = Depends(get_current_user)):
    """Clear conversation history."""
    redis = get_redis()
    if redis:
        try:
            await redis.delete(f"conversation:{conversation_id}")
        except Exception:
            pass
    return {"cleared": True}


@router.get("/conversations")
async def list_conversations(user_id: str = Depends(get_current_user)):
    """List user's recent conversations."""
    redis = get_redis()
    if not redis:
        return {"conversations": []}
    
    try:
        pattern = f"conversation:conv_{user_id}_*"
        conversations = []
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                conv_id = key_str.replace("conversation:", "")
                data = await redis.get(key)
                if data:
                    msgs = json.loads(data)
                    preview = msgs[0]["content"][:50] + "..." if msgs else ""
                    conversations.append({"id": conv_id, "preview": preview, "count": len(msgs)})
            if cursor == 0:
                break
        return {"conversations": conversations[:20]}
    except Exception:
        return {"conversations": []}
