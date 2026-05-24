"""SMS-провайдеры: аренда виртуального номера РФ и приём входящих SMS."""
from __future__ import annotations

from loguru import logger

from config import settings
from src.sms.base import IncomingSMS, SmsProvider
from src.sms.manual import ManualSmsProvider
from src.sms.onlinesim import OnlineSimProvider
from src.sms.webhook import WebhookSmsProvider


def build_provider() -> SmsProvider:
    name = settings.sms_provider
    if name == "onlinesim":
        if not settings.onlinesim_api_key:
            raise RuntimeError("ONLINESIM_API_KEY не задан в .env")
        return OnlineSimProvider(api_key=settings.onlinesim_api_key)
    if name == "manual":
        return ManualSmsProvider(prefilled_phone=settings.manual_phone)
    if name == "webhook":
        return WebhookSmsProvider(
            phone=settings.webhook_phone or settings.manual_phone,
            host=settings.webhook_host,
            port=settings.webhook_port,
            token=settings.webhook_token,
        )
    raise ValueError(f"Неизвестный SMS_PROVIDER: {name}")


__all__ = ["IncomingSMS", "SmsProvider", "build_provider"]
