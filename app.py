"""Streamlit-интерфейс к парсеру МФО.

Запуск:
    streamlit run app.py
или просто:
    ./gui.sh
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


ROOT = Path(__file__).parent.resolve()
VENV_PY = ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PY) if VENV_PY.exists() else sys.executable

DATA_DIR = ROOT / "data"
LOG_FILE = DATA_DIR / "run.log"
XLSX_FILE = DATA_DIR / "sms_log.xlsx"
CSV_FILE = DATA_DIR / "sms_log.csv"
DB_FILE = DATA_DIR / "sms_log.db"
CHROME_PID_FILE = DATA_DIR / ".chrome.pid"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"

TRACKER_URL = "https://t.leads.tech/click/8/330/?sub1=bizdev&sub2=Name_vacancy"
DEFAULT_PHONE = "+79160651766"


def _env_default_phone() -> str:
    """Берём дефолтный телефон из .env, без побочных эффектов на settings."""
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text("utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("MANUAL_PHONE="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                return val
    return DEFAULT_PHONE


def normalize_phone(raw: str) -> str | None:
    """Привести к виду +7XXXXXXXXXX или вернуть None если непохоже на номер."""
    if not raw:
        return None
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("7"):
        return None
    return "+" + digits


st.set_page_config(
    page_title="Парсер МФО — Гидфинанс",
    page_icon="💸",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def ss():
    return st.session_state


def _init_state():
    for key, default in [
        ("run_proc", None),
        ("last_sent", []),
        ("sms_mode", "iPhone Forward SMS (SMS авто)"),
        ("phone_number", _env_default_phone()),
        ("listen_minutes", 720),
    ]:
        if key not in ss():
            ss()[key] = default


_init_state()


def proc_alive(p) -> bool:
    return p is not None and p.poll() is None


def webhook_mode_enabled() -> bool:
    return ss().sms_mode != "Ручной ввод в интерфейсе"


def android_mode_enabled() -> bool:
    return ss().sms_mode.startswith("Android")


CDP_PORT = 9222


def _port_listening(port: int = CDP_PORT) -> bool:
    """Самая надёжная проверка: реально ли CDP-порт принимает соединения."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def chrome_alive() -> bool:
    return _port_listening()


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _chrome_app_running() -> bool:
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "Google Chrome.app/Contents/MacOS/Google Chrome"],
            text=True,
        )
        return any(line.strip().isdigit() for line in out.splitlines())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _force_quit_chrome() -> None:
    subprocess.run(
        ["osascript", "-e", 'tell application "Google Chrome" to quit'],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    for _ in range(20):
        if not _chrome_app_running():
            break
        time.sleep(0.3)
    if _chrome_app_running():
        subprocess.run(
            ["pkill", "-9", "-f", "Google Chrome"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        time.sleep(2)
    try:
        CHROME_PID_FILE.unlink()
    except FileNotFoundError:
        pass


def _open_in_running_chrome(url: str) -> bool:
    """Открыть URL в CDP-Chrome. Fallback — обычный open."""
    try:
        cdp_url = "http://127.0.0.1:9222/json/new?" + urllib.parse.quote(url, safe="")
        req = urllib.request.Request(cdp_url, method="PUT")
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception:
        pass
    try:
        res = subprocess.run(
            ["open", "-a", "Google Chrome", url],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def start_chrome():
    if _port_listening():
        st.toast("CDP-Chrome уже работает", icon="✅")
        return

    _force_quit_chrome()

    res = subprocess.run(
        [PYTHON, "main.py", "chrome"],
        cwd=ROOT,
        env={**os.environ, "CDP_USE_DEFAULT_PROFILE": "false"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    for _ in range(40):
        if _port_listening():
            break
        time.sleep(0.5)

    if not _port_listening():
        st.error(
            "Chrome не открыл CDP-порт 9222. "
            "Закрой все окна Chrome вручную и нажми ещё раз.\n\n"
            f"{(res.stderr or res.stdout)[:500]}"
        )
        return

    st.toast("Chrome готов с CDP :9222", icon="✅")
    time.sleep(1)
    if _open_in_running_chrome(TRACKER_URL):
        st.toast("Открыл партнёрскую ссылку", icon="🌐")


def kill_chrome():
    subprocess.run(
        [PYTHON, "main.py", "kill-chrome"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    _force_quit_chrome()
    st.toast("Chrome закрыт", icon="🛑")


def open_mfo_url():
    if not chrome_alive():
        st.error("CDP-Chrome не запущен. Нажми «Перезапустить Chrome для парсера».")
        return
    if _open_in_running_chrome(TRACKER_URL):
        st.toast("Ссылка открыта в Chrome. Дождись страницы калькулятора.", icon="🌐")
    else:
        st.error("Не удалось открыть ссылку через open(1). Попробуй ещё раз.")


def start_run():
    phone = normalize_phone(ss().phone_number)
    if not phone:
        st.error(
            "Введи номер телефона в формате +7XXXXXXXXXX (Шаг 2) — "
            "парсер подставит его в анкету и будет ждать на него SMS."
        )
        return
    ss().phone_number = phone
    listen_minutes = int(ss().listen_minutes or 720)
    use_webhook = webhook_mode_enabled()

    if use_webhook and _port_listening(8765):
        st.error(
            "Порт 8765 уже занят старым запуском парсера. "
            "Нажми «Остановить парсер» или перезапусти интерфейс, затем попробуй снова."
        )
        return

    env = {
        **os.environ,
        "CDP_USE_DEFAULT_PROFILE": "false",
        "MANUAL_PHONE": phone,
        "WEBHOOK_PHONE": phone,
        "SMS_PROVIDER": "webhook" if use_webhook else "manual",
        "CALL_PROVIDER": "webhook" if use_webhook else "off",
        "WEBHOOK_HOST": "0.0.0.0",
        "WEBHOOK_PORT": "8765",
        "SMS_LISTEN_MINUTES": str(listen_minutes),
        "BROWSER_MODE": "cdp",
        "CDP_ENDPOINT": "http://127.0.0.1:9222",
        # GUI cannot answer terminal prompts. Reuse an already-open Web-Zaim tab
        # if present; otherwise open the tracker automatically and fail fast on 403.
        "BROWSER_LANDING": "auto",
        "BROWSER_MANUAL_RECOVERY": "false",
        "KEEP_BROWSER_OPEN": "false",
        "CLOSE_BROWSER_AFTER_SUBMIT": "true",
    }
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    child_log = LOG_FILE.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        [PYTHON, "-u", "main.py", "run"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=child_log,
        stderr=child_log,
        text=True,
        bufsize=1,
    )
    ss().run_proc = proc
    ss().last_sent = []
    st.toast(f"Парсер запущен с номером {phone}", icon="▶")


def stop_run(send_stop: bool = True):
    p = ss().run_proc
    if not p:
        return
    if send_stop and p.poll() is None:
        try:
            p.stdin.write(".stop\n")
            p.stdin.flush()
        except Exception:
            pass
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
    ss().run_proc = None
    st.toast("Парсер остановлен", icon="⏹")


def send_line(line: str, label: str | None = None) -> bool:
    p = ss().run_proc
    if not p or p.poll() is not None:
        st.error("Парсер не запущен — нажми «Запустить парсер» (шаг 2)")
        return False
    if not line.endswith("\n"):
        line += "\n"
    try:
        p.stdin.write(line)
        p.stdin.flush()
    except Exception as e:
        st.error(f"Не удалось отправить в парсер: {e}")
        return False
    ss().last_sent.insert(0, (datetime.now().strftime("%H:%M:%S"), label or line.rstrip()))
    ss().last_sent = ss().last_sent[:5]
    return True


def read_xlsx_sheet(sheet: str) -> pd.DataFrame | None:
    if not XLSX_FILE.exists():
        return None
    try:
        df = pd.read_excel(XLSX_FILE, sheet_name=sheet)
        return df
    except Exception:
        return None


def tail_log(n_bytes: int = 7000) -> str:
    if not LOG_FILE.exists():
        return ""
    try:
        size = LOG_FILE.stat().st_size
        with LOG_FILE.open("rb") as f:
            f.seek(max(0, size - n_bytes))
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except Exception as e:
        return f"(не смог прочитать лог: {e})"


def parser_phase(log_text: str, *, running: bool) -> str:
    if not running:
        return "—"
    marker = "СТАРТ парсера МФО"
    current = log_text.rsplit(marker, 1)[-1] if marker in log_text else log_text
    if "теперь слушаю SMS + звонки" in current or "Chrome больше не нужен" in current:
        return "слушает SMS/звонки"
    if "код" in current and "вписан" in current:
        return "завершает браузер"
    if "Жду SMS на телефон" in current or "Жду SMS" in current:
        return "ждёт OTP"
    if "заполнено полей" in current or "Продолжить" in current:
        return "заполняет заявку"
    if "Selenium" in current or "Webhook слушает" in current:
        return "стартует"
    return "работает"


def clear_table() -> None:
    """Удалить sms_log.xlsx/csv/db и подчистить лог."""
    if proc_alive(ss().run_proc):
        st.error("Сначала остановите парсер (⏹), потом очищайте таблицу.")
        return
    removed = []
    for path in (XLSX_FILE, CSV_FILE, DB_FILE):
        if path.exists():
            try:
                path.unlink()
                removed.append(path.name)
            except Exception as e:
                st.warning(f"Не смог удалить {path.name}: {e}")
    if LOG_FILE.exists():
        try:
            LOG_FILE.write_text("", encoding="utf-8")
        except Exception:
            pass
    if SCREENSHOTS_DIR.exists():
        for shot in SCREENSHOTS_DIR.glob("*.png"):
            try:
                shot.unlink()
            except Exception:
                pass
    st.toast(
        f"Таблица очищена ({', '.join(removed) or 'файлов не было'})",
        icon="🧹",
    )


def last_screenshot() -> Path | None:
    if not SCREENSHOTS_DIR.exists():
        return None
    images = sorted(SCREENSHOTS_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return images[0] if images else None


st.markdown(
    """
    <style>
      .main .block-container { padding-top: 2rem; max-width: 1200px; }
      div[data-testid="stMetric"] { background: #0e1117; padding: 12px; border-radius: 10px; }
      .stButton button { font-weight: 600; }
      .step { background: rgba(120,120,140,0.07); padding: 18px; border-radius: 12px; margin-bottom: 14px; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("💸 Парсер МФО — Гидфинанс")
st.caption(f"Партнёрская ссылка: `{TRACKER_URL}`")


is_chrome = chrome_alive()
is_run = proc_alive(ss().run_proc)
sms_df_now = read_xlsx_sheet("sms")
calls_df_now = read_xlsx_sheet("calls")
log_text_now = tail_log()
phase_now = parser_phase(log_text_now, running=is_run)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Chrome (CDP)", "✓ работает" if is_chrome else "—")
m2.metric("Этап", phase_now)
m3.metric("SMS в таблице", 0 if sms_df_now is None else len(sms_df_now))
m4.metric("Звонков", 0 if calls_df_now is None else len(calls_df_now))

if is_run and phase_now == "слушает SMS/звонки":
    st.success(
        "Браузерный этап завершён. Парсер сейчас только слушает входящие SMS и звонки; "
        "Chrome можно закрыть, сбор продолжится через Forward SMS / Cloudflare."
    )
elif is_run and phase_now == "ждёт OTP":
    st.info(
        "Парсер ждёт SMS-код от Web-Zaim. Как только Forward SMS пришлёт его на `/sms`, "
        "код будет введён автоматически."
    )


st.markdown("### Шаг 1. Подготовь браузер")
with st.container():
    c1, c2, c3 = st.columns(3)
    with c1:
        st.button(
            "▶ Запустить Chrome + открыть страницу МФО",
            type="primary",
            use_container_width=True,
            disabled=is_chrome,
            on_click=start_chrome,
            help=(
                "Закрывает обычный Chrome, поднимает его с remote-debugging :9222 "
                "и сразу открывает партнёрскую ссылку."
            ),
        )
    with c2:
        st.button(
            "🌐 Открыть страницу МФО",
            use_container_width=True,
            disabled=not is_chrome,
            on_click=open_mfo_url,
            help="Откроет партнёрскую ссылку в том же Chrome. Дождись калькулятора займа.",
        )
    with c3:
        st.button(
            "🛑 Закрыть Chrome",
            use_container_width=True,
            disabled=not is_chrome,
            on_click=kill_chrome,
        )
    st.caption(
        "Интерфейс в Safari. Chrome парсера — отдельный профиль `~/mfo-chrome-profile`, "
        "окно открывается на экране."
    )


st.markdown("### Шаг 2. Введи номер телефона и запусти парсер")
with st.container():
    c_phone, c_run = st.columns([1, 1])
    with c_phone:
        st.radio(
            "Источник SMS и звонков",
            options=(
                "iPhone Forward SMS (SMS авто)",
                "Android SMS + звонки (webhook)",
                "Ручной ввод в интерфейсе",
            ),
            key="sms_mode",
            horizontal=False,
            disabled=is_run,
            help=(
                "iPhone: SMS через Forward SMS. "
                "Android: SMS и call log через webhook-приложение. "
                "Ручной режим: SMS вставляются в Шаге 3."
            ),
        )
        st.text_input(
            "Номер телефона для заявки",
            key="phone_number",
            placeholder=DEFAULT_PHONE,
            help=(
                "Этот номер парсер подставит в анкету Web-Zaim. "
                "На него же должна прийти SMS с кодом — её ты потом вставишь в Шаге 3. "
                "Можно вводить как +7XXXXXXXXXX, 8XXXXXXXXXX или 9XXXXXXXXX."
            ),
            disabled=is_run,
        )
        norm = normalize_phone(ss().phone_number) if ss().phone_number else None
        if ss().phone_number and not norm:
            st.warning(f"Номер некорректный. Пример: {DEFAULT_PHONE}")
        elif norm and norm != ss().phone_number:
            st.caption(f"Будет использовано: `{norm}`")
        st.number_input(
            "Сколько минут слушать после заявки",
            min_value=15,
            max_value=1440,
            step=15,
            key="listen_minutes",
            disabled=is_run,
            help="720 минут = 12 часов, 1440 минут = сутки. Mac должен не засыпать.",
        )
        if webhook_mode_enabled():
            base = f"http://{local_ip()}:8765"
            if android_mode_enabled():
                st.info(
                    "На Android скачай `MacroDroid - Device Automation`.\n\n"
                    "В MacroDroid укажи:\n\n"
                    f"- SMS: `{base}/sms`\n"
                    f"- Звонки: `{base}/call`\n\n"
                    "Отдельное приложение для SMS не нужно: MacroDroid может отправлять "
                    "и SMS-события, и события входящих звонков.\n\n"
                    "Android и Mac должны быть в одной Wi-Fi сети или используй Cloudflare URL."
                )
            else:
                st.info(
                    "На iPhone скачай `Forward SMS`.\n\n"
                    "В Forward SMS укажи:\n\n"
                    f"- SMS: `{base}/sms`\n\n"
                    "iPhone и Mac должны быть в одной Wi-Fi сети или используй Cloudflare URL. "
                    "Звонки с iPhone автоматически не передаются из-за ограничений iOS."
                )
    with c_run:
        st.markdown("&nbsp;")
        st.button(
            "▶ Запустить парсер (заполнить заявку)",
            type="primary",
            use_container_width=True,
            disabled=not is_chrome or is_run or not norm,
            on_click=start_run,
            help=(
                "Парсер подхватит вкладку Web-Zaim, заполнит анкету фейк-персоной "
                "с указанным номером и нажмёт «Продолжить»."
            ),
        )


st.markdown("### Шаг 3. SMS и звонки")
with st.container():
    if webhook_mode_enabled():
        base = f"http://{local_ip()}:8765"
        if android_mode_enabled():
            st.success(
                "Android webhook-режим: SMS и события звонков приходят в парсер сами. "
                "После OTP парсер введёт код в форму и продолжит собирать SMS/звонки."
            )
        else:
            st.success(
                "iPhone webhook-режим: SMS приходят в парсер через Forward SMS. "
                "После OTP парсер введёт код в форму и продолжит собирать следующие SMS."
            )
        st.code(
            f"""SMS URL:   {base}/sms
CALL URL:  {base}/call

Пример JSON для SMS:
{{"sender":"WEBZAIM","text":"Код 1234 https://example.ru","timestamp":1710000000000}}

Пример JSON для звонка:
{{"caller":"+79837210053","duration_sec":25,"status":"answered","direction":"incoming","timestamp":1710000000000}}

Минимальный JSON для звонка:
{{"number":"+79837210053","timestamp":"2026-05-24T09:20:00+03:00"}}""",
            language="text",
        )
        if android_mode_enabled():
            st.caption(
                "Рекомендуемое Android-приложение: MacroDroid. В нём делаются два макроса: "
                "`SMS Received -> HTTP Request POST /sms` и "
                "`Call Incoming -> HTTP Request POST /call`. Для звонков передавай "
                "`number`/`caller`, `timestamp`/`date`, `duration` и `type`/`status`."
            )
        else:
            st.caption(
                "Если Forward SMS присылает отправителя как SIM 1, парсер достанет "
                "альфа-имя из хвоста сообщения вида `@web-zaim.ru #1234 (23.05.2026 21:38)`."
            )
    else:
        c_text, c_sender = st.columns([3, 1])
        with c_text:
            sms_text = st.text_area(
                "Текст SMS (полный, не только код)",
                key="sms_text",
                placeholder="например: Код для регистрации: 7877 в сервисе web-zaim.ru",
                height=110,
            )
        with c_sender:
            sender = st.text_input("Альфа-имя", value="web-zaim.ru", key="sms_sender")
            if st.button(
                "✉ Отправить SMS в парсер",
                type="primary",
                use_container_width=True,
                disabled=not is_run,
            ):
                # stdin у manual-провайдера читает построчно. Если пользователь
                # вставил SMS в 2 строки, склеиваем его обратно в одно событие.
                text = " ".join((sms_text or "").split())
                if not text:
                    st.warning("Пустой текст")
                else:
                    line = f"{sender}|{text}" if sender else text
                    if send_line(line, label=f"SMS: {text[:50]}"):
                        st.toast(
                            "SMS отправлена парсеру. Код будет введён в браузере.",
                            icon="✉",
                        )


st.markdown("### Шаг 4. Завершить и забрать результат")
with st.container():
    c1, c2, c3 = st.columns(3)
    with c1:
        st.button(
            "⏹ Остановить парсер",
            type="primary" if is_run else "secondary",
            use_container_width=True,
            disabled=not is_run,
            on_click=stop_run,
            help="Завершает прослушивание SMS/звонков и закрывает Selenium-сессию.",
        )
    with c2:
        st.button(
            "🛑 Закрыть Chrome",
            use_container_width=True,
            disabled=not is_chrome,
            on_click=kill_chrome,
            key="kill_chrome_step4",
        )
    with c3:
        if XLSX_FILE.exists():
            with XLSX_FILE.open("rb") as f:
                st.download_button(
                    "⬇ Скачать sms_log.xlsx",
                    data=f.read(),
                    file_name="sms_log.xlsx",
                    use_container_width=True,
                    key="dl_xlsx_step4",
                )
        else:
            st.button(
                "⬇ Скачать sms_log.xlsx",
                use_container_width=True,
                disabled=True,
                key="dl_xlsx_step4_disabled",
            )
    st.caption("После «Остановить парсер» данные уже сохранены в `data/sms_log.xlsx`.")


st.divider()


tab_table, tab_log, tab_shot = st.tabs(["📊 Таблица (ТЗ)", "📋 Лог парсера", "🖼 Скриншот"])


with tab_table:
    analytics_df = read_xlsx_sheet("Analytics")
    if analytics_df is not None and len(analytics_df):
        st.caption("Лист «Analytics» — итоги, сообщения по дням, отправители и домены.")
        st.dataframe(analytics_df, use_container_width=True, hide_index=True)

    sms_df = read_xlsx_sheet("sms")
    if sms_df is None:
        st.info("Файл sms_log.xlsx ещё не создан. Запусти парсер и пришли SMS.")
    else:
        st.caption("Лист «sms» — ровно 4 колонки из тестового задания.")
        st.dataframe(sms_df, use_container_width=True, hide_index=True)

    links_df = read_xlsx_sheet("Links")
    if links_df is not None and len(links_df):
        st.caption("Лист «Links» — найденные ссылки и финальные URL после редиректов.")
        st.dataframe(links_df, use_container_width=True, hide_index=True)

    alphas_df = read_xlsx_sheet("Alphas")
    if alphas_df is not None and len(alphas_df):
        st.caption("Лист «Alphas» — сводка по альфа-именам отправителей.")
        st.dataframe(alphas_df, use_container_width=True, hide_index=True)

    calls_df = read_xlsx_sheet("calls")
    if calls_df is not None and len(calls_df):
        st.caption("Лист «calls» — входящие звонки.")
        st.dataframe(calls_df, use_container_width=True, hide_index=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        if XLSX_FILE.exists():
            with XLSX_FILE.open("rb") as f:
                st.download_button(
                    "⬇ Скачать sms_log.xlsx",
                    data=f.read(),
                    file_name="sms_log.xlsx",
                    use_container_width=True,
                )
    with c2:
        if XLSX_FILE.exists() and st.button("📂 Открыть в Excel", use_container_width=True):
            subprocess.run(["open", str(XLSX_FILE)], check=False)
    with c3:
        st.button(
            "🧹 Очистить таблицу",
            use_container_width=True,
            disabled=proc_alive(ss().run_proc),
            on_click=clear_table,
            help="Удаляет sms_log.xlsx/csv/db, лог и скриншоты. Парсер должен быть остановлен.",
        )


with tab_log:
    log_text = tail_log()
    if log_text:
        st.code(log_text, language="text")
    else:
        st.info("Лог пуст — запусти парсер, чтобы увидеть процесс.")
    if ss().last_sent:
        st.markdown("**Последние действия из интерфейса:**")
        for ts, label in ss().last_sent:
            st.markdown(f"- `{ts}` — {label}")


with tab_shot:
    shot = last_screenshot()
    if shot is None:
        st.info("Скриншотов пока нет. Они появятся после запуска парсера.")
    else:
        st.caption(f"Последний скриншот: `{shot.name}` ({datetime.fromtimestamp(shot.stat().st_mtime):%H:%M:%S})")
        st.image(str(shot), use_container_width=True)


st.divider()
left, right = st.columns([1, 3])
with left:
    if st.button("Обновить данные", use_container_width=True):
        st.rerun()
with right:
    st.caption(
        "Совет: если на сайте Web-Zaim увидишь 403 — выключи VPN, "
        "обнови страницу в Chrome и снова нажми «Запустить парсер»."
    )


if is_run or is_chrome:
    time.sleep(2)
    st.rerun()
