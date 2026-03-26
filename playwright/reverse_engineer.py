from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.config import load_settings


async def run_probe(output_dir: Path) -> None:
    settings = load_settings()
    output_dir.mkdir(parents=True, exist_ok=True)
    requests: list[dict] = []
    responses: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.playwright_headless)
        context = await browser.new_context()
        page = await context.new_page()

        def on_request(req) -> None:
            if req.resource_type not in {"xhr", "fetch", "document"}:
                return
            if settings.nazar_domain not in req.url:
                return
            requests.append(
                {
                    "method": req.method,
                    "resource_type": req.resource_type,
                    "url": req.url,
                    "headers": req.headers,
                    "post_data": req.post_data,
                }
            )

        async def on_response(resp) -> None:
            if resp.request.resource_type not in {"xhr", "fetch"}:
                return
            if settings.nazar_domain not in resp.url:
                return
            text = ""
            try:
                text = (await resp.text())[:5000]
            except Exception:
                pass
            responses.append(
                {
                    "status": resp.status,
                    "url": resp.url,
                    "headers": resp.headers,
                    "body_snippet": text,
                }
            )

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        await page.goto(settings.nazar_login_url, wait_until="domcontentloaded")
        await page.fill('input[name="email"]', settings.nazar_login)
        await page.fill('input[name="password"]', settings.nazar_password)
        await page.click('button[type="submit"], input[type="submit"]')
        await page.wait_for_timeout(2200)
        await page.goto(settings.nazar_products_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        # Open BOOK modal and proceed once to discover booking endpoint.
        if await page.locator("button.book-btn").count() > 0:
            await page.locator("button.book-btn").first.click()
            await page.wait_for_timeout(1000)
            if await page.locator("#modal_submit").count() > 0:
                await page.click("#modal_submit")
                await page.wait_for_timeout(1800)

        cookies = await context.cookies()
        html = await page.content()
        await browser.close()

    output_dir.joinpath("requests.json").write_text(json.dumps(requests, ensure_ascii=False, indent=2), encoding="utf-8")
    output_dir.joinpath("responses.json").write_text(json.dumps(responses, ensure_ascii=False, indent=2), encoding="utf-8")
    output_dir.joinpath("cookies.json").write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    output_dir.joinpath("products_page.html").write_text(html, encoding="utf-8")
    print(f"Saved probe outputs to {output_dir.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reverse-engineer Nazar Gift network flow with Playwright.")
    parser.add_argument("--out", default="playwright/probe_output", help="Output directory for captured network files.")
    args = parser.parse_args()
    asyncio.run(run_probe(Path(args.out)))


if __name__ == "__main__":
    main()
