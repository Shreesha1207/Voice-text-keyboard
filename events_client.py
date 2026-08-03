"""Live settings channel: the server tells the app, the app never asks.

The desktop used to discover setting changes by re-fetching them on a timer.
That is backwards — it costs a request every few seconds forever to carry a
change that happens a handful of times, and between ticks the app is showing
values it knows may be stale. Worse, a language picked on the website simply did
not arrive until the app was restarted, so saving it looked like it did nothing.

This holds one long-lived HTTP response open instead. No request is made while
it is connected; the server writes to it when something actually changes. On
reconnect a single catch-up read covers anything missed while offline, which is
the only fetch this module ever performs.
"""
from __future__ import annotations

import json
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

# Backoff between reconnection attempts. Starts quick so a blip is invisible,
# then backs well off — a server that is down stays down for minutes, not
# milliseconds, and a tight retry loop would be the very polling this replaces.
RECONNECT_MIN_SECONDS = 2
RECONNECT_MAX_SECONDS = 120

# Read timeout on the stream. Must exceed the server's heartbeat interval, or a
# perfectly healthy idle connection is torn down between pings. The server sends
# one every 20s; this allows two to go missing before we treat it as dead.
STREAM_READ_TIMEOUT = 65
CONNECT_TIMEOUT = 15


class EventStream:
    """Background SSE listener. start() once; it manages itself from there."""

    def __init__(self, base_url, token_provider, on_event, on_reconnect=None,
                 on_unauthorized=None):
        self.base_url = base_url.rstrip("/")
        self.token_provider = token_provider
        self.on_event = on_event
        self.on_reconnect = on_reconnect
        self.on_unauthorized = on_unauthorized
        self._thread = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="XvoiceEventStream", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._running = False

    # ── internals ────────────────────────────────────────────────────────────
    def _run(self):
        delay = RECONNECT_MIN_SECONDS
        while self._running:
            try:
                if self._listen_once():
                    # Connected and served for a while, so the next drop is a
                    # fresh incident rather than a continuing failure.
                    delay = RECONNECT_MIN_SECONDS
            except Exception as e:
                logger.debug(f"Event stream ended: {e}")

            if not self._running:
                break
            time.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_SECONDS)

    def _listen_once(self) -> bool:
        token = self.token_provider()
        if not token:
            return False

        connected = False
        with requests.get(
            f"{self.base_url}/events/stream",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
            },
            stream=True,
            # No total timeout: the whole point is to stay open. The read timeout
            # is what detects a dead connection.
            timeout=(CONNECT_TIMEOUT, STREAM_READ_TIMEOUT),
        ) as r:
            if r.status_code == 401:
                logger.info("Event stream unauthorized; refreshing the session.")
                if self.on_unauthorized:
                    self.on_unauthorized()
                return False
            if r.status_code != 200:
                logger.debug(f"Event stream refused: HTTP {r.status_code}")
                return False

            logger.info("Live settings channel connected.")
            connected = True
            if self.on_reconnect:
                # One read to catch up on anything that changed while we were
                # disconnected. This is the only fetch the module makes.
                try:
                    self.on_reconnect()
                except Exception as e:
                    logger.error(f"Catch-up after reconnect failed: {e}")

            for raw in r.iter_lines(decode_unicode=True):
                if not self._running:
                    break
                if not raw:
                    continue          # blank line = end of an SSE frame
                if raw.startswith(":"):
                    continue          # comment / heartbeat
                if not raw.startswith("data:"):
                    continue
                body = raw[len("data:"):].strip()
                if not body:
                    continue
                try:
                    event = json.loads(body)
                except ValueError:
                    logger.debug(f"Ignoring unparseable event: {body[:120]}")
                    continue
                try:
                    self.on_event(event.get("type"), event.get("data") or {})
                except Exception as e:
                    logger.error(f"Handling event {event.get('type')!r} failed: {e}")

        logger.info("Live settings channel closed.")
        return connected
