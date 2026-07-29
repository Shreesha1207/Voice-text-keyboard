"""
Redis-backed rate limiting.

Built on the Redis connection the queue already uses, so it needs no new
dependency and works correctly across multiple web replicas (an in-process
counter would reset on every deploy and be wrong the moment you scale out).

Design notes
------------
* Fixed window per (scope, identity). Simple, cheap, and good enough for the
  threats here: credential stuffing and runaway API cost.

* **Fails open.** If Redis is unreachable the request is allowed and a warning is
  logged. A rate limiter must never be the component that takes the API down.

* Two identities are used, because they defend against different things:
    - by IP    — broad, cheap, but spoofable via X-Forwarded-For, so it is a
                 speed bump rather than a wall.
    - by account/email — not spoofable, so this is the one that actually stops
                 someone grinding a single account's password.
  Login uses both.
"""

import logging
from typing import Optional

from fastapi import HTTPException, Request

from queue_manager import queue_manager

logger = logging.getLogger(__name__)


def client_ip(request: Request) -> str:
    """Best-effort client IP.

    Railway terminates TLS and forwards, so request.client.host is the proxy.
    X-Forwarded-For carries the original, but the left-most entry is supplied by
    the caller and can be forged — which is exactly why anything that matters is
    also limited by account identity, not just by IP.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def hit(scope: str, identity: str, limit: int, window_seconds: int) -> None:
    """Count one request against (scope, identity); raise 429 if over the limit.

    Raises HTTPException(429) with a Retry-After header when the caller has
    exceeded `limit` requests inside `window_seconds`.
    """
    key = f"ratelimit:{scope}:{identity}"
    try:
        redis = queue_manager.redis
        count = await redis.incr(key)
        if count == 1:
            # First hit in this window — start the clock.
            await redis.expire(key, window_seconds)
        if count > limit:
            ttl = await redis.ttl(key)
            retry_after = ttl if ttl and ttl > 0 else window_seconds
            logger.warning(
                f"Rate limit hit: scope={scope} identity={identity} "
                f"count={count} limit={limit}/{window_seconds}s"
            )
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait a moment and try again.",
                headers={"Retry-After": str(retry_after)},
            )
    except HTTPException:
        raise
    except Exception as e:
        # Redis down, misconfigured, etc. Allow the request through.
        logger.warning(f"Rate limiter unavailable ({e}); allowing request for {scope}.")


def limit_by_ip(scope: str, limit: int, window_seconds: int):
    """FastAPI dependency: limit a route by caller IP.

    Usage:  dependencies=[Depends(limit_by_ip("login", 10, 300))]
    """
    async def _dependency(request: Request) -> None:
        await hit(scope, client_ip(request), limit, window_seconds)

    return _dependency


async def limit_by_identity(
    scope: str,
    identity: Optional[str],
    limit: int,
    window_seconds: int,
) -> None:
    """Limit by account identity (user id or email), called from inside a handler
    once that identity is known. Unlike an IP this cannot be spoofed."""
    if not identity:
        return
    await hit(scope, str(identity).strip().lower(), limit, window_seconds)
