"""Selenium-сценарий заполнения формы МФО.

Логика (соответствие ТЗ):
1. Открыть партнёрскую ссылку, дождаться приземления на лендинг МФО.
2. Заполнить форму первого шага фейковой персоной.
3. Отправить — спровоцировать SMS на номер.
4. Дождаться кода (через `code_event`, который main.py выставит при получении SMS).
5. Ввести код в браузер.

Все шаги логируются скриншотами в data/screenshots/.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from loguru import logger

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait

from src.form.browser import (
    find_healthy_webzaim_tab,
    human_pause,
    open_browser,
    page_is_blocked,
    prompt_manual_landing,
    recover_browser_window,
    recover_from_403,
    safe_current_url,
    switch_to_active_tab,
    wait_page_unblocked,
)
from src.form.fake_data import Persona


@dataclass
class FormFillResult:
    landing_url: str
    submitted: bool
    sms_code_used: str | None
    screenshots: list[str]
    error: str | None = None


def _click_get_money_link_if_present(driver: WebDriver) -> None:
    """Если мы на /installment — кликнем «Получить деньги», чтобы попасть на /registration."""
    if "registration" in safe_current_url(driver):
        return
    try:
        from selenium.webdriver.common.by import By
        link = driver.find_element(
            By.XPATH,
            "//a[contains(translate(., 'ПОЛУЧИТЬ ДЕНГИ', 'получить денги'), 'получить деньги')]",
        )
        driver.execute_script("arguments[0].click();", link)
        time.sleep(3)
    except Exception:
        pass


def _wait_landing(
    driver: WebDriver,
    tracker_url: str,
    *,
    landing_mode: str = "manual",
    allow_manual: bool = True,
) -> str:
    existing = find_healthy_webzaim_tab(driver)
    if existing:
        logger.success(f"приземление на МФО (уже открыто): {existing}")
        return existing

    if landing_mode == "manual":
        if prompt_manual_landing(driver, tracker_url):
            url = find_healthy_webzaim_tab(driver)
            if url:
                return url
        if allow_manual and recover_from_403(
            driver, tracker_url, allow_manual=True
        ):
            url = find_healthy_webzaim_tab(driver)
            if url:
                return url
        return safe_current_url(driver, tracker_url)

    logger.info(f"открываю трекинг {tracker_url}")
    if not switch_to_active_tab(driver):
        recover_browser_window(driver)
    try:
        driver.get(tracker_url)
    except TimeoutException:
        logger.warning(
            "загрузка трекера превысила таймаут, но страница могла успеть открыться — "
            "останавливаю загрузку и продолжаю проверку текущего URL"
        )
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass
    deadline = time.time() + 25
    final = ""
    while time.time() < deadline:
        time.sleep(0.25)
        switch_to_active_tab(driver)
        cur = safe_current_url(driver)
        if cur and "leads.tech" not in cur and cur.startswith("http"):
            time.sleep(1.2)
            if wait_page_unblocked(driver, timeout=6.0):
                final = safe_current_url(driver, cur)
                break
            if page_is_blocked(driver) and recover_from_403(
                driver, tracker_url, allow_manual=allow_manual
            ):
                final = safe_current_url(driver, cur)
                break
    if not final:
        final = safe_current_url(driver, tracker_url)
    if page_is_blocked(driver):
        if recover_from_403(driver, tracker_url, allow_manual=allow_manual):
            final = safe_current_url(driver, final)
        else:
            logger.error("приземление: страница всё ещё 403")
    else:
        logger.success(f"приземление на МФО: {final}")
    return final


def fill_and_submit_sync(
    *,
    tracker_url: str,
    persona: Persona,
    amount: int,
    term_days: int,
    headless: bool,
    screenshots_dir: Path,
    browser_mode: str,
    cdp_endpoint: str,
    user_agent: str,
    code_event: threading.Event,
    code_holder: dict,
    code_wait_seconds: int,
    allow_manual_recovery: bool = True,
    landing_mode: str = "manual",
    use_default_profile: bool = True,
    submit_enabled: bool = True,
    keep_browser_open: bool = True,
    on_waiting_sms: Callable[[], None] | None = None,
) -> FormFillResult:
    """Sync-функция: запускается из asyncio.to_thread.

    `code_event` — main.py выставит его, когда придёт SMS;
    `code_holder['code']` — сам код.
    """
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    shots: list[str] = []
    landing = ""
    submitted = False
    code_used: str | None = None
    error: str | None = None

    def snap(name: str) -> None:
        try:
            p = screenshots_dir / f"{name}.png"
            driver.save_screenshot(str(p))
            shots.append(str(p))
        except Exception:
            pass

    manual_landing = landing_mode == "manual"
    with open_browser(
        mode=browser_mode,
        cdp_endpoint=cdp_endpoint,
        headless=headless,
        user_agent=user_agent,
        use_default_profile=use_default_profile,
        open_new_tab=not manual_landing,
        keep_open=keep_browser_open,
    ) as driver:
        try:
            if manual_landing:
                logger.info(
                    "Режим manual: открой web-zaim вручную в Chrome от "
                    "«python main.py chrome», парсер не будет сам открывать ссылку."
                )
            else:
                logger.info(
                    "Не закрывай вкладку Chrome, которую открыл парсер — "
                    "иначе сценарий оборвётся."
                )
            landing = _wait_landing(
                driver,
                tracker_url,
                landing_mode=landing_mode,
                allow_manual=allow_manual_recovery,
            )
            snap("01_landing")

            if page_is_blocked(driver):
                if not recover_from_403(
                    driver, tracker_url, allow_manual=allow_manual_recovery
                ):
                    error = (
                        "Web-Zaim: 403 Forbidden. Выключи VPN, смени сеть "
                        "(мобильный хотспот) или подожди 30 мин."
                    )
                    logger.error(error)
                    return FormFillResult(landing, False, None, shots, error)
                landing = safe_current_url(driver, landing)
                snap("01b_after_403_recovery")

            human_pause(0.3, 0.6)
            switch_to_active_tab(driver)
            is_webzaim = "web-zaim" in landing.lower()

            if is_webzaim:
                from src.form.sites.webzaim_selenium import (
                    enter_sms_code,
                    fill_webzaim_form,
                    is_waiting_for_sms_code,
                    logout_if_needed,
                    otp_page_matches_phone,
                    reset_registration_state,
                    submit_webzaim,
                )

                if page_is_blocked(driver):
                    if not recover_from_403(
                        driver, tracker_url, allow_manual=allow_manual_recovery
                    ):
                        error = (
                            "Web-Zaim: 403 Forbidden. Выключи VPN, "
                            "открой сайт вручную в этом Chrome или смени Wi-Fi."
                        )
                        logger.error(error)
                        return FormFillResult(landing, False, None, shots, error)
                    snap("01c_recovered")

                # 1) Если страница уже в состоянии «введите SMS-код» — заявка уже подана.
                # Пропускаем повторное заполнение и сразу ждём код.
                _click_get_money_link_if_present(driver)
                if is_waiting_for_sms_code(driver):
                    if otp_page_matches_phone(driver, persona.phone):
                        logger.warning(
                            "Web-Zaim уже ждёт SMS-код для текущего номера "
                            "— пропускаю заполнение, переключаюсь в режим автоввода кода."
                        )
                        submitted = True
                        filled = 0
                    else:
                        logger.warning(
                            "Web-Zaim ждёт SMS-код от старой заявки/другого номера — "
                            "сбрасываю сессию и заполняю новую анкету."
                        )
                        reset_registration_state(driver)
                        landing = safe_current_url(driver, landing)
                        snap("01d_after_old_otp_reset")
                        if logout_if_needed(driver):
                            snap("01e_after_logout")
                        filled = fill_webzaim_form(driver, persona, amount, term_days)
                        snap("02_amount_set")
                        logger.info(f"заполнено полей: {filled}")
                        snap("03_filled")
                        if filled == 0:
                            error = "не удалось заполнить ни одного поля шага 1"
                            return FormFillResult(landing, False, None, shots, error)
                        if submit_enabled:
                            submitted = submit_webzaim(driver, filled_fields=filled)
                        else:
                            logger.warning(
                                "DRY RUN: форма заполнена, «Продолжить» не нажимаю"
                            )
                            submitted = False
                        snap("04_submitted")
                        logger.info(f"URL после submit: {safe_current_url(driver)}")
                else:
                    if logout_if_needed(driver):
                        snap("01b_after_logout")
                    if page_is_blocked(driver):
                        error = (
                            "Web-Zaim 403 Forbidden после разлогина — "
                            "Cloudflare блокирует IP. Подожди 15–30 мин или сменить сеть."
                        )
                        logger.error(error)
                        return FormFillResult(landing, False, None, shots, error)
                    filled = fill_webzaim_form(driver, persona, amount, term_days)
                    snap("02_amount_set")
                    logger.info(f"заполнено полей: {filled}")
                    snap("03_filled")

                    if filled == 0:
                        error = "не удалось заполнить ни одного поля шага 1"
                        return FormFillResult(landing, False, None, shots, error)

                    if submit_enabled:
                        submitted = submit_webzaim(driver, filled_fields=filled)
                    else:
                        logger.warning(
                            "DRY RUN: форма заполнена, «Продолжить» не нажимаю"
                        )
                        submitted = False
                    snap("04_submitted")
                    logger.info(f"URL после submit: {safe_current_url(driver)}")

                if submitted:
                    if on_waiting_sms:
                        on_waiting_sms()
                    logger.info("")
                    logger.info("  >>>  ВПИШИ КОД ИЗ SMS В ТЕРМИНАЛ  <<<")
                    logger.info(f"  Жду SMS на телефон до {code_wait_seconds // 60} мин.")
                    logger.info("  Когда придёт — скопируй текст SMS и вставь СЮДА (в терминал).")
                    logger.info("  В браузер код впишется автоматически.")
                    logger.info("")
                    got = code_event.wait(timeout=code_wait_seconds)
                    if not got:
                        logger.warning("SMS-код не получен за отведённое время")
                    else:
                        code_used = code_holder.get("code")
                        if code_used:
                            enter_sms_code(driver, code_used)
                            snap("05_code_entered")
                            time.sleep(3)
                            snap("06_after_code")
                            logger.info(f"URL после кода: {safe_current_url(driver)}")
            else:
                from src.form.sites.generic_selenium import fill_generic_form

                filled = fill_generic_form(driver, persona, amount)
                snap("02_amount_set")
                logger.info(f"заполнено полей (generic): {filled}")
                snap("03_filled")
                submitted = filled > 0
                snap("04_submitted")

        except Exception as e:
            error = repr(e)
            logger.exception("ошибка при заполнении формы")
            snap("99_error")
        finally:
            if on_waiting_sms and not submitted:
                on_waiting_sms()

    return FormFillResult(
        landing_url=landing,
        submitted=submitted,
        sms_code_used=code_used,
        screenshots=shots,
        error=error,
    )
