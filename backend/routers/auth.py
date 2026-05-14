from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
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
    GoogleAuthCode, UserOut, HotkeyUpdate, LanguageUpdate, TranslationUpdate
)
from security import (
    get_password_hash, verify_password, create_access_token, create_refresh_token,
    SECRET_KEY, ALGORITHM
)
from dependencies import get_current_user
from jose import jwt, JWTError
from datetime import datetime, timezone

from email_service import send_welcome_email

router = APIRouter(prefix="/api/auth", tags=["Auth"])

@router.post("/register", response_model=TokenResponse)
async def register(user_data: UserRegister, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
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

    access_token = create_access_token(subject=str(new_user.id), token_version=new_user.token_version)
    refresh_token = create_refresh_token(subject=str(new_user.id), token_version=new_user.token_version)

    background_tasks.add_task(send_welcome_email, new_user.email, new_user.display_name)

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

    access_token = create_access_token(subject=str(user.id), token_version=user.token_version)
    refresh_token = create_refresh_token(subject=str(user.id), token_version=user.token_version)

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(refresh_data.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("type")
        token_ver = payload.get("ver", 0)
        if user_id is None or token_type != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
            
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Validate token_version against DB — prevents revoked refresh tokens from working
    stmt = select(User).where(User.id == uuid.UUID(user_id))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    if token_ver != user.token_version:
        raise HTTPException(status_code=401, detail="Session invalidated (logged out)")

    access_token = create_access_token(subject=user_id, token_version=user.token_version)
    new_refresh_token = create_refresh_token(subject=user_id, token_version=user.token_version)

    return TokenResponse(access_token=access_token, refresh_token=new_refresh_token)

@router.post("/google", response_model=TokenResponse)
async def google_auth(auth_data: GoogleAuthCode, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
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

        # Send welcome email for new Google signups
        background_tasks.add_task(send_welcome_email, user.email, user.display_name)

    access_token = create_access_token(subject=str(user.id), token_version=user.token_version)
    refresh_token = create_refresh_token(subject=str(user.id), token_version=user.token_version)

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
        user_id=str(current_user.id),
        custom_hotkey=current_user.custom_hotkey,
        preferred_language=current_user.preferred_language,
        is_translation_enabled=current_user.is_translation_enabled
    )

@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user profile"""
    return current_user

@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Logout from all devices by incrementing token_version.
    All existing access & refresh tokens become invalid immediately."""
    current_user.token_version += 1
    await db.commit()
    return {"status": "ok", "detail": "Logged out from all devices"}

@router.patch("/timezone")
async def update_timezone(
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update the user's timezone preference (IANA format, e.g. 'Asia/Kolkata')."""
    tz_name = data.get("timezone", "").strip()
    if not tz_name:
        raise HTTPException(status_code=400, detail="timezone field is required")

    # Validate the timezone string
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz_name}")

    current_user.timezone = tz_name
    await db.commit()
    return {"status": "ok", "timezone": tz_name}

@router.patch("/hotkey")
async def update_hotkey(
    data: HotkeyUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update custom push-to-talk hotkey. Only available for Pro users."""
    if current_user.tier != "paid":
        raise HTTPException(status_code=403, detail="Custom hotkeys are a Pro feature.")
        
    hotkey = data.hotkey.strip().lower()
    
    # Strict validation to prevent the desktop app from crashing
    # These are the safe, commonly used keys supported by the 'keyboard' library
    allowed_keys = {
        "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
        "ctrl", "alt", "shift", "space", "tab", "caps lock", "caps_lock", "esc", "enter",
        "left_ctrl", "right_ctrl", "left_alt", "right_alt", "left_shift", "right_shift",
        "`", "~", "insert", "delete", "home", "end", "page up", "page_down", "page_up", "page down",
        "up", "down", "left", "right"
    }
    
    if hotkey not in allowed_keys and not (len(hotkey) == 1 and hotkey.isalnum()):
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid hotkey. Must be a single letter/number or a standard key like f8, ctrl, alt, shift."
        )
    
    current_user.custom_hotkey = hotkey
    await db.commit()
    return {"status": "ok", "hotkey": hotkey}

@router.patch("/language")
async def update_language(
    data: LanguageUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update preferred transcription language. Only available for Pro users."""
    if current_user.tier != "paid":
        raise HTTPException(status_code=403, detail="Custom languages are a Pro feature.")
        
    lang = data.language.strip().lower()
    
    # Common ISO-639-1 language codes supported by OpenAI Whisper
    allowed_languages = {
        "af", "ar", "as", "az", "be", "bg", "bn", "br", "bs", "ca", "cs", "cy", "da", "de",
        "el", "en", "es", "et", "eu", "fa", "fi", "fo", "fr", "gl", "gu", "ha", "he", "hi",
        "hr", "hu", "hy", "id", "is", "it", "ja", "jw", "ka", "kk", "kn", "ko", "la", "lb",
        "ln", "lo", "lt", "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt", "my", "ne",
        "nl", "nn", "no", "oc", "pa", "pl", "ps", "pt", "ro", "ru", "sa", "sd", "si", "sk",
        "sl", "sn", "so", "sq", "sr", "st", "su", "sv", "sw", "ta", "te", "tg", "th", "tk",
        "tl", "tr", "tt", "uk", "ur", "uz", "vi", "yo", "zh"
    }
    
    if lang not in allowed_languages:
        raise HTTPException(status_code=400, detail=f"Invalid language code: {lang}")
    
    current_user.preferred_language = lang
    await db.commit()
    return {"status": "ok", "language": lang}

@router.patch("/translation")
async def update_translation(
    data: TranslationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Toggle translation feature. Only available for Pro users."""
    if current_user.tier != "paid":
        raise HTTPException(status_code=403, detail="Translation is a Pro feature.")
    
    current_user.is_translation_enabled = data.enabled
    await db.commit()
    return {"status": "ok", "enabled": data.enabled}
