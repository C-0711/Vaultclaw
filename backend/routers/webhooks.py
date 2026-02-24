"""
Webhook System for 0711-Vault
Event-driven notifications for file operations
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from datetime import datetime
from enum import Enum
import uuid
import httpx
import hmac
import hashlib
import json
import asyncio

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

class WebhookEvent(str, Enum):
    FILE_UPLOADED = "file.uploaded"
    FILE_DELETED = "file.deleted"
    FILE_UPDATED = "file.updated"
    FOLDER_CREATED = "folder.created"
    FOLDER_DELETED = "folder.deleted"
    SHARE_CREATED = "share.created"
    SHARE_REVOKED = "share.revoked"
    CONTAINER_BUILT = "container.built"
    EXTRACTION_COMPLETE = "extraction.complete"

class WebhookCreate(BaseModel):
    url: HttpUrl
    events: List[WebhookEvent]
    secret: Optional[str] = None
    description: Optional[str] = None
    active: bool = True

class WebhookResponse(BaseModel):
    id: str
    url: str
    events: List[WebhookEvent]
    description: Optional[str]
    active: bool
    created_at: datetime
    last_triggered: Optional[datetime]
    success_count: int
    failure_count: int

class WebhookDelivery(BaseModel):
    id: str
    webhook_id: str
    event: WebhookEvent
    payload: dict
    response_code: Optional[int]
    response_body: Optional[str]
    success: bool
    delivered_at: datetime
    duration_ms: int

# In-memory storage (replace with DB)
webhooks_db: dict = {}
deliveries_db: List[dict] = []

def generate_signature(payload: str, secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload"""
    return hmac.new(
        secret.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()


async def deliver_webhook(webhook_id: str, event: WebhookEvent, data: dict):
    """Deliver webhook with retry logic"""
    webhook = webhooks_db.get(webhook_id)
    if not webhook or not webhook["active"]:
        return
    
    payload = {
        "event": event.value,
        "timestamp": datetime.now().isoformat(),
        "data": data
    }
    payload_json = json.dumps(payload)
    
    headers = {
        "Content-Type": "application/json",
        "X-0711-Event": event.value,
        "X-0711-Delivery": str(uuid.uuid4()),
    }
    
    if webhook.get("secret"):
        signature = generate_signature(payload_json, webhook["secret"])
        headers["X-0711-Signature"] = f"sha256={signature}"
    
    delivery_id = str(uuid.uuid4())
    start_time = datetime.now()
    
    # Retry logic with exponential backoff
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    str(webhook["url"]),
                    content=payload_json,
                    headers=headers
                )
                
                duration = (datetime.now() - start_time).total_seconds() * 1000
                success = 200 <= response.status_code < 300
                
                delivery = {
                    "id": delivery_id,
                    "webhook_id": webhook_id,
                    "event": event,
                    "payload": payload,
                    "response_code": response.status_code,
                    "response_body": response.text[:1000],
                    "success": success,
                    "delivered_at": datetime.now(),
                    "duration_ms": int(duration),
                }
                deliveries_db.append(delivery)
                
                if success:
                    webhook["success_count"] += 1
                    webhook["last_triggered"] = datetime.now()
                    return
                else:
                    webhook["failure_count"] += 1
                    
        except Exception as e:
            if attempt == max_retries - 1:
                webhook["failure_count"] += 1
                deliveries_db.append({
                    "id": delivery_id,
                    "webhook_id": webhook_id,
                    "event": event,
                    "payload": payload,
                    "response_code": None,
                    "response_body": str(e),
                    "success": False,
                    "delivered_at": datetime.now(),
                    "duration_ms": 0,
                })
        
        # Exponential backoff
        await asyncio.sleep(2 ** attempt)


async def trigger_webhooks(event: WebhookEvent, data: dict, background_tasks: BackgroundTasks):
    """Trigger all webhooks subscribed to an event"""
    for webhook_id, webhook in webhooks_db.items():
        if event in webhook["events"] and webhook["active"]:
            background_tasks.add_task(deliver_webhook, webhook_id, event, data)


@router.post("", response_model=WebhookResponse)
async def create_webhook(webhook: WebhookCreate):
    """Register a new webhook"""
    webhook_id = str(uuid.uuid4())
    now = datetime.now()
    
    new_webhook = {
        "id": webhook_id,
        "url": str(webhook.url),
        "events": webhook.events,
        "secret": webhook.secret,
        "description": webhook.description,
        "active": webhook.active,
        "created_at": now,
        "last_triggered": None,
        "success_count": 0,
        "failure_count": 0,
    }
    
    webhooks_db[webhook_id] = new_webhook
    return WebhookResponse(**new_webhook)


@router.get("", response_model=List[WebhookResponse])
async def list_webhooks():
    """List all webhooks"""
    return [WebhookResponse(**w) for w in webhooks_db.values()]


@router.get("/{webhook_id}", response_model=WebhookResponse)
async def get_webhook(webhook_id: str):
    """Get webhook by ID"""
    webhook = webhooks_db.get(webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return WebhookResponse(**webhook)


@router.delete("/{webhook_id}")
async def delete_webhook(webhook_id: str):
    """Delete webhook"""
    if webhook_id not in webhooks_db:
        raise HTTPException(status_code=404, detail="Webhook not found")
    del webhooks_db[webhook_id]
    return {"status": "deleted", "webhook_id": webhook_id}


@router.put("/{webhook_id}/toggle")
async def toggle_webhook(webhook_id: str):
    """Toggle webhook active status"""
    webhook = webhooks_db.get(webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    webhook["active"] = not webhook["active"]
    return {"active": webhook["active"]}


@router.get("/{webhook_id}/deliveries", response_model=List[WebhookDelivery])
async def get_deliveries(webhook_id: str, limit: int = 50):
    """Get delivery history for a webhook"""
    webhook_deliveries = [
        WebhookDelivery(**d) for d in deliveries_db 
        if d["webhook_id"] == webhook_id
    ]
    return webhook_deliveries[-limit:]


@router.post("/{webhook_id}/test")
async def test_webhook(webhook_id: str, background_tasks: BackgroundTasks):
    """Send test event to webhook"""
    webhook = webhooks_db.get(webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    
    test_data = {
        "test": True,
        "message": "This is a test webhook delivery from 0711-Vault",
        "timestamp": datetime.now().isoformat()
    }
    
    background_tasks.add_task(
        deliver_webhook, 
        webhook_id, 
        WebhookEvent.FILE_UPLOADED, 
        test_data
    )
    
    return {"status": "test_queued", "webhook_id": webhook_id}
