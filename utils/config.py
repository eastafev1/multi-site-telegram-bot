from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: str, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    nazar_domain: str
    nazar_login_url: str
    nazar_products_url: str
    nazar_login: str
    nazar_password: str
    telegram_bot_token: str
    telegram_chat_id: int
    run_mode: str
    country: str
    products_page_size: int
    scan_interval_sec: int
    request_timeout_sec: int
    max_retries: int
    playwright_headless: bool
    log_level: str
    state_file: Path
    amazstar_domain: str
    amazstar_login_url: str
    amazstar_products_url: str
    amazstar_login: str
    amazstar_password: str
    amazstar_check_interval_sec: int
    amazstar_products_page_size: int
    amazstar_run_mode: str


def load_settings(env_file: str = "config.env") -> Settings:
    load_dotenv(env_file)
    state_path = Path(os.getenv("STATE_FILE", "data/state.json"))
    if not state_path.is_absolute():
        state_path = Path.cwd() / state_path

    return Settings(
        nazar_domain=os.getenv("NAZAR_DOMAIN", "https://nazar.gift").rstrip("/"),
        nazar_login_url=os.getenv("NAZAR_LOGIN_URL", "https://nazar.gift/login"),
        nazar_products_url=os.getenv("NAZAR_PRODUCTS_URL", "https://nazar.gift/products/es"),
        nazar_login=os.getenv("NAZAR_LOGIN", ""),
        nazar_password=os.getenv("NAZAR_PASSWORD", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_as_int(os.getenv("TELEGRAM_CHAT_ID"), 0),
        run_mode=os.getenv("RUN_MODE", "auto").strip().lower(),
        country=os.getenv("COUNTRY", "es").strip().lower(),
        products_page_size=_as_int(os.getenv("PRODUCTS_PAGE_SIZE"), 200),
        scan_interval_sec=_as_int(os.getenv("SCAN_INTERVAL_SEC"), 4),
        request_timeout_sec=_as_int(os.getenv("REQUEST_TIMEOUT_SEC"), 15),
        max_retries=_as_int(os.getenv("MAX_RETRIES"), 3),
        playwright_headless=_as_bool(os.getenv("PLAYWRIGHT_HEADLESS"), True),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        state_file=state_path,
        amazstar_domain=os.getenv("AMAZSTAR_DOMAIN", "https://amazstar.club").rstrip("/"),
        amazstar_login_url=os.getenv("AMAZSTAR_LOGIN_URL", "https://amazstar.club/login"),
        amazstar_products_url=os.getenv("AMAZSTAR_PRODUCTS_URL", "https://amazstar.club/products?country=es"),
        amazstar_login=os.getenv("AMAZSTAR_LOGIN", ""),
        amazstar_password=os.getenv("AMAZSTAR_PASSWORD", ""),
        amazstar_check_interval_sec=_as_int(os.getenv("AMAZSTAR_CHECK_INTERVAL_SEC"), 5),
        amazstar_products_page_size=_as_int(os.getenv("AMAZSTAR_PRODUCTS_PAGE_SIZE"), 500),
        amazstar_run_mode=os.getenv("AMAZSTAR_RUN_MODE", "auto").strip().lower(),
    )
