#!/usr/bin/env python3
"""Parallel H200V Pipeline Test - 4 workers"""
import asyncio
import asyncpg
import aiohttp
import json
import time

DATABASE_URL = "postgresql://vault:vault@localhost:9500/vault"
OLLAMA_URL = "http://localhost:11434"
NUM_WORKERS = 4

processed = 0
failed = 0
lock = asyncio.Lock()

async def worker(worker_id, pool):
    global processed, failed
    
    async with aiohttp.ClientSession() as session:
        while True:
            async with pool.acquire() as conn:
                jobs = await conn.fetch('''
                    UPDATE vault_processing_jobs
                    SET status = 'processing', started_at = NOW()
                    WHERE id IN (
                        SELECT id FROM vault_processing_jobs
                        WHERE status = 'pending'
                        LIMIT 5
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id, input_data
                ''')
                
                if not jobs:
                    return
                
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
                        
                        async with lock:
                            processed += 1
                    except Exception as e:
                        async with lock:
                            failed += 1
                        await conn.execute(
                            "UPDATE vault_processing_jobs SET status = 'failed', error_message = $1 WHERE id = $2",
                            str(e)[:500], job_id
                        )

async def main():
    global processed, failed
    
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=NUM_WORKERS*2, max_size=NUM_WORKERS*4)
    
    print(f"🚀 H200V Parallel Test - {NUM_WORKERS} workers")
    start = time.time()
    
    # Start workers
    workers = [worker(i, pool) for i in range(NUM_WORKERS)]
    
    # Progress monitor
    async def monitor():
        while True:
            await asyncio.sleep(2)
            elapsed = time.time() - start
            rate = processed / elapsed if elapsed > 0 else 0
            print(f"⚡ {processed} done | {rate:.1f}/sec | {failed} failed")
            if processed >= 500:
                return
    
    await asyncio.gather(*workers, monitor())
    
    elapsed = time.time() - start
    print(f"\n✅ {processed} jobs in {elapsed:.1f}s = {processed/elapsed:.1f}/sec")
    print(f"   Target for 100K/hr: 27.8/sec")
    print(f"   Current rate would process: {processed/elapsed*3600:.0f}/hour")
    
    await pool.close()

asyncio.run(main())
