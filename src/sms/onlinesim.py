"""onlinesim.io — резервный SMS-провайдер (User Forwarding API)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import AsyncIterator

import httpx
from loguru import logger

from src.sms.base import IncomingSMS, RentedNumber, SmsProvider

API_BASE = "https://onlinesim.io/api"


class OnlineSimProvider(SmsProvider):
    def __init__(self, api_key: str, country: int = 7) -> None:
        self.api_key = api_key
        self.country = country
        self._client = httpx.AsyncClient(timeout=30.0)
        self._seen_ids: set[int] = set()

    async def _get(self, path: str, **params) -> dict:
        params = {"apikey": self.api_key, **params}
        r = await self._client.get(f"{API_BASE}/{path}", params=params)
        r.raise_for_status()
        return r.json()

    async def rent_number(self) -> RentedNumber:
        data = await self._get("getRentNum.php", country=self.country)
        item = data["item"]
        phone = str(item["number"])
        if not phone.startswith("+"):
            phone = "+" + phone
        rent = RentedNumber(phone=phone, rent_id=str(item["tzid"]))
        logger.success(f"onlinesim: {rent.phone}")
        return rent

    async def stream_sms(self, rent: RentedNumber) -> AsyncIterator[IncomingSMS]:
        from config import settings
        deadline = asyncio.get_event_loop().time() + settings.sms_listen_minutes * 60
        while asyncio.get_event_loop().time() < deadline:
            try:
                data = await self._get("getRentState.php", tzid=rent.rent_id)
                for item in data.get("data", []) or []:
                    msg_id = int(item.get("msg_id", 0))
                    if msg_id in self._seen_ids:
                        continue
                    self._seen_ids.add(msg_id)
                    yield IncomingSMS(
                        sender=str(item.get("service") or item.get("from") or "unknown"),
                        text=str(item.get("msg", "")),
                        received_at=_parse_dt(item.get("data_humans")),
                        raw=item,
                    )
            except Exception as e:
                logger.warning(f"onlinesim poll: {e}")
            await asyncio.sleep(settings.sms_poll_interval)

    async def release(self, rent: RentedNumber) -> None:
        try:
            await self._get("closeRentNum.php", tzid=rent.rent_id)
        finally:
            await self._client.aclose()


def _parse_dt(s) -> datetime:
    if not s:
        return datetime.now()
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now()
