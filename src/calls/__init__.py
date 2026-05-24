"""Приём входящих звонков на виртуальный номер.

Поддерживается:
- Zadarma — российский VoIP, отдаёт CDR через REST API + запись разговора.
- Mock — для отладки и демо без реального оборудования.

Звонки от МФО — критичный сигнал: после заявки звонят верификаторы,
служба безопасности банка, через 1-2 дня — отдел повторных продаж,
через 30+ дней (если не вернул) — коллекторы. Каждый звонок ценен
для понимания шлейфа лида.
"""
from __future__ import annotations

from config import settings
from src.calls.base import CallProvider, IncomingCall
from src.calls.mock import MockCallProvider
from src.calls.webhook import WebhookCallProvider
from src.calls.zadarma import ZadarmaCallProvider


def build_call_provider() -> CallProvider | None:
    name = settings.call_provider
    if name == "off":
        return None
    if name == "zadarma":
        if not settings.zadarma_api_key or not settings.zadarma_api_secret:
            raise RuntimeError("ZADARMA_API_KEY / ZADARMA_API_SECRET не заданы в .env")
        return ZadarmaCallProvider(
            api_key=settings.zadarma_api_key,
            api_secret=settings.zadarma_api_secret,
            virtual_number=settings.zadarma_virtual_number,
        )
    if name == "mock":
        return MockCallProvider()
    if name == "webhook":
        return WebhookCallProvider(
            host=settings.webhook_host,
            port=settings.webhook_port,
            token=settings.webhook_token,
        )
    raise ValueError(f"Неизвестный CALL_PROVIDER: {name}")


__all__ = ["CallProvider", "IncomingCall", "build_call_provider"]
