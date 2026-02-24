"""
PROJEKT GENESIS Sprint 3: Pipeline API
Batch upload and processing endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import asyncio
import json
import time
import uuid
import base64
from datetime import datetime

# Import pipeline components (adjust path as needed)
import sys

from .redis_queue import RedisJobQueue, PipelineJob, JobPriority, get_queue
from .gpu_orchestrator import get_orchestrator

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


# --- Pydantic Models ---

class BatchUploadRequest(BaseModel):
    space_id: str
    files: List[str]  # List of file paths or base64 data
    job_type: str = "full"  # full, ocr, extract, embed
    priority: int = JobPriority.NORMAL.value
    schema: str = "ETIM-9.0"
    options: Dict[str, Any] = {}

class BatchUploadResponse(BaseModel):
    batch_id: str
    job_count: int
    space_id: str
    status: str
    created_at: str

class PipelineStatus(BaseModel):
    space_id: str
    total: int
    pending: int
    processing: int
    completed: int
    failed: int
    rate_per_second: float
    eta_seconds: float
    started_at: Optional[str]

class QueueStats(BaseModel):
    pending: int
    processing: int
    total_enqueued: int
    total_completed: int
    total_failed: int
    total_retries: int
    avg_processing_time: float

class GPUStatus(BaseModel):
    gpu_count: int
    total_memory_mb: int
    used_memory_mb: int
    free_memory_mb: int
    memory_utilization: float
    avg_gpu_utilization: float
    gpus: List[Dict[str, Any]]


# --- Background Task ---

async def process_batch_background(
    batch_id: str,
    space_id: str,
    files: List[str],
    job_type: str,
    priority: int,
    schema: str,
    options: Dict[str, Any]
):
    """Background task to enqueue batch jobs."""
    queue = await get_queue()
    
    # Create jobs for each file
    jobs = []
    for file_path in files:
        job = PipelineJob(
            id=f"{batch_id}-{len(jobs)}",
            space_id=space_id,
            job_type=job_type,
            input_data={
                "file_path": file_path,
                "schema": schema,
                "options": options
            },
            priority=priority
        )
        jobs.append(job)
    
    # Bulk enqueue
    await queue.enqueue_batch(jobs)
    
    # Initialize progress
    progress_key = f"{queue.PROGRESS_PREFIX}{space_id}"
    await queue.redis.hset(progress_key, mapping={
        "total": str(len(jobs)),
        "pending": str(len(jobs)),
        "processing": "0",
        "completed": "0",
        "failed": "0",
        "rate": "0",
        "eta": "0",
        "batch_id": batch_id,
        "started_at": datetime.utcnow().isoformat()
    })
    
    print(f"📦 Batch {batch_id}: {len(jobs)} jobs enqueued for space {space_id}")


# --- Endpoints ---

@router.post("/batch", response_model=BatchUploadResponse)
async def create_batch_upload(
    request: BatchUploadRequest,
    background_tasks: BackgroundTasks
):
    """
    Create a batch upload job.
    
    Enqueues files for processing on the H200V pipeline.
    Returns immediately - processing happens in background.
    """
    batch_id = str(uuid.uuid4())
    
    background_tasks.add_task(
        process_batch_background,
        batch_id=batch_id,
        space_id=request.space_id,
        files=request.files,
        job_type=request.job_type,
        priority=request.priority,
        schema=request.schema,
        options=request.options
    )
    
    return BatchUploadResponse(
        batch_id=batch_id,
        job_count=len(request.files),
        space_id=request.space_id,
        status="queued",
        created_at=datetime.utcnow().isoformat()
    )


@router.post("/batch/upload", response_model=BatchUploadResponse)
async def upload_files_batch(
    space_id: str,
    files: List[UploadFile] = File(...),
    job_type: str = "full",
    priority: int = JobPriority.NORMAL.value,
    background_tasks: BackgroundTasks = None
):
    """
    Upload multiple files and queue for processing.
    
    Files are stored and processing jobs are created.
    """
    batch_id = str(uuid.uuid4())
    file_paths = []
    
    for file in files:
        # Read and encode file
        content = await file.read()
        b64 = base64.b64encode(content).decode()
        
        # Store temporarily (would use proper storage)
        file_paths.append({
            "filename": file.filename,
            "content_type": file.content_type,
            "base64": b64
        })
    
    background_tasks.add_task(
        process_batch_background,
        batch_id=batch_id,
        space_id=space_id,
        files=[f["filename"] for f in file_paths],
        job_type=job_type,
        priority=priority,
        schema="ETIM-9.0",
        options={"files": file_paths}
    )
    
    return BatchUploadResponse(
        batch_id=batch_id,
        job_count=len(files),
        space_id=space_id,
        status="queued",
        created_at=datetime.utcnow().isoformat()
    )


@router.get("/status/{space_id}", response_model=PipelineStatus)
async def get_pipeline_status(space_id: str):
    """Get real-time processing status for a space."""
    queue = await get_queue()
    progress = await queue.get_progress(space_id)
    
    # Get started_at from Redis
    progress_key = f"{queue.PROGRESS_PREFIX}{space_id}"
    started_at = await queue.redis.hget(progress_key, "started_at")
    
    return PipelineStatus(
        space_id=space_id,
        total=progress["total"],
        pending=progress["pending"],
        processing=progress["processing"],
        completed=progress["completed"],
        failed=progress["failed"],
        rate_per_second=progress["rate_per_second"],
        eta_seconds=progress["eta_seconds"],
        started_at=started_at
    )


@router.get("/status/{space_id}/stream")
async def stream_pipeline_status(space_id: str):
    """
    Server-Sent Events stream for real-time progress.
    Connect with EventSource in frontend.
    """
    async def event_generator():
        queue = await get_queue()
        last_completed = 0
        
        while True:
            progress = await queue.get_progress(space_id)
            
            yield f"data: {json.dumps(progress)}\n\n"
            
            # Stop if all done
            if progress["completed"] + progress["failed"] >= progress["total"] > 0:
                yield f"data: {json.dumps({event: complete, **progress})}\n\n"
                break
            
            await asyncio.sleep(1)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )


@router.get("/queue/stats", response_model=QueueStats)
async def get_queue_stats():
    """Get overall queue statistics."""
    queue = await get_queue()
    stats = await queue.get_queue_stats()
    return QueueStats(**stats)


@router.get("/gpu/status", response_model=GPUStatus)
async def get_gpu_status():
    """Get GPU cluster status."""
    orchestrator = get_orchestrator()
    capacity = await orchestrator.get_total_capacity()
    return GPUStatus(**capacity)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a pending job."""
    queue = await get_queue()
    
    # Remove from pending queue
    removed = await queue.redis.zrem(queue.QUEUE_PENDING, job_id)
    
    if removed:
        # Update job status
        job = await queue._load_job(job_id)
        if job:
            job.status = "cancelled"
            await queue._save_job(job)
        
        return {"status": "cancelled", "job_id": job_id}
    
    return {"status": "not_found", "job_id": job_id}


@router.post("/batch/{batch_id}/cancel")
async def cancel_batch(batch_id: str):
    """Cancel all jobs in a batch."""
    queue = await get_queue()
    
    # Find all jobs with this batch prefix
    cancelled = 0
    cursor = 0
    
    while True:
        cursor, keys = await queue.redis.scan(
            cursor,
            match=f"{queue.JOB_PREFIX}{batch_id}-*",
            count=100
        )
        
        for key in keys:
            job_id = key.replace(queue.JOB_PREFIX, "")
            result = await cancel_job(job_id)
            if result["status"] == "cancelled":
                cancelled += 1
        
        if cursor == 0:
            break
    
    return {"status": "cancelled", "batch_id": batch_id, "jobs_cancelled": cancelled}


# --- Health Check ---

@router.get("/health")
async def pipeline_health():
    """Pipeline health check."""
    try:
        queue = await get_queue()
        await queue.redis.ping()
        redis_ok = True
    except Exception as e:
        redis_ok = False
    
    try:
        orchestrator = get_orchestrator()
        gpus = await orchestrator.get_gpu_status()
        gpu_ok = len(gpus) > 0
    except Exception:
        gpu_ok = False
    
    return {
        "status": "healthy" if (redis_ok and gpu_ok) else "degraded",
        "redis": redis_ok,
        "gpu": gpu_ok,
        "timestamp": datetime.utcnow().isoformat()
    }
