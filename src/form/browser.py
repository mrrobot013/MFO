"""Браузерный слой на Selenium.

Два режима:
- `cdp`        — attach к Google Chrome на :9222 (обход 403 web-zaim.ru).
- `undetected` — undetected_chromedriver (fallback).
"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal
from urllib.parse import urlparse

from loguru import logger
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.common.exceptions import NoSuchWindowException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


BrowserMode = Literal["cdp", "undetected"]

CDP_PROFILE_DIR = Path.home() / "mfo-chrome-profile"
CDP_PORT = 9222
CHROME_PID_FILE = Path(__file__).resolve().parents[2] / "data" / ".chrome.pid"
_DETACHED_UNDETECTED_DRIVERS: list[webdriver.Chrome] = []


def _chrome_executable_path() -> str | None:
    if sys.platform.startswith("win"):
        import os

        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
        for p in candidates:
            if p.exists():
                return str(p)
    p = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if p.exists():
        return str(p)
    try:
        import shutil

        return shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("chromium")
    except Exception:
        return None


def kill_cdp_chrome(port: int = CDP_PORT) -> bool:
    """Закрыть Chrome, поднятый нами через CDP. Возвращает True если что-то убили."""
    import os
    import signal

    killed = False
    if CHROME_PID_FILE.exists():
        try:
            pid = int(CHROME_PID_FILE.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
            killed = True
            logger.success(f"Chrome (pid {pid}) → SIGTERM")
        except ProcessLookupError:
            logger.info("Chrome уже остановлен")
        except Exception as e:
            logger.warning(f"kill chrome: {e}")
        try:
            CHROME_PID_FILE.unlink()
        except Exception:
            pass

    if not killed:
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", f"remote-debugging-port={port}"],
                text=True,
            ).strip()
            for line in out.splitlines():
                line = line.strip()
                if not line.isdigit():
                    continue
                try:
                    os.kill(int(line), signal.SIGTERM)
                    killed = True
                    logger.success(f"Chrome (pid {line}) → SIGTERM")
                except Exception:
                    continue
        except subprocess.CalledProcessError:
            pass
        except FileNotFoundError:
            pass

    if not killed:
        logger.info(f"Не нашёл CDP-Chrome (порт {port})")
    return killed


def detect_chrome_version() -> str:
    exe = _chrome_executable_path()
    if not exe:
        return "130.0.0.0"
    if sys.platform.startswith("win"):
        try:
            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-Item -LiteralPath '{exe}').VersionInfo.ProductVersion",
                ],
                text=True,
                timeout=5,
            )
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
            if m:
                return m.group(1)
        except Exception:
            pass
    try:
        out = subprocess.check_output([exe, "--version"], text=True, timeout=5)
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
        return m.group(1) if m else "130.0.0.0"
    except Exception:
        return "130.0.0.0"


def build_desktop_chrome_user_agent(chrome_version: str | None = None) -> str:
    ver = chrome_version or detect_chrome_version()
    if sys.platform.startswith("win"):
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"
        )
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"
    )


def resolve_user_agent(configured: str = "") -> str:
    ua = (configured or "").strip()
    if ua and "iPhone" not in ua and "Mobile" not in ua:
        return ua
    if ua:
        logger.warning("USER_AGENT мобильный → подставляю desktop Chrome")
    ua = build_desktop_chrome_user_agent()
    logger.info(f"User-Agent (HTTP): {ua[:90]}…")
    return ua


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _ensure_chrome_has_tab(port: int) -> None:
    """Selenium-attach падает, если у Chrome нет открытых вкладок."""
    import json as _json
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json", timeout=2
        ) as resp:
            tabs = _json.loads(resp.read().decode("utf-8"))
    except Exception:
        return
    if any(t.get("type") == "page" for t in tabs):
        return
    logger.info("CDP: у Chrome нет вкладок — открываю about:blank")
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT"
        )
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/new?about:blank", timeout=3
            ).read()
        except Exception as e:
            logger.warning(f"не смог открыть вкладку через CDP HTTP: {e}")


def ensure_cdp_chrome_running(
    port: int = CDP_PORT,
    profile_dir: Path | None = None,
    *,
    use_default_profile: bool = False,
) -> None:
    """Запустить Chrome с remote debugging, если порт ещё не слушает."""
    if _port_open("127.0.0.1", port):
        logger.info(f"CDP: порт {port} уже открыт — Chrome слушает")
        _ensure_chrome_has_tab(port)
        return

    chrome = _chrome_executable_path()
    if not chrome:
        raise RuntimeError("Google Chrome не найден в /Applications")

    args = [
        chrome,
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1366,820",
    ]
    if use_default_profile:
        logger.warning(
            "CDP: запускаю Chrome с ОСНОВНЫМ профилем. "
            "Сначала полностью закрой Chrome (Cmd+Q), иначе порт не поднимется."
        )
    else:
        udd = profile_dir or CDP_PROFILE_DIR
        udd.mkdir(parents=True, exist_ok=True)
        args.append(f"--user-data-dir={udd}")
        logger.info(f"CDP: запускаю Chrome (профиль {udd})")

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        CHROME_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHROME_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except Exception as e:
        logger.debug(f"chrome pid file: {e}")
    for _ in range(40):
        if _port_open("127.0.0.1", port):
            logger.success(f"CDP: Chrome готов на порту {port}")
            time.sleep(1)
            _ensure_chrome_has_tab(port)
            return
        time.sleep(0.25)
    raise RuntimeError(
        f"Chrome не поднял CDP на порту {port}. "
        "Закрой все окна Chrome и повтори: python main.py chrome"
    )


def _make_cdp_driver(
    endpoint: str,
    *,
    use_default_profile: bool = False,
) -> webdriver.Chrome:
    parsed = urlparse(endpoint)
    port = parsed.port or CDP_PORT
    host = parsed.hostname or "127.0.0.1"
    ensure_cdp_chrome_running(port=port, use_default_profile=use_default_profile)

    opts = ChromeOptions()
    opts.add_experimental_option("debuggerAddress", f"{host}:{port}")
    opts.page_load_strategy = "eager"

    service = ChromeService(ChromeDriverManager().install())
    logger.info(f"Selenium: attach к Chrome через CDP {host}:{port}")
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(45)
    logger.success("Selenium-CDP: подключение установлено")
    return driver


def _make_undetected_driver(
    *,
    headless: bool,
    user_agent: str | None,
    user_data_dir: Path | None,
) -> webdriver.Chrome:
    import undetected_chromedriver as uc

    opts = uc.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--lang=ru-RU")
    opts.add_argument("--window-size=1366,820")
    chrome = _chrome_executable_path()
    if chrome:
        opts.binary_location = chrome
    if user_agent:
        opts.add_argument(f"--user-agent={user_agent}")
    opts.page_load_strategy = "eager"

    udd = user_data_dir or Path(".browser-profile").absolute()
    udd.mkdir(parents=True, exist_ok=True)
    opts.add_argument(f"--user-data-dir={udd}")

    chrome_version = detect_chrome_version()
    try:
        version_main = int(chrome_version.split(".", 1)[0])
    except Exception:
        version_main = None
    logger.info(
        "Selenium: undetected_chromedriver "
        f"(Chrome {chrome_version}, driver major {version_main or 'auto'})"
    )
    driver = uc.Chrome(
        options=opts,
        headless=headless,
        use_subprocess=True,
        version_main=version_main,
    )
    driver.set_page_load_timeout(45)
    return driver


def _detach_undetected_driver(driver: webdriver.Chrome) -> None:
    """Detach WebDriver while leaving the browser window alive."""
    try:
        proc = getattr(driver.service, "process", None)
        if proc:
            proc.kill()
        driver.service.process = None
    except Exception:
        pass
    try:
        driver.reactor.event.set()
    except Exception:
        pass
    try:
        driver.browser_pid = None
    except Exception:
        pass
    _DETACHED_UNDETECTED_DRIVERS.append(driver)
    logger.success("Selenium: окно Chrome оставлено открытым")


def _open_cdp_work_tab(driver: webdriver.Chrome) -> str:
    """Открыть отдельную вкладку под парсер; не трогать вкладки пользователя."""
    before = set(driver.window_handles)
    try:
        driver.switch_to.new_window("tab")
    except WebDriverException:
        pass
    after = set(driver.window_handles)
    new_handles = after - before
    if new_handles:
        handle = new_handles.pop()
        driver.switch_to.window(handle)
        return handle
    if after:
        handle = list(after)[-1]
        driver.switch_to.window(handle)
        return handle
    raise RuntimeError("CDP: не удалось открыть рабочую вкладку")


def _close_cdp_work_tab(driver: webdriver.Chrome, handle: str | None) -> None:
    """Закрыть только нашу вкладку; Chrome и другие вкладки остаются."""
    if not handle:
        return
    try:
        handles = driver.window_handles
    except Exception:
        return
    if handle not in handles:
        return
    try:
        driver.switch_to.window(handle)
        driver.close()
    except Exception:
        pass
    try:
        if driver.window_handles:
            driver.switch_to.window(driver.window_handles[-1])
    except Exception:
        pass


def prompt_manual_landing(driver: webdriver.Chrome, tracker_url: str) -> bool:
    """Пользователь сам открывает Web-Zaim — парсер подхватывает вкладку."""
    from src.stdin_lock import STDIN_LOCK

    logger.info("=" * 72)
    logger.info("  ОТКРОЙ САЙТ ВРУЧНУЮ (в Chrome от «python main.py chrome»)")
    logger.info(f"  1) Cmd+L → вставь: {tracker_url}")
    logger.info("  2) Enter — дождись калькулятора займа (НЕ страница 403)")
    logger.info("  3) Вернись в терминал и нажми Enter — парсер заполнит форму")
    logger.info("=" * 72)
    try:
        with STDIN_LOCK:
            input()
    except EOFError:
        return False
    switch_to_active_tab(driver)
    url = find_healthy_webzaim_tab(driver)
    if url:
        logger.success(f"подхватил вкладку: {url[:85]}…")
        return True
    logger.error(
        "не нашёл web-zaim.ru без 403. Открой ссылку в ЭТОМ Chrome (не в другом окне)."
    )
    return False


@contextmanager
def open_browser(
    *,
    mode: BrowserMode = "cdp",
    cdp_endpoint: str = "http://127.0.0.1:9222",
    headless: bool = False,
    user_agent: str | None = None,
    user_data_dir: Path | None = None,
    use_default_profile: bool = False,
    open_new_tab: bool = True,
    keep_open: bool = False,
) -> Iterator[webdriver.Chrome]:
    if mode == "cdp":
        driver = _make_cdp_driver(
            cdp_endpoint, use_default_profile=use_default_profile
        )
        work_handle: str | None = None
        try:
            if open_new_tab:
                work_handle = _open_cdp_work_tab(driver)
                logger.info(f"CDP: рабочая вкладка {work_handle[:8]}…")
            else:
                switch_to_active_tab(driver)
                logger.info("CDP: новую вкладку не открываю — работаю в твоих")
            yield driver
        finally:
            if open_new_tab and not keep_open:
                _close_cdp_work_tab(driver, work_handle)
            elif open_new_tab and keep_open:
                logger.success("CDP: рабочая вкладка оставлена открытой")
            try:
                driver.quit()  # отсоединяем WebDriver, Chrome не закрываем
            except Exception:
                pass
    else:
        driver = _make_undetected_driver(
            headless=headless, user_agent=user_agent, user_data_dir=user_data_dir
        )
        try:
            yield driver
        finally:
            if keep_open:
                _detach_undetected_driver(driver)
            else:
                try:
                    driver.quit()
                except Exception:
                    pass


def switch_to_active_tab(driver: webdriver.Chrome) -> bool:
    """Переключиться на живую вкладку, если текущая закрыта."""
    try:
        _ = driver.current_url
        return True
    except (NoSuchWindowException, WebDriverException):
        pass
    try:
        handles = driver.window_handles
    except Exception:
        return False
    if not handles:
        return recover_browser_window(driver)
    for handle in reversed(handles):
        try:
            driver.switch_to.window(handle)
            _ = driver.current_url
            return True
        except Exception:
            continue
    return recover_browser_window(driver)


def recover_browser_window(driver: webdriver.Chrome) -> bool:
    """Открыть новую вкладку, если все handle'ы мёртвые."""
    try:
        driver.switch_to.new_window("tab")
        logger.warning("браузер: открыта новая вкладка (предыдущая была закрыта)")
        return True
    except Exception as e:
        logger.error(f"не удалось восстановить вкладку: {e}")
        return False


def safe_current_url(driver: webdriver.Chrome, default: str = "") -> str:
    if not switch_to_active_tab(driver):
        return default
    try:
        return driver.current_url or default
    except Exception:
        return default


def page_is_blocked(driver: webdriver.Chrome) -> bool:
    """403 / Forbidden / Cloudflare block."""
    if not switch_to_active_tab(driver):
        return False
    try:
        title = (driver.title or "").lower()
        body = driver.page_source.lower()[:80000]
    except Exception:
        return False
    if "403" in title or "forbidden" in title or "403 forbidden" in body:
        return True
    if "guru meditation" in body and "403" in body:
        return True
    return "доступ к сайту" in body and "запрещен" in body


def page_is_waiting_for_sms_code(driver: webdriver.Chrome) -> bool:
    """Web-Zaim OTP page from a previous registration attempt."""
    if not switch_to_active_tab(driver):
        return False
    try:
        body = (driver.execute_script("return document.body.innerText") or "").lower()
    except Exception:
        return False
    return "отправлено sms с кодом" in body or "введите полученный код" in body


def find_healthy_webzaim_tab(driver: webdriver.Chrome) -> str | None:
    """Вкладка web-zaim.ru без 403 (уже открыта пользователем)."""
    try:
        handles = list(driver.window_handles)
    except Exception:
        return None
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            url = safe_current_url(driver)
            if "web-zaim.ru" not in url.lower():
                continue
            if not page_is_blocked(driver):
                if page_is_waiting_for_sms_code(driver):
                    logger.warning(
                        "нашёл старую вкладку Web-Zaim с ожиданием SMS-кода — "
                        "пропускаю, чтобы не переиспользовать прошлый номер"
                    )
                    continue
                logger.info(f"найдена живая вкладка Web-Zaim: {url[:70]}…")
                return url
        except Exception:
            continue
    return None


def wait_page_unblocked(driver: webdriver.Chrome, timeout: float = 8.0) -> bool:
    """Подождать, пока SPA отрисует страницу (иногда 403 появляется с задержкой)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not page_is_blocked(driver):
            return True
        time.sleep(0.4)
    return not page_is_blocked(driver)


def _reload_current(driver: webdriver.Chrome) -> None:
    try:
        driver.refresh()
        time.sleep(2.5)
    except Exception as e:
        logger.debug(f"reload: {e}")


def prompt_manual_recovery(driver: webdriver.Chrome, tracker_url: str) -> bool:
    """Пользователь вручную открывает Web-Zaim в том же Chrome."""
    logger.warning("=" * 72)
    logger.warning("  403 Forbidden — Web-Zaim блокирует IP (часто из-за VPN)")
    logger.warning("=" * 72)
    logger.info("Сделай в ЭТОМ окне Chrome:")
    logger.info("  1) ВЫКЛЮЧИ VPN или выбери сервер РФ / мобильный интернет")
    logger.info("  2) Закрой вкладку с «403 Error»")
    logger.info(f"  3) Вручную открой: {tracker_url}")
    logger.info("     (или https://web-zaim.ru/installment)")
    logger.info("  4) Убедись, что видишь калькулятор займа, а НЕ «403 Forbidden»")
    logger.info("  5) Вернись в терминал и нажми Enter")
    logger.warning("=" * 72)
    from src.stdin_lock import STDIN_LOCK

    try:
        with STDIN_LOCK:
            input()
    except EOFError:
        return False
    switch_to_active_tab(driver)
    healthy = find_healthy_webzaim_tab(driver)
    if healthy:
        return True
    return wait_page_unblocked(driver, timeout=5.0)


def recover_from_403(
    driver: webdriver.Chrome,
    tracker_url: str,
    *,
    allow_manual: bool = True,
) -> bool:
    """Попытки обойти 403: другая вкладка → reload → ручной режим."""
    healthy = find_healthy_webzaim_tab(driver)
    if healthy:
        return True

    logger.info("403: пробую обновить страницу…")
    _reload_current(driver)
    if wait_page_unblocked(driver, timeout=6.0):
        return True

    logger.info("403: повторный переход по партнёрской ссылке…")
    try:
        driver.get(tracker_url)
        time.sleep(3)
    except Exception:
        pass
    if wait_page_unblocked(driver, timeout=8.0):
        return True

    if allow_manual:
        return prompt_manual_recovery(driver, tracker_url)
    return False


def human_pause(min_s: float = 0.4, max_s: float = 1.2) -> None:
    import random

    time.sleep(random.uniform(min_s, max_s))


def wait_for(driver: webdriver.Chrome, css: str, timeout: float = 10):
    return WebDriverWait(driver, timeout).until(
        lambda d: d.find_element(By.CSS_SELECTOR, css)
    )
