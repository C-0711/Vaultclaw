#!/usr/bin/env python3
"""
0711 Vault Background Worker
Processes images: face detection, embeddings, OCR
"""

import sys
# Unbuffered output for Docker logs
sys.stdout = sys.stderr

import asyncio
import os
import httpx
import asyncpg
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vault:vault@localhost:5432/vault")
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8001")

# Albert Storage (replaces MinIO)
from storage_albert import VaultCrypto

async def process_item(conn, item_id: str, user_id: str, task_type: str):
    """Process a single item from the queue."""
    print(f"Processing item {item_id} ({task_type})")
    
    try:
        # Get item details
        item = await conn.fetchrow("""
            SELECT id, storage_key, item_type, mime_type
            FROM vault_items
            WHERE id = $1 AND user_id = $2
        """, item_id, user_id)
        
        if not item:
            print(f"Item {item_id} not found")
            return False
        
        # Retrieve file from Albert Storage (PostgreSQL)
        print(f"  Retrieving from Albert Storage: {item['storage_key']}")
        
        content_row = await conn.fetchrow("""
            SELECT encrypted_content FROM vault_content
            WHERE storage_key = $1 AND user_id = $2
        """, item['storage_key'], user_id)
        
        if not content_row:
            print(f"  Content not found in storage")
            return False
        
        # Decrypt content
        try:
            file_data = VaultCrypto.decrypt(content_row['encrypted_content'])
            print(f"  Decrypted {len(file_data)} bytes")
        except Exception as e:
            print(f"  Decryption error: {e}")
            return False

        async with httpx.AsyncClient() as client:
            
            # Process with AI service
            print(f"  Processing with AI service at {AI_SERVICE_URL}")
            if item['item_type'] == 'photo':
                # Full image processing
                response = await client.post(
                    f"{AI_SERVICE_URL}/process/full",
                    files={"file": ("image.jpg", file_data, item['mime_type'] or "image/jpeg")},
                    data={
                        "detect_faces": "true",
                        "generate_embedding": "true",
                        "analyze": "true"
                    },
                    timeout=120
                )
                
                print(f"  AI service response: {response.status_code}")
                if response.status_code == 200:
                    result = response.json()
                    print(f"  Got result: {len(result.get('faces', []))} faces, embedding={bool(result.get('embedding'))}")

                    # Save embedding
                    if "embedding" in result and result["embedding"]:
                        embedding_str = "[" + ",".join(map(str, result["embedding"])) + "]"
                        await conn.execute("""
                            INSERT INTO embeddings (item_id, user_id, embedding_type, embedding)
                            VALUES ($1, $2, 'clip', $3::vector)
                        """, item_id, user_id, embedding_str)
                    
                    # Save detected faces
                    if "faces" in result:
                        for face in result["faces"]:
                            # Check if face has embedding
                            if "embedding" in face and face["embedding"]:
                                embedding_str = "[" + ",".join(map(str, face["embedding"])) + "]"
                                await conn.execute("""
                                    INSERT INTO faces (item_id, user_id, bbox_x, bbox_y, bbox_width, bbox_height, detection_confidence, embedding)
                                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)
                                """, item_id, user_id,
                                    face["bbox_x"], face["bbox_y"],
                                    face["bbox_width"], face["bbox_height"],
                                    face.get("confidence", 0.0), embedding_str)
                            else:
                                await conn.execute("""
                                    INSERT INTO faces (item_id, user_id, bbox_x, bbox_y, bbox_width, bbox_height, detection_confidence)
                                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                                """, item_id, user_id,
                                    face["bbox_x"], face["bbox_y"],
                                    face["bbox_width"], face["bbox_height"],
                                    face.get("confidence", 0.0))
                    
                    print(f"Processed photo: {len(result.get('faces', []))} faces, embedding saved")
                
            elif item['item_type'] == 'document':
                # OCR and categorization
                response = await client.post(
                    f"{AI_SERVICE_URL}/categorize/document",
                    files={"file": ("document.pdf", file_data, item['mime_type'] or "application/pdf")},
                    timeout=120
                )
                
                if response.status_code == 200:
                    result = response.json()
                    
                    await conn.execute("""
                        INSERT INTO document_metadata (item_id, user_id, category, encrypted_summary)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (item_id) DO UPDATE SET category = $3, encrypted_summary = $4
                    """, item_id, user_id, 
                        result.get("category", "other"),
                        result.get("summary", ""))
                    
                    print(f"Processed document: {result.get('category')}")
        
        # Update item status
        await conn.execute("""
            UPDATE vault_items 
            SET processing_status = 'complete', processed_at = NOW()
            WHERE id = $1
        """, item_id)
        
        return True
        
    except Exception as e:
        print(f"Error processing item {item_id}: {e}")
        return False


async def worker_loop():
    """Main worker loop - polls queue and processes items."""
    print("🚀 Starting 0711 Worker...")
    
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    
    while True:
        try:
            async with pool.acquire() as conn:
                # Get next item from queue
                task = await conn.fetchrow("""
                    UPDATE processing_queue
                    SET status = 'processing', started_at = NOW(), attempts = attempts + 1
                    WHERE id = (
                        SELECT id FROM processing_queue
                        WHERE status = 'pending' AND attempts < max_attempts
                        ORDER BY priority DESC, created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id, item_id, user_id, task_type
                """)
                
                if task:
                    success = await process_item(
                        conn, 
                        str(task["item_id"]), 
                        str(task["user_id"]), 
                        task["task_type"]
                    )
                    
                    if success:
                        await conn.execute("""
                            UPDATE processing_queue
                            SET status = 'complete', completed_at = NOW()
                            WHERE id = $1
                        """, task["id"])
                    else:
                        await conn.execute("""
                            UPDATE processing_queue
                            SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
                                error_message = 'Processing failed'
                            WHERE id = $1
                        """, task["id"])
                else:
                    # No tasks, sleep
                    await asyncio.sleep(5)
                    
        except Exception as e:
            print(f"Worker error: {e}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(worker_loop())
