from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta

from database import get_db
from models import User, SubscriptionStatus
from schemas import BillingStatusResponse, UpgradeRequest
from dependencies import get_current_user

router = APIRouter(prefix="/api/billing", tags=["Billing"])

@router.get("/status", response_model=BillingStatusResponse)
async def get_billing_status(current_user: User = Depends(get_current_user)):
    """Get current user's billing/trial status."""
    
    trial_remaining = None
    next_billing = None
    plan_name = "Trial"
    
    if current_user.subscription_status == SubscriptionStatus.PAID:
        plan_name = "Pro (Monthly)" # Hardcoded for now
        # Mock next billing date ~ 30 days from some arbitrary point
        next_billing = datetime.utcnow() + timedelta(days=20)
    else:
        delta = datetime.utcnow() - current_user.trial_start_at.replace(tzinfo=None)
        if delta.days < 14:
            trial_remaining = 14 - delta.days
        else:
            trial_remaining = 0
            plan_name = "Expired"

    return BillingStatusResponse(
        status=current_user.subscription_status,
        trial_days_remaining=trial_remaining,
        next_billing_date=next_billing,
        plan=plan_name
    )

@router.post("/upgrade")
async def upgrade_account(
    req: UpgradeRequest, 
    current_user: User = Depends(get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    """
    Mock endpoint to simulate a successful Stripe payment.
    In real life this would generate a Checkout URL and a webhook would update the DB.
    """
    if required_plan := req.plan:
         pass # Normally save the plan type

    # Fast forward: "User paid"
    current_user.subscription_status = SubscriptionStatus.PAID
    await db.commit()

    return {"status": "success", "message": "Account upgraded to Pro!"}

@router.post("/cancel")
async def cancel_subscription(
    current_user: User = Depends(get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    """Mock to cancel active sub"""
    if current_user.subscription_status == SubscriptionStatus.PAID:
        current_user.subscription_status = SubscriptionStatus.EXPIRED # Simplified
        await db.commit()
        return {"status": "success", "message": "Subscription cancelled"}
    return {"status": "error", "message": "No active subscription to cancel"}
