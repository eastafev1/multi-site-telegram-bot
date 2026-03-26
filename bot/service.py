from __future__ import annotations

import logging
import time
from typing import Literal

from api.nazar_api_client import NazarApiClient
from bot.models import Product
from bot.playwright_fallback import PlaywrightFallbackClient
from utils.config import Settings

LOGGER = logging.getLogger("nazar.service")

Mode = Literal["api_mode", "playwright_mode"]


class NazarService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_client = NazarApiClient(settings)
        self.playwright_client = PlaywrightFallbackClient(settings)
        self.mode: Mode = "api_mode"

    async def start(self) -> None:
        configured_mode = self.settings.run_mode
        if configured_mode == "api":
            await self.api_client.login()
            self.mode = "api_mode"
            LOGGER.info("mode=api_mode")
            return

        if configured_mode == "playwright":
            await self.playwright_client.start()
            self.mode = "playwright_mode"
            LOGGER.info("mode=playwright_mode")
            return

        try:
            await self.api_client.login()
            self.mode = "api_mode"
            LOGGER.info("mode=api_mode (auto)")
        except Exception as exc:
            LOGGER.warning("API start failed, switching to Playwright fallback: %s", exc)
            await self.playwright_client.start()
            self.mode = "playwright_mode"
            LOGGER.info("mode=playwright_mode (auto fallback)")

    async def close(self) -> None:
        await self.api_client.close()
        await self.playwright_client.close()

    async def _switch_to_playwright(self) -> None:
        await self.playwright_client.start()
        self.mode = "playwright_mode"
        LOGGER.info("mode=playwright_mode (runtime fallback)")

    async def fetch_products(self) -> list[Product]:
        started = time.perf_counter()
        if self.mode == "api_mode":
            try:
                result = await self.api_client.fetch_products(
                    country=self.settings.country,
                    length=self.settings.products_page_size,
                )
                LOGGER.info("fetch complete | mode=%s | response_ms=%.1f", self.mode, (time.perf_counter() - started) * 1000)
                return result
            except Exception as exc:
                LOGGER.exception("API error on product fetch: %s", exc)
                if self.settings.run_mode == "api":
                    raise
                await self._switch_to_playwright()

        result = await self.playwright_client.fetch_products(
            country=self.settings.country,
            length=self.settings.products_page_size,
        )
        LOGGER.info("fetch complete | mode=%s | response_ms=%.1f", self.mode, (time.perf_counter() - started) * 1000)
        return result

    async def book_product(self, product_id: int) -> dict:
        started = time.perf_counter()
        if self.mode == "api_mode":
            try:
                result = await self.api_client.book_product(product_id)
                LOGGER.info("book complete | mode=%s | response_ms=%.1f", self.mode, (time.perf_counter() - started) * 1000)
                return result
            except Exception as exc:
                LOGGER.exception("API error on booking: %s", exc)
                if self.settings.run_mode == "api":
                    raise
                await self._switch_to_playwright()

        result = await self.playwright_client.book_product(product_id)
        LOGGER.info("book complete | mode=%s | response_ms=%.1f", self.mode, (time.perf_counter() - started) * 1000)
        return result
