"""
Stripe Payment Integration for 0711 Vault
"""

import os
import stripe
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

# Placeholder auth - in production, import from main or auth module
async def get_current_user():
    """Placeholder - override in main.py if needed."""
    return {"id": "demo-user", "email": "demo@example.com", "plan": "free"}

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

router = APIRouter(prefix="/billing", tags=["billing"])

# Price IDs (create these in Stripe Dashboard)
PRICE_IDS = {
    "pro_monthly": os.getenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_monthly"),
    "pro_yearly": os.getenv("STRIPE_PRICE_PRO_YEARLY", "price_pro_yearly"),
    "family_monthly": os.getenv("STRIPE_PRICE_FAMILY_MONTHLY", "price_family_monthly"),
    "family_yearly": os.getenv("STRIPE_PRICE_FAMILY_YEARLY", "price_family_yearly"),
}

# Plan limits (in bytes)
PLAN_LIMITS = {
    "free": 5 * 1024 * 1024 * 1024,        # 5 GB
    "pro": 100 * 1024 * 1024 * 1024,       # 100 GB
    "family": 500 * 1024 * 1024 * 1024,    # 500 GB
}


class CreateCheckoutRequest(BaseModel):
    price_id: str
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CustomerPortalRequest(BaseModel):
    return_url: Optional[str] = None


@router.get("/plans")
async def get_plans():
    """Get available subscription plans."""
    return {
        "plans": [
            {
                "id": "free",
                "name": "Free",
                "storage_gb": 5,
                "price_monthly": 0,
                "price_yearly": 0,
                "features": [
                    "5 GB Speicher",
                    "Ende-zu-Ende Verschlüsselung",
                    "Web & Mobile Apps",
                    "Gesichtserkennung (lokal)",
                    "Email Support"
                ]
            },
            {
                "id": "pro",
                "name": "Pro",
                "storage_gb": 100,
                "price_monthly": 9,
                "price_yearly": 90,
                "stripe_price_monthly": PRICE_IDS["pro_monthly"],
                "stripe_price_yearly": PRICE_IDS["pro_yearly"],
                "features": [
                    "100 GB Speicher",
                    "Alles aus Free",
                    "Prioritäts-Support",
                    "Erweiterte AI-Features",
                    "Teilen mit Familie"
                ]
            },
            {
                "id": "family",
                "name": "Family",
                "storage_gb": 500,
                "price_monthly": 19,
                "price_yearly": 190,
                "stripe_price_monthly": PRICE_IDS["family_monthly"],
                "stripe_price_yearly": PRICE_IDS["family_yearly"],
                "features": [
                    "500 GB geteilter Speicher",
                    "Bis zu 6 Personen",
                    "Geteilte Alben & Ordner",
                    "Familienkalender",
                    "Premium Support"
                ]
            }
        ]
    }


@router.get("/subscription")
async def get_subscription(user=Depends(get_current_user)):
    """Get current user's subscription status."""
    # In production, fetch from database
    return {
        "plan": user.get("plan", "free"),
        "status": user.get("subscription_status", "active"),
        "storage_limit": PLAN_LIMITS.get(user.get("plan", "free")),
        "storage_used": user.get("storage_used", 0),
        "current_period_end": user.get("subscription_end"),
        "cancel_at_period_end": user.get("cancel_at_period_end", False)
    }


@router.post("/checkout")
async def create_checkout_session(
    request: CreateCheckoutRequest,
    user=Depends(get_current_user)
):
    """Create a Stripe Checkout session for subscription."""
    if not stripe.api_key:
        raise HTTPException(500, "Stripe not configured")
    
    try:
        # Get or create Stripe customer
        customer_id = user.get("stripe_customer_id")
        
        if not customer_id:
            customer = stripe.Customer.create(
                email=user["email"],
                metadata={"user_id": user["id"]}
            )
            customer_id = customer.id
            # TODO: Save customer_id to database
        
        # Create checkout session
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{
                "price": request.price_id,
                "quantity": 1,
            }],
            mode="subscription",
            success_url=request.success_url or f"{FRONTEND_URL}/settings?payment=success",
            cancel_url=request.cancel_url or f"{FRONTEND_URL}/settings?payment=cancelled",
            metadata={
                "user_id": user["id"]
            },
            subscription_data={
                "metadata": {
                    "user_id": user["id"]
                }
            },
            allow_promotion_codes=True,
        )
        
        return {
            "checkout_url": session.url,
            "session_id": session.id
        }
        
    except stripe.error.StripeError as e:
        raise HTTPException(400, str(e))


@router.post("/portal")
async def create_customer_portal(
    request: CustomerPortalRequest,
    user=Depends(get_current_user)
):
    """Create a Stripe Customer Portal session for managing subscription."""
    if not stripe.api_key:
        raise HTTPException(500, "Stripe not configured")
    
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(400, "No active subscription")
    
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=request.return_url or f"{FRONTEND_URL}/settings"
        )
        
        return {"portal_url": session.url}
        
    except stripe.error.StripeError as e:
        raise HTTPException(400, str(e))


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhooks for subscription events."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    if not STRIPE_WEBHOOK_SECRET:
        # In development, skip signature verification
        event = stripe.Event.construct_from(
            await request.json(), stripe.api_key
        )
    else:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        except ValueError:
            raise HTTPException(400, "Invalid payload")
        except stripe.error.SignatureVerificationError:
            raise HTTPException(400, "Invalid signature")
    
    # Handle the event
    if event.type == "checkout.session.completed":
        session = event.data.object
        await handle_checkout_completed(session)
        
    elif event.type == "customer.subscription.updated":
        subscription = event.data.object
        await handle_subscription_updated(subscription)
        
    elif event.type == "customer.subscription.deleted":
        subscription = event.data.object
        await handle_subscription_deleted(subscription)
        
    elif event.type == "invoice.payment_failed":
        invoice = event.data.object
        await handle_payment_failed(invoice)
    
    return {"status": "ok"}


async def handle_checkout_completed(session):
    """Handle successful checkout."""
    user_id = session.metadata.get("user_id")
    subscription_id = session.subscription
    
    if subscription_id:
        subscription = stripe.Subscription.retrieve(subscription_id)
        price_id = subscription["items"]["data"][0]["price"]["id"]
        
        # Determine plan from price ID
        plan = "pro"  # default
        for plan_name, pid in PRICE_IDS.items():
            if pid == price_id:
                plan = plan_name.split("_")[0]  # "pro_monthly" -> "pro"
                break
        
        # TODO: Update user in database
        print(f"User {user_id} subscribed to {plan}")


async def handle_subscription_updated(subscription):
    """Handle subscription updates (upgrades, downgrades, cancellations)."""
    user_id = subscription.metadata.get("user_id")
    status = subscription.status
    cancel_at_period_end = subscription.cancel_at_period_end
    current_period_end = datetime.fromtimestamp(subscription.current_period_end)
    
    # TODO: Update user subscription in database
    print(f"Subscription updated for user {user_id}: status={status}")


async def handle_subscription_deleted(subscription):
    """Handle subscription cancellation/expiration."""
    user_id = subscription.metadata.get("user_id")
    
    # TODO: Downgrade user to free plan in database
    print(f"Subscription deleted for user {user_id}")


async def handle_payment_failed(invoice):
    """Handle failed payment."""
    customer_id = invoice.customer
    
    # TODO: Send notification to user
    print(f"Payment failed for customer {customer_id}")
