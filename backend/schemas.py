import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, AliasChoices
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
    custom_hotkey: str = "f8"
    preferred_language: str = "en"
    is_translation_enabled: bool = False
    plan_product: str = "dictation"
    writing_enabled: bool = False
    dictation_enabled: bool = True


class HotkeyUpdate(BaseModel):
    # The web app sends {"custom_hotkey": ...} while this required {"hotkey": ...},
    # so every save from the dashboard failed validation with a 422 and the hotkey
    # silently never changed. Accept either spelling — the frontend deploys
    # separately, so the backend meeting it halfway is what actually fixes this for
    # users already on the current web build.
    hotkey: str = Field(
        ..., max_length=20,
        validation_alias=AliasChoices("hotkey", "custom_hotkey"),
    )

    model_config = {"populate_by_name": True}


class LeaderboardOptInUpdate(BaseModel):
    opt_in: bool


class LanguageUpdate(BaseModel):
    language: str = Field(..., max_length=10)


class TranslationUpdate(BaseModel):
    enabled: bool


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


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
    timezone: str = "UTC"
    custom_hotkey: str = "f8"
    preferred_language: str = "en"
    is_translation_enabled: bool = False
    # ── Writing / Platform entitlements ────────────────────────────────
    plan_product: str = "dictation"        # dictation | writing | platform
    writing_enabled: bool = False          # True when user may use Writing Engine
    dictation_enabled: bool = True         # True when user may use Dictation

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


class DailyStat(BaseModel):
    date: str       # ISO date string e.g. "2026-04-01"
    words: int


class DailyStatsResponse(BaseModel):
    days: list[DailyStat]
    best_day: Optional[DailyStat] = None


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


# ─── Writing Engine ────────────────────────────────────────────────────────────

class WritingValidateResponse(BaseModel):
    """Returned by GET /api/writing/validate — mirrors the Dictation validate shape."""
    allowed: bool
    reason: str              # 'paid' | 'trial_active' | 'trial_expired' | 'quota_exceeded'
    plan_product: str        # 'dictation' | 'writing' | 'platform'
    writing_quota: int       # monthly action limit (0 = unlimited)
    writing_used: int        # actions used this month
    user_id: str


class WritingActionHistoryEntry(BaseModel):
    id: uuid.UUID
    action: str
    input_snippet: str       # first 120 chars of input
    output_snippet: Optional[str]  # first 120 chars of output
    language: Optional[str]
    success: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class WritingStatsResponse(BaseModel):
    actions_this_month: int
    quota: int               # 0 = unlimited
    quota_resets_at: Optional[datetime]
    total_actions_all_time: int
    most_used_action: Optional[str]
    chars_processed_all_time: int


# ─── Writing Rewrite (Lovable frontend shape) ─────────────────────────────────

class WritingRewriteRequest(BaseModel):
    action: str                            # improve | professional | shorten …
    text: str = Field(..., max_length=8000)
    tone: Optional[str] = "neutral"        # neutral | professional | friendly | confident | casual
    language: Optional[str] = "en"        # ISO code; only used for translate


class WritingRewriteResponse(BaseModel):
    id: uuid.UUID
    action: str
    output: str
    tokens_in: int
    tokens_out: int


class WritingRecordRequest(BaseModel):
    action_key: Optional[str] = None
    action: Optional[str] = None
    char_count: int = 0


# ─── Writing Usage (Lovable dashboard shape) ──────────────────────────────────

class DailyWritingCount(BaseModel):
    date: str    # ISO date e.g. "2026-07-14"
    count: int


class WritingUsageResponse(BaseModel):
    total_rewrites: int
    chars_saved_estimate: int
    top_action: Optional[str]
    by_action: dict[str, int]
    daily: list[DailyWritingCount]


# ─── Writing Preferences ──────────────────────────────────────────────────────

class WritingPreferencesOut(BaseModel):
    default_action: str = "improve"
    default_tone: str = "neutral"
    default_language: str = "en"
    auto_replace: bool = False
    show_preview: bool = True
    custom_hotkey: str = "right_click"
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class WritingPreferencesUpdate(BaseModel):
    default_action: Optional[str] = None
    default_tone: Optional[str] = None
    default_language: Optional[str] = None
    auto_replace: Optional[bool] = None
    show_preview: Optional[bool] = None
    custom_hotkey: Optional[str] = None


class WritingHotkeyUpdate(BaseModel):
    hotkey: str = Field(..., max_length=30)
