#!/usr/bin/env python3
"""
PROJEKT GENESIS Sprint 3: Multi-Worker Orchestrator
Runs multiple pipeline workers across both H200 GPUs
"""

import asyncio
import signal
import sys
from typing import List, Optional
import asyncpg
import aiohttp

# Add pipeline to path
sys.path.insert(0, "/home/christoph.bertsch/0711-Vault/backend/services/pipeline")
from redis_queue import RedisJobQueue, PipelineJob, get_queue
from gpu_orchestrator import GPUOrchestrator, get_orchestrator

class PipelineWorker:
    """Single pipeline worker instance."""
    
    def __init__(
        self,
        worker_id: str,
        queue: RedisJobQueue,
        orchestrator: GPUOrchestrator,
        ollama_url: str = "http://localhost:11434",
        batch_size: int = 10
    ):
        self.worker_id = worker_id
        self.queue = queue
        self.orchestrator = orchestrator
        self.ollama_url = ollama_url
        self.batch_size = batch_size
        self.running = False
        self.jobs_processed = 0
    
    async def start(self):
        """Start processing loop."""
        self.running = True
        print(f"🚀 Worker {self.worker_id} starting...")
        
        async with aiohttp.ClientSession() as session:
            while self.running:
                try:
                    # Claim jobs
                    jobs = await self.queue.dequeue(self.worker_id, count=self.batch_size)
                    
                    if not jobs:
                        await asyncio.sleep(0.5)
                        continue
                    
                    # Process jobs in parallel
                    tasks = [self._process_job(session, job) for job in jobs]
                    await asyncio.gather(*tasks, return_exceptions=True)
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"❌ Worker {self.worker_id} error: {e}")
                    await asyncio.sleep(1)
        
        print(f"✅ Worker {self.worker_id} stopped. Processed: {self.jobs_processed}")
    
    async def stop(self):
        """Stop the worker gracefully."""
        self.running = False
    
    async def _process_job(self, session: aiohttp.ClientSession, job: PipelineJob):
        """Process a single job."""
        try:
            job_type = job.job_type
            input_data = job.input_data
            
            # Select GPU for this job
            gpu_id = await self.orchestrator.select_gpu()
            
            # Route to handler
            if job_type == "ocr":
                result = await self._process_ocr(session, input_data, gpu_id)
            elif job_type == "extract":
                result = await self._process_extract(session, input_data, gpu_id)
            elif job_type == "embed":
                result = await self._process_embed(session, input_data, gpu_id)
            elif job_type == "full":
                result = await self._process_full(session, input_data, gpu_id)
            else:
                raise ValueError(f"Unknown job type: {job_type}")
            
            await self.queue.complete(job.id, result)
            self.jobs_processed += 1
            
        except Exception as e:
            await self.queue.fail(job.id, str(e))
    
    async def _process_ocr(self, session, input_data, gpu_id):
        """OCR a document."""
        async with session.post(
            f"{self.ollama_url}/api/generate",
            json={
                "model": "pixtral-large",
                "prompt": "Extract all text from this document. Return raw text only.",
                "images": [input_data.get("base64", "")],
                "stream": False,
                "options": {"num_gpu": 1, "main_gpu": gpu_id}
            },
            timeout=aiohttp.ClientTimeout(total=120)
        ) as resp:
            data = await resp.json()
            return {"text": data.get("response", ""), "gpu_id": gpu_id}
    
    async def _process_extract(self, session, input_data, gpu_id):
        """Extract structured data."""
        text = input_data.get("text", "")
        schema = input_data.get("schema", "ETIM-9.0")
        
        async with session.post(
            f"{self.ollama_url}/api/generate",
            json={
                "model": "llama3.1:70b",
                "prompt": f"Extract data from this text according to {schema}:\n\n{text[:8000]}\n\nReturn JSON with: product_name, features[], citations[]",
                "stream": False,
                "format": "json",
                "options": {"num_gpu": 1, "main_gpu": gpu_id}
            },
            timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            data = await resp.json()
            try:
                import json
                return json.loads(data.get("response", "{}"))
            except:
                return {"raw": data.get("response")}
    
    async def _process_embed(self, session, input_data, gpu_id):
        """Generate embeddings."""
        text = input_data.get("text", "")
        
        async with session.post(
            f"{self.ollama_url}/api/embeddings",
            json={"model": "bge-m3:latest", "prompt": text[:8000]},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            data = await resp.json()
            return {
                "embedding": data.get("embedding", [])[:10],  # Truncate for storage
                "dimensions": len(data.get("embedding", []))
            }
    
    async def _process_full(self, session, input_data, gpu_id):
        """Full pipeline: OCR -> Extract -> Embed."""
        # OCR
        ocr_result = await self._process_ocr(session, input_data, gpu_id)
        text = ocr_result.get("text", "")
        
        if not text:
            return {"error": "OCR returned empty text"}
        
        # Extract
        extract_result = await self._process_extract(
            session, {"text": text, "schema": input_data.get("schema")}, gpu_id
        )
        
        # Embed
        embed_result = await self._process_embed(session, {"text": text}, gpu_id)
        
        return {
            "text": text[:500],  # Preview
            "extracted": extract_result,
            "embedding_dims": embed_result.get("dimensions"),
            "gpu_id": gpu_id
        }


class MultiWorkerOrchestrator:
    """Manages multiple pipeline workers."""
    
    def __init__(self, num_workers: int = 8, batch_size: int = 10):
        self.num_workers = num_workers
        self.batch_size = batch_size
        self.workers: List[PipelineWorker] = []
        self.queue: Optional[RedisJobQueue] = None
        self.orchestrator: Optional[GPUOrchestrator] = None
    
    async def start(self):
        """Start all workers."""
        print(f"🚀 Starting {self.num_workers} workers...")
        
        # Initialize shared resources
        self.queue = await get_queue()
        self.orchestrator = get_orchestrator()
        
        # Check GPUs
        gpus = await self.orchestrator.get_gpu_status()
        print(f"📊 GPUs available: {len(gpus)}")
        for gpu in gpus:
            print(f"   GPU {gpu.index}: {gpu.name} - {gpu.memory_free}MB free")
        
        # Create workers
        for i in range(self.num_workers):
            worker = PipelineWorker(
                worker_id=f"worker-{i}",
                queue=self.queue,
                orchestrator=self.orchestrator,
                batch_size=self.batch_size
            )
            self.workers.append(worker)
        
        # Start all workers
        tasks = [worker.start() for worker in self.workers]
        
        # Handle shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))
        
        await asyncio.gather(*tasks)
    
    async def shutdown(self):
        """Gracefully shutdown all workers."""
        print("\n🛑 Shutting down workers...")
        for worker in self.workers:
            await worker.stop()
        
        if self.queue:
            await self.queue.disconnect()
        
        print("✅ All workers stopped")


async def main():
    """Main entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="H200V Pipeline Workers")
    parser.add_argument("--workers", type=int, default=8, help="Number of workers")
    parser.add_argument("--batch", type=int, default=10, help="Batch size per worker")
    args = parser.parse_args()
    
    print("=" * 60)
    print("🔥 PROJEKT GENESIS: H200V Pipeline Workers")
    print(f"   Workers: {args.workers}")
    print(f"   Batch size: {args.batch}")
    print(f"   Target: 100K docs/hour (~28/sec)")
    print("=" * 60)
    
    orchestrator = MultiWorkerOrchestrator(
        num_workers=args.workers,
        batch_size=args.batch
    )
    await orchestrator.start()


if __name__ == "__main__":
    asyncio.run(main())
