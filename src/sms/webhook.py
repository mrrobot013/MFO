"""SMS provider that receives messages from a phone HTTP forwarder."""
from __future__ import annotations

import asyncio
import queue
from datetime import datetime
from typing import AsyncIterator

from loguru import logger

from config import settings
from src.sms.base import IncomingSMS, RentedNumber, SmsProvider
from src.webhook_server import get_webhook_receiver


class WebhookSmsProvider(SmsProvider):
    def __init__(self, phone: str, host: str, port: int, token: str = "") -> None:
        self.phone = phone.strip() or "+7XXXXXXXXXX"
        self.receiver = get_webhook_receiver(host, port, token)

    async def rent_number(self) -> RentedNumber:
        logger.info(f"Webhook SMS: использую номер телефона {self.phone}")
        logger.info(f"  POST SMS → {self.receiver.public_base_url}/sms")
        return RentedNumber(phone=self.phone, rent_id="phone-webhook")

    async def stream_sms(
        self,
        rent: RentedNumber,
        **kwargs,
    ) -> AsyncIterator[IncomingSMS]:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + settings.sms_listen_minutes * 60
        logger.info(
            f"Webhook SMS: слушаю входящие SMS {settings.sms_listen_minutes} мин"
        )
        while loop.time() < deadline:
            timeout = min(1.0, max(0.1, deadline - loop.time()))
            try:
                item = await loop.run_in_executor(
                    None,
                    lambda: self.receiver.sms_queue.get(timeout=timeout),
                )
            except queue.Empty:
                continue
            yield IncomingSMS(
                sender=str(item.get("sender") or "unknown"),
                text=str(item.get("text") or ""),
                received_at=item.get("received_at")
                if isinstance(item.get("received_at"), datetime)
                else datetime.now(),
                raw=item.get("raw") if isinstance(item.get("raw"), dict) else item,
            )

    async def release(self, rent: RentedNumber) -> None:
        logger.info("Webhook SMS: завершил прослушивание")
        self.receiver.stop()
