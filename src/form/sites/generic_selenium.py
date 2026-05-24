"""Fallback-заполнение неизвестных МФО-форм по эвристикам name/placeholder."""
from __future__ import annotations

import re
import time

from loguru import logger
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


FIELD_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(phone|tel|mobile|телефон|моб)", re.I), "phone"),
    (re.compile(r"(last.?name|surname|familiya|фамил)", re.I), "last_name"),
    (re.compile(r"(first.?name|givenname|name|имя)", re.I), "first_name"),
    (re.compile(r"(middle.?name|patronymic|otchestvo|отчест)", re.I), "middle_name"),
    (re.compile(r"(birth|dob|date.?of.?birth|рожден)", re.I), "birth_date"),
    (re.compile(r"(email|почта)", re.I), "email"),
]


def _describe(el) -> str:
    parts = []
    for attr in ("name", "id", "placeholder", "aria-label"):
        v = el.get_attribute(attr)
        if v:
            parts.append(v)
    return " ".join(parts).lower()


def fill_generic_form(driver: WebDriver, persona, amount: int) -> int:
    time.sleep(2)
    data = persona.as_dict()
    inputs = driver.find_elements(By.CSS_SELECTOR, "input, textarea")
    filled = 0
    for el in inputs:
        try:
            if not el.is_displayed():
                continue
            t = (el.get_attribute("type") or "").lower()
            if t in ("hidden", "submit", "button", "checkbox", "radio"):
                continue
            desc = _describe(el)
            for rx, key in FIELD_RULES:
                if rx.search(desc):
                    val = data.get(key) or ""
                    if not val:
                        break
                    try:
                        el.click()
                        el.send_keys(Keys.CONTROL, "a")
                        el.send_keys(Keys.DELETE)
                        el.send_keys(val)
                        filled += 1
                        logger.info(f"  ✓ {key}")
                    except Exception:
                        pass
                    break
        except Exception:
            continue
    return filled
