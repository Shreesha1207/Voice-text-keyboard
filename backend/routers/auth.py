from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
from pydantic import ValidationError
import os
import uuid

from database import get_db
from models import User, SubscriptionStatus
from schemas import (
    UserRegister, UserLogin, TokenResponse, RefreshRequest, ValidateResponse,
    GoogleAuthCode, UserOut
)
from security import (
    get_password_hash, verify_password, create_access_token, create_refresh_token,
    SECRET_KEY, ALGORITHM
)
from dependencies import get_current_user
from jose import jwt, JWTError
from datetime import datetime, timezone

router = APIRouter(prefix="/api/auth", tags=["Auth"])

@router.post("/register", response_model=TokenResponse)
async def register(user_data: UserRegister, db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.email == user_data.email)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = User(
        email=user_data.email,
        password_hash=get_password_hash(user_data.password),
        display_name=user_data.display_name,
        subscription_status=SubscriptionStatus.TRIAL
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    access_token = create_access_token(subject=str(new_user.id))
    refresh_token = create_refresh_token(subject=str(new_user.id))

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)

@router.post("/login", response_model=TokenResponse)
async def login(user_data: UserLogin, db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.email == user_data.email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not user.password_hash:
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    
    if not verify_password(user_data.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(refresh_data.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("type")
        if user_id is None or token_type != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
            
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    access_token = create_access_token(subject=user_id)
    new_refresh_token = create_refresh_token(subject=user_id)

    return TokenResponse(access_token=access_token, refresh_token=new_refresh_token)

@router.post("/google", response_model=TokenResponse)
async def google_auth(auth_data: GoogleAuthCode, db: AsyncSession = Depends(get_db)):
    # Very basic Google OAuth verification - needs real google client secret in prod
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="Google Auth not configured")

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": auth_data.code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": auth_data.redirect_uri,
        "grant_type": "authorization_code",
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=data)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Google authentication failed")
        
        token_data = response.json()
        id_token = token_data.get("id_token")
        
        if not id_token:
             raise HTTPException(status_code=400, detail="Invalid token response from Google")
             
        # Decoding JWT (in production use google.auth library to verify signature)
        try:
             decoded_id_token = jwt.get_unverified_claims(id_token)
             email = decoded_id_token.get("email")
             google_id = decoded_id_token.get("sub")
             name = decoded_id_token.get("name")
        except Exception:
             raise HTTPException(status_code=400, detail="Could not parse Google token")

    if not email:
        raise HTTPException(status_code=400, detail="Email not provided by Google")

    # Check if user exists
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user:
        if not user.google_id:
            user.google_id = google_id
            await db.commit()
    else:
        # Create new user
        user = User(
            email=email,
            google_id=google_id,
            display_name=name,
            subscription_status=SubscriptionStatus.TRIAL
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)

@router.get("/validate", response_model=ValidateResponse)
async def validate_status(current_user: User = Depends(get_current_user)):
    """Check if the user is allowed to use the service (trial active or paid)"""
    allowed = True
    reason = "active"
    trial_remaining = None

    if current_user.subscription_status == SubscriptionStatus.PAID:
        allowed = True
        reason = "paid"
    else:
        delta = datetime.now(timezone.utc) - current_user.trial_start_at.replace(tzinfo=timezone.utc)
        days_used = delta.days
        if days_used >= 14:
            allowed = False
            reason = "trial_expired"
            trial_remaining = 0
            # Update DB status if needed
            if current_user.subscription_status != SubscriptionStatus.EXPIRED:
                 current_user.subscription_status = SubscriptionStatus.EXPIRED
        else:
            trial_remaining = 14 - days_used
            reason = "trial_active"

    return ValidateResponse(
        allowed=allowed,
        reason=reason,
        tier=current_user.tier,
        trial_days_remaining=trial_remaining,
        user_id=str(current_user.id)
    )

@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user profile"""
    return current_user
