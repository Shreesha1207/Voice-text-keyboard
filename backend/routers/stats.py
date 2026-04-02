from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from datetime import datetime, date, timedelta, timezone
import uuid

from database import get_db
from models import User, Session, WordRecord
from schemas import (
    RecordWordsRequest, StatsSummaryResponse, LeaderboardResponse, LeaderboardEntry, StartSessionResponse
)
from dependencies import get_current_user

router = APIRouter(prefix="/api/stats", tags=["Stats"])

@router.post("/session/start", response_model=StartSessionResponse)
async def start_session(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Create a new dictation session"""
    session = Session(user_id=current_user.id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return StartSessionResponse(session_id=session.id)

@router.post("/record")
async def record_words(
    data: RecordWordsRequest, 
    current_user: User = Depends(get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    """Save word count from a transcription event."""
    
    # 1. Update the session if one is active
    if data.session_id:
        stmt = select(Session).where(Session.id == data.session_id, Session.user_id == current_user.id)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()
        if session:
            session.word_count += data.word_count
            session.char_count += data.char_count
            if data.wpm:
                if session.peak_wpm is None or data.wpm > session.peak_wpm:
                     session.peak_wpm = data.wpm
            session.ended_at = datetime.utcnow()

    # 2. Add an explicit WordRecord (for history/achievements)
    record = WordRecord(
        user_id=current_user.id,
        session_id=data.session_id,
        word_count=data.word_count,
        char_count=data.char_count,
        wpm=data.wpm
    )
    db.add(record)

    # 3. Update User's total stats
    current_user.total_words += data.word_count
    
    # Update Streak Logic
    today = datetime.now(timezone.utc).date()
    # Check if last_active_date was yesterday
    if current_user.last_active_date:
        delta = today - current_user.last_active_date
        if delta.days == 1:
            current_user.streak_days += 1
        elif delta.days > 1:
            current_user.streak_days = 1 # Reset streak if missed a day
        # If it's 0 it means same day, don't change streak_days yet unless it's the very first time.
    else:
        current_user.streak_days = 1 # First activity ever
        
    current_user.last_active_date = today
    if current_user.streak_days > current_user.longest_streak:
        current_user.longest_streak = current_user.streak_days

    await db.commit()
    
    # NOTE: Normally here we would call the achievement check logic asynchronously
    # (e.g. via Celery/arq) but we'll do it synchronously for simplicity in Phase 1 
    # or expose a specific endpoint to check them manually via the dashboard.
    
    return {"status": "ok", "recorded_words": data.word_count}

@router.get("/summary", response_model=StatsSummaryResponse)
async def get_summary(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get the user's dashboard stats summary"""
    today = datetime.now(timezone.utc).date()
    start_of_today = datetime.combine(today, datetime.min.time())
    start_of_week = start_of_today - timedelta(days=start_of_today.weekday())

    # Words Today
    stmt_today = select(func.sum(WordRecord.word_count)).where(
        WordRecord.user_id == current_user.id,
        WordRecord.recorded_at >= start_of_today
    )
    res_today = await db.execute(stmt_today)
    words_today = res_today.scalar_one_or_none() or 0

    # Words this week
    stmt_week = select(func.sum(WordRecord.word_count)).where(
        WordRecord.user_id == current_user.id,
        WordRecord.recorded_at >= start_of_week
    )
    res_week = await db.execute(stmt_week)
    words_week = res_week.scalar_one_or_none() or 0

    # Total sessions
    stmt_sessions = select(func.count(Session.id)).where(Session.user_id == current_user.id)
    res_sessions = await db.execute(stmt_sessions)
    total_sessions = res_sessions.scalar_one_or_none() or 0
    
    avg_words = current_user.total_words / total_sessions if total_sessions > 0 else 0

    # Peak WPM overall
    stmt_peak = select(func.max(Session.peak_wpm)).where(Session.user_id == current_user.id)
    res_peak = await db.execute(stmt_peak)
    peak_wpm = res_peak.scalar_one_or_none()

    return StatsSummaryResponse(
        total_words=current_user.total_words,
        words_today=words_today,
        words_this_week=words_week,
        streak_days=current_user.streak_days,
        longest_streak=current_user.longest_streak,
        peak_wpm=peak_wpm,
        total_sessions=total_sessions,
        avg_words_per_session=round(avg_words, 1),
        most_productive_day="Monday" # Mocked for simplicity
    )

@router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get top 100 users by total words"""
    
    stmt = (
        select(User)
        .where(User.is_leaderboard_opt_in == True)
        .order_by(desc(User.total_words))
        .limit(100)
    )
    result = await db.execute(stmt)
    top_users = result.scalars().all()
    
    entries = []
    user_rank = None
    
    for rank, u in enumerate(top_users, start=1):
        if u.id == current_user.id:
            user_rank = rank
        
        entries.append(LeaderboardEntry(
            rank=rank,
            display_name=u.display_name or f"User {str(u.id)[:4]}",
            total_words=u.total_words,
            streak_days=u.streak_days
        ))
        
    return LeaderboardResponse(
        entries=entries,
        user_rank=user_rank
    )
