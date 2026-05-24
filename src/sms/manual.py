"""Ручной режим — для отладки и демонстрации без API.

Просим у пользователя ввести номер своего телефона и потом вручную
ввести пришедшие SMS в консоль. Удобно когда нет ключа API.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from typing import AsyncIterator

from loguru import logger

from src.sms.base import IncomingSMS, RentedNumber, SmsProvider


class ManualSmsProvider(SmsProvider):
    """Полу-автоматический режим: парсер использует реальный номер пользователя
    (например, его iPhone), а сами SMS пользователь вводит в консоль по мере
    получения. Это вынужденная мера — iOS не даёт сторонним программам читать
    SMS, поэтому полностью автоматизировать этот шаг невозможно.
    """

    def __init__(self, prefilled_phone: str = "") -> None:
        self.prefilled_phone = prefilled_phone.strip()

    async def rent_number(self) -> RentedNumber:
        if self.prefilled_phone:
            logger.info(f"Manual режим: номер → {self.prefilled_phone}")
            return RentedNumber(phone=self.prefilled_phone, rent_id="manual")
        loop = asyncio.get_event_loop()
        phone = await loop.run_in_executor(
            None,
            lambda: input("Введите свой номер в формате +7XXXXXXXXXX: ").strip(),
        )
        return RentedNumber(phone=phone, rent_id="manual")

    async def stream_sms(
        self,
        rent: RentedNumber,
        *,
        ready_event: asyncio.Event | None = None,
    ) -> AsyncIterator[IncomingSMS]:
        from config import settings
        from src.stdin_lock import STDIN_LOCK

        loop = asyncio.get_event_loop()
        if ready_event is not None:
            logger.info("SMS: жду, пока форма дойдёт до шага «жду код» (или завершится)…")
            await ready_event.wait()
        deadline = loop.time() + settings.sms_listen_minutes * 60
        default_sender = settings.manual_default_sender or "unknown"
        bar = "=" * 72
        logger.info(bar)
        logger.info("  >>>  ВПИШИ КОД ИЗ SMS В ЭТОТ ТЕРМИНАЛ  <<<")
        logger.info(bar)
        logger.info(f"  Слушаю SMS на номер {rent.phone}  (макс. {settings.sms_listen_minutes} мин)")
        logger.info("")
        logger.info("  Как только на телефон придёт SMS — скопируй её текст,")
        logger.info("  вставь СЮДА (в терминал) одной строкой и нажми Enter.")
        logger.info("")
        logger.info("  Парсер сам:")
        logger.info("    • запишет SMS в data/sms_log.xlsx")
        logger.info("    • достанет из неё код (4–6 цифр)")
        logger.info("    • впишет код в браузер и нажмёт «Получить деньги»")
        logger.info("")
        logger.info("  Формат ввода:")
        logger.info(f"    1)  просто текст SMS         → sender = '{default_sender}'")
        logger.info("    2)  sender|текст SMS          → задать имя отправителя")
        logger.info("    3)  +7965... или call|+7965... → входящий звонок")
        logger.info("    например:  web-zaim.ru|Код для регистрации: 5544")
        logger.info("")
        logger.info("  Завершить и показать файл:  введи  .stop  и Enter")
        logger.info(bar)
        def _readline_locked() -> str:
            with STDIN_LOCK:
                return sys.stdin.readline()

        while loop.time() < deadline:
            line = await loop.run_in_executor(None, _readline_locked)
            raw = line.strip()
            if not raw:
                continue
            if raw.lower() in (".stop", ".exit", ".quit"):
                logger.info("Manual: команда .stop → завершаю приём SMS")
                break
            if "|" in raw:
                sender, text = raw.split("|", 1)
                sender = sender.strip() or default_sender
                text = text.strip()
            else:
                sender, text = default_sender, raw
            if not text:
                continue
            if text.isdigit() and len(text) <= 6:
                logger.warning(
                    "Похоже, введён только код. Для ТЗ вставь ПОЛНЫЙ текст SMS "
                    "(с альфа-именем и ссылкой, если есть), например:\n"
                    "  web-zaim.ru|Код для регистрации: 5544 в сервисе web-zaim.ru"
                )
            logger.success(f"Manual SMS принята: [{sender}] {text[:80]}")
            yield IncomingSMS(
                sender=sender,
                text=text,
                received_at=datetime.now(),
            )

    async def release(self, rent: RentedNumber) -> None:
        return None
