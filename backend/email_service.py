import os
import httpx
import logging

logger = logging.getLogger(__name__)

SUPABASE_EMAIL_URL = os.getenv(
    "SUPABASE_EMAIL_URL",
    "https://frirlcfbpqsltacwnrwj.supabase.co/functions/v1/send-transactional-email"
)
EMAIL_WEBHOOK_SECRET = os.getenv("EMAIL_WEBHOOK_SECRET", "")


def _call_edge_function(payload: dict) -> bool:
    """POST to the Supabase send-transactional-email edge function."""
    if not EMAIL_WEBHOOK_SECRET:
        logger.warning("EMAIL_WEBHOOK_SECRET not set — logging email instead of sending.")
        logger.info(f"[DEBUG EMAIL] payload={payload}")
        return True

    try:
        response = httpx.post(
            SUPABASE_EMAIL_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Email-Secret": EMAIL_WEBHOOK_SECRET,
            },
            timeout=10.0,
        )
        if response.status_code == 200:
            logger.info(f"Email sent via edge function: type={payload.get('type')} to={payload.get('to')}")
            return True
        else:
            logger.error(f"Edge function returned {response.status_code}: {response.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"Failed to call email edge function: {e}")
        return False


def send_welcome_email(to_email: str, display_name: str = None) -> bool:
    return _call_edge_function({
        "type": "welcome",
        "to": to_email,
        "name": display_name or "there",
    })


def send_trial_expired_email(to_email: str, display_name: str = None) -> bool:
    return _call_edge_function({
        "type": "trial_expired",
        "to": to_email,
        "name": display_name or "there",
    })


def send_password_reset_email(to_email: str, reset_link: str, display_name: str = None) -> bool:
    return _call_edge_function({
        "type": "reset_password",
        "to": to_email,
        "name": display_name or "there",
        "reset_link": reset_link,
    })
