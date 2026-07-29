"""
Security and billing audit trail.

Why this exists: before it, nothing recorded logins, password resets, logouts or
subscription changes. If an account were compromised, or a payment silently
failed to apply, there was no record to investigate with — and no way to notice
credential stuffing while it was happening.

Rows are written to the database rather than only to stderr, because platform
logs have short retention and are exactly what you need months later.

Every write is best-effort: an audit failure must never break the request that
triggered it.
"""

import logging
from typing import Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from models import AuditLog

logger = logging.getLogger(__name__)


# ─── Event names ──────────────────────────────────────────────────────────────
# Kept as constants so they stay greppable and consistent across call sites.

LOGIN_SUCCESS         = "login.success"
LOGIN_FAILED          = "login.failed"
REGISTER              = "user.register"
GOOGLE_LOGIN          = "login.google"
LOGOUT_ALL            = "logout.all_devices"
REFRESH_REJECTED      = "token.refresh_rejected"
PASSWORD_RESET_ASKED  = "password.reset_requested"
PASSWORD_RESET_DONE   = "password.reset_completed"
PASSWORD_RESET_FAILED = "password.reset_failed"
BILLING_SYNC_APPLIED  = "billing.sync_applied"
BILLING_SYNC_NO_USER  = "billing.sync_no_matching_user"
BILLING_WEBHOOK       = "billing.stripe_webhook"
SUBSCRIPTION_CHANGED  = "billing.subscription_changed"


async def record(
    db: AsyncSession,
    event: str,
    *,
    user_id=None,
    email: Optional[str] = None,
    request: Optional[Request] = None,
    detail: Optional[str] = None,
    commit: bool = False,
) -> None:
    """Write one audit row.

    Never raises. `commit=False` by default so the row joins the caller's
    transaction; pass commit=True from paths that would otherwise roll back
    (a failed login, for instance, where nothing else is being written).
    """
    try:
        ip = None
        user_agent = None
        if request is not None:
            # Local import avoids a circular dependency at module load.
            from rate_limit import client_ip
            ip = client_ip(request)
            user_agent = (request.headers.get("user-agent") or "")[:300] or None

        db.add(
            AuditLog(
                user_id=user_id,
                email=(email or "").strip().lower() or None,
                event=event,
                ip=ip,
                user_agent=user_agent,
                detail=detail[:500] if detail else None,
            )
        )
        if commit:
            await db.commit()
    except Exception as e:
        # An audit write must not take down the operation it was recording.
        logger.error(f"Failed to write audit log for event={event}: {e}")
