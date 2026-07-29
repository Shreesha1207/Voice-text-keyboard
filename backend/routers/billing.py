import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta, timezone
import os
import stripe
from uuid import UUID
import hmac
import hashlib
import json

from database import get_db
from models import User, SubscriptionStatus
from schemas import BillingStatusResponse
from dependencies import get_current_user
import audit

logger = logging.getLogger(__name__)

# Ensure stripe API key is set for portal creation
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Map Stripe price IDs to Xvoice product slugs.
# Set STRIPE_DICTATION_PRICE_ID, STRIPE_WRITING_PRICE_ID, STRIPE_PLATFORM_PRICE_ID
# in Railway env vars to match your Stripe Dashboard price IDs.
PRICE_TO_PRODUCT: dict[str, str] = {k: v for k, v in [
    (os.getenv("STRIPE_DICTATION_PRICE_ID"), "dictation"),
    (os.getenv("STRIPE_WRITING_PRICE_ID"),   "writing"),
    (os.getenv("STRIPE_PLATFORM_PRICE_ID"),  "platform"),
] if k}  # skip None keys (env var not set)

def _plan_product_from_subscription(sub_object: dict) -> str | None:
    """Extract the plan_product slug from a Stripe subscription object.
    Returns None if we can't determine the product (don't overwrite existing value)."""
    items = sub_object.get("items", {}).get("data", [])
    for item in items:
        price_id = item.get("price", {}).get("id")
        if price_id and price_id in PRICE_TO_PRODUCT:
            return PRICE_TO_PRODUCT[price_id]
    return None

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
    # NOTE: Stripe is configured to deliver to Lovable's payments-webhook, which
    # verifies the signature and forwards a signed event to /lovable-sync. This
    # handler is therefore believed to be unreachable. Rather than delete it blind
    # — if some endpoint still points here, deleting would drop live billing events
    # — it now announces itself loudly. If this line never appears in the logs, the
    # handler is confirmed dead and can be removed.
    logger.warning(
        "Direct Stripe webhook invoked on Railway. Billing is expected to flow "
        "Stripe -> Lovable payments-webhook -> /lovable-sync. If you see this, one "
        "of those assumptions is wrong."
    )

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
        # Fetch the full subscription to read the price_id → plan_product
        sub_id = data_object.get('subscription')
        if sub_id:
            try:
                sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
                product = _plan_product_from_subscription(sub)
                if product:
                    user.plan_product = product
            except Exception as e:
                logger.warning(f"Could not fetch subscription for plan_product: {e}")
        # We'll get current_period_end from the upcoming customer.subscription.updated event

    elif event_type == 'customer.subscription.updated':
        status = data_object.get('status')
        if status in ['active', 'trialing']:
            user.subscription_status = SubscriptionStatus.PAID
            # Determine which product the subscription is for
            product = _plan_product_from_subscription(data_object)
            if product:
                user.plan_product = product
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
        # Log the detail; don't hand raw Stripe internals to the client.
        logger.error(f"Billing portal creation failed for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=400,
            detail="Could not open the billing portal. Please try again."
        )

@router.post("/cancel")
async def cancel_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel at the end of the current billing period.

    The web dashboard has always called POST /billing/cancel, but no such route
    existed — the Cancel button returned 404 and customers could not self-serve a
    cancellation.

    This defers to Stripe rather than writing subscription state directly: Stripe
    emits customer.subscription.updated, Lovable's payments-webhook upserts its own
    row and forwards the signed event to /lovable-sync, and the status lands here
    through the one authoritative path. Setting the flag locally as well would put
    two writers on the same field again.
    """
    if not current_user.stripe_subscription_id:
        raise HTTPException(
            status_code=400,
            detail="No active subscription to cancel.",
        )
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY not configured")

    try:
        subscription = stripe.Subscription.modify(
            current_user.stripe_subscription_id,
            cancel_at_period_end=True,
        )
    except Exception as e:
        logger.error(f"Cancel failed for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=400,
            detail="Could not cancel the subscription. Please try again.",
        )

    # Reflect it immediately so the UI updates without waiting for the webhook
    # round-trip. The authoritative value still arrives via /lovable-sync.
    current_user.cancel_at_period_end = True
    await audit.record(
        db, audit.SUBSCRIPTION_CHANGED, user_id=current_user.id, email=current_user.email,
        detail=f"cancel_at_period_end=True via /billing/cancel "
               f"(subscription={current_user.stripe_subscription_id})",
    )
    await db.commit()

    period_end = getattr(subscription, "current_period_end", None)
    return {
        "status": "ok",
        "cancel_at_period_end": True,
        "current_period_end": period_end,
        "detail": "Your subscription will remain active until the end of the current billing period.",
    }


@router.post("/lovable-sync")
async def lovable_sync(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Secure sync endpoint called by Lovable Edge Functions.
    Verifies X-Lovable-Signature to prevent spoofing.
    """
    payload_raw = await request.body()
    signature = request.headers.get("X-Lovable-Signature")
    secret = os.getenv("LOVABLE_SYNC_SECRET")

    if not secret:
        raise HTTPException(status_code=500, detail="LOVABLE_SYNC_SECRET not configured")

    # 1. Verify Signature
    expected_signature = hmac.new(
        secret.encode(),
        payload_raw,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature or ""):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # 2. Process Payload
    try:
        data = json.loads(payload_raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
    email = data.get("email")
    status_str = data.get("status")

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    # Reject anything that is not a live payment. Lovable's subscriptions table
    # carries an environment column and the web app filters on it, but this payload
    # historically had no such field — so a Stripe test-mode payment could flip a
    # user to PAID here for real. Defence on the receiving side: do not rely on the
    # sender to filter. Payloads with no environment are treated as live so existing
    # senders keep working.
    environment = (data.get("environment") or "live").strip().lower()
    if environment != "live":
        logger.warning(
            f"lovable-sync: ignoring {environment!r} (non-live) event for {email!r} "
            f"status={status_str!r}"
        )
        return {"status": "ignored", "reason": f"environment={environment} is not live"}

    # 3. Lookup User
    #    Case-insensitive: emails are stored as the user typed them, and Postgres
    #    compares case-sensitively, so "User@Gmail.com" would otherwise miss a
    #    stored "user@gmail.com".
    stmt = select(User).where(func.lower(User.email) == email.strip().lower())
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        # This is the primary billing path — the customer has already paid. Returning
        # 200 here made Lovable record the sync as delivered and never retry, leaving
        # a paying customer silently on the trial plan. Fail loudly instead.
        logger.error(
            f"lovable-sync: no user matches email {email!r} — subscription NOT applied. "
            f"status={status_str!r} plan_product={data.get('plan_product')!r}"
        )
        await audit.record(
            db, audit.BILLING_SYNC_NO_USER, email=email, request=request,
            detail=f"status={status_str} plan_product={data.get('plan_product')} "
                   f"stripe_customer={data.get('stripe_customer_id')}",
            commit=True,
        )
        raise HTTPException(
            status_code=404,
            detail="No account matches that email address; subscription was not applied.",
        )

    # 4. Map and Update Status
    # active/trialing -> PAID, else map directly
    if status_str in ["active", "trialing"]:
        user.subscription_status = SubscriptionStatus.PAID
    elif status_str == "past_due":
        user.subscription_status = SubscriptionStatus.PAST_DUE
    elif status_str == "canceled":
        user.subscription_status = SubscriptionStatus.CANCELED
    elif status_str == "expired":
        user.subscription_status = SubscriptionStatus.EXPIRED
    
    # Update other fields.
    # Only overwrite the Stripe identifiers when the payload actually carries them.
    # A status-only sync (a cancellation or expiry, say) used to null them out, which
    # broke that customer's billing portal AND stopped the Stripe webhook's
    # customer-id fallback from ever matching them again.
    if customer_id := data.get("stripe_customer_id"):
        user.stripe_customer_id = customer_id
    if subscription_id := data.get("stripe_subscription_id"):
        user.stripe_subscription_id = subscription_id
    user.cancel_at_period_end = data.get("cancel_at_period_end", False)

    # Update plan_product if provided by the Lovable sync payload
    # Lovable should pass 'plan_product': 'dictation' | 'writing' | 'platform'
    if plan_product := data.get("plan_product"):
        if plan_product in ("dictation", "writing", "platform"):
            user.plan_product = plan_product
    
    period_end = data.get("current_period_end")
    if period_end:
        try:
            # Handle ISO format from Lovable (e.g. 2026-06-13T00:00:00Z)
            # Remove Z and replace with +00:00 for fromisoformat, then strip tz for naive DateTime
            dt = datetime.fromisoformat(period_end.replace('Z', '+00:00'))
            user.current_period_end = dt.replace(tzinfo=None)
        except (ValueError, TypeError):
            pass

    await audit.record(
        db, audit.BILLING_SYNC_APPLIED, user_id=user.id, email=user.email, request=request,
        detail=f"status={status_str} plan_product={user.plan_product} "
               f"subscription_status={user.subscription_status.value}",
    )
    await db.commit()
    return {"status": "success", "user_id": str(user.id)}
