import uuid
from datetime import datetime, date
from sqlalchemy import String, DateTime, Boolean, Integer, Float, ForeignKey, Date, Text, UniqueConstraint, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from database import Base
import enum


class SubscriptionStatus(str, enum.Enum):
    TRIAL = "trial"
    PAID = "paid"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    trial_start_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    subscription_status: Mapped[SubscriptionStatus] = mapped_column(
        SAEnum(SubscriptionStatus), default=SubscriptionStatus.TRIAL
    )
    is_leaderboard_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    total_words: Mapped[int] = mapped_column(Integer, default=0)
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    longest_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_active_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", server_default="UTC")
    
    # Stripe Billing fields
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    trial_expired_email_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    custom_hotkey: Mapped[str] = mapped_column(String(20), default="f8", server_default="'f8'")
    preferred_language: Mapped[str] = mapped_column(String(10), default="en", server_default="'en'")
    is_translation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # ── Writing Engine ─────────────────────────────────────────────────────────
    # Which Xvoice product(s) the user has paid for.
    # Values: 'dictation' | 'writing' | 'platform'
    plan_product: Mapped[str] = mapped_column(String(20), default="dictation", server_default="'dictation'")
    # Monthly writing action counter (resets each calendar month)
    writing_actions_this_month: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Timestamp of the last quota reset — used to detect when a new month has started
    writing_quota_reset_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Writing-specific free trial (separate from dictation trial)
    # NULL = never started; set to utcnow() when POST /writing/trial/start is called
    writing_trial_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Daily writing action counter — resets at midnight UTC
    writing_actions_today: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    writing_today_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # the date the counter applies to

    # Password reset fields
    password_reset_token: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    password_reset_expires: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    sessions: Mapped[list["Session"]] = relationship("Session", back_populates="user")
    word_records: Mapped[list["WordRecord"]] = relationship("WordRecord", back_populates="user")
    unlocked_achievements: Mapped[list["UserAchievement"]] = relationship("UserAchievement", back_populates="user")
    writing_actions: Mapped[list["WritingAction"]] = relationship("WritingAction", back_populates="user")
    writing_preferences: Mapped["WritingPreferences | None"] = relationship("WritingPreferences", back_populates="user", uselist=False)

    @property
    def is_trial_expired(self) -> bool:
        from datetime import timezone
        delta = datetime.now(timezone.utc) - self.trial_start_at.replace(tzinfo=timezone.utc)
        return delta.days >= 14

    @property
    def subscription_is_active(self) -> bool:
        """True while the user is paying, including the run-out after cancelling."""
        if self.subscription_status == SubscriptionStatus.PAID:
            return True
        if self.subscription_status == SubscriptionStatus.CANCELED:
            # Cancelled but still inside the paid period — access continues.
            return bool(self.current_period_end and self.current_period_end > datetime.utcnow())
        return False

    @property
    def tier(self) -> str:
        """Paid **dictation** access.

        This gates the dictation premium features — custom hotkey, transcription
        language, live translation — and the priority transcription queue.

        It previously looked only at subscription_status, ignoring which product was
        bought. Because any active subscription sets that field, buying Writing
        alone unlocked the whole of Dictation Pro for free. The two products are
        strictly siloed everywhere else (the web app grants across products only for
        'platform'), so the entitlement must be product-aware here too.

        plan_product defaults to 'dictation', so customers who subscribed before the
        column existed keep their access.
        """
        if self.subscription_is_active and self.plan_product in ("dictation", "platform"):
            return "paid"
        return "trial"

    @property
    def writing_is_paid(self) -> bool:
        """Paid **writing** access — the mirror of `tier` for the other product.

        The writing gates used to test plan_product on its own, with no check that
        the subscription was still live. plan_product records what someone bought
        and is never cleared when a subscription lapses, so anyone who had ever
        held a Writing or Platform plan kept unlimited Writing forever: cancelling
        correctly removed their Dictation premium, which does check, but left
        Writing untouched.

        Deliberately excludes the free writing trial — callers that should honour
        the trial OR it with this in their own condition.
        """
        return self.subscription_is_active and self.plan_product in ("writing", "platform")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    peak_wpm: Mapped[float | None] = mapped_column(Float, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="sessions")
    word_records: Mapped[list["WordRecord"]] = relationship("WordRecord", back_populates="session")


class WordRecord(Base):
    __tablename__ = "word_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    wpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="word_records")
    session: Mapped["Session | None"] = relationship("Session", back_populates="word_records")


class Achievement(Base):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    icon: Mapped[str] = mapped_column(String(10), nullable=False)  # emoji
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)
    trigger_value: Mapped[str] = mapped_column(String(100), nullable=False)

    user_achievements: Mapped[list["UserAchievement"]] = relationship("UserAchievement", back_populates="achievement")


class UserAchievement(Base):
    __tablename__ = "user_achievements"
    # Two concurrent transcriptions could both observe an achievement as un-unlocked
    # and both insert it, giving the user duplicate badges.
    __table_args__ = (
        UniqueConstraint("user_id", "achievement_slug", name="uq_user_achievement"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    achievement_slug: Mapped[str] = mapped_column(String(100), ForeignKey("achievements.slug"), nullable=False)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="unlocked_achievements")
    achievement: Mapped["Achievement"] = relationship("Achievement", back_populates="user_achievements")


class WritingAction(Base):
    """One row per AI writing action performed by a user.
    Powers the Writing dashboard history view, usage metering, and analytics."""
    __tablename__ = "writing_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Action key, e.g. 'translate', 'improve', 'shorten', 'fix_grammar' …
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL when the action failed
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Populated for translate; NULL for tone/style actions
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    chars_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chars_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user: Mapped["User"] = relationship("User", back_populates="writing_actions")

    # ── extended fields for Lovable dashboard ──────────────────────────────────
    tone: Mapped[str | None] = mapped_column(String(50), nullable=True)   # neutral/professional/friendly/confident/casual
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class WritingPreferences(Base):
    """One row per user — stores their Writing Engine default settings.
    Created with defaults on first GET; updated via PATCH."""
    __tablename__ = "writing_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True, nullable=False
    )
    # Default action shown pre-selected in the action menu
    default_action: Mapped[str] = mapped_column(String(50), default="improve", server_default="'improve'")
    # Default tone applied when not translate
    default_tone: Mapped[str] = mapped_column(String(50), default="neutral", server_default="'neutral'")
    # Default target language for translate action
    default_language: Mapped[str] = mapped_column(String(20), default="en", server_default="'en'")
    # When True: replace selection immediately; when False: show VS Code-style preview
    auto_replace: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Show the inline diff preview widget
    show_preview: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Separate hotkey from dictation (stored for desktop app sync; actual trigger is right-click)
    custom_hotkey: Mapped[str] = mapped_column(String(30), default="right_click", server_default="'right_click'")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="writing_preferences")


class AuditLog(Base):
    """Security and billing event trail.

    Deliberately append-only and independent of the user row: a failed login for
    an address that does not exist still needs recording, so user_id is nullable
    and the raw email is kept alongside it.
    """
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable: the event may concern an address with no matching account.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
