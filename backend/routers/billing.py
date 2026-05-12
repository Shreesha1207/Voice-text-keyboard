from fastapi import APIRouter, Depends, HTTPException, Request, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
import os
import stripe
from uuid import UUID

from database import get_db
from models import User, SubscriptionStatus
from schemas import BillingStatusResponse
from dependencies import get_current_user

# Ensure stripe API key is set for portal creation
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

router = APIRouter(prefix="/api/billing", tags=["Billing"])

@router.get("/status", response_model=BillingStatusResponse)
async def get_billing_status(current_user: User = Depends(get_current_user)):
    """Get current user's billing/trial status based on the database fields."""
    
    trial_remaining = None
    next_billing = current_user.current_period_end
    
    # Check if they have an active paid subscription
    if current_user.subscription_status in [SubscriptionStatus.PAID, SubscriptionStatus.CANCELED]:
        # If canceled but still active, they get access until current_period_end
        if current_user.subscription_status == SubscriptionStatus.CANCELED:
            if not next_billing or next_billing < datetime.utcnow():
                status = SubscriptionStatus.EXPIRED
                plan_name = "Expired"
            else:
                status = SubscriptionStatus.PAID
                plan_name = "Pro (Canceling)"
        else:
            status = SubscriptionStatus.PAID
            plan_name = "Pro"
    elif current_user.subscription_status == SubscriptionStatus.PAST_DUE:
        status = SubscriptionStatus.PAST_DUE
        plan_name = "Pro (Past Due)"
    else:
        # Fall back to trial logic
        delta = datetime.utcnow() - current_user.trial_start_at.replace(tzinfo=None)
        if delta.days < 14:
            trial_remaining = 14 - delta.days
            status = SubscriptionStatus.TRIAL
            plan_name = "Trial"
        else:
            trial_remaining = 0
            status = SubscriptionStatus.EXPIRED
            plan_name = "Expired"

    return BillingStatusResponse(
        status=status,
        trial_days_remaining=trial_remaining,
        next_billing_date=next_billing,
        plan=plan_name
    )

@router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Secure Webhook endpoint for Stripe handling subscriptions and payments.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    if not webhook_secret:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET not configured")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event['type']
    data_object = event['data']['object']

    # Helper function to find user by metadata or stripe IDs
    async def get_user_from_event(obj):
        # 1. Try metadata.userId
        metadata = obj.get("metadata", {})
        if user_id_str := metadata.get("userId"):
            try:
                stmt = select(User).where(User.id == UUID(user_id_str))
                return (await db.execute(stmt)).scalar_one_or_none()
            except ValueError:
                pass
                
        # 2. Try client_reference_id (checkout session only)
        if client_ref := obj.get("client_reference_id"):
            try:
                stmt = select(User).where(User.id == UUID(client_ref))
                return (await db.execute(stmt)).scalar_one_or_none()
            except ValueError:
                pass
                
        # 3. Fallback: Lookup by stripe_customer_id
        customer_id = obj.get("customer")
        if customer_id and isinstance(customer_id, str):
            stmt = select(User).where(User.stripe_customer_id == customer_id)
            return (await db.execute(stmt)).scalar_one_or_none()
            
        return None

    # Handle the specific events
    user = await get_user_from_event(data_object)
    if not user:
        # We might receive webhooks for customers that don't exist in our DB (e.g. testing)
        return {"status": "ignored", "reason": "User not found"}

    if event_type == 'checkout.session.completed':
        user.subscription_status = SubscriptionStatus.PAID
        user.stripe_customer_id = data_object.get('customer')
        user.stripe_subscription_id = data_object.get('subscription')
        # We'll get current_period_end from the upcoming customer.subscription.updated event

    elif event_type == 'customer.subscription.updated':
        status = data_object.get('status')
        if status in ['active', 'trialing']:
            user.subscription_status = SubscriptionStatus.PAID
        elif status == 'past_due':
            user.subscription_status = SubscriptionStatus.PAST_DUE
        elif status == 'canceled':
            user.subscription_status = SubscriptionStatus.CANCELED
            
        user.cancel_at_period_end = data_object.get('cancel_at_period_end', False)
        
        # Stripe sends timestamps in seconds
        period_end = data_object.get('current_period_end')
        if period_end:
            user.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc).replace(tzinfo=None)

    elif event_type == 'customer.subscription.deleted':
        user.subscription_status = SubscriptionStatus.CANCELED
        user.cancel_at_period_end = True

    elif event_type == 'invoice.payment_failed':
        user.subscription_status = SubscriptionStatus.PAST_DUE

    elif event_type == 'invoice.payment_succeeded':
        # Extend the subscription period
        lines = data_object.get('lines', {}).get('data', [])
        if lines:
            period_end = lines[0].get('period', {}).get('end')
            if period_end:
                 user.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc).replace(tzinfo=None)
        user.subscription_status = SubscriptionStatus.PAID

    await db.commit()
    return {"status": "success"}

@router.post("/portal")
async def create_billing_portal(
    payload: dict = Body(...),
    current_user: User = Depends(get_current_user)
):
    """Create a Stripe Customer Portal session."""
    if not current_user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No active Stripe customer found.")
        
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY not configured")

    return_url = payload.get("return_url", "https://xvoicekeyboard.com")

    try:
        session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id,
            return_url=return_url
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
