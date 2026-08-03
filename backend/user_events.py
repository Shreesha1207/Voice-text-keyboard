"""Server → desktop push channel.

The desktop app used to discover setting changes by asking for them on a timer.
That is the wrong shape: it costs a request every few seconds forever to deliver
a change that happens a handful of times in a user's life, and it still leaves
the app showing stale values in between. Instead, whatever mutates a setting
publishes it here, and every connected desktop app is told immediately.

Redis pub/sub is the transport, so this works across however many API instances
Railway is running — the process holding the user's stream is usually not the
process handling their PATCH.
"""
from __future__ import annotations

import json
import logging

from queue_manager import queue_manager

logger = logging.getLogger(__name__)

# Event names, so producer and consumer cannot drift apart on a typo.
EVENT_PREFERENCES_UPDATED = "preferences_updated"
EVENT_ENTITLEMENTS_UPDATED = "entitlements_updated"


def user_channel(user_id) -> str:
    return f"user_events:{user_id}"


async def publish_user_event(user_id, event_type: str, payload: dict) -> None:
    """Push an event to every desktop app signed in as this user.

    Never raises. A settings save must still succeed when Redis is unreachable —
    the desktop simply picks the change up when its stream next reconnects.
    """
    try:
        await queue_manager.redis.publish(
            user_channel(user_id),
            json.dumps({"type": event_type, "data": payload}, default=str),
        )
    except Exception as e:
        logger.warning(f"Could not publish {event_type} for {user_id}: {e}")
