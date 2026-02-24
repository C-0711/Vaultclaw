#!/usr/bin/env python3
"""
PROJEKT GENESIS: Pipeline Worker Launcher
Starts the H200V processing pipeline
"""

import asyncio
import os
import asyncpg
from worker import PipelineWorker, H200V_CONFIG

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://vault:vault@localhost:9500/vault"
)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

async def main():
    print("🚀 PROJEKT GENESIS: H200V Pipeline Starting...")
    print(f"   Database: {DATABASE_URL.split('@')[1]}")
    print(f"   Ollama: {OLLAMA_URL}")
    print(f"   Workers: {H200V_CONFIG['workers']}")
    print(f"   Target: {H200V_CONFIG['target_throughput']} files/sec")
    print()
    
    # Connect to database
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=20)
    print("✅ Database connected")
    
    # Test Ollama connection
    import aiohttp
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{OLLAMA_URL}/api/tags") as resp:
                data = await resp.json()
                models = [m['name'] for m in data.get('models', [])]
                print(f"✅ Ollama connected: {len(models)} models")
        except Exception as e:
            print(f"⚠️ Ollama warning: {e}")
    
    # Start workers
    workers = []
    for i in range(H200V_CONFIG['workers']):
        worker = PipelineWorker(
            db_pool=pool,
            ollama_url=OLLAMA_URL,
            worker_id=f"h200v-worker-{i+1}",
            batch_size=H200V_CONFIG['batch_size'],
            max_concurrent=H200V_CONFIG['max_concurrent_per_worker']
        )
        workers.append(worker)
    
    print(f"🔧 Starting {len(workers)} workers...")
    
    # Run all workers
    await asyncio.gather(*[w.start() for w in workers])

if __name__ == "__main__":
    asyncio.run(main())
