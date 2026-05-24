"""Базовые типы для провайдеров входящих звонков."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator


@dataclass(frozen=True)
class IncomingCall:
    """Один входящий звонок на виртуальный номер."""

    caller: str                # CallerID — номер или альфа-имя (если есть)
    started_at: datetime
    duration_sec: int          # 0 если не подняли
    recording_url: str | None  # ссылка на mp3 разговора, если есть
    status: str = ""           # missed / answered / rejected, если форвардер прислал
    direction: str = "incoming"
    summary: str = ""
    raw: dict | None = None


class CallProvider(ABC):
    @abstractmethod
    async def stream_calls(self) -> AsyncIterator[IncomingCall]:
        """Асинхронный поток входящих звонков, пока не остановят таймаут."""

    async def close(self) -> None:
        return None
