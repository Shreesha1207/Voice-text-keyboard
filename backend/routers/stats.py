from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, extract
from datetime import datetime, date, timedelta, timezone
import uuid
import calendar

from database import get_db
from models import User, Session, WordRecord
from schemas import (
    RecordWordsRequest, StatsSummaryResponse, LeaderboardResponse, LeaderboardEntry, StartSessionResponse
)
from dependencies import get_current_user
from routers.achievements import check_and_grant_achievements

router = APIRouter(prefix="/api/stats", tags=["Stats"])

@router.post("/session/start", response_model=StartSessionResponse)
async def start_session(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Create a new dictation session"""
    session = Session(user_id=current_user.id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return StartSessionResponse(session_id=session.id)

async def internal_record_stats(user: User, db: AsyncSession, data: RecordWordsRequest) -> list[str]:
    """Internal helper to save word count and update user stats."""
    new_unlocks: list[str] = []

    # 1. Update the session if one is active
    if data.session_id:
        stmt = select(Session).where(Session.id == data.session_id, Session.user_id == user.id)
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
        user_id=user.id,
        session_id=data.session_id,
        word_count=data.word_count,
        char_count=data.char_count,
        wpm=data.wpm
    )
    db.add(record)

    # 3. Update User's total stats
    user.total_words += data.word_count
    
    # Update Streak Logic
    today = datetime.now(timezone.utc).date()
    if user.last_active_date:
        delta = today - user.last_active_date
        if delta.days == 1:
            user.streak_days += 1
        elif delta.days > 1:
            user.streak_days = 1
    else:
        user.streak_days = 1
        
    user.last_active_date = today
    if user.streak_days > user.longest_streak:
        user.longest_streak = user.streak_days

    new_unlocks = await check_and_grant_achievements(user, db)
    await db.commit()
    return new_unlocks

@router.post("/record")
async def record_words(
    data: RecordWordsRequest, 
    current_user: User = Depends(get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    """Save word count from a transcription event."""
    new_unlocks = await internal_record_stats(current_user, db, data)
    return {
        "status": "ok",
        "recorded_words": data.word_count,
        "new_achievements": new_unlocks,
    }

@router.get("/summary", response_model=StatsSummaryResponse)
async def get_summary(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get the user's dashboard stats summary"""
    today = datetime.now(timezone.utc).date()
    start_of_today = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).replace(tzinfo=None)
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

    # Most productive weekday by cumulative words over all recorded history
    # Using SQL aggregation for real-time performance
    stmt_best_day = (
        select(
            extract('dow', WordRecord.recorded_at).label('day'),
            func.sum(WordRecord.word_count).label('total')
        )
        .where(WordRecord.user_id == current_user.id)
        .group_by('day')
        .order_by(desc('total'))
        .limit(1)
    )
    res_best_day = await db.execute(stmt_best_day)
    best_day_row = res_best_day.first()
    
    weekday_name = None
    if best_day_row:
        # PostgreSQL 'dow' is 0 for Sunday to 6 for Saturday
        # Python calendar.day_name starts with Monday at 0
        dow_index = int(best_day_row[0])
        # Convert Sunday (0) -> (6) and others shift left by 1 to match calendar.day_name
        python_day_idx = (dow_index - 1) % 7
        weekday_name = calendar.day_name[python_day_idx]

    return StatsSummaryResponse(
        total_words=current_user.total_words,
        words_today=words_today,
        words_this_week=words_week,
        streak_days=current_user.streak_days,
        longest_streak=current_user.longest_streak,
        peak_wpm=peak_wpm,
        total_sessions=total_sessions,
        avg_words_per_session=round(avg_words, 1),
        most_productive_day=weekday_name
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
