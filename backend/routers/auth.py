from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request
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
    GoogleAuthCode, UserOut, HotkeyUpdate, LanguageUpdate, TranslationUpdate,
    LeaderboardOptInUpdate,
    ForgotPasswordRequest, ResetPasswordRequest
)
from security import (
    get_password_hash, verify_password, create_access_token, create_refresh_token,
    SECRET_KEY, ALGORITHM
)
from dependencies import get_current_user
from jose import jwt, JWTError
from datetime import datetime, timezone

from email_service import send_welcome_email, send_password_reset_email
import secrets
from datetime import timedelta

import audit
import hashlib
from rate_limit import limit_by_ip, limit_by_identity

router = APIRouter(prefix="/api/auth", tags=["Auth"])


def _hash_reset_token(token: str) -> str:
    """Hash a password-reset token for storage.

    Only the hash is persisted; the plaintext exists solely in the email we send.
    SHA-256 without a salt is appropriate here because the input is 256 bits of
    cryptographic randomness — there is no dictionary to attack, unlike a password.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

@router.post(
    "/register",
    response_model=TokenResponse,
    # Caps disposable-email trial farming and stops bulk account creation.
    dependencies=[Depends(limit_by_ip("register_ip", limit=5, window_seconds=3600))],
)
async def register(
    user_data: UserRegister,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
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

    await audit.record(
        db, audit.REGISTER, user_id=new_user.id, email=new_user.email,
        request=request, commit=True,
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)

@router.post(
    "/login",
    response_model=TokenResponse,
    # Broad per-IP ceiling. Spoofable via X-Forwarded-For, so it is a speed bump —
    # the per-account limit below is the one that actually protects an account.
    dependencies=[Depends(limit_by_ip("login_ip", limit=20, window_seconds=300))],
)
async def login(user_data: UserLogin, request: Request, db: AsyncSession = Depends(get_db)):
    # Per-account limit: 8 attempts per 5 minutes against one email address,
    # regardless of how many IPs the attempts come from. This is what stops
    # someone grinding a single account's password.
    await limit_by_identity("login_email", user_data.email, limit=8, window_seconds=300)

    stmt = select(User).where(User.email == user_data.email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not user.password_hash:
        await audit.record(
            db, audit.LOGIN_FAILED, email=user_data.email, request=request,
            detail="no such account, or account has no password set", commit=True,
        )
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    if not verify_password(user_data.password, user.password_hash):
        await audit.record(
            db, audit.LOGIN_FAILED, user_id=user.id, email=user_data.email,
            request=request, detail="wrong password", commit=True,
        )
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    access_token = create_access_token(subject=str(user.id), token_version=user.token_version)
    refresh_token = create_refresh_token(subject=str(user.id), token_version=user.token_version)

    await audit.record(
        db, audit.LOGIN_SUCCESS, user_id=user.id, email=user.email,
        request=request, commit=True,
    )
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

        # Validate the claims. The token came straight from Google's token endpoint
        # over TLS using our client secret, so the signature is implicitly trusted —
        # but nothing checked who the token was *for*. Without an audience check, an
        # ID token minted for a different application would be accepted here.
        issuer = decoded_id_token.get("iss")
        if issuer not in ("accounts.google.com", "https://accounts.google.com"):
            raise HTTPException(status_code=400, detail="Google token has an unexpected issuer")

        audience = decoded_id_token.get("aud")
        if audience != client_id:
            raise HTTPException(status_code=400, detail="Google token was not issued for this application")

        exp = decoded_id_token.get("exp")
        if exp and datetime.now(timezone.utc).timestamp() > float(exp):
            raise HTTPException(status_code=400, detail="Google token has expired")

        # Google sets this false for unverified addresses; accepting them would let
        # someone claim an email they do not control.
        if decoded_id_token.get("email_verified") is False:
            raise HTTPException(status_code=400, detail="Google account email is not verified")

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
    trial_active = False   # dictation trial active (non-paid user within 14-day window)

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
            trial_active = True
            trial_remaining = 14 - days_used
            reason = "trial_active"

    # Compute entitlements
    dictation_enabled = (current_user.tier == "paid") or trial_active

    # Check writing trial state
    writing_trial_active = False
    if current_user.writing_trial_started_at is not None:
        w_started = current_user.writing_trial_started_at.replace(tzinfo=timezone.utc) if current_user.writing_trial_started_at.tzinfo is None else current_user.writing_trial_started_at
        w_elapsed = (datetime.now(timezone.utc) - w_started).days
        writing_trial_active = w_elapsed < 14

    # Writing access is governed by the WRITING trial only (writing_trial_started_at),
    # independent of the dictation/keyboard trial — do NOT couple it to trial_active.
    writing_enabled = (
        current_user.writing_is_paid
        or writing_trial_active
    )

    if writing_enabled or dictation_enabled:
        allowed = True

    return ValidateResponse(
        allowed=allowed,
        reason=reason,
        tier=current_user.tier,
        trial_days_remaining=trial_remaining,
        user_id=str(current_user.id),
        custom_hotkey=current_user.custom_hotkey,
        preferred_language=current_user.preferred_language,
        is_translation_enabled=current_user.is_translation_enabled,
        plan_product=current_user.plan_product,
        writing_enabled=writing_enabled,
        dictation_enabled=dictation_enabled,
    )

@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user profile including writing/dictation entitlements."""
    from models import SubscriptionStatus as SS

    trial_active = False
    if current_user.subscription_status not in (SS.PAID, SS.CANCELED):
        delta = datetime.now(timezone.utc) - current_user.trial_start_at.replace(tzinfo=timezone.utc)
        trial_active = delta.days < 14

    is_paid = current_user.tier == "paid"

    # Dictation: paid OR active dictation/keyboard trial
    dictation_enabled = is_paid or trial_active

    # Writing: writing/platform plan, OR an active WRITING trial (separate from the
    # keyboard trial — keyed on writing_trial_started_at, 14-day window).
    writing_trial_active = False
    if current_user.writing_trial_started_at is not None:
        w_started = (
            current_user.writing_trial_started_at.replace(tzinfo=timezone.utc)
            if current_user.writing_trial_started_at.tzinfo is None
            else current_user.writing_trial_started_at
        )
        writing_trial_active = (datetime.now(timezone.utc) - w_started).days < 14

    writing_enabled = (
        current_user.writing_is_paid
        or writing_trial_active
    )

    return UserOut(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        created_at=current_user.created_at,
        trial_start_at=current_user.trial_start_at,
        subscription_status=current_user.subscription_status,
        total_words=current_user.total_words,
        streak_days=current_user.streak_days,
        longest_streak=current_user.longest_streak,
        is_leaderboard_opt_in=current_user.is_leaderboard_opt_in,
        tier=current_user.tier,
        timezone=current_user.timezone,
        custom_hotkey=current_user.custom_hotkey,
        preferred_language=current_user.preferred_language,
        is_translation_enabled=current_user.is_translation_enabled,
        plan_product=current_user.plan_product,
        writing_enabled=writing_enabled,
        dictation_enabled=dictation_enabled,
    )

@router.post("/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Logout from all devices by incrementing token_version.
    All existing access & refresh tokens become invalid immediately."""
    current_user.token_version += 1
    await audit.record(
        db, audit.LOGOUT_ALL, user_id=current_user.id, email=current_user.email,
        request=request,
    )
    await db.commit()
    return {"status": "ok", "detail": "Logged out from all devices"}


@router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Request a password reset link. Always returns 200 to prevent email enumeration."""
    # Limit per address as well as per IP: without this, anyone can spray reset
    # mail at a victim's inbox indefinitely.
    await limit_by_identity("forgot_password", data.email, limit=3, window_seconds=900)

    stmt = select(User).where(User.email == data.email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user and user.password_hash:  # Only for email/password accounts
        token = secrets.token_urlsafe(32)
        # Store only a hash. The plaintext goes in the email and nowhere else, so a
        # database leak cannot be replayed into an account takeover inside the
        # 15-minute window. Plain SHA-256 is right here (unlike passwords): the token
        # is 256 bits of randomness, so there is nothing to brute-force.
        user.password_reset_token = _hash_reset_token(token)
        user.password_reset_expires = datetime.now(timezone.utc) + timedelta(minutes=15)
        await db.commit()

        frontend_url = os.getenv("FRONTEND_URL", "https://xvoicekeyboard.com")
        reset_link = f"{frontend_url}/reset-password?token={token}"
        background_tasks.add_task(
            send_password_reset_email, user.email, reset_link, user.display_name
        )
        await audit.record(
            db, audit.PASSWORD_RESET_ASKED, user_id=user.id, email=user.email,
            request=request, commit=True,
        )
    else:
        # Record the attempt even when the address is unknown — a burst of these is
        # how account enumeration looks from the outside. The response stays
        # identical either way so the caller learns nothing.
        await audit.record(
            db, audit.PASSWORD_RESET_ASKED, email=data.email, request=request,
            detail="no matching account", commit=True,
        )

    return {"status": "ok", "detail": "If that email exists, a reset link has been sent."}


@router.post(
    "/reset-password",
    dependencies=[Depends(limit_by_ip("reset_password_ip", limit=10, window_seconds=900))],
)
async def reset_password(
    data: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Reset password using a valid token from the reset email."""
    # Look the token up by its hash — see _hash_reset_token.
    stmt = select(User).where(User.password_reset_token == _hash_reset_token(data.token))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not user.password_reset_expires:
        await audit.record(
            db, audit.PASSWORD_RESET_FAILED, request=request,
            detail="unknown or already-used reset token", commit=True,
        )
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

    if datetime.now(timezone.utc) > user.password_reset_expires.replace(tzinfo=timezone.utc):
        await audit.record(
            db, audit.PASSWORD_RESET_FAILED, user_id=user.id, email=user.email,
            request=request, detail="token expired", commit=True,
        )
        raise HTTPException(status_code=400, detail="Reset link has expired. Please request a new one.")

    # Update password, clear token, invalidate all existing sessions
    user.password_hash = get_password_hash(data.new_password)
    user.password_reset_token = None
    user.password_reset_expires = None
    user.token_version += 1  # Invalidates all active sessions
    await audit.record(
        db, audit.PASSWORD_RESET_DONE, user_id=user.id, email=user.email,
        request=request,
    )
    await db.commit()

    return {"status": "ok", "detail": "Password updated successfully. Please sign in with your new password."}

@router.patch("/leaderboard-opt-in")
async def update_leaderboard_opt_in(
    data: LeaderboardOptInUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Opt in or out of the public leaderboard.

    The column defaulted to True and nothing anywhere could change it, so every
    user's display name was published without consent and with no way to withdraw.
    """
    current_user.is_leaderboard_opt_in = data.opt_in
    await db.commit()
    return {"status": "ok", "is_leaderboard_opt_in": data.opt_in}


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

    # The client sends this fire-and-forget on every user fetch, almost always with
    # the value already stored. Skip unchanged updates entirely — no write, and no
    # rate-limit budget consumed — so the guard below only sees genuine changes.
    if tz_name == current_user.timezone:
        return {"status": "ok", "timezone": tz_name}

    # Validate the timezone string
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz_name}")

    # "Today" for streaks is derived from this zone, so rapidly flipping it could
    # farm a streak. A handful of real changes a day (travel, DST) is plenty; this
    # only bites automated flip-flopping.
    await limit_by_identity("timezone_change", str(current_user.id), limit=5, window_seconds=3600)

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
    
    # Single letters/numbers are deliberately NOT allowed: the client binds the key
    # globally, so a hotkey of "a" would start recording every time the user types A.
    if hotkey not in allowed_keys:
        raise HTTPException(
            status_code=400,
            detail="Invalid hotkey. Use a function key (f2–f12) or a standard key like ctrl, alt, shift, space."
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
