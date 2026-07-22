"""
Writing Engine endpoints.

Endpoints:
  GET   /api/writing/status          → trial/paid/inactive status + daily cap info
  POST  /api/writing/trial/start     → start 14-day free writing trial (idempotent)
  POST  /api/writing/rewrite         → AI rewrite (enforces 50/day cap for trial)
  GET   /api/writing/preferences     → user's writing preferences (creates row on first call)
  PATCH /api/writing/preferences     → partial update
  PATCH /api/auth/writing-hotkey     → hotkey update
  GET   /api/writing/usage           → dashboard stats (Lovable shape)
"""

from __future__ import annotations

import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from openai import AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models import User, WritingAction, WritingPreferences, SubscriptionStatus
from schemas import (
    WritingPreferencesOut,
    WritingPreferencesUpdate,
    WritingHotkeyUpdate,
    WritingUsageResponse,
    DailyWritingCount,
    WritingRewriteRequest,
    WritingRewriteResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Writing"])

openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "sk-mock-key"))

MAX_TEXT_CHARS  = 8_000
TRIAL_DAYS      = 14
TRIAL_DAILY_CAP = 50   # max rewrites per day for trial users


# ─────────────────────────────────────────────────────────────────────────────
#  Status helpers
# ─────────────────────────────────────────────────────────────────────────────

def _writing_status(user: User) -> dict:
    """
    Returns a dict describing the user's writing entitlement:
      status: "inactive" | "trial" | "expired" | "paid"
      trial_started_at: ISO str or None
      trial_days_remaining: int (0 for non-trial)
      actions_today: int
      daily_limit: int | None  (None = unlimited)
    """
    now = datetime.now(timezone.utc)

    # Paid: plan_product covers writing OR platform
    is_paid = user.plan_product in ("writing", "platform")
    if is_paid:
        return {
            "status": "paid",
            "trial_started_at": None,
            "trial_days_remaining": 0,
            "actions_today": _today_count(user),
            "daily_limit": None,   # unlimited
        }

    # Trial started?
    if user.writing_trial_started_at is not None:
        started = user.writing_trial_started_at.replace(tzinfo=timezone.utc)
        elapsed = (now - started).days
        remaining = max(TRIAL_DAYS - elapsed, 0)
        if remaining > 0:
            return {
                "status": "trial",
                "trial_started_at": started.isoformat(),
                "trial_days_remaining": remaining,
                "actions_today": _today_count(user),
                "daily_limit": TRIAL_DAILY_CAP,
            }
        else:
            return {
                "status": "expired",
                "trial_started_at": started.isoformat(),
                "trial_days_remaining": 0,
                "actions_today": _today_count(user),
                "daily_limit": TRIAL_DAILY_CAP,
            }

    # Never started a trial
    return {
        "status": "inactive",
        "trial_started_at": None,
        "trial_days_remaining": 0,
        "actions_today": 0,
        "daily_limit": TRIAL_DAILY_CAP,
    }


def _today_count(user: User) -> int:
    """Return writing_actions_today, treating a stale date as 0."""
    today = datetime.now(timezone.utc).date()
    if user.writing_today_date == today:
        return user.writing_actions_today
    return 0


def _require_writing_access(user: User) -> None:
    """Raises 402 if user has no active writing entitlement."""
    st = _writing_status(user)
    if st["status"] in ("inactive", "expired"):
        raise HTTPException(
            status_code=402,
            detail="Writing Engine requires a Writing Pro subscription or an active trial.",
        )


def _enforce_daily_cap(user: User) -> None:
    """Raises 429 if trial user has hit their daily limit."""
    st = _writing_status(user)
    if st["status"] == "paid":
        return   # unlimited
    if st["daily_limit"] is not None and _today_count(user) >= st["daily_limit"]:
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit of {st['daily_limit']} rewrites reached. Upgrade to Writing Pro for unlimited access.",
        )


def _bump_daily_counter(user: User) -> None:
    """Increment today's counter, resetting if the date has changed."""
    today = datetime.now(timezone.utc).date()
    if user.writing_today_date != today:
        user.writing_actions_today = 0
        user.writing_today_date = today
    user.writing_actions_today += 1


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/writing/status
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/writing/status")
async def writing_status(
    current_user: User = Depends(get_current_user),
):
    """Return the user's writing entitlement status."""
    return _writing_status(current_user)


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/writing/trial/start
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/writing/trial/start")
async def start_writing_trial(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Start a 14-day free writing trial.
    Idempotent: returns current status if trial/expired/paid already.
    """
    st = _writing_status(current_user)

    if st["status"] in ("trial", "expired", "paid"):
        # Already started or paid — return current state without changing anything
        return st

    # Start the trial
    current_user.writing_trial_started_at = datetime.now(timezone.utc)
    await db.commit()

    return _writing_status(current_user)


# ─────────────────────────────────────────────────────────────────────────────
#  System-prompt helpers
# ─────────────────────────────────────────────────────────────────────────────

_TONE_ADDENDUM: dict[str, str] = {
    "professional": "Use formal, polished business language.",
    "friendly":     "Use a warm, approachable, and conversational tone.",
    "confident":    "Use assertive, direct language. Avoid hedging.",
    "casual":       "Use relaxed, informal everyday language.",
    "neutral":      "",
}

_ACTION_PROMPTS: dict[str, str] = {
    "translate":    "You are a professional translator. Translate the provided text into {language}.",
    "improve":      "You are an expert editor. Improve the clarity, flow, and quality of the text while keeping the original meaning.",
    "shorten":      "You are a concise editor. Shorten the text while preserving all key information.",
    "expand":       "You are a skilled writer. Expand the text with more detail, examples, and context.",
    "professional": "You are a business writing specialist. Rewrite the text in a professional, formal tone.",
    "casual":       "You are a friendly copywriter. Rewrite the text in a casual, conversational tone.",
    "persuasive":   "You are a persuasion expert. Rewrite the text to be more compelling and motivating.",
    "summarise":    "You are a precise summariser. Summarise the text into a short paragraph.",
    "rephrase":     "You are a paraphrasing expert. Rephrase the text using different words while keeping the exact same meaning.",
    "fix_grammar":  "You are a grammar editor. Fix all grammar, spelling, and punctuation errors. Do not change meaning or style.",
}

_BASE_INSTRUCTION = (
    "Preserve all formatting, punctuation, line breaks, emojis, numbering, and bullet lists "
    "unless the action explicitly requires changes. Do not explain your work. Return only the transformed text."
)


def _build_prompt(action: str, tone: str | None, language: str | None) -> str:
    base = _ACTION_PROMPTS.get(action, _ACTION_PROMPTS["improve"])
    if action == "translate":
        base = base.format(language=language or "English")
    tone_note = _TONE_ADDENDUM.get(tone or "neutral", "")
    parts = [base, tone_note, _BASE_INSTRUCTION]
    return "\n".join(p for p in parts if p)


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/writing/rewrite
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/writing/rewrite", response_model=WritingRewriteResponse)
async def writing_rewrite(
    req: WritingRewriteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI rewrite — core Writing Engine endpoint. Enforces 50/day cap for trial users."""
    _require_writing_access(current_user)
    _enforce_daily_cap(current_user)

    if req.action not in _ACTION_PROMPTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action '{req.action}'. Allowed: {sorted(_ACTION_PROMPTS.keys())}",
        )

    if len(req.text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail=f"Text exceeds {MAX_TEXT_CHARS} character limit.")

    system_prompt = _build_prompt(req.action, req.tone, req.language)

    try:
        chat_res = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": req.text},
            ],
        )
    except Exception as e:
        logger.exception(f"writing_rewrite LLM call failed: {e}")
        raise HTTPException(status_code=502, detail="AI service temporarily unavailable.")

    output_text = chat_res.choices[0].message.content.strip()
    tokens_in   = chat_res.usage.prompt_tokens     if chat_res.usage else 0
    tokens_out  = chat_res.usage.completion_tokens if chat_res.usage else 0

    # Persist the rewrite record
    record = WritingAction(
        user_id=current_user.id,
        action=req.action,
        input_text=req.text,
        output_text=output_text,
        language=req.language if req.action == "translate" else None,
        tone=req.tone,
        success=True,
        chars_in=len(req.text),
        chars_out=len(output_text),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    db.add(record)

    # Bump counters
    _bump_daily_counter(current_user)
    current_user.writing_actions_this_month += 1
    await db.commit()
    await db.refresh(record)

    return WritingRewriteResponse(
        id=record.id,
        action=record.action,
        output=output_text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/writing/preferences
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/writing/preferences", response_model=WritingPreferencesOut)
async def get_writing_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return user's writing preferences. Creates a row with defaults on first call."""
    prefs = current_user.writing_preferences
    if prefs is None:
        prefs = WritingPreferences(user_id=current_user.id)
        db.add(prefs)
        await db.commit()
        await db.refresh(prefs)
    return prefs


# ─────────────────────────────────────────────────────────────────────────────
#  PATCH /api/writing/preferences
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/writing/preferences", response_model=WritingPreferencesOut)
async def update_writing_preferences(
    data: WritingPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Partial update of writing preferences."""
    prefs = current_user.writing_preferences
    if prefs is None:
        prefs = WritingPreferences(user_id=current_user.id)
        db.add(prefs)

    update = data.model_dump(exclude_none=True)
    for field, value in update.items():
        setattr(prefs, field, value)

    prefs.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(prefs)
    return prefs


# ─────────────────────────────────────────────────────────────────────────────
#  PATCH /api/auth/writing-hotkey
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/auth/writing-hotkey", response_model=WritingPreferencesOut)
async def update_writing_hotkey(
    data: WritingHotkeyUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the Writing Engine custom hotkey (stored in writing_preferences)."""
    prefs = current_user.writing_preferences
    if prefs is None:
        prefs = WritingPreferences(user_id=current_user.id)
        db.add(prefs)

    prefs.custom_hotkey = data.hotkey.strip()
    prefs.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(prefs)
    return prefs


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/writing/usage
# ─────────────────────────────────────────────────────────────────────────────

# Action keys Lovable expects in the usage breakdown
_USAGE_ACTION_KEYS = [
    "improve", "professional", "shorten", "translate",
    "fix_grammar", "summarise", "casual", "expand",
    "persuasive", "rephrase",
]

@router.get("/writing/usage")
async def writing_usage(
    range: str = Query(default="7d", pattern="^(7d|30d)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Usage stats for the Writing dashboard.
    Shape:
      actions: [{ key, used }]
      total_this_month: int
      daily_used: int
      daily_limit: int | None
      weekly: [{ date: "YYYY-MM-DD", count }]  ← last 7 days, oldest first
    """
    now = datetime.now(timezone.utc)
    # Always use last 7 days for the weekly chart (regardless of range param)
    week_ago   = now - timedelta(days=7)
    month_ago  = now - timedelta(days=30)

    def naive(dt: datetime) -> datetime:
        return dt.replace(tzinfo=None)

    # ── Weekly rows (last 7 days) ──────────────────────────────────────────
    weekly_q = await db.execute(
        select(WritingAction)
        .where(
            WritingAction.user_id == current_user.id,
            WritingAction.success == True,
            WritingAction.created_at >= naive(week_ago),
        )
        .order_by(WritingAction.created_at)
    )
    weekly_rows = weekly_q.scalars().all()

    # Build 7-day chart (oldest → newest)
    daily_map: dict[str, int] = defaultdict(int)
    for r in weekly_rows:
        daily_map[r.created_at.strftime("%Y-%m-%d")] += 1

    weekly = []
    for i in range(7):
        d = (now - timedelta(days=6 - i)).strftime("%Y-%m-%d")
        weekly.append({"date": d, "count": daily_map.get(d, 0)})

    # ── This-month rows ───────────────────────────────────────────────────
    month_q = await db.execute(
        select(WritingAction)
        .where(
            WritingAction.user_id == current_user.id,
            WritingAction.success == True,
            WritingAction.created_at >= naive(month_ago),
        )
    )
    month_rows = month_q.scalars().all()

    # Action breakdown
    by_action: dict[str, int] = defaultdict(int)
    for r in month_rows:
        by_action[r.action] += 1

    actions = [
        {"key": key, "used": by_action.get(key, 0)}
        for key in _USAGE_ACTION_KEYS
    ]

    # Daily status
    st = _writing_status(current_user)

    return {
        "actions":          actions,
        "total_this_month": len(month_rows),
        "daily_used":       st["actions_today"],
        "daily_limit":      st["daily_limit"],   # None for paid
        "weekly":           weekly,
    }
