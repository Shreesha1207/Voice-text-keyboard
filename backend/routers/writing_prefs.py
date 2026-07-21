"""
Writing Engine preference endpoints + usage dashboard.

Endpoints:
  GET  /api/writing/preferences         → returns (or creates) user's writing prefs
  PATCH /api/writing/preferences         → partial update
  PATCH /api/auth/writing-hotkey         → dedicated hotkey update
  GET  /api/writing/usage?range=7d|30d  → Lovable dashboard stats shape
  POST  /api/writing/rewrite             → primary rewrite endpoint (Lovable shape)
"""

from __future__ import annotations

import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
router = APIRouter(prefix="/api", tags=["Writing Preferences"])

openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "sk-mock-key"))

MAX_TEXT_CHARS = 8_000

# ─────────────────────────────────────────────────────────────────────────────
#  Entitlement helper
# ─────────────────────────────────────────────────────────────────────────────

def _writing_enabled(user: User) -> bool:
    """True if the user has access to the Writing Engine."""
    if user.plan_product in ("writing", "platform"):
        return True
    # Trial users get Writing access during trial
    if user.subscription_status in (SubscriptionStatus.TRIAL,):
        delta = datetime.now(timezone.utc) - user.trial_start_at.replace(tzinfo=timezone.utc)
        return delta.days < 14
    return False


def _require_writing(user: User) -> None:
    if not _writing_enabled(user):
        raise HTTPException(
            status_code=402,
            detail="Writing Engine requires a Writing Pro or Platform subscription.",
        )


# ─────────────────────────────────────────────────────────────────────────────
#  System-prompt helpers (mirrors transform.py — tone-aware version)
# ─────────────────────────────────────────────────────────────────────────────

_TONE_ADDENDUM: dict[str, str] = {
    "professional": "Use formal, polished business language.",
    "friendly":     "Use a warm, approachable, and conversational tone.",
    "confident":    "Use assertive, direct language. Avoid hedging.",
    "casual":       "Use relaxed, informal everyday language.",
    "neutral":      "",
}

_ACTION_PROMPTS: dict[str, str] = {
    "translate":     "You are a professional translator. Translate the provided text into {language}.",
    "improve":       "You are an expert editor. Improve the clarity, flow, and quality of the text while keeping the original meaning.",
    "shorten":       "You are a concise editor. Shorten the text while preserving all key information.",
    "expand":        "You are a skilled writer. Expand the text with more detail, examples, and context.",
    "professional":  "You are a business writing specialist. Rewrite the text in a professional, formal tone.",
    "casual":        "You are a friendly copywriter. Rewrite the text in a casual, conversational tone.",
    "persuasive":    "You are a persuasion expert. Rewrite the text to be more compelling and motivating.",
    "summarise":     "You are a precise summariser. Summarise the text into a short paragraph.",
    "rephrase":      "You are a paraphrasing expert. Rephrase the text using different words while keeping the exact same meaning.",
    "fix_grammar":   "You are a grammar editor. Fix all grammar, spelling, and punctuation errors. Do not change meaning or style.",
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
#  POST /api/writing/rewrite  — primary endpoint Lovable's frontend calls
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/writing/rewrite", response_model=WritingRewriteResponse)
async def writing_rewrite(
    req: WritingRewriteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI rewrite — the core Writing Engine endpoint used by the Lovable dashboard."""
    _require_writing(current_user)

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

    output_text  = chat_res.choices[0].message.content.strip()
    tokens_in    = chat_res.usage.prompt_tokens     if chat_res.usage else 0
    tokens_out   = chat_res.usage.completion_tokens if chat_res.usage else 0

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
#  PATCH /api/auth/writing-hotkey  — dedicated hotkey update
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
#  GET /api/writing/usage?range=7d|30d
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/writing/usage", response_model=WritingUsageResponse)
async def writing_usage(
    range: str = Query(default="7d", pattern="^(7d|30d)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Usage stats for the Writing dashboard. range = '7d' or '30d'."""
    days = 7 if range == "7d" else 30
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_naive = since.replace(tzinfo=None)

    # All rows in the window for this user
    rows_q = await db.execute(
        select(WritingAction)
        .where(
            WritingAction.user_id == current_user.id,
            WritingAction.success == True,
            WritingAction.created_at >= since_naive,
        )
        .order_by(WritingAction.created_at)
    )
    rows = rows_q.scalars().all()

    total_rewrites = len(rows)

    # Chars saved estimate: (chars_in - chars_out) for shorten/summarise,
    # chars_out for everything else (chars produced)
    chars_saved = sum(
        max(r.chars_in - r.chars_out, 0)
        if r.action in ("shorten", "summarise")
        else r.chars_out
        for r in rows
    )

    # Action breakdown
    by_action: dict[str, int] = defaultdict(int)
    for r in rows:
        by_action[r.action] += 1

    top_action = max(by_action, key=by_action.get) if by_action else None

    # Daily counts — fill gaps with zeros
    daily_map: dict[str, int] = defaultdict(int)
    for r in rows:
        day_str = r.created_at.strftime("%Y-%m-%d")
        daily_map[day_str] += 1

    daily = []
    for i in range(days):
        d = (datetime.now(timezone.utc) - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        daily.append(DailyWritingCount(date=d, count=daily_map.get(d, 0)))

    return WritingUsageResponse(
        total_rewrites=total_rewrites,
        chars_saved_estimate=chars_saved,
        top_action=top_action,
        by_action=dict(by_action),
        daily=daily,
    )
