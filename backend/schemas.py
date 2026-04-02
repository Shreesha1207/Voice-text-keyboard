import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from models import SubscriptionStatus


# ─── Auth ────────────────────────────────────────────────────────────────────

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthCode(BaseModel):
    code: str
    redirect_uri: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class ValidateResponse(BaseModel):
    allowed: bool
    reason: str
    tier: str
    trial_days_remaining: Optional[int] = None
    user_id: str


# ─── User ─────────────────────────────────────────────────────────────────────

class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: Optional[str]
    created_at: datetime
    trial_start_at: datetime
    subscription_status: SubscriptionStatus
    total_words: int
    streak_days: int
    longest_streak: int
    is_leaderboard_opt_in: bool
    tier: str

    model_config = {"from_attributes": True}


# ─── Stats ────────────────────────────────────────────────────────────────────

class RecordWordsRequest(BaseModel):
    word_count: int = Field(gt=0)
    char_count: int = Field(default=0, ge=0)
    wpm: Optional[float] = None
    session_id: Optional[uuid.UUID] = None
    audio_duration_seconds: Optional[float] = None


class StatsSummaryResponse(BaseModel):
    total_words: int
    words_today: int
    words_this_week: int
    streak_days: int
    longest_streak: int
    peak_wpm: Optional[float]
    total_sessions: int
    avg_words_per_session: float
    most_productive_day: Optional[str]


class LeaderboardEntry(BaseModel):
    rank: int
    display_name: str
    total_words: int
    streak_days: int


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]
    user_rank: Optional[int]


# ─── Achievements ─────────────────────────────────────────────────────────────

class AchievementOut(BaseModel):
    slug: str
    name: str
    description: str
    icon: str
    unlocked: bool
    unlocked_at: Optional[datetime] = None
    progress: Optional[float] = None  # 0.0 to 1.0

    model_config = {"from_attributes": True}


class AchievementsResponse(BaseModel):
    achievements: list[AchievementOut]
    newly_unlocked: list[str]  # slugs of newly unlocked achievements


# ─── Transcription ────────────────────────────────────────────────────────────

class TranscribeResponse(BaseModel):
    text: str
    word_count: int
    char_count: int
    wpm: Optional[float] = None
    queue_wait_ms: int


# ─── Billing ──────────────────────────────────────────────────────────────────

class BillingStatusResponse(BaseModel):
    status: SubscriptionStatus
    trial_days_remaining: Optional[int]
    next_billing_date: Optional[datetime]
    plan: str


class UpgradeRequest(BaseModel):
    plan: str = "monthly"  # monthly | annual | lifetime


# ─── Session ──────────────────────────────────────────────────────────────────

class StartSessionResponse(BaseModel):
    session_id: uuid.UUID


class SessionHistoryEntry(BaseModel):
    id: uuid.UUID
    started_at: datetime
    ended_at: Optional[datetime]
    word_count: int
    peak_wpm: Optional[float]

    model_config = {"from_attributes": True}
