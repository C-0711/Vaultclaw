"""
Search routes - Semantic and Graph search
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy import text
import numpy as np

from config import settings
from database import get_db, get_neo4j, get_ollama

router = APIRouter()


# ===========================================
# SCHEMAS
# ===========================================

class SemanticSearchRequest(BaseModel):
    query: str  # Natural language query
    item_types: Optional[List[str]] = None  # Filter by type
    limit: int = 20


class GraphSearchRequest(BaseModel):
    query_type: str  # people, locations, events, timeline
    filters: Optional[dict] = None
    limit: int = 50


class SearchResult(BaseModel):
    item_id: str
    item_type: str
    score: float
    encrypted_metadata: str


# ===========================================
# ENDPOINTS
# ===========================================

@router.post("/semantic", response_model=List[SearchResult])
async def semantic_search(request: SemanticSearchRequest, db=Depends(get_db)):
    """
    Semantic search using vector embeddings.
    
    Examples:
    - "Fotos vom Strand letzten Sommer"
    - "Dokumente über Versicherung"
    - "Bilder mit Maria"
    """
    ollama = get_ollama()
    
    # Generate embedding for query
    response = await ollama.embeddings(
        model=settings.EMBEDDING_MODEL,
        prompt=request.query
    )
    query_embedding = response['embedding']

    # Format embedding as PostgreSQL vector literal
    embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"

    # Vector similarity search
    result = await db.execute(text("""
        SELECT
            vi.id as item_id,
            vi.item_type,
            vi.encrypted_metadata,
            1 - (e.embedding <=> CAST(:query_embedding AS vector)) as score
        FROM embeddings e
        JOIN vault_items vi ON e.item_id = vi.id
        WHERE vi.deleted_at IS NULL
        ORDER BY e.embedding <=> CAST(:query_embedding AS vector)
        LIMIT :limit
    """), {
        "query_embedding": embedding_str,
        "limit": request.limit
    })
    
    items = result.fetchall()
    
    return [
        SearchResult(
            item_id=str(item.item_id),
            item_type=item.item_type,
            score=item.score,
            encrypted_metadata=item.encrypted_metadata
        )
        for item in items
    ]


@router.post("/graph")
async def graph_search(request: GraphSearchRequest):
    """
    Graph-based search using Neo4j.
    
    Query types:
    - people: Find items with specific people
    - locations: Find items from specific locations
    - events: Find items from events
    - timeline: Get chronological view
    - connections: Find related items
    """
    neo4j = get_neo4j()
    
    if not neo4j:
        raise HTTPException(status_code=503, detail="Graph database not available")
    
    async with neo4j.session() as session:
        if request.query_type == "people":
            # Find items with specific people
            result = await session.run("""
                MATCH (p:Person)-[:APPEARS_IN]->(i:VaultItem)
                WHERE p.name CONTAINS $name
                RETURN i.id as item_id, p.name as person_name, i.type as item_type
                LIMIT $limit
            """, name=request.filters.get("name", ""), limit=request.limit)
            
        elif request.query_type == "locations":
            # Find items from locations
            result = await session.run("""
                MATCH (l:Location)<-[:TAKEN_AT]-(i:VaultItem)
                WHERE l.name CONTAINS $location
                RETURN i.id as item_id, l.name as location, i.type as item_type
                LIMIT $limit
            """, location=request.filters.get("location", ""), limit=request.limit)
            
        elif request.query_type == "timeline":
            # Chronological timeline
            result = await session.run("""
                MATCH (i:VaultItem)
                WHERE i.captured_at >= $start_date AND i.captured_at <= $end_date
                RETURN i.id as item_id, i.captured_at as date, i.type as item_type
                ORDER BY i.captured_at DESC
                LIMIT $limit
            """, 
                start_date=request.filters.get("start_date"),
                end_date=request.filters.get("end_date"),
                limit=request.limit
            )
            
        elif request.query_type == "connections":
            # Find related items
            result = await session.run("""
                MATCH (i:VaultItem {id: $item_id})-[r]-(connected)
                RETURN type(r) as relationship, connected
                LIMIT $limit
            """, item_id=request.filters.get("item_id"), limit=request.limit)
        
        else:
            raise HTTPException(status_code=400, detail=f"Unknown query type: {request.query_type}")
        
        records = await result.data()
        return {"results": records}


@router.get("/suggestions")
async def search_suggestions(q: str, db=Depends(get_db)):
    """
    Get search suggestions based on partial query.
    """
    # TODO: Implement autocomplete using indexed terms
    return {"suggestions": []}
