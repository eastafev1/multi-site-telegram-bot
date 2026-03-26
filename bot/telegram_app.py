from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.formatter import render_product_caption
from bot.models import Product
from bot.state_store import ScanDiff, StateStore
from utils.config import Settings

LOGGER = logging.getLogger("bot.telegram")


class NazarTelegramBot:
    def __init__(self, settings: Settings, service: Any, amazstar_service: Any | None = None):
        self.settings = settings
        self.services: dict[str, Any] = {"nazar": service}
        if amazstar_service is not None:
            self.services["amazstar"] = amazstar_service

        self.site_labels = {
            "nazar": "NAZAR GIFT",
            "amazstar": "AMAZSTAR",
        }
        self.site_domains = {
            "nazar": settings.nazar_domain,
            "amazstar": settings.amazstar_domain,
        }
        self.site_intervals = {
            "nazar": settings.scan_interval_sec,
            "amazstar": settings.amazstar_check_interval_sec,
        }

        self.state = StateStore(settings.state_file)
        self.application = (
            Application.builder()
            .token(settings.telegram_bot_token)
            .post_init(self._post_init)
            .post_shutdown(self._post_shutdown)
            .build()
        )
        self._monitor_tasks: dict[str, asyncio.Task] = {}
        self._scan_locks = {site_key: asyncio.Lock() for site_key in self.services}
        self._last_scan_at: dict[str, datetime | None] = {site_key: None for site_key in self.services}
        self._last_scan_summary: dict[str, str] = {site_key: "No scans yet" for site_key in self.services}
        self._last_products: dict[str, list[Product]] = {site_key: [] for site_key in self.services}
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("status", self.cmd_status))
        self.application.add_handler(CommandHandler("scan", self.cmd_scan))
        self.application.add_handler(CommandHandler("products", self.cmd_products))
        self.application.add_handler(
            CallbackQueryHandler(self.on_book_callback, pattern=r"^book:(?:nazar|amazstar):\d+$|^book:\d+$")
        )

    async def _post_init(self, app: Application) -> None:
        for site_key, service in self.services.items():
            await service.start()
            self._monitor_tasks[site_key] = asyncio.create_task(
                self._monitor_loop(site_key),
                name=f"monitor-{site_key}",
            )
        LOGGER.info("telegram bot started | sites=%s", list(self.services.keys()))

    async def _post_shutdown(self, app: Application) -> None:
        for task in self._monitor_tasks.values():
            task.cancel()
        for task in self._monitor_tasks.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for service in self.services.values():
            await service.close()

    async def _send_product(self, product: Product, reason: str, site_key: str) -> None:
        site_label = self.site_labels.get(site_key, site_key.upper())
        caption = render_product_caption(product, site_label=site_label)
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("BOOK", callback_data=f"book:{site_key}:{product.id}")]]
        )
        image_url = product.image_url(self.site_domains.get(site_key, self.settings.nazar_domain))
        full_caption = f"[{site_label}] {reason}\n\n{caption}"
        try:
            if image_url:
                await self.application.bot.send_photo(
                    chat_id=self.settings.telegram_chat_id,
                    photo=image_url,
                    caption=full_caption[:1024],
                    reply_markup=keyboard,
                )
            else:
                await self.application.bot.send_message(
                    chat_id=self.settings.telegram_chat_id,
                    text=full_caption,
                    reply_markup=keyboard,
                )
        except Exception as exc:
            LOGGER.warning("failed to send photo message, falling back to text: %s", exc)
            await self.application.bot.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=full_caption,
                reply_markup=keyboard,
            )

    async def _scan_once(self, site_key: str, notify: bool) -> ScanDiff:
        service = self.services[site_key]
        async with self._scan_locks[site_key]:
            products = await service.fetch_products()
            self._last_products[site_key] = products
            diff = self.state.process_scan(products, site_key=site_key)
            self._last_scan_at[site_key] = datetime.now(timezone.utc)
            self._last_scan_summary[site_key] = (
                f"site={site_key} fetched={len(products)} known={diff.known_products} "
                f"new_0_to_1={len(diff.newly_available)} new_items={len(diff.new_products_available)} "
                f"mode={service.mode}"
            )

            if notify and not diff.first_sync:
                for product in diff.newly_available:
                    LOGGER.info("%s found availability 0->1 | product_id=%s", site_key, product.id)
                    await self._send_product(product, reason="Available changed: 0 -> 1", site_key=site_key)
                for product in diff.new_products_available:
                    LOGGER.info("%s found new available product | product_id=%s", site_key, product.id)
                    await self._send_product(product, reason="New product with Available = 1", site_key=site_key)
            elif diff.first_sync:
                LOGGER.info("%s first run: silent state sync complete | known=%s", site_key, diff.known_products)
            return diff

    async def _monitor_loop(self, site_key: str) -> None:
        interval = self.site_intervals.get(site_key, self.settings.scan_interval_sec)
        while True:
            try:
                await self._scan_once(site_key=site_key, notify=True)
            except Exception as exc:
                LOGGER.exception("%s monitor loop error: %s", site_key, exc)
            await asyncio.sleep(interval)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        sites_text = ", ".join(self.site_labels.get(key, key.upper()) for key in self.services)
        text = (
            f"Bot is running.\nSites: {sites_text}\n"
            "Commands:\n"
            "/status - current modes and last scans\n"
            "/scan - manual scan now\n"
            "/products - current available products snapshot"
        )
        await update.effective_message.reply_text(text)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        lines: list[str] = []
        for site_key, service in self.services.items():
            ts = self._last_scan_at[site_key].isoformat() if self._last_scan_at[site_key] else "never"
            label = self.site_labels.get(site_key, site_key.upper())
            lines.append(f"[{label}] mode={service.mode} last_scan={ts}")
            lines.append(self._last_scan_summary[site_key])
        await update.effective_message.reply_text("\n".join(lines))

    async def cmd_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        lines: list[str] = []
        for site_key, service in self.services.items():
            diff = await self._scan_once(site_key=site_key, notify=False)
            label = self.site_labels.get(site_key, site_key.upper())
            lines.extend(
                [
                    f"[{label}]",
                    f"known={diff.known_products}",
                    f"new_0_to_1={len(diff.newly_available)}",
                    f"new_items={len(diff.new_products_available)}",
                    f"first_sync={diff.first_sync}",
                    f"mode={service.mode}",
                    "",
                ]
            )
        await update.effective_message.reply_text("Manual scan complete:\n" + "\n".join(lines).strip())

    async def cmd_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        lines: list[str] = []
        for site_key in self.services:
            available = [p for p in self._last_products[site_key] if p.is_available]
            label = self.site_labels.get(site_key, site_key.upper())
            lines.append(f"[{label}] available now: {len(available)} (showing up to 20)")
            for product in available[:20]:
                lines.append(f"- {product.id} | {product.shop} | A={product.available} | {product.name[:60]}")
            lines.append("")
        await update.effective_message.reply_text("\n".join(lines).strip() or "No snapshots yet.")

    async def on_book_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.callback_query:
            return
        query = update.callback_query
        await query.answer()

        payload = query.data or ""
        site_key = "nazar"
        product_id: int | None = None

        parts = payload.split(":")
        if len(parts) == 2 and parts[0] == "book":
            # backward compatibility for old Nazar inline buttons: book:<id>
            try:
                product_id = int(parts[1])
            except Exception:
                product_id = None
        elif len(parts) == 3 and parts[0] == "book":
            site_key = parts[1].lower()
            try:
                product_id = int(parts[2])
            except Exception:
                product_id = None

        if product_id is None or site_key not in self.services:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("Invalid BOOK payload.")
            return

        site_label = self.site_labels.get(site_key, site_key.upper())
        await query.message.reply_text(f"[{site_label}] Booking started for product {product_id}...")
        try:
            result = await self.services[site_key].book_product(product_id)
            if result.get("success"):
                await query.message.reply_text(
                    f"[{site_label}] Booking success for product {product_id}\n{result.get('msg', '')}"
                )
            else:
                await query.message.reply_text(
                    f"[{site_label}] Booking failed for product {product_id}\n{result.get('msg', result)}"
                )
        except Exception as exc:
            LOGGER.exception("%s booking error via callback: %s", site_key, exc)
            await query.message.reply_text(f"[{site_label}] Booking error for product {product_id}: {exc}")

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.application.run_polling(drop_pending_updates=True)
