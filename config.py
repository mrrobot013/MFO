"""Загрузка конфига из .env через pydantic-settings."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def normalize_env_quotes(cls, data: Any) -> Any:
        """Tolerate cmd.exe values like set NAME='value'."""
        if not isinstance(data, dict):
            return data
        return {key: cls._strip_outer_quotes(value) for key, value in data.items()}

    @staticmethod
    def _strip_outer_quotes(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            return value[1:-1]
        return value

    tracker_url: str = "https://t.leads.tech/click/8/330/?sub1=bizdev&sub2=Name_vacancy"

    sms_provider: Literal["onlinesim", "manual", "webhook"] = "manual"
    sms_listen_minutes: int = 15
    sms_poll_interval: int = 5

    onlinesim_api_key: str = ""

    manual_phone: str = ""
    manual_default_sender: str = "web-zaim.ru"

    call_provider: Literal["zadarma", "mock", "webhook", "off"] = "off"
    zadarma_api_key: str = ""
    zadarma_api_secret: str = ""
    zadarma_virtual_number: str = ""

    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8765
    webhook_token: str = ""
    webhook_phone: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    loan_amount: int = 15000
    loan_term_days: int = 14
    fake_email_domain: str = "mail.ru"

    headless: bool = False
    slow_mo_ms: int = 0  # оставлено для обратной совместимости с CLI
    # cdp        — Selenium attach к твоему Chrome на :9222 (главный режим против 403);
    # undetected — undetected_chromedriver (fallback на свежий Chrome).
    browser_mode: Literal["cdp", "undetected"] = "cdp"
    cdp_endpoint: str = "http://127.0.0.1:9222"
    # false = отдельный профиль парсера; на новых Chrome основной профиль
    # часто не поднимает remote-debugging-port вообще.
    cdp_use_default_profile: bool = False
    # manual = ты сам открываешь ссылку в Chrome, парсер только заполняет форму.
    # auto   = парсер сам делает driver.get (часто ловит 403).
    browser_landing: Literal["auto", "manual"] = "manual"
    # При 403 — пауза и ручное открытие сайта в том же Chrome (Enter для продолжения).
    browser_manual_recovery: bool = True
    # true = заполнить форму и сделать скриншоты, но НЕ нажимать «Продолжить».
    dry_run_no_submit: bool = False
    # true = после отправки SMS-кода не закрывать окно/вкладку Selenium.
    keep_browser_open: bool = True
    # true = после успешного OTP закрыть Chrome и оставить только сбор SMS/звонков.
    close_browser_after_submit: bool = False
    # Пусто = авто User-Agent из установленного Google Chrome (desktop).
    user_agent: str = ""

    output_dir: Path = Field(default=Path("./data"))
    db_path: Path = Field(default=Path("./data/sms_log.db"))
    xlsx_path: Path = Field(default=Path("./data/sms_log.xlsx"))
    csv_path: Path = Field(default=Path("./data/sms_log.csv"))

    log_level: str = "INFO"

    def ensure_dirs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "screenshots").mkdir(exist_ok=True)
        (self.output_dir / "traces").mkdir(exist_ok=True)


settings = Settings()
settings.ensure_dirs()
