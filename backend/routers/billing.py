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
VALID_PLAN_PRODUCTS = frozenset({"dictation", "writing", "platform"})

PRICE_TO_PRODUCT: dict[str, str] = {k: v for k, v in [
    (os.getenv("STRIPE_DICTATION_PRICE_ID"), "dictation"),
    (os.getenv("STRIPE_WRITING_PRICE_ID"),   "writing"),
    (os.getenv("STRIPE_PLATFORM_PRICE_ID"),  "platform"),
] if k}  # skip None keys (env var not set)


def log_plan_product_config() -> None:
    """Report which price IDs are configured, once, at start-up.

    An unset STRIPE_*_PRICE_ID silently drops that product from the mapping, so a
    real subscription to it resolves to None and the user keeps whatever
    plan_product they already had — in practice the "dictation" default. Nothing
    is logged and nothing raises: Dictation Pro works, Writing never unlocks, and
    the account looks like a mystery. Missing config belongs in the deploy log,
    not inferred weeks later from a confused user.
    """
    configured = sorted(set(PRICE_TO_PRODUCT.values()))
    missing = sorted(VALID_PLAN_PRODUCTS - set(configured))
    if configured:
        logger.info(f"Stripe price IDs configured for: {configured}")
    if missing:
        logger.warning(
            f"No Stripe price ID set for {missing}. A Stripe subscription to "
            f"{'/'.join(missing)} cannot be recognised, so those customers will "
            f"not get that product unless plan_product is supplied explicitly by "
            f"the billing sync. Missing env vars: "
            + ", ".join(f"STRIPE_{m.upper()}_PRICE_ID" for m in missing)
        )


def _plan_product_from_subscription(sub_object: dict) -> str | None:
    """Extract the plan_product slug from a Stripe subscription object.
    Returns None if we can't determine the product (don't overwrite existing value)."""
    items = sub_object.get("items", {}).get("data", [])
    seen = []
    for item in items:
        price_id = item.get("price", {}).get("id")
        if not price_id:
            continue
        seen.append(price_id)
        if price_id in PRICE_TO_PRODUCT:
            return PRICE_TO_PRODUCT[price_id]
    if seen:
        # Silent before. The customer pays, the webhook arrives, the product is
        # never applied, and nothing records that it happened.
        logger.error(
            f"No price ID in {seen} is mapped. Known: {sorted(PRICE_TO_PRODUCT)}. "
            f"plan_product is left unchanged, so the product they paid for will "
            f"not unlock. Check the STRIPE_*_PRICE_ID env vars against Stripe."
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Per-product subscription writes
#
#  Every path that changes billing state goes through here. The point is that a
#  write for one product can never touch another: buying Writing must not alter
#  Dictation, and a Platform lapse must not disturb an independently owned
#  Dictation subscription.
# ─────────────────────────────────────────────────────────────────────────────

def apply_product_subscription(user, product: str, status: str,
                               period_end=None, subscription_id=None) -> bool:
    """Set one product's subscription state. Returns False for an unknown product."""
    if product not in VALID_PLAN_PRODUCTS:
        logger.error(
            f"Refusing to apply subscription for unknown product {product!r}; "
            f"expected one of {sorted(VALID_PLAN_PRODUCTS)}."
        )
        return False

    # Un-migrated legacy account: carry its existing access into the per-product
    # columns first, or this write would silently strand it (see the docstring).
    if user.materialize_legacy_products():
        logger.info(
            f"Materialized legacy {user.plan_product!r} subscription into "
            f"per-product columns for user {user.id} before applying {product!r}."
        )

    setattr(user, f"{product}_sub_status", status)
    if period_end is not None:
        setattr(user, f"{product}_period_end", period_end)
    if subscription_id is not None:
        setattr(user, f"{product}_sub_id", subscription_id)

    _mirror_legacy_columns(user)
    return True


def _mirror_legacy_columns(user) -> None:
    """Keep the old single-subscription columns roughly in step.

    They are no longer the source of truth for entitlement — the per-product
    columns are — but the billing status endpoint, the trial-expiry cron and
    older clients still read them, so leaving them stale would be its own bug.

    plan_product is a LABEL here, not an entitlement. It is set to 'platform'
    only when a Platform subscription genuinely exists; owning Dictation and
    Writing separately is two subscriptions and must never be relabelled as
    Platform.
    """
    if user.platform_subscription_active:
        user.plan_product = "platform"
    elif user.dictation_subscription_active and not user.writing_subscription_active:
        user.plan_product = "dictation"
    elif user.writing_subscription_active and not user.dictation_subscription_active:
        user.plan_product = "writing"
    # Both owned separately, or none active: leave plan_product as the historical
    # record of what was bought. It is not consulted for access any more.

    # The account-wide status reflects whether ANY product is live, which is what
    # the billing page and the trial cron mean by it.
    ends = [e for e in (user.dictation_period_end, user.writing_period_end,
                        user.platform_period_end) if e]
    if user.dictation_is_paid or user.writing_is_paid:
        statuses = {user.dictation_sub_status, user.writing_sub_status,
                    user.platform_sub_status}
        user.subscription_status = (
            SubscriptionStatus.CANCELED if statuses == {"canceled"} or (
                "canceled" in statuses and "paid" not in statuses)
            else SubscriptionStatus.PAID
        )
        if ends:
            user.current_period_end = max(ends)
    elif any(st in ("expired", "canceled") for st in (
            user.dictation_sub_status, user.writing_sub_status,
            user.platform_sub_status)):
        user.subscription_status = SubscriptionStatus.EXPIRED


def _product_for_subscription(user, sub_object: dict, subscription_id: str | None) -> str | None:
    """Work out which product a Stripe subscription belongs to.

    Price ID first, because that is authoritative. Falling back to the stored
    per-product subscription ids matters for events that carry no price data —
    without it a cancellation would be applied to the wrong product, or to all
    of them.
    """
    product = _plan_product_from_subscription(sub_object or {})
    if product:
        return product
    if subscription_id:
        for candidate in VALID_PLAN_PRODUCTS:
            if getattr(user, f"{candidate}_sub_id", None) == subscription_id:
                return candidate
    return None


router = APIRouter(prefix="/api/billing", tags=["Billing"])


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/billing/products — the billing page's source of truth
# ─────────────────────────────────────────────────────────────────────────────

def _product_state(user, product: str, trial_active: bool, trial_days_left):
    """One product's state, resolved independently of the other two."""
    sub_active = getattr(user, f"{product}_subscription_active")
    status = getattr(user, f"{product}_sub_status")
    if product == "platform":
        return {
            "product": "platform",
            "state": "active" if sub_active else ("expired" if status in ("expired", "canceled") else "inactive"),
            "subscription_active": sub_active,
            "period_end": getattr(user, "platform_period_end"),
        }

    premium = getattr(user, f"{product}_is_paid")
    if premium:
        state = "premium"
    elif trial_active:
        state = "trial"
    elif status in ("expired", "canceled"):
        state = "expired"
    else:
        state = "free"
    return {
        "product": product,
        "state": state,
        "premium": premium,
        # True only for a subscription to THIS product — Platform grants premium
        # without the user owning a Dictation or Writing subscription, and the
        # billing page must be able to tell those apart.
        "own_subscription_active": sub_active,
        "via_platform": premium and not sub_active,
        "trial_active": trial_active,
        "trial_days_remaining": trial_days_left,
        "period_end": getattr(user, f"{product}_period_end"),
    }


@router.get("/products")
async def get_product_entitlements(current_user: User = Depends(get_current_user)):
    """Per-product billing state for all three offerings.

    Exists because /status returns a single account-wide plan, which cannot
    describe an account that is premium for one product and on trial for the
    other — the exact case the billing page has to show.
    """
    from routers.auth import writing_trial_active, WRITING_TRIAL_DAYS

    dict_trial = not current_user.is_trial_expired
    dict_days = None
    if dict_trial:
        used = (datetime.utcnow() - current_user.trial_start_at).days
        dict_days = max(0, 14 - used)

    w_trial = writing_trial_active(current_user)
    w_days = None
    if w_trial and current_user.writing_trial_started_at:
        used = (datetime.utcnow() - current_user.writing_trial_started_at.replace(tzinfo=None)).days
        w_days = max(0, WRITING_TRIAL_DAYS - used)

    return {
        "dictation": _product_state(current_user, "dictation", dict_trial, dict_days),
        "writing":   _product_state(current_user, "writing", w_trial, w_days),
        "platform":  _product_state(current_user, "platform", False, None),
        # Only subscriptions actually held. Owning Dictation and Writing
        # separately is two subscriptions and must never be shown as Platform.
        "owned_products": current_user.owned_products,
    }

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
        user.stripe_customer_id = data_object.get('customer')
        sub_id = data_object.get('subscription')
        if sub_id:
            user.stripe_subscription_id = sub_id
            product = None
            try:
                sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
                product = _plan_product_from_subscription(sub)
            except Exception as e:
                logger.warning(f"Could not fetch subscription {sub_id} for its product: {e}")
            if product:
                # Only this product becomes paid. A Dictation purchase must not
                # touch Writing, and must not consume the Writing trial.
                apply_product_subscription(user, product, "paid", subscription_id=sub_id)
            else:
                logger.error(
                    f"checkout.session.completed for {sub_id} could not be mapped to a "
                    f"product; no entitlement granted. Check the STRIPE_*_PRICE_ID vars."
                )
        # current_period_end arrives with the following subscription.updated event

    elif event_type == 'customer.subscription.updated':
        sub_id = data_object.get('id')
        product = _product_for_subscription(user, data_object, sub_id)
        if not product:
            logger.error(
                f"customer.subscription.updated for {sub_id} could not be mapped to a "
                f"product; ignoring rather than guessing and changing the wrong one."
            )
        else:
            status = data_object.get('status')
            period_end = data_object.get('current_period_end')
            end_dt = (datetime.fromtimestamp(period_end, tz=timezone.utc).replace(tzinfo=None)
                      if period_end else None)

            if status in ('active', 'trialing'):
                apply_product_subscription(user, product, "paid", end_dt, sub_id)
            elif status == 'past_due':
                # Still inside the paid period; Stripe retries. Not an entitlement
                # change on its own — the period end decides.
                apply_product_subscription(user, product, "canceled", end_dt, sub_id)
                user.subscription_status = SubscriptionStatus.PAST_DUE
            elif status in ('canceled', 'unpaid', 'incomplete_expired'):
                apply_product_subscription(user, product, "canceled", end_dt, sub_id)

            user.cancel_at_period_end = data_object.get('cancel_at_period_end', False)

    elif event_type == 'customer.subscription.deleted':
        sub_id = data_object.get('id')
        product = _product_for_subscription(user, data_object, sub_id)
        if not product:
            logger.error(
                f"customer.subscription.deleted for {sub_id} could not be mapped to a "
                f"product; ignoring. Cancelling the wrong product would remove access "
                f"the customer is still paying for."
            )
        else:
            # Deleted outright: access ends now, whatever the period said. Only
            # this product — anything else the account owns is untouched.
            apply_product_subscription(user, product, "expired", subscription_id=sub_id)
            user.cancel_at_period_end = True

    elif event_type == 'invoice.payment_failed':
        user.subscription_status = SubscriptionStatus.PAST_DUE

    elif event_type == 'invoice.payment_succeeded':
        lines = data_object.get('lines', {}).get('data', [])
        sub_id = data_object.get('subscription')
        product = _product_for_subscription(user, {"items": {"data": lines}}, sub_id)
        end_dt = None
        if lines:
            period_end = lines[0].get('period', {}).get('end')
            if period_end:
                end_dt = datetime.fromtimestamp(period_end, tz=timezone.utc).replace(tzinfo=None)
        if product:
            apply_product_subscription(user, product, "paid", end_dt, sub_id)
        else:
            logger.error(
                f"invoice.payment_succeeded for subscription {sub_id} could not be "
                f"mapped to a product; entitlement left unchanged."
            )

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

    # 4. Apply the subscription to the RIGHT product.
    #
    # A sync carries one product's subscription, so it must change only that
    # product. Writing a shared status here is what made a Dictation purchase
    # unlock Writing (and a Writing lapse remove Dictation).
    STATUS_MAP = {
        "active": "paid", "trialing": "paid",
        "past_due": "canceled",          # still inside the paid period
        "canceled": "canceled", "expired": "expired",
    }
    product_status = STATUS_MAP.get(status_str)
    if product_status is None:
        logger.error(
            f"lovable-sync sent status={status_str!r} for {email!r}, which is not "
            f"recognised. Nothing was changed."
        )
        raise HTTPException(status_code=400, detail=f"Unknown status: {status_str}")

    if customer_id := data.get("stripe_customer_id"):
        user.stripe_customer_id = customer_id
    subscription_id = data.get("stripe_subscription_id") or None
    if subscription_id:
        user.stripe_subscription_id = subscription_id
    user.cancel_at_period_end = data.get("cancel_at_period_end", False)

    period_end = data.get("current_period_end")
    end_dt = None
    if period_end:
        try:
            # ISO from Lovable, e.g. 2026-06-13T00:00:00Z
            end_dt = datetime.fromisoformat(str(period_end).replace('Z', '+00:00')).replace(tzinfo=None)
        except (ValueError, TypeError):
            logger.warning(f"lovable-sync: could not parse current_period_end={period_end!r}")

    # Which product? Same normalisation as before: casing and stray whitespace
    # were silently discarding the value, leaving the account dictation-only.
    raw_product = data.get("plan_product")
    normalised = str(raw_product).strip().lower() if raw_product is not None else None
    if normalised is not None and normalised not in VALID_PLAN_PRODUCTS:
        logger.error(
            f"lovable-sync sent plan_product={raw_product!r}, which is not one of "
            f"{sorted(VALID_PLAN_PRODUCTS)}. Nothing was changed for {email!r}."
        )
        raise HTTPException(
            status_code=400,
            detail=f"Unknown plan_product: {raw_product}. Expected one of "
                   f"{sorted(VALID_PLAN_PRODUCTS)}.",
        )

    if normalised is None:
        # No product named. Historically this meant Dictation, which was the only
        # product that existed — keep that reading rather than guessing, but say so.
        normalised = "dictation"
        logger.warning(
            f"lovable-sync for {email!r} carried no plan_product; assuming "
            f"'dictation' for backwards compatibility."
        )

    apply_product_subscription(user, normalised, product_status, end_dt, subscription_id)
    logger.info(
        f"lovable-sync applied: {email} {normalised}={product_status} "
        f"(dictation_premium={user.dictation_is_paid}, "
        f"writing_premium={user.writing_is_paid}, "
        f"platform_active={user.platform_subscription_active})"
    )

    await audit.record(
        db, audit.BILLING_SYNC_APPLIED, user_id=user.id, email=user.email, request=request,
        detail=f"status={status_str} product={normalised} "
               f"dictation_premium={user.dictation_is_paid} "
               f"writing_premium={user.writing_is_paid} "
               f"platform_active={user.platform_subscription_active}",
    )
    await db.commit()
    # Echo what was actually stored, plus the resulting entitlements. The caller
    # previously got a bare "success" whether or not its plan_product was
    # understood, so a rejected value looked exactly like an accepted one.
    return {
        "status": "success",
        "user_id": str(user.id),
        "product_applied": normalised,
        "subscription_status": user.subscription_status.value,
        # The three answers that matter, so a caller can see exactly what its
        # sync did rather than inferring it from a bare "success".
        "dictation_premium": user.dictation_is_paid,
        "writing_premium": user.writing_is_paid,
        "platform_active": user.platform_subscription_active,
        "owned_products": user.owned_products,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Development-only entitlement simulator
#
#  Lets every combination in the test matrix be set without a real Stripe
#  payment. Registered ONLY when ENVIRONMENT is explicitly a development value,
#  so in production the route does not exist at all — a 404, not a 403 that
#  depends on a check inside the handler being right.
# ─────────────────────────────────────────────────────────────────────────────

DEV_ENVIRONMENTS = {"development", "dev", "local", "test", "testing"}


def dev_billing_enabled() -> bool:
    return (os.getenv("ENVIRONMENT") or "").strip().lower() in DEV_ENVIRONMENTS


if dev_billing_enabled():

    _SIM_PRODUCT_STATES = {
        # state name -> (sub_status, period_end offset in days or None)
        "free":    (None, None),
        "premium": ("paid", 30),
        "expired": ("expired", -1),
        # Cancelled but still inside the paid period — access continues.
        "canceling": ("canceled", 7),
    }

    @router.post("/dev/simulate")
    async def simulate_entitlements(
        payload: dict = Body(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Force this account into a given entitlement combination.

        Body, all optional:
            {"dictation": "free|trial|premium|expired|canceling",
             "writing":   "free|trial|premium|expired|canceling",
             "platform":  "inactive|active|expired"}

        Only the products named are touched, so a test can change one and
        confirm the others are unaffected — which is the whole point.
        """
        now = datetime.utcnow()
        applied = {}

        # Preserve whatever a legacy account already had, so products the caller
        # does NOT name keep their current access instead of vanishing the moment
        # the first per-product column is written.
        current_user.materialize_legacy_products()

        for product in ("dictation", "writing"):
            want = payload.get(product)
            if want is None:
                continue
            want = str(want).strip().lower()

            if want == "trial":
                # Trials are tracked separately from subscriptions, so clear the
                # subscription and (re)start this product's own trial clock.
                setattr(current_user, f"{product}_sub_status", None)
                setattr(current_user, f"{product}_period_end", None)
                if product == "dictation":
                    current_user.trial_start_at = now
                else:
                    current_user.writing_trial_started_at = now
            elif want in _SIM_PRODUCT_STATES:
                status, days = _SIM_PRODUCT_STATES[want]
                setattr(current_user, f"{product}_sub_status", status)
                setattr(current_user, f"{product}_period_end",
                        now + timedelta(days=days) if days is not None else None)
                if want == "free":
                    # Expire this product's trial too, or "free" still reads as trial.
                    if product == "dictation":
                        current_user.trial_start_at = now - timedelta(days=365)
                    else:
                        current_user.writing_trial_started_at = now - timedelta(days=365)
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown {product} state {want!r}. Expected one of "
                           f"{['trial'] + sorted(_SIM_PRODUCT_STATES)}.",
                )
            applied[product] = want

        want = payload.get("platform")
        if want is not None:
            want = str(want).strip().lower()
            mapping = {"inactive": (None, None), "active": ("paid", 30),
                       "expired": ("expired", -1), "canceling": ("canceled", 7)}
            if want not in mapping:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown platform state {want!r}. Expected one of "
                           f"{sorted(mapping)}.",
                )
            status, days = mapping[want]
            current_user.platform_sub_status = status
            current_user.platform_period_end = (
                now + timedelta(days=days) if days is not None else None)
            applied["platform"] = want

        if not current_user.products_migrated:
            # Every product was simulated back to "no subscription". The account
            # now looks un-migrated, so the legacy fallback would re-grant exactly
            # what was just cleared — clear the legacy columns to match.
            current_user.subscription_status = SubscriptionStatus.TRIAL
            current_user.current_period_end = None
            current_user.stripe_subscription_id = None

        _mirror_legacy_columns(current_user)
        await db.commit()
        await db.refresh(current_user)

        logger.info(f"[dev] simulated entitlements for {current_user.email}: {applied}")
        return {
            "applied": applied,
            "dictation_premium": current_user.dictation_is_paid,
            "writing_premium": current_user.writing_is_paid,
            "platform_active": current_user.platform_subscription_active,
            "owned_products": current_user.owned_products,
            "tier": current_user.tier,
        }
