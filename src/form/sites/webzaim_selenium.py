"""Web-Zaim (web-zaim.ru) — Selenium-сценарий.

1. /installment — калькулятор, ссылка «Получить деньги» (это <a>, не button).
2. /client/registration — клик «Заполнить анкету вручную».
3. Поля: name=phoneNumber, surname, name, patronymic, birthDate, email, password.
4. Чекбоксы согласий → кнопка «Продолжить» → SMS.
5. Получаем SMS, вписываем код, отправляем.
"""
from __future__ import annotations

import re
import time

from loguru import logger
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.form.browser import safe_current_url, switch_to_active_tab


NAME_FIELDS: list[tuple[str, str]] = [
    ("phoneNumber", "phone"),
    ("surname", "last_name"),
    ("name", "first_name"),
    ("patronymic", "middle_name"),
    ("birthDate", "birth_date"),
    ("email", "email"),
    ("password", "password"),
]

_CODE_RX = re.compile(
    r"(?:код|code|пароль|password)\s*[:\s#№]*(\d{4,6})",
    re.IGNORECASE,
)
_CODE_FALLBACK = re.compile(r"\b(\d{4,6})\b")


def extract_sms_code(text: str) -> str | None:
    if not text:
        return None
    m = _CODE_RX.search(text)
    if m:
        return m.group(1)
    m = _CODE_FALLBACK.search(text)
    return m.group(1) if m else None


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if digits.startswith("7") and len(digits) == 11:
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def _safe_find(driver: WebDriver, by: str, value: str):
    try:
        el = driver.find_element(by, value)
        if el.is_displayed():
            return el
    except (NoSuchElementException, StaleElementReferenceException):
        pass
    return None


def _wait_displayed(driver: WebDriver, by: str, value: str, timeout: int = 15):
    try:
        return WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((by, value))
        )
    except TimeoutException:
        return None


def is_already_logged_in(driver: WebDriver) -> bool:
    try:
        for link in driver.find_elements(By.XPATH, "//*[normalize-space(.)='Выйти']"):
            if link.is_displayed():
                return True
    except Exception:
        pass
    return False


def logout_if_needed(driver: WebDriver) -> bool:
    """Если в шапке Web-Zaim есть «Выйти» — нажать. Вернёт True если разлогинились."""
    if not is_already_logged_in(driver):
        return False
    try:
        link = driver.find_element(
            By.XPATH,
            "//*[normalize-space(.)='Выйти' and not(ancestor::*[contains(@style,'display: none')])]",
        )
        driver.execute_script("arguments[0].click();", link)
        logger.info("  ▶ нажал «Выйти» — разлогиниваюсь от прошлой персоны")
        time.sleep(2)
        # Подчистим cookies на всякий случай (если ЛК сидит в localStorage/cookie).
        try:
            driver.delete_all_cookies()
        except Exception:
            pass
        # Возвращаемся на /installment
        driver.get("https://web-zaim.ru/installment?utm_source=leadstech")
        time.sleep(2)
        return True
    except NoSuchElementException:
        logger.debug("кнопка «Выйти» найдена через is_already_logged_in, но click failed")
        return False


def is_waiting_for_sms_code(driver: WebDriver) -> bool:
    """Текст «Создание аккаунта … отправлено SMS с кодом»."""
    try:
        body = driver.execute_script("return document.body.innerText") or ""
    except Exception:
        return False
    body = body.lower()
    return "отправлено sms с кодом" in body or "введите полученный код" in body


def otp_page_matches_phone(driver: WebDriver, phone: str) -> bool:
    """Проверить, что OTP-страница относится к текущему номеру."""
    expected10 = _normalize_phone(phone)
    if not expected10:
        return False
    try:
        body = driver.execute_script("return document.body.innerText") or ""
    except Exception:
        return False
    digits = re.sub(r"\D", "", body)
    if expected10 in digits or f"7{expected10}" in digits:
        return True
    # Web-Zaim can mask the phone and leave only the last digits visible.
    return expected10[-4:] in digits if digits else False


def reset_registration_state(driver: WebDriver) -> None:
    """Сбросить старую регистрацию/OTP-сессию перед новой анкетой."""
    try:
        driver.delete_all_cookies()
    except Exception:
        pass
    try:
        driver.execute_script("window.localStorage.clear(); window.sessionStorage.clear();")
    except Exception:
        pass
    driver.get("https://web-zaim.ru/installment?utm_source=leadstech")
    time.sleep(2)


_GET_MONEY_XPATH = (
    "//a[contains(translate(., 'ПОЛУЧИТЬ ДЕНГИ', 'получить денги'), 'получить деньги')]"
)


def _click_get_money(driver: WebDriver) -> None:
    switch_to_active_tab(driver)
    if "registration" in safe_current_url(driver):
        return

    link = None
    try:
        link = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, _GET_MONEY_XPATH))
        )
    except TimeoutException:
        logger.debug("ссылка «Получить деньги» не появилась на /installment")

    if link is None:
        # fallback по жесткому URL
        logger.info("  fallback: открываю /client/registration напрямую")
        driver.get("https://web-zaim.ru/client/registration?utm_source=leadstech")
        time.sleep(1.5)
        return

    href_dest = link.get_attribute("href") or ""
    try:
        driver.execute_script(
            "arguments[0].removeAttribute('target'); arguments[0].click();", link
        )
        logger.info("  ▶ «Получить деньги» → переход на регистрацию")
    except Exception:
        pass

    deadline = time.time() + 6
    while time.time() < deadline:
        time.sleep(0.25)
        switch_to_active_tab(driver)
        if "registration" in safe_current_url(driver):
            return

    if href_dest and "registration" in href_dest:
        logger.info("  fallback: открываю /client/registration напрямую")
        driver.get(href_dest)
        time.sleep(1.5)


def _open_manual_form(driver: WebDriver) -> None:
    if _safe_find(driver, By.CSS_SELECTOR, 'input[name="phoneNumber"]'):
        return
    try:
        manual = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//*[contains(text(), 'Заполнить анкету вручную')]")
            )
        )
        driver.execute_script("arguments[0].click();", manual)
        logger.info("  ▶ «Заполнить анкету вручную»")
        _wait_displayed(driver, By.CSS_SELECTOR, 'input[name="phoneNumber"]', 8)
    except TimeoutException:
        logger.warning("кнопка «Заполнить анкету вручную» не найдена")


def _set_amount(driver: WebDriver, amount: int) -> None:
    el = _safe_find(driver, By.ID, "calculatorTextInput_amount")
    if not el:
        return
    try:
        el.click()
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.DELETE)
        el.send_keys(str(amount))
        logger.info(f"сумма {amount}")
    except Exception:
        pass


_CLEAR_REACT_JS = """
const el = arguments[0];
const proto = el.tagName === 'TEXTAREA'
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
setter.call(el, '');
el.dispatchEvent(new Event('input', {bubbles: true}));
el.dispatchEvent(new Event('change', {bubbles: true}));
"""


def _hard_clear(driver: WebDriver, el) -> None:
    """Очистить React/MUI-инпут гарантированно: JS + selenium .clear() + Cmd/Ctrl+A+Del."""
    try:
        driver.execute_script(_CLEAR_REACT_JS, el)
    except Exception:
        pass
    try:
        el.clear()
    except Exception:
        pass
    try:
        el.click()
        for combo in (Keys.COMMAND, Keys.CONTROL):
            try:
                el.send_keys(combo, "a")
                el.send_keys(Keys.DELETE)
            except Exception:
                continue
    except Exception:
        pass


def _fill_input(driver: WebDriver, name: str, value: str, *, phone: bool = False) -> bool:
    el = _safe_find(driver, By.CSS_SELECTOR, f'input[name="{name}"]')
    if not el:
        return False

    def _value_ok(actual_value: str) -> bool:
        actual_clean = actual_value.strip()
        expected = value.strip()
        if phone:
            actual_digits = re.sub(r"\D", "", actual_clean)
            expected_digits = re.sub(r"\D", "", expected)
            return bool(expected_digits) and actual_digits.endswith(expected_digits)
        return actual_clean == expected

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        el.click()
        _hard_clear(driver, el)
        if phone:
            for ch in value:
                el.send_keys(ch)
                time.sleep(0.02)
        else:
            el.send_keys(value)

        # проверка, что в поле ровно то, что ожидаем
        actual = (el.get_attribute("value") or "").strip()
        if not _value_ok(actual):
            logger.warning(
                f"  ⚠ {name}: в поле «{actual[:60]}», переписываю принудительно"
            )
            _hard_clear(driver, el)
            try:
                driver.execute_script(
                    """
                    const el = arguments[0]; const v = arguments[1];
                    const proto = el.tagName === 'TEXTAREA'
                        ? window.HTMLTextAreaElement.prototype
                        : window.HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    setter.call(el, v);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    """,
                    el,
                    value,
                )
            except Exception:
                pass
            actual = (el.get_attribute("value") or "").strip()

        if _value_ok(actual):
            logger.info(f"  ✓ {name}")
            return True
        logger.warning(f"  ✗ {name}: в поле «{actual[:60]}», ожидал «{value[:60]}»")
        return False
    except (ElementClickInterceptedException, ElementNotInteractableException) as e:
        logger.debug(f"  ✗ {name}: {e}")
        return False


def _tick_agreements(driver: WebDriver) -> int:
    """Отмечаем все непомеченные чекбоксы (агрегатно — все обязательные согласия)."""
    boxes = driver.find_elements(By.CSS_SELECTOR, "input[type=checkbox]")
    ticked = 0
    for cb in boxes:
        try:
            if not cb.is_displayed() and cb.get_attribute("type") != "checkbox":
                continue
            if cb.is_selected():
                continue
            label = cb.find_element(By.XPATH, "ancestor::label | following::label[1]")
            try:
                label.click()
            except Exception:
                driver.execute_script("arguments[0].click();", cb)
            ticked += 1
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", cb)
                ticked += 1
            except Exception:
                continue
    if ticked:
        logger.info(f"  ✓ согласия отмечены: {ticked}")
    return ticked


def fill_webzaim_form(driver: WebDriver, persona, amount: int, term_days: int) -> int:
    switch_to_active_tab(driver)
    time.sleep(1.0)

    if "installment" in safe_current_url(driver):
        _set_amount(driver, amount)

    _click_get_money(driver)
    _open_manual_form(driver)

    phone10 = _normalize_phone(persona.phone)
    data = persona.as_dict()
    filled = 0

    for field_name, data_key in NAME_FIELDS:
        if data_key == "phone":
            value, is_phone = phone10, True
        elif data_key == "password":
            value, is_phone = "TestPass123", False
        else:
            value, is_phone = data.get(data_key, ""), False
        if not value:
            continue
        if _fill_input(driver, field_name, value, phone=is_phone):
            filled += 1

    _tick_agreements(driver)
    return filled


def submit_webzaim(driver: WebDriver, *, filled_fields: int = 0) -> bool:
    if filled_fields == 0:
        logger.warning("полей не заполнено — «Продолжить» не нажимаю")
        return False
    if not _safe_find(driver, By.CSS_SELECTOR, 'input[name="phoneNumber"]'):
        logger.warning("поля шага 1 не видны — пропускаю «Продолжить»")
        return False

    try:
        btn = driver.find_element(
            By.XPATH, "//button[normalize-space(.)='Продолжить']"
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
        btn.click()
        logger.success("  ▶ «Продолжить» — отправка регистрации (шаг 1/4)")
        time.sleep(1.5)
        return True
    except NoSuchElementException:
        logger.warning("кнопка «Продолжить» не найдена")
        return False


def enter_sms_code(driver: WebDriver, code: str) -> bool:
    if not code:
        return False
    time.sleep(1)
    # 1) одно поле «Код подтверждения»
    for selector in (
        'input[name*="code" i]',
        'input[autocomplete="one-time-code"]',
        'input[inputmode="numeric"]',
        'input[placeholder*="код" i]',
    ):
        el = _safe_find(driver, By.CSS_SELECTOR, selector)
        if el:
            try:
                el.click()
                el.send_keys(Keys.CONTROL, "a")
                el.send_keys(Keys.DELETE)
                for ch in code:
                    el.send_keys(ch)
                    time.sleep(0.05)
                logger.success(f"  ✓ код {code} вписан в поле подтверждения")
                _click_get_money_after_code(driver)
                return True
            except Exception as e:
                logger.debug(f"не смог: {e}")

    # 2) OTP по одной цифре (MUI)
    otp = [
        e for e in driver.find_elements(By.CSS_SELECTOR, 'input[maxlength="1"]')
        if e.is_displayed()
    ]
    if len(otp) >= len(code):
        for i, ch in enumerate(code):
            try:
                otp[i].send_keys(ch)
            except Exception:
                pass
        logger.success(f"  ✓ код {code} вписан в OTP-инпуты ({len(otp)} ячеек)")
        _click_get_money_after_code(driver)
        return True

    logger.warning("поле «Код подтверждения» не найдено — введи код в браузере руками")
    return False


def _click_get_money_after_code(driver: WebDriver) -> None:
    time.sleep(1)
    for xpath in (
        "//button[normalize-space(.)='Получить деньги']",
        "//button[contains(., 'Подтвердить')]",
        "//button[normalize-space(.)='Продолжить']",
    ):
        try:
            btn = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].click();", btn)
            logger.success(f"  ▶ нажат «{btn.text}» после ввода кода")
            return
        except NoSuchElementException:
            continue
