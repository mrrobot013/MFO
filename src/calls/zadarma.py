"""Zadarma — приём звонков и запись разговоров через REST API.

Документация: https://zadarma.com/ru/support/api/
Используем endpoint /v1/statistics/ — это CDR (call detail records),
там видны все входящие на наш виртуальный номер: кто звонил, когда,
сколько говорили, ссылка на запись.

Подписи запросов формируем по схеме Zadarma: HMAC-SHA1 от
"method+params+md5(body)" с секретом API.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import AsyncIterator
from urllib.parse import urlencode

import httpx
from loguru import logger

from src.calls.base import CallProvider, IncomingCall


API_BASE = "https://api.zadarma.com"


class ZadarmaCallProvider(CallProvider):
    def __init__(self, api_key: str, api_secret: str, virtual_number: str = "") -> None:
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.virtual_number = virtual_number
        self._client = httpx.AsyncClient(timeout=30.0)
        self._seen_ids: set[str] = set()
        self._started = datetime.utcnow()

    def _sign(self, method: str, params: dict) -> str:
        params_str = urlencode(sorted(params.items()))
        body_md5 = hashlib.md5(params_str.encode()).hexdigest()
        sig_payload = f"{method}{params_str}{body_md5}".encode()
        signature = hmac.new(self.api_secret, sig_payload, hashlib.sha1).hexdigest()
        return base64.b64encode(f"{self.api_key}:{signature}".encode()).decode()

    async def _request(self, method: str, params: dict | None = None) -> dict:
        params = params or {}
        params_str = urlencode(sorted(params.items()))
        body_md5 = hashlib.md5(params_str.encode()).hexdigest()
        sig_payload = f"{method}{params_str}{body_md5}".encode()
        signature = hmac.new(self.api_secret, sig_payload, hashlib.sha1).hexdigest()
        header = f"{self.api_key}:{signature}"
        url = f"{API_BASE}{method}?{params_str}" if params else f"{API_BASE}{method}"
        r = await self._client.get(url, headers={"Authorization": header})
        r.raise_for_status()
        return r.json()

    async def stream_calls(self) -> AsyncIterator[IncomingCall]:
        from config import settings
        loop = asyncio.get_event_loop()
        deadline = loop.time() + settings.sms_listen_minutes * 60
        while loop.time() < deadline:
            try:
                start = (self._started - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
                end = (datetime.utcnow() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
                params = {"start": start, "end": end, "version": 2}
                resp = await self._request("/v1/statistics/", params)
            except Exception as e:
                logger.warning(f"zadarma poll: {e}")
                await asyncio.sleep(settings.sms_poll_interval)
                continue

            for item in resp.get("stats", []) or []:
                if str(item.get("disposition", "")).lower() not in {"answered", "busy", "no answer", "answered", "cancel"}:
                    continue
                if item.get("destination") and self.virtual_number and \
                        self.virtual_number not in str(item.get("destination")):
                    continue
                cid = str(item.get("id") or item.get("call_id") or item.get("sip_call_id") or "")
                if not cid or cid in self._seen_ids:
                    continue
                self._seen_ids.add(cid)

                recording = None
                try:
                    rec_resp = await self._request(
                        "/v1/pbx/record/request/", {"call_id": cid, "lifetime": 60}
                    )
                    recording = rec_resp.get("link")
                except Exception:
                    pass

                yield IncomingCall(
                    caller=str(item.get("caller_id") or item.get("from") or "unknown"),
                    started_at=_parse_dt(item.get("callstart")),
                    duration_sec=int(item.get("seconds", 0)),
                    recording_url=recording,
                    raw=item,
                )
            await asyncio.sleep(settings.sms_poll_interval)

    async def close(self) -> None:
        await self._client.aclose()


def _parse_dt(s) -> datetime:
    if not s:
        return datetime.now()
    try:
        return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now()
