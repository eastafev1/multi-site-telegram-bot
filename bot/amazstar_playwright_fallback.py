from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from playwright.async_api import Browser, Page, Playwright, async_playwright

from api.amazstar_payloads import build_amazstar_products_payload
from bot.models import Product
from utils.config import Settings

LOGGER = logging.getLogger("amazstar.playwright")


class AmazstarPlaywrightFallbackClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._started = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.settings.playwright_headless)
            context = await self._browser.new_context()
            self._page = await context.new_page()
            await self._login()
            self._started = True
            LOGGER.info("playwright session ready")

    async def close(self) -> None:
        async with self._lock:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self._playwright = None
            self._browser = None
            self._page = None
            self._started = False

    async def _login(self) -> None:
        if not self._page:
            raise RuntimeError("Playwright page is not initialized")
        started = time.perf_counter()
        await self._page.goto(self.settings.amazstar_login_url, wait_until="domcontentloaded")
        if await self._page.locator('input[name="email"]').count() > 0:
            await self._page.fill('input[name="email"]', self.settings.amazstar_login)
            await self._page.fill('input[name="password"]', self.settings.amazstar_password)
            await self._page.click('button[type="submit"], input[type="submit"]')
            await self._page.wait_for_timeout(1500)
        await self._page.goto(self.settings.amazstar_products_url, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(1000)
        LOGGER.info("successful login (playwright) | response_ms=%.1f", (time.perf_counter() - started) * 1000)

    async def _ensure_ready(self) -> None:
        if not self._started:
            await self.start()
        if not self._page:
            raise RuntimeError("Playwright page unavailable")

    @staticmethod
    def _masked_headers(headers: dict[str, str]) -> dict[str, str]:
        masked: dict[str, str] = {}
        for key, value in headers.items():
            lowered = key.lower()
            if any(secret in lowered for secret in ("token", "cookie", "authorization")):
                masked[key] = "***masked***"
            else:
                masked[key] = value
        return masked

    @staticmethod
    def _safe_snippet(text: str, limit: int = 300) -> str:
        compact = " ".join((text or "").split())
        return compact[:limit]

    async def _fetch_products_page(self, *, country: str, start: int, length: int, draw: int) -> tuple[list[Product], int]:
        if not self._page:
            raise RuntimeError("Playwright page unavailable")
        payload = build_amazstar_products_payload(country=country, start=start, length=length, draw=draw)
        result = await self._page.evaluate(
            """
            async ({payload}) => {
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const body = new URLSearchParams();
                for (const [k, v] of Object.entries(payload)) {
                    body.append(k, String(v));
                }
                const resp = await fetch('/getProducts', {
                    method: 'POST',
                    headers: {
                        'X-CSRF-TOKEN': csrf,
                        'X-Requested-With': 'XMLHttpRequest',
                        'Accept': 'application/json, text/javascript, */*; q=0.01',
                        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                    },
                    body: body.toString(),
                });
                return {status: resp.status, text: await resp.text()};
            }
            """,
            {"payload": payload},
        )
        if int(result["status"]) >= 400:
            raise RuntimeError(f"Playwright getProducts failed status={result['status']}")
        parsed = json.loads(result["text"])
        rows = parsed.get("data") or []
        total = int(parsed.get("recordsFiltered") or parsed.get("recordsTotal") or 0)
        products: list[Product] = []
        for row in rows:
            try:
                products.append(Product.from_amazstar_row(row, default_country=country))
            except Exception:
                continue
        return products, total

    async def _fetch_products_impl(self, country: str, length: int) -> list[Product]:
        page_size = max(1, length)
        results: list[Product] = []
        total_expected = 0
        draw = 1
        start = 0
        while True:
            page_items, reported_total = await self._fetch_products_page(
                country=country,
                start=start,
                length=page_size,
                draw=draw,
            )
            if draw == 1:
                total_expected = reported_total
            if not page_items:
                break
            results.extend(page_items)
            if len(page_items) < page_size:
                break
            if total_expected and len(results) >= total_expected:
                break
            start += page_size
            draw += 1
        LOGGER.info(
            "successful product fetch (playwright) | count=%s | expected_total=%s | pages=%s",
            len(results),
            total_expected,
            draw,
        )
        return results

    async def fetch_products(self, country: str = "es", length: int = 500) -> list[Product]:
        for attempt in range(1, self.settings.max_retries + 1):
            started = time.perf_counter()
            async with self._lock:
                try:
                    await self._ensure_ready()
                    results = await self._fetch_products_impl(country=country, length=length)
                    LOGGER.info(
                        "fetch complete (playwright) | count=%s | response_ms=%.1f",
                        len(results),
                        (time.perf_counter() - started) * 1000,
                    )
                    return results
                except Exception as exc:
                    LOGGER.warning("playwright products fetch failed (attempt %s): %s", attempt, exc)
                    await self._login()
            if attempt >= self.settings.max_retries:
                raise RuntimeError("Playwright Amazstar fetch products failed")
            await asyncio.sleep(1)
        return []

    async def _book_via_fetch(self, product_id: int) -> dict[str, Any]:
        if not self._page:
            raise RuntimeError("Playwright page unavailable")
        await self._page.goto(self.settings.amazstar_products_url, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(700)
        flow = await self._page.evaluate(
            """
            async (pid) => {
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const modalPayload = new URLSearchParams();
                modalPayload.append('prod_id', String(pid));
                const modalHeaders = {
                    'X-CSRF-TOKEN': csrf,
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': '*/*',
                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                };
                const modalResp = await fetch('/booking', {
                    method: 'POST',
                    headers: modalHeaders,
                    body: modalPayload.toString(),
                });
                const modalText = await modalResp.text();

                const confirmPayload = new URLSearchParams();
                confirmPayload.append('product_id', String(pid));
                const confirmHeaders = {
                    'X-CSRF-TOKEN': csrf,
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': '*/*',
                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                };
                let confirmStatus = null;
                let confirmText = '';
                if (modalResp.status < 400) {
                    const confirmResp = await fetch('/bookings', {
                        method: 'POST',
                        headers: confirmHeaders,
                        body: confirmPayload.toString(),
                    });
                    confirmStatus = confirmResp.status;
                    confirmText = await confirmResp.text();
                }

                return {
                    modal_request: {
                        url: '/booking',
                        method: 'POST',
                        headers: modalHeaders,
                        payload: modalPayload.toString(),
                    },
                    modal_response: {
                        status: modalResp.status,
                        body: modalText,
                    },
                    confirm_request: {
                        url: '/bookings',
                        method: 'POST',
                        headers: confirmHeaders,
                        payload: confirmPayload.toString(),
                    },
                    confirm_response: {
                        status: confirmStatus,
                        body: confirmText,
                    },
                };
            }
            """,
            product_id,
        )
        if int(flow["modal_response"]["status"]) >= 400:
            raise RuntimeError(f"book modal request failed status={flow['modal_response']['status']}")
        if flow["confirm_response"]["status"] is None:
            raise RuntimeError("book confirm request was not executed")
        if int(flow["confirm_response"]["status"]) >= 400:
            raise RuntimeError(f"book confirm request failed status={flow['confirm_response']['status']}")
        try:
            flow["parsed_result"] = json.loads(flow["confirm_response"]["body"])
        except json.JSONDecodeError:
            flow["parsed_result"] = {"success": False, "msg": "Invalid confirm response payload"}
        return flow

    async def book_product(self, product_id: int) -> dict[str, Any]:
        for attempt in range(1, self.settings.max_retries + 1):
            started = time.perf_counter()
            async with self._lock:
                try:
                    await self._ensure_ready()
                    current_products = await self._fetch_products_impl(
                        country="es",
                        length=self.settings.amazstar_products_page_size,
                    )
                    current_product = next((p for p in current_products if p.id == product_id), None)
                    if not current_product:
                        return {"success": False, "msg": f"Product {product_id} not found in current ES list"}
                    LOGGER.info(
                        "book flow (playwright) | pre-check | product_id=%s | available_before=%s",
                        product_id,
                        current_product.available,
                    )
                    if current_product.available <= 0:
                        return {
                            "success": False,
                            "msg": f"Product {product_id} is not available right now (available={current_product.available})",
                        }

                    flow = await self._book_via_fetch(product_id)
                    result = flow.get("parsed_result") or {"success": False, "msg": "Unknown booking response"}

                    LOGGER.info(
                        "book flow (playwright) | modal request | url=%s | method=%s | payload=%s | headers=%s",
                        flow["modal_request"]["url"],
                        flow["modal_request"]["method"],
                        flow["modal_request"]["payload"],
                        self._masked_headers(flow["modal_request"]["headers"]),
                    )
                    LOGGER.info(
                        "book flow (playwright) | modal response | status=%s | body_snippet=%s",
                        flow["modal_response"]["status"],
                        self._safe_snippet(flow["modal_response"]["body"]),
                    )
                    LOGGER.info(
                        "book flow (playwright) | confirm request | url=%s | method=%s | payload=%s | headers=%s",
                        flow["confirm_request"]["url"],
                        flow["confirm_request"]["method"],
                        flow["confirm_request"]["payload"],
                        self._masked_headers(flow["confirm_request"]["headers"]),
                    )
                    LOGGER.info(
                        "book flow (playwright) | confirm response | status=%s | body_snippet=%s",
                        flow["confirm_response"]["status"],
                        self._safe_snippet(flow["confirm_response"]["body"]),
                    )

                    refreshed = await self._fetch_products_impl(
                        country="es",
                        length=self.settings.amazstar_products_page_size,
                    )
                    refreshed_product = next((p for p in refreshed if p.id == product_id), None)
                    available_after = refreshed_product.available if refreshed_product else None
                    LOGGER.info(
                        "book flow (playwright) | post-check | product_id=%s | available_after=%s",
                        product_id,
                        available_after,
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    if result.get("success"):
                        LOGGER.info(
                            "successful booking (playwright) | product_id=%s | response_ms=%.1f | message=%s",
                            product_id,
                            elapsed_ms,
                            result.get("msg"),
                        )
                    else:
                        LOGGER.warning(
                            "booking failed (playwright) | product_id=%s | response_ms=%.1f | response=%s",
                            product_id,
                            elapsed_ms,
                            result,
                        )
                    return result
                except Exception as exc:
                    LOGGER.warning("playwright booking failed (attempt %s) | product_id=%s | %s", attempt, product_id, exc)
                    await self._login()
            if attempt >= self.settings.max_retries:
                raise RuntimeError("Playwright Amazstar booking failed")
            await asyncio.sleep(1)
        return {"success": False, "msg": "Unknown booking error"}
