"""
PROJEKT GENESIS Sprint 3: Multi-GPU Orchestrator
Load balances processing across both H200 GPUs
"""

import asyncio
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
import aiohttp
import subprocess
import json

@dataclass
class GPUInfo:
    """GPU status information."""
    index: int
    name: str
    memory_total: int  # MB
    memory_used: int   # MB
    memory_free: int   # MB
    utilization: int   # %
    temperature: int   # °C
    
    @property
    def memory_utilization(self) -> float:
        return self.memory_used / self.memory_total if self.memory_total > 0 else 0
    
    @property
    def is_available(self) -> bool:
        """GPU is available if utilization < 90% and memory < 85%."""
        return self.utilization < 90 and self.memory_utilization < 0.85


class GPUOrchestrator:
    """
    Orchestrates work across multiple H200 GPUs.
    
    H200V has 2x NVIDIA H200 NVL with 141GB VRAM each (282GB total).
    Uses nvidia-smi for monitoring and load balancing.
    """
    
    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self.ollama_url = ollama_url
        self.gpu_count = 2
        self.gpus: List[GPUInfo] = []
        self.last_poll = 0
        self.poll_interval = 5  # seconds
    
    async def get_gpu_status(self, force: bool = False) -> List[GPUInfo]:
        """Get current GPU status via nvidia-smi."""
        now = time.time()
        if not force and (now - self.last_poll) < self.poll_interval and self.gpus:
            return self.gpus
        
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits"
                ],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            self.gpus = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 7:
                    self.gpus.append(GPUInfo(
                        index=int(parts[0]),
                        name=parts[1],
                        memory_total=int(parts[2]),
                        memory_used=int(parts[3]),
                        memory_free=int(parts[4]),
                        utilization=int(parts[5]),
                        temperature=int(parts[6])
                    ))
            
            self.last_poll = now
            
        except Exception as e:
            print(f"⚠️ nvidia-smi failed: {e}")
            # Return cached or empty
        
        return self.gpus
    
    async def select_gpu(self) -> int:
        """
        Select the best GPU for the next job.
        Strategy: least loaded by memory, then utilization.
        """
        gpus = await self.get_gpu_status()
        
        available = [g for g in gpus if g.is_available]
        if not available:
            # All busy, pick least loaded
            available = sorted(gpus, key=lambda g: (g.memory_utilization, g.utilization))
            if available:
                return available[0].index
            return 0  # Default
        
        # Sort by memory free (descending), then utilization (ascending)
        available.sort(key=lambda g: (-g.memory_free, g.utilization))
        return available[0].index
    
    async def get_total_capacity(self) -> Dict[str, Any]:
        """Get total GPU capacity summary."""
        gpus = await self.get_gpu_status()
        
        total_memory = sum(g.memory_total for g in gpus)
        used_memory = sum(g.memory_used for g in gpus)
        free_memory = sum(g.memory_free for g in gpus)
        avg_utilization = sum(g.utilization for g in gpus) / len(gpus) if gpus else 0
        
        return {
            "gpu_count": len(gpus),
            "total_memory_mb": total_memory,
            "used_memory_mb": used_memory,
            "free_memory_mb": free_memory,
            "memory_utilization": used_memory / total_memory if total_memory > 0 else 0,
            "avg_gpu_utilization": avg_utilization,
            "gpus": [
                {
                    "index": g.index,
                    "name": g.name,
                    "memory_free_mb": g.memory_free,
                    "utilization": g.utilization,
                    "temperature": g.temperature,
                    "available": g.is_available
                }
                for g in gpus
            ]
        }


class BatchEmbedder:
    """
    High-throughput batch embedding generator.
    Uses both GPUs for parallel embedding generation.
    """
    
    def __init__(
        self,
        orchestrator: GPUOrchestrator,
        ollama_url: str = "http://localhost:11434",
        model: str = "bge-m3:latest",
        batch_size: int = 50
    ):
        self.orchestrator = orchestrator
        self.ollama_url = ollama_url
        self.model = model
        self.batch_size = batch_size
    
    async def embed_batch(self, texts: List[str]) -> List[Dict[str, Any]]:
        """
        Generate embeddings for a batch of texts.
        Parallelizes across available GPUs.
        """
        results = []
        
        # Split into chunks for parallel processing
        chunks = [
            texts[i:i + self.batch_size]
            for i in range(0, len(texts), self.batch_size)
        ]
        
        async with aiohttp.ClientSession() as session:
            tasks = [
                self._embed_chunk(session, chunk)
                for chunk in chunks
            ]
            chunk_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for chunk_result in chunk_results:
            if isinstance(chunk_result, Exception):
                print(f"⚠️ Embedding chunk failed: {chunk_result}")
                continue
            results.extend(chunk_result)
        
        return results
    
    async def _embed_chunk(
        self,
        session: aiohttp.ClientSession,
        texts: List[str]
    ) -> List[Dict[str, Any]]:
        """Embed a chunk of texts."""
        results = []
        
        for text in texts:
            try:
                async with session.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    data = await resp.json()
                    results.append({
                        "text": text[:100],  # Truncate for reference
                        "embedding": data.get("embedding", []),
                        "dimensions": len(data.get("embedding", []))
                    })
            except Exception as e:
                results.append({
                    "text": text[:100],
                    "error": str(e)
                })
        
        return results


class ParallelOCR:
    """
    Parallel OCR processing using Pixtral across GPUs.
    """
    
    def __init__(
        self,
        orchestrator: GPUOrchestrator,
        ollama_url: str = "http://localhost:11434",
        model: str = "pixtral-large"
    ):
        self.orchestrator = orchestrator
        self.ollama_url = ollama_url
        self.model = model
    
    async def ocr_batch(
        self,
        images: List[Dict[str, Any]],  # {"id": str, "base64": str}
        max_concurrent: int = 4
    ) -> List[Dict[str, Any]]:
        """
        OCR multiple images in parallel.
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def ocr_one(image: Dict) -> Dict:
            async with semaphore:
                return await self._ocr_image(image)
        
        tasks = [ocr_one(img) for img in images]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return [
            r if not isinstance(r, Exception) else {"error": str(r)}
            for r in results
        ]
    
    async def _ocr_image(self, image: Dict) -> Dict:
        """OCR a single image."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": "Extract ALL text from this image. Return only the raw text, preserving structure and formatting. Include tables, headers, footers, and any visible text.",
                        "images": [image["base64"]],
                        "stream": False
                    },
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    data = await resp.json()
                    return {
                        "id": image.get("id"),
                        "text": data.get("response", ""),
                        "model": self.model,
                        "eval_count": data.get("eval_count", 0)
                    }
            except Exception as e:
                return {
                    "id": image.get("id"),
                    "error": str(e)
                }


# Singleton orchestrator
_orchestrator: Optional[GPUOrchestrator] = None

def get_orchestrator() -> GPUOrchestrator:
    """Get the global GPU orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = GPUOrchestrator()
    return _orchestrator
