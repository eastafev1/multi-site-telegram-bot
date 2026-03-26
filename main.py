from __future__ import annotations

from bot.amazstar_service import AmazstarService
from bot.service import NazarService
from bot.telegram_app import NazarTelegramBot
from utils.config import load_settings
from utils.logging_setup import configure_logging


def main() -> None:
    settings = load_settings("config.env")
    configure_logging(settings.log_level)
    nazar_service = NazarService(settings)
    amazstar_service = AmazstarService(settings)
    app = NazarTelegramBot(settings=settings, service=nazar_service, amazstar_service=amazstar_service)
    app.run()


if __name__ == "__main__":
    main()
