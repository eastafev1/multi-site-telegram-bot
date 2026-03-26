from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from playwright.async_api import Browser, Page, Playwright, async_playwright

from api.payloads import build_products_payload
from bot.models import Product
from utils.config import Settings

LOGGER = logging.getLogger("nazar.playwright")


class BookingFlowError(RuntimeError):
    def __init__(self, message: str, flow: dict[str, Any]):
        super().__init__(message)
        self.flow = flow


class PlaywrightFallbackClient:
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
            self._browser = None
            self._playwright = None
            self._page = None
            self._started = False

    async def _login(self) -> None:
        if not self._page:
            raise RuntimeError("Playwright page is not initialized")
        started = time.perf_counter()
        await self._page.goto(self.settings.nazar_login_url, wait_until="domcontentloaded")
        if await self._page.locator('input[name="email"]').count() > 0:
            await self._page.fill('input[name="email"]', self.settings.nazar_login)
            await self._page.fill('input[name="password"]', self.settings.nazar_password)
            await self._page.click('button[type="submit"], input[type="submit"]')
            await self._page.wait_for_timeout(1800)
        await self._page.goto(self.settings.nazar_products_url, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(1200)
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

    async def _fetch_products_once(
        self,
        *,
        country: str,
        length: int,
        start: int,
        draw: int,
    ) -> tuple[list[Product], int]:
        if not self._page:
            raise RuntimeError("Playwright page unavailable")
        payload = build_products_payload(country=country, length=length, start=start, draw=draw)
        result = await self._page.evaluate(
            """
            async ({payload}) => {
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const body = new URLSearchParams();
                for (const [k, v] of Object.entries(payload)) {
                    body.append(k, String(v));
                }
                const resp = await fetch('/products/list', {
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
            raise RuntimeError(f"Playwright fetch products failed status={result['status']}")
        parsed = json.loads(result["text"])
        rows = parsed.get("data") or []
        total = int(parsed.get("recordsFiltered") or parsed.get("recordsTotal") or 0)
        products: list[Product] = []
        for row in rows:
            try:
                products.append(Product.from_row(row, default_country=country))
            except Exception:
                continue
        return products, total

    async def _fetch_products_impl(self, *, country: str, length: int) -> list[Product]:
        page_size = max(1, length)
        all_products: list[Product] = []
        total_expected = 0
        draw = 1
        start = 0
        while True:
            page_products, reported_total = await self._fetch_products_once(
                country=country,
                length=page_size,
                start=start,
                draw=draw,
            )
            if draw == 1:
                total_expected = reported_total
            if not page_products:
                break
            all_products.extend(page_products)
            if len(page_products) < page_size:
                break
            if total_expected and len(all_products) >= total_expected:
                break
            start += page_size
            draw += 1
        LOGGER.info(
            "playwright fetch pages complete | count=%s | expected_total=%s | pages=%s",
            len(all_products),
            total_expected,
            draw,
        )
        return all_products

    async def fetch_products(self, country: str, length: int = 200) -> list[Product]:
        for attempt in range(1, self.settings.max_retries + 1):
            started = time.perf_counter()
            async with self._lock:
                try:
                    await self._ensure_ready()
                    products = await self._fetch_products_impl(country=country, length=length)
                    LOGGER.info(
                        "successful product fetch (playwright) | count=%s | response_ms=%.1f",
                        len(products),
                        (time.perf_counter() - started) * 1000,
                    )
                    return products
                except Exception as exc:
                    LOGGER.warning("playwright products fetch failed (attempt %s): %s", attempt, exc)
                    await self._login()
            if attempt >= self.settings.max_retries:
                raise RuntimeError("Playwright fetch products failed")
            await asyncio.sleep(1)
        return []

    async def _book_via_fetch(self, product_id: int) -> dict[str, Any]:
        if not self._page:
            raise RuntimeError("Playwright page unavailable")
        await self._page.goto(self.settings.nazar_products_url, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(700)
        flow = await self._page.evaluate(
            """
            async (pid) => {
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const bookButtons = Array.from(document.querySelectorAll('button.book-btn'));
                let foundButton = null;
                for (const btn of bookButtons) {
                    const raw = btn.getAttribute('data-data');
                    if (!raw) {
                        continue;
                    }
                    try {
                        const parsed = JSON.parse(raw);
                        if (Number(parsed.product_id) === Number(pid)) {
                            foundButton = raw;
                            break;
                        }
                    } catch (_) {}
                }
                const modalBody = new URLSearchParams();
                modalBody.append('product_id', String(pid));
                const modalHeaders = {
                    'X-CSRF-TOKEN': csrf,
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': '*/*',
                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                };

                const modalResp = await fetch('/book_product_modal', {
                    method: 'POST',
                    headers: modalHeaders,
                    body: modalBody.toString(),
                });
                const modalText = await modalResp.text();
                const m = modalText.match(/name="_url"\\s+type="hidden"\\s+value="([^"]+)"/);
                const endpoint = m?.[1] || `/product/${pid}/book`;
                const hasProceed = modalText.includes('id="modal_submit"') || modalText.includes('Proceed');

                let bookStatus = null;
                let bookText = '';
                let bookHeaders = {
                    'X-CSRF-TOKEN': csrf,
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': '*/*',
                };
                let bookPayload = `product_id=${pid}`;
                if (modalResp.status < 400) {
                    const fd = new FormData();
                    fd.append('product_id', String(pid));
                    const bookResp = await fetch(endpoint, {
                        method: 'POST',
                        headers: bookHeaders,
                        body: fd,
                    });
                    bookStatus = bookResp.status;
                    bookText = await bookResp.text();
                }

                return {
                    product_id: Number(pid),
                    book_button_found: Boolean(foundButton),
                    book_button_data: foundButton,
                    modal_has_proceed_button: hasProceed,
                    modal_request: {
                        url: '/book_product_modal',
                        method: 'POST',
                        headers: modalHeaders,
                        payload: modalBody.toString(),
                    },
                    modal_response: {
                        status: modalResp.status,
                        body: modalText,
                    },
                    confirm_request: {
                        url: endpoint,
                        method: 'POST',
                        headers: bookHeaders,
                        payload: bookPayload,
                    },
                    confirm_response: {
                        status: bookStatus,
                        body: bookText,
                    },
                };
            }
            """,
            product_id,
        )
        if int(flow["modal_response"]["status"]) >= 400:
            raise BookingFlowError(
                f"book modal request failed status={flow['modal_response']['status']}",
                flow=flow,
            )
        if flow["confirm_response"]["status"] is None:
            raise BookingFlowError("book confirm request was not executed", flow=flow)
        if int(flow["confirm_response"]["status"]) >= 400:
            raise BookingFlowError(
                f"book confirm request failed status={flow['confirm_response']['status']}",
                flow=flow,
            )
        try:
            flow["parsed_result"] = json.loads(flow["confirm_response"]["body"])
        except json.JSONDecodeError:
            flow["parsed_result"] = {
                "success": False,
                "msg": "Invalid confirm response payload",
            }
        return flow

    async def book_product(self, product_id: int) -> dict[str, Any]:
        for attempt in range(1, self.settings.max_retries + 1):
            started = time.perf_counter()
            async with self._lock:
                try:
                    await self._ensure_ready()
                    current_products = await self._fetch_products_impl(
                        country=self.settings.country,
                        length=self.settings.products_page_size,
                    )
                    current_product = next((p for p in current_products if p.id == product_id), None)
                    if not current_product:
                        LOGGER.warning(
                            "booking skipped (playwright) | product_id=%s not found in current products list (likely stale Telegram message)",
                            product_id,
                        )
                        return {
                            "success": False,
                            "msg": f"Product {product_id} not found in current /products/{self.settings.country} list",
                        }
                    LOGGER.info(
                        "book flow (playwright) | pre-check | product_id=%s | available_before=%s",
                        product_id,
                        current_product.available,
                    )
                    if current_product.available <= 0:
                        LOGGER.warning(
                            "booking skipped (playwright) | product_id=%s has available=%s",
                            product_id,
                            current_product.available,
                        )
                        return {
                            "success": False,
                            "msg": f"Product {product_id} is not available right now (available={current_product.available})",
                        }

                    flow = await self._book_via_fetch(product_id)
                    result = flow.get("parsed_result") or {"success": False, "msg": "Unknown booking response"}

                    LOGGER.info(
                        "book flow (playwright) | ui-check | product_id=%s | book_button_found=%s | proceed_button_found=%s",
                        product_id,
                        flow.get("book_button_found"),
                        flow.get("modal_has_proceed_button"),
                    )
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

                    refreshed_products = await self._fetch_products_impl(
                        country=self.settings.country,
                        length=self.settings.products_page_size,
                    )
                    refreshed_product = next((p for p in refreshed_products if p.id == product_id), None)
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
                except BookingFlowError as exc:
                    flow = exc.flow
                    LOGGER.warning(
                        "playwright booking flow error (attempt %s) | product_id=%s | %s",
                        attempt,
                        product_id,
                        exc,
                    )
                    LOGGER.warning(
                        "book flow (playwright) | modal request failure details | endpoint=%s | method=%s | payload=%s | headers=%s | status=%s | body_snippet=%s",
                        flow.get("modal_request", {}).get("url"),
                        flow.get("modal_request", {}).get("method"),
                        flow.get("modal_request", {}).get("payload"),
                        self._masked_headers(flow.get("modal_request", {}).get("headers") or {}),
                        flow.get("modal_response", {}).get("status"),
                        self._safe_snippet(flow.get("modal_response", {}).get("body") or ""),
                    )
                    if attempt >= self.settings.max_retries:
                        return {
                            "success": False,
                            "msg": str(exc),
                            "debug": {
                                "modal_endpoint": flow.get("modal_request", {}).get("url"),
                                "modal_status": flow.get("modal_response", {}).get("status"),
                                "modal_body_snippet": self._safe_snippet(flow.get("modal_response", {}).get("body") or ""),
                                "confirm_endpoint": flow.get("confirm_request", {}).get("url"),
                                "confirm_status": flow.get("confirm_response", {}).get("status"),
                                "confirm_body_snippet": self._safe_snippet(flow.get("confirm_response", {}).get("body") or ""),
                            },
                        }
                    await self._login()
                except Exception as exc:
                    LOGGER.warning("playwright booking failed (attempt %s) | product_id=%s | %s", attempt, product_id, exc)
                    await self._login()
            if attempt >= self.settings.max_retries:
                raise RuntimeError("Playwright booking failed")
            await asyncio.sleep(1)
        return {"success": False, "msg": "Unknown booking error"}
