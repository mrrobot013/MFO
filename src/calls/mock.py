"""Mock-провайдер звонков — генерит фейковые входящие для demo."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import AsyncIterator

from src.calls.base import CallProvider, IncomingCall


SAMPLE_CALLS = [
    {
        "caller": "WebZaim",
        "delay": 8,
        "duration": 47,
        "recording_url": "https://example.com/rec/1.mp3",
    },
    {
        "caller": "+74951234567",
        "delay": 65,
        "duration": 132,
        "recording_url": "https://example.com/rec/2.mp3",
    },
    {
        "caller": "MoneyMan",
        "delay": 180,
        "duration": 0,  # не подняли
        "recording_url": None,
    },
]


class MockCallProvider(CallProvider):
    async def stream_calls(self) -> AsyncIterator[IncomingCall]:
        start = datetime.now()
        elapsed = 0
        for sample in SAMPLE_CALLS:
            wait = sample["delay"] - elapsed
            if wait > 0:
                await asyncio.sleep(min(wait, 2))
                elapsed = sample["delay"]
            yield IncomingCall(
                caller=sample["caller"],
                started_at=start + timedelta(seconds=sample["delay"]),
                duration_sec=sample["duration"],
                recording_url=sample["recording_url"],
                raw={"mock": True, **sample},
            )
