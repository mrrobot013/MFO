"""Базовые типы и интерфейс SMS-провайдера."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator


@dataclass(frozen=True)
class IncomingSMS:
    """Одно входящее SMS."""

    sender: str                # альфа-имя или номер (CreditPlus, MIG, +7900...)
    text: str
    received_at: datetime
    raw: dict | None = None    # сырой ответ API провайдера (для отладки)


@dataclass
class RentedNumber:
    """Арендованный виртуальный номер."""

    phone: str                 # в формате +7XXXXXXXXXX
    rent_id: str               # внутренний id у провайдера


class SmsProvider(ABC):
    """Абстрактный интерфейс приёма SMS."""

    @abstractmethod
    async def rent_number(self) -> RentedNumber:
        """Снять номер в аренду."""

    @abstractmethod
    async def stream_sms(
        self, rent: RentedNumber, **kwargs
    ) -> AsyncIterator[IncomingSMS]:
        """Асинхронный генератор: выдаёт каждое новое SMS, пока не остановят."""

    @abstractmethod
    async def release(self, rent: RentedNumber) -> None:
        """Освободить номер (вернуть деньги / завершить аренду)."""

    async def __aenter__(self) -> "SmsProvider":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None
