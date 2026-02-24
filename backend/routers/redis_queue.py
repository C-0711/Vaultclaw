"""
PROJEKT GENESIS Sprint 3: Redis Job Queue
High-performance distributed job queue for H200V pipeline
"""

import asyncio
import json
import time
import uuid
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime, timedelta
import redis.asyncio as redis

class JobPriority(Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 10
    CRITICAL = 100

class JobStatus(Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"

@dataclass
class PipelineJob:
    """A job in the processing pipeline."""
    id: str
    space_id: str
    job_type: str  # ocr, extract, embed, full
    input_data: Dict[str, Any]
    priority: int = JobPriority.NORMAL.value
    status: str = JobStatus.PENDING.value
    worker_id: Optional[str] = None
    attempts: int = 0
    max_attempts: int = 3
    created_at: float = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    output_data: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = time.time()
        if not self.id:
            self.id = str(uuid.uuid4())
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "PipelineJob":
        return cls(**data)


class RedisJobQueue:
    """
    Redis-based job queue for H200V pipeline.
    
    Uses Redis sorted sets for priority queuing:
    - vault:queue:pending - pending jobs (scored by priority + timestamp)
    - vault:queue:processing - jobs being processed
    - vault:jobs:{id} - job data
    - vault:progress:{space_id} - real-time progress
    """
    
    QUEUE_PENDING = "vault:queue:pending"
    QUEUE_PROCESSING = "vault:queue:processing"
    QUEUE_COMPLETED = "vault:queue:completed"
    QUEUE_FAILED = "vault:queue:failed"
    JOB_PREFIX = "vault:jobs:"
    PROGRESS_PREFIX = "vault:progress:"
    STATS_KEY = "vault:stats"
    
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self.redis: Optional[redis.Redis] = None
    
    async def connect(self):
        """Connect to Redis."""
        self.redis = redis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
        await self.redis.ping()
        print(f"✅ Connected to Redis: {self.redis_url}")
    
    async def disconnect(self):
        """Disconnect from Redis."""
        if self.redis:
            await self.redis.close()
    
    async def enqueue(self, job: PipelineJob) -> str:
        """Add a job to the queue."""
        # Store job data
        job_key = f"{self.JOB_PREFIX}{job.id}"
        await self.redis.hset(job_key, mapping={
            k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) if v is not None else ""
            for k, v in job.to_dict().items()
        })
        
        # Add to pending queue with priority score
        # Higher priority = lower score (processed first)
        # Timestamp ensures FIFO within same priority
        score = (100 - job.priority) * 1e10 + job.created_at
        await self.redis.zadd(self.QUEUE_PENDING, {job.id: score})
        
        # Update stats
        await self.redis.hincrby(self.STATS_KEY, "total_enqueued", 1)
        
        return job.id
    
    async def enqueue_batch(self, jobs: List[PipelineJob]) -> List[str]:
        """Enqueue multiple jobs efficiently."""
        pipe = self.redis.pipeline()
        
        for job in jobs:
            job_key = f"{self.JOB_PREFIX}{job.id}"
            pipe.hset(job_key, mapping={
                k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) if v is not None else ""
                for k, v in job.to_dict().items()
            })
            score = (100 - job.priority) * 1e10 + job.created_at
            pipe.zadd(self.QUEUE_PENDING, {job.id: score})
        
        pipe.hincrby(self.STATS_KEY, "total_enqueued", len(jobs))
        await pipe.execute()
        
        return [job.id for job in jobs]
    
    async def dequeue(self, worker_id: str, count: int = 1) -> List[PipelineJob]:
        """
        Atomically claim jobs from the queue.
        Uses ZPOPMIN for atomic dequeue + ZADD to processing.
        """
        jobs = []
        
        for _ in range(count):
            # Atomic pop from pending
            result = await self.redis.zpopmin(self.QUEUE_PENDING, count=1)
            if not result:
                break
            
            job_id, _ = result[0]
            
            # Move to processing queue
            await self.redis.zadd(self.QUEUE_PROCESSING, {job_id: time.time()})
            
            # Load and update job
            job_data = await self._load_job(job_id)
            if job_data:
                job_data.status = JobStatus.PROCESSING.value
                job_data.worker_id = worker_id
                job_data.started_at = time.time()
                job_data.attempts += 1
                await self._save_job(job_data)
                jobs.append(job_data)
        
        return jobs
    
    async def complete(self, job_id: str, result: Dict[str, Any]):
        """Mark a job as completed."""
        job = await self._load_job(job_id)
        if not job:
            return
        
        job.status = JobStatus.COMPLETED.value
        job.completed_at = time.time()
        job.output_data = result
        await self._save_job(job)
        
        # Move from processing to completed
        await self.redis.zrem(self.QUEUE_PROCESSING, job_id)
        await self.redis.zadd(self.QUEUE_COMPLETED, {job_id: time.time()})
        
        # Update stats
        elapsed = job.completed_at - (job.started_at or job.created_at)
        await self.redis.hincrby(self.STATS_KEY, "total_completed", 1)
        await self.redis.hincrbyfloat(self.STATS_KEY, "total_processing_time", elapsed)
        
        # Update space progress
        await self._update_progress(job.space_id, "completed", 1)
    
    async def fail(self, job_id: str, error: str, retry: bool = True):
        """Mark a job as failed, optionally retry."""
        job = await self._load_job(job_id)
        if not job:
            return
        
        job.error_message = error
        
        if retry and job.attempts < job.max_attempts:
            # Requeue with delay (exponential backoff)
            job.status = JobStatus.RETRYING.value
            await self._save_job(job)
            
            delay = 2 ** job.attempts  # 2, 4, 8 seconds
            score = (100 - job.priority) * 1e10 + time.time() + delay
            
            await self.redis.zrem(self.QUEUE_PROCESSING, job_id)
            await self.redis.zadd(self.QUEUE_PENDING, {job_id: score})
            
            await self.redis.hincrby(self.STATS_KEY, "total_retries", 1)
        else:
            # Permanent failure
            job.status = JobStatus.FAILED.value
            job.completed_at = time.time()
            await self._save_job(job)
            
            await self.redis.zrem(self.QUEUE_PROCESSING, job_id)
            await self.redis.zadd(self.QUEUE_FAILED, {job_id: time.time()})
            
            await self.redis.hincrby(self.STATS_KEY, "total_failed", 1)
            await self._update_progress(job.space_id, "failed", 1)
    
    async def get_progress(self, space_id: str) -> Dict[str, Any]:
        """Get real-time progress for a space."""
        key = f"{self.PROGRESS_PREFIX}{space_id}"
        data = await self.redis.hgetall(key)
        
        return {
            "space_id": space_id,
            "total": int(data.get("total", 0)),
            "pending": int(data.get("pending", 0)),
            "processing": int(data.get("processing", 0)),
            "completed": int(data.get("completed", 0)),
            "failed": int(data.get("failed", 0)),
            "rate_per_second": float(data.get("rate", 0)),
            "eta_seconds": float(data.get("eta", 0)),
        }
    
    async def get_queue_stats(self) -> Dict[str, Any]:
        """Get overall queue statistics."""
        stats = await self.redis.hgetall(self.STATS_KEY)
        
        pending = await self.redis.zcard(self.QUEUE_PENDING)
        processing = await self.redis.zcard(self.QUEUE_PROCESSING)
        
        total_completed = int(stats.get("total_completed", 0))
        total_time = float(stats.get("total_processing_time", 0))
        avg_time = total_time / total_completed if total_completed > 0 else 0
        
        return {
            "pending": pending,
            "processing": processing,
            "total_enqueued": int(stats.get("total_enqueued", 0)),
            "total_completed": total_completed,
            "total_failed": int(stats.get("total_failed", 0)),
            "total_retries": int(stats.get("total_retries", 0)),
            "avg_processing_time": avg_time,
        }
    
    async def _load_job(self, job_id: str) -> Optional[PipelineJob]:
        """Load a job from Redis."""
        key = f"{self.JOB_PREFIX}{job_id}"
        data = await self.redis.hgetall(key)
        if not data:
            return None
        
        # Parse JSON fields
        for field in ["input_data", "output_data"]:
            if data.get(field):
                try:
                    data[field] = json.loads(data[field])
                except json.JSONDecodeError:
                    data[field] = None
        
        # Parse numeric fields
        for field in ["priority", "attempts", "max_attempts"]:
            if data.get(field):
                data[field] = int(data[field])
        
        for field in ["created_at", "started_at", "completed_at"]:
            if data.get(field):
                try:
                    data[field] = float(data[field])
                except ValueError:
                    data[field] = None
        
        return PipelineJob.from_dict(data)
    
    async def _save_job(self, job: PipelineJob):
        """Save a job to Redis."""
        key = f"{self.JOB_PREFIX}{job.id}"
        await self.redis.hset(key, mapping={
            k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) if v is not None else ""
            for k, v in job.to_dict().items()
        })
    
    async def _update_progress(self, space_id: str, field: str, delta: int):
        """Update progress counter for a space."""
        key = f"{self.PROGRESS_PREFIX}{space_id}"
        await self.redis.hincrby(key, field, delta)


class ProgressTracker:
    """Tracks and calculates processing progress with ETAs."""
    
    def __init__(self, queue: RedisJobQueue):
        self.queue = queue
        self.window_size = 60  # seconds for rate calculation
        self.completion_times: List[float] = []
    
    async def record_completion(self, space_id: str, elapsed: float):
        """Record a job completion for rate calculation."""
        now = time.time()
        self.completion_times.append(now)
        
        # Prune old entries
        cutoff = now - self.window_size
        self.completion_times = [t for t in self.completion_times if t > cutoff]
        
        # Calculate rate
        rate = len(self.completion_times) / self.window_size
        
        # Update Redis
        key = f"{self.queue.PROGRESS_PREFIX}{space_id}"
        progress = await self.queue.redis.hgetall(key)
        pending = int(progress.get("pending", 0))
        
        eta = pending / rate if rate > 0 else 0
        
        await self.queue.redis.hset(key, mapping={
            "rate": str(rate),
            "eta": str(eta)
        })


# Singleton instance
_queue_instance: Optional[RedisJobQueue] = None

async def get_queue() -> RedisJobQueue:
    """Get or create the global queue instance."""
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = RedisJobQueue("redis://vault-redis:6379")
        await _queue_instance.connect()
    return _queue_instance
