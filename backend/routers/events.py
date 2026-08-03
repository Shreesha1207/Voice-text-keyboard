"""Server-Sent Events stream the desktop app holds open.

One long-lived GET replaces the old polling loop entirely. The app makes no
periodic requests at all: it connects once and is told when something changes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from dependencies import get_current_user
from models import User
from queue_manager import queue_manager
from user_events import user_channel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Events"])

# A silent comment line every this often. Idle TCP connections are dropped by
# proxies and load balancers within a minute or two, and without traffic the
# client would sit holding a dead socket believing it is still subscribed.
HEARTBEAT_SECONDS = 20

# How long get_message waits before looping. Also bounds how quickly we notice
# the client has gone away.
POLL_TIMEOUT_SECONDS = 1.0


@router.get("/events/stream")
async def stream_user_events(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Hold a stream open and forward this user's events as they happen."""
    channel = user_channel(current_user.id)
    user_id = str(current_user.id)

    async def event_source():
        pubsub = queue_manager.redis.pubsub()
        try:
            await pubsub.subscribe(channel)
        except Exception as e:
            # Redis is down. Say so and close, rather than holding a stream that
            # can never deliver anything — the client will reconnect with backoff.
            logger.error(f"Could not subscribe {user_id} to {channel}: {e}")
            yield _sse_event("stream_error", {"reason": "unavailable"})
            return

        logger.info(f"Event stream opened for {user_id}")
        # Tell the client it is live. It uses this to run a single catch-up read
        # for anything it missed while disconnected.
        yield _sse_event("connected", {"heartbeat": HEARTBEAT_SECONDS})

        last_beat = time.monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=POLL_TIMEOUT_SECONDS,
                    )
                except Exception as e:
                    logger.warning(f"Event stream read failed for {user_id}: {e}")
                    break

                if msg and msg.get("type") == "message":
                    data = msg.get("data")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", "replace")
                    yield f"data: {data}\n\n"

                now = time.monotonic()
                if now - last_beat >= HEARTBEAT_SECONDS:
                    last_beat = now
                    # A comment line: valid SSE, ignored by the parser, and enough
                    # to keep every proxy in the path from reaping the connection.
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass
            logger.info(f"Event stream closed for {user_id}")

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Tells nginx-style proxies not to buffer, which would otherwise hold
            # events back until enough bytes accumulated and defeat the point.
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(event_type: str, payload: dict) -> str:
    return "data: " + json.dumps({"type": event_type, "data": payload}) + "\n\n"
