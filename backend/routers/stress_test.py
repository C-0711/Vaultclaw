#!/usr/bin/env python3
import asyncio
import asyncpg
import aiohttp
import json
import time

DATABASE_URL = "postgresql://vault:vault@localhost:9500/vault"
OLLAMA_URL = "http://localhost:11434"

async def process_jobs():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    
    print("🚀 H200V Pipeline Stress Test")
    start = time.time()
    processed = 0
    failed = 0
    
    async with aiohttp.ClientSession() as session:
        while True:
            async with pool.acquire() as conn:
                jobs = await conn.fetch('''
                    UPDATE vault_processing_jobs
                    SET status = 'processing', started_at = NOW()
                    WHERE id IN (
                        SELECT id FROM vault_processing_jobs
                        WHERE status = 'pending'
                        LIMIT 10
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id, input_data
                ''')
                
                if not jobs:
                    break
                
                for job in jobs:
                    job_id = job['id']
                    try:
                        raw = job['input_data']
                        data = json.loads(raw) if isinstance(raw, str) else raw
                        text = data.get('text', 'test')
                        
                        async with session.post(
                            f"{OLLAMA_URL}/api/embeddings",
                            json={"model": "bge-m3:latest", "prompt": text}
                        ) as resp:
                            result = await resp.json()
                            dims = len(result.get('embedding', []))
                        
                        await conn.execute(
                            "UPDATE vault_processing_jobs SET status = 'completed', completed_at = NOW(), output_data = $1 WHERE id = $2",
                            json.dumps({'dims': dims}), job_id
                        )
                        processed += 1
                    except Exception as e:
                        failed += 1
                        await conn.execute(
                            "UPDATE vault_processing_jobs SET status = 'failed', error_message = $1 WHERE id = $2",
                            str(e)[:500], job_id
                        )
            
            elapsed = time.time() - start
            rate = processed / elapsed if elapsed > 0 else 0
            print(f"⚡ {processed} done | {rate:.1f}/sec | {failed} failed")
    
    elapsed = time.time() - start
    print(f"\n✅ {processed} jobs in {elapsed:.1f}s = {processed/elapsed:.1f}/sec")
    await pool.close()

asyncio.run(process_jobs())
