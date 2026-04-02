from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from database import get_db
from models import User, Achievement, UserAchievement
from schemas import AchievementsResponse, AchievementOut
from dependencies import get_current_user

router = APIRouter(prefix="/api/achievements", tags=["Achievements"])

@router.get("", response_model=list[AchievementOut])
async def get_my_achievements(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Fetch all achievements and the current user's unlock statuses."""
    
    # Needs a seeded db of achievements (first seed happens in main.py startup event later)
    stmt_all = select(Achievement)
    res_all = await db.execute(stmt_all)
    all_achievements = res_all.scalars().all()
    
    stmt_unlocked = select(UserAchievement).where(UserAchievement.user_id == current_user.id)
    res_unlocked = await db.execute(stmt_unlocked)
    user_achievements = {ua.achievement_slug: ua.unlocked_at for ua in res_unlocked.scalars()}
    
    response_achievements = []
    
    for a in all_achievements:
        unlocked = a.slug in user_achievements
        unlocked_at = user_achievements.get(a.slug)
        
        # Calculate progress purely mathematically
        progress = 1.0 if unlocked else 0.0
        
        if not unlocked:
             if a.trigger_type == "total_words":
                  progress = min(current_user.total_words / int(a.trigger_value), 1.0)
             elif a.trigger_type == "streak":
                  progress = min(current_user.streak_days / int(a.trigger_value), 1.0)
                  
        response_achievements.append(AchievementOut(
             slug=a.slug,
             name=a.name,
             description=a.description,
             icon=a.icon,
             unlocked=unlocked,
             unlocked_at=unlocked_at,
             progress=round(progress, 2)
        ))
        
    return response_achievements


async def check_and_grant_achievements(user: User, db: AsyncSession) -> list[str]:
    """
    Utility function called after stats are recorded internally.
    Returns list of newly unlocked achievement slugs.
    """
    
    stmt_all = select(Achievement)
    res_all = await db.execute(stmt_all)
    all_achievements = res_all.scalars().all()
    
    stmt_unlocked = select(UserAchievement.achievement_slug).where(UserAchievement.user_id == user.id)
    res_unlocked = await db.execute(stmt_unlocked)
    already_unlocked = set(res_unlocked.scalars().all())
    
    new_unlocks = []
    
    for a in all_achievements:
         if a.slug in already_unlocked:
              continue
              
         should_unlock = False
         
         if a.trigger_type == "total_words":
              if user.total_words >= int(a.trigger_value):
                   should_unlock = True
         elif a.trigger_type == "streak":
              if user.streak_days >= int(a.trigger_value):
                   should_unlock = True
                   
         if should_unlock:
              new_ug = UserAchievement(
                   user_id=user.id,
                   achievement_slug=a.slug,
                   unlocked_at=datetime.utcnow()
              )
              db.add(new_ug)
              new_unlocks.append(a.slug)
              
    if new_unlocks:
         await db.commit()
         
    return new_unlocks
