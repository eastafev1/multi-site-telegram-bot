from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from api.amazstar_payloads import build_amazstar_products_payload
from bot.models import Product
from utils.config import Settings

LOGGER = logging.getLogger("amazstar.api")

TOKEN_RE = re.compile(r'name="_token"\s+value="([^"]+)"')
CSRF_META_RE = re.compile(r'<meta name="csrf-token"\s+content="([^"]+)"')


class AmazstarApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.amazstar_domain,
            timeout=settings.request_timeout_sec,
            follow_redirects=True,
        )
        self._csrf_token: str | None = None
        self._logged_in = False

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_login_form_token(self) -> str:
        response = await self._client.get(self.settings.amazstar_login_url)
        response.raise_for_status()
        match = TOKEN_RE.search(response.text)
        if not match:
            raise RuntimeError("Could not find Amazstar login form token")
        return match.group(1)

    async def _refresh_csrf_token(self) -> str:
        response = await self._client.get(self.settings.amazstar_products_url)
        response.raise_for_status()
        match = CSRF_META_RE.search(response.text)
        if not match:
            raise RuntimeError("Could not find Amazstar csrf-token meta")
        self._csrf_token = match.group(1)
        return self._csrf_token

    async def login(self) -> None:
        started = time.perf_counter()
        form_token = await self._get_login_form_token()
        payload = {
            "_token": form_token,
            "email": self.settings.amazstar_login,
            "password": self.settings.amazstar_password,
        }
        response = await self._client.post(self.settings.amazstar_login_url, data=payload)
        response.raise_for_status()
        await self._refresh_csrf_token()
        self._logged_in = True
        LOGGER.info("successful login | response_ms=%.1f", (time.perf_counter() - started) * 1000)

    def _ajax_headers(self, referer: str | None = None) -> dict[str, str]:
        if not self._csrf_token:
            raise RuntimeError("CSRF token missing")
        return {
            "X-CSRF-TOKEN": self._csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": referer or self.settings.amazstar_products_url,
        }

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

    async def _ensure_session(self) -> None:
        if self._logged_in and self._csrf_token:
            return
        await self.login()

    @staticmethod
    def _parse_products(payload_text: str, country: str) -> tuple[list[Product], int]:
        parsed = json.loads(payload_text)
        rows = parsed.get("data") or []
        total = int(parsed.get("recordsFiltered") or parsed.get("recordsTotal") or 0)
        products: list[Product] = []
        for row in rows:
            try:
                products.append(Product.from_amazstar_row(row, default_country=country))
            except Exception:
                continue
        return products, total

    async def fetch_products(self, country: str = "es", length: int = 500) -> list[Product]:
        for attempt in range(1, self.settings.max_retries + 1):
            started = time.perf_counter()
            try:
                await self._ensure_session()
                results: list[Product] = []
                total_expected = 0
                draw = 1
                start = 0
                page_size = max(1, length)
                while True:
                    payload = build_amazstar_products_payload(
                        country=country,
                        start=start,
                        length=page_size,
                        draw=draw,
                    )
                    response = await self._client.post(
                        "/getProducts",
                        data=payload,
                        headers=self._ajax_headers(self.settings.amazstar_products_url),
                    )
                    response.raise_for_status()
                    page_products, reported_total = self._parse_products(response.text, country=country)
                    if draw == 1:
                        total_expected = reported_total
                    if not page_products:
                        break
                    results.extend(page_products)
                    if len(page_products) < page_size:
                        break
                    if total_expected and len(results) >= total_expected:
                        break
                    start += page_size
                    draw += 1

                elapsed_ms = (time.perf_counter() - started) * 1000
                LOGGER.info(
                    "successful product fetch | count=%s | expected_total=%s | pages=%s | response_ms=%.1f",
                    len(results),
                    total_expected,
                    draw,
                    elapsed_ms,
                )
                return results
            except Exception as exc:
                LOGGER.warning("amazstar api products fetch failed (attempt %s): %s", attempt, exc)
                self._logged_in = False
                self._csrf_token = None
                if attempt >= self.settings.max_retries:
                    raise
                await asyncio.sleep(1)
        return []

    async def _book_once(self, product_id: int) -> dict[str, Any]:
        modal_headers = self._ajax_headers(self.settings.amazstar_products_url)
        modal_payload = {"prod_id": str(product_id)}
        LOGGER.info(
            "book flow (api) | modal request | product_id=%s | url=/booking | method=POST | payload=%s | headers=%s",
            product_id,
            modal_payload,
            self._masked_headers(modal_headers),
        )
        modal = await self._client.post(
            "/booking",
            data=modal_payload,
            headers=modal_headers,
        )
        LOGGER.info(
            "book flow (api) | modal response | product_id=%s | status=%s | body_snippet=%s",
            product_id,
            modal.status_code,
            self._safe_snippet(modal.text),
        )
        if modal.status_code >= 400:
            return {
                "success": False,
                "msg": f"Book modal failed with status {modal.status_code}",
                "debug": {
                    "endpoint": "/booking",
                    "method": "POST",
                    "payload": modal_payload,
                    "headers": self._masked_headers(modal_headers),
                    "response_status": modal.status_code,
                    "response_snippet": self._safe_snippet(modal.text),
                },
            }

        confirm_headers = self._ajax_headers(self.settings.amazstar_products_url)
        confirm_payload = {"product_id": str(product_id)}
        LOGGER.info(
            "book flow (api) | confirm request | product_id=%s | url=/bookings | method=POST | payload=%s | headers=%s",
            product_id,
            confirm_payload,
            self._masked_headers(confirm_headers),
        )
        confirm = await self._client.post(
            "/bookings",
            data=confirm_payload,
            headers=confirm_headers,
        )
        LOGGER.info(
            "book flow (api) | confirm response | product_id=%s | status=%s | body_snippet=%s",
            product_id,
            confirm.status_code,
            self._safe_snippet(confirm.text),
        )
        if confirm.status_code >= 400:
            return {
                "success": False,
                "msg": f"Book confirm failed with status {confirm.status_code}",
                "debug": {
                    "endpoint": "/bookings",
                    "method": "POST",
                    "payload": confirm_payload,
                    "headers": self._masked_headers(confirm_headers),
                    "response_status": confirm.status_code,
                    "response_snippet": self._safe_snippet(confirm.text),
                },
            }
        try:
            return json.loads(confirm.text)
        except json.JSONDecodeError:
            return {
                "success": False,
                "msg": "Invalid booking response payload",
                "debug": {
                    "endpoint": "/bookings",
                    "method": "POST",
                    "payload": confirm_payload,
                    "headers": self._masked_headers(confirm_headers),
                    "response_status": confirm.status_code,
                    "response_snippet": self._safe_snippet(confirm.text),
                },
            }

    async def book_product(self, product_id: int) -> dict[str, Any]:
        for attempt in range(1, self.settings.max_retries + 1):
            started = time.perf_counter()
            try:
                await self._ensure_session()
                current_products = await self.fetch_products(
                    country="es",
                    length=self.settings.amazstar_products_page_size,
                )
                current_product = next((p for p in current_products if p.id == product_id), None)
                if not current_product:
                    LOGGER.warning("booking skipped | product_id=%s not found in current products list", product_id)
                    return {
                        "success": False,
                        "msg": f"Product {product_id} not found in current ES product list",
                    }
                LOGGER.info(
                    "book flow (api) | pre-check | product_id=%s | available_before=%s",
                    product_id,
                    current_product.available,
                )
                if current_product.available <= 0:
                    return {
                        "success": False,
                        "msg": f"Product {product_id} is not available right now (available={current_product.available})",
                    }
                result = await self._book_once(product_id)
                refreshed = await self.fetch_products(
                    country="es",
                    length=self.settings.amazstar_products_page_size,
                )
                refreshed_product = next((p for p in refreshed if p.id == product_id), None)
                available_after = refreshed_product.available if refreshed_product else None
                elapsed_ms = (time.perf_counter() - started) * 1000
                LOGGER.info(
                    "book flow (api) | post-check | product_id=%s | available_after=%s",
                    product_id,
                    available_after,
                )
                if result.get("success"):
                    LOGGER.info(
                        "successful booking | product_id=%s | response_ms=%.1f | message=%s",
                        product_id,
                        elapsed_ms,
                        result.get("msg"),
                    )
                else:
                    LOGGER.warning(
                        "booking failed | product_id=%s | response_ms=%.1f | response=%s",
                        product_id,
                        elapsed_ms,
                        result,
                    )
                return result
            except Exception as exc:
                LOGGER.warning("amazstar api booking failed (attempt %s) | product_id=%s | %s", attempt, product_id, exc)
                self._logged_in = False
                self._csrf_token = None
                if attempt >= self.settings.max_retries:
                    raise
                await asyncio.sleep(1)
        return {"success": False, "msg": "Unknown booking error"}
