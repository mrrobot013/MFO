"""Call provider that receives forwarded phone call events over HTTP."""
from __future__ import annotations

import asyncio
import queue
from datetime import datetime
from typing import AsyncIterator

from loguru import logger

from config import settings
from src.calls.base import CallProvider, IncomingCall
from src.webhook_server import get_webhook_receiver


class WebhookCallProvider(CallProvider):
    def __init__(self, host: str, port: int, token: str = "") -> None:
        self.receiver = get_webhook_receiver(host, port, token)

    async def stream_calls(self) -> AsyncIterator[IncomingCall]:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + settings.sms_listen_minutes * 60
        logger.info(f"Webhook CALL: POST звонки → {self.receiver.public_base_url}/call")
        while loop.time() < deadline:
            timeout = min(1.0, max(0.1, deadline - loop.time()))
            try:
                item = await loop.run_in_executor(
                    None,
                    lambda: self.receiver.call_queue.get(timeout=timeout),
                )
            except queue.Empty:
                continue
            yield IncomingCall(
                caller=str(item.get("caller") or "unknown"),
                started_at=item.get("started_at")
                if isinstance(item.get("started_at"), datetime)
                else datetime.now(),
                duration_sec=int(item.get("duration_sec") or 0),
                recording_url=item.get("recording_url"),
                status=str(item.get("status") or ""),
                direction=str(item.get("direction") or "incoming"),
                summary=str(item.get("summary") or ""),
                raw=item.get("raw") if isinstance(item.get("raw"), dict) else item,
            )

    async def close(self) -> None:
        self.receiver.stop()
