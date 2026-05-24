"""AI-классификация SMS и расшифровка звонков через OpenAI.

Зачем: после заявки в МФО на номер падает шлейф из 30-50 событий за месяц.
Без классификации это просто шум. С классификацией мы получаем структуру:
  - сколько SMS-кодов было (показатель работы основной воронки),
  - сколько повторных предложений займа (cross-sell от других МФО),
  - сколько коллекторских звонков (сигнал просрочки в БКИ),
  - сколько чисто маркетинговых SMS (показатель утечки базы).

Если OPENAI_API_KEY не задан — модуль работает в "rule-based" режиме на
регулярках, чтобы demo продолжал работать.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

import httpx
from loguru import logger


Category = Literal[
    "verification_code",   # код подтверждения от МФО
    "loan_offer",          # повторное предложение займа
    "marketing",           # промо/реклама
    "collector",           # коллекторское давление
    "service_info",        # инфо-уведомление (статус заявки/платежа)
    "unknown",
]


@dataclass
class Classification:
    category: Category
    confidence: float           # 0..1
    transcript: str | None      # для звонков — текст расшифровки
    summary: str                # короткое объяснение
    risk_flag: bool             # True если что-то стрёмное (коллектор/слив базы)


_RULES: list[tuple[re.Pattern[str], Category]] = [
    (re.compile(r"\b(код|kod|code|otp|pin)\b.*\d{3,6}|парол|password|подтвержд|podtverzhd|confirm", re.I), "verification_code"),
    (re.compile(r"коллект|долг|просроч|prosroch|prosrochka|пристав|приставы|суд|collector|debt|overdue", re.I), "collector"),
    (re.compile(r"скидк|акция|подарок|бонус|промокод|discount|promo|sale|скидка", re.I), "marketing"),
    (re.compile(r"одобрен|оформи|получите.*займ|новый.*займ|повторн|odobren|zaim|loan", re.I), "loan_offer"),
    (re.compile(r"платеж|статус.*заявки|погашен|остаток|status|balance", re.I), "service_info"),
]


def _rule_based(text: str) -> Classification:
    for rx, cat in _RULES:
        if rx.search(text):
            return Classification(
                category=cat,
                confidence=0.55,
                transcript=None,
                summary=f"rule-based: совпала маска {rx.pattern[:40]}",
                risk_flag=cat == "collector",
            )
    return Classification(
        category="unknown",
        confidence=0.3,
        transcript=None,
        summary="rule-based: маска не сработала",
        risk_flag=False,
    )


SYSTEM = (
    "Ты помощник аналитика арбитражной команды в МФО-вертикали. "
    "Классифицируй входящее SMS или запись звонка по категориям: "
    "verification_code, loan_offer, marketing, collector, service_info, unknown. "
    "Верни строго JSON: {category, confidence (0..1), summary (1-2 фразы по-русски), "
    "risk_flag (true только для коллекторских/жёстких/мошеннических сообщений)}."
)


class AIClassifier:
    def __init__(self, api_key: str = "", model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key
        self.model = model
        self._client = httpx.AsyncClient(timeout=30.0) if api_key else None

    async def classify_sms(self, sender: str, text: str) -> Classification:
        if not self._client:
            return _rule_based(text)
        try:
            payload = {
                "model": self.model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {
                        "role": "user",
                        "content": f"Отправитель: {sender}\nТекст SMS: {text}",
                    },
                ],
                "temperature": 0,
            }
            r = await self._client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            r.raise_for_status()
            data = json.loads(r.json()["choices"][0]["message"]["content"])
            return Classification(
                category=data.get("category", "unknown"),
                confidence=float(data.get("confidence", 0.5)),
                transcript=None,
                summary=str(data.get("summary", "")),
                risk_flag=bool(data.get("risk_flag", False)),
            )
        except Exception as e:
            logger.warning(f"OpenAI classify error: {e}, fallback на правила")
            return _rule_based(text)

    async def classify_call(self, caller: str, duration_sec: int) -> Classification:
        if duration_sec == 0:
            return Classification(
                category="unknown",
                confidence=0.7,
                transcript=None,
                summary="звонок не подняли, классифицировать нечего",
                risk_flag=False,
            )
        if not self._client:
            cat: Category = "loan_offer" if duration_sec > 30 else "service_info"
            return Classification(
                category=cat,
                confidence=0.4,
                transcript=None,
                summary=f"rule-based по длительности {duration_sec} сек",
                risk_flag=False,
            )
        try:
            payload = {
                "model": self.model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Звонок от {caller}, длительность {duration_sec} сек. "
                            "Расшифровки нет, классифицируй по эвристике: "
                            "длинный (>60s) — обычно скоринг/верификация; "
                            "средний (30-60s) — повторное предложение; "
                            "короткий (<30s) — авто-обзвон/маркетинг."
                        ),
                    },
                ],
                "temperature": 0,
            }
            r = await self._client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            r.raise_for_status()
            data = json.loads(r.json()["choices"][0]["message"]["content"])
            return Classification(
                category=data.get("category", "unknown"),
                confidence=float(data.get("confidence", 0.5)),
                transcript=None,
                summary=str(data.get("summary", "")),
                risk_flag=bool(data.get("risk_flag", False)),
            )
        except Exception as e:
            logger.warning(f"OpenAI classify_call error: {e}")
            return Classification(
                category="unknown", confidence=0.3, transcript=None,
                summary=f"fallback после ошибки OpenAI: {e}", risk_flag=False,
            )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
