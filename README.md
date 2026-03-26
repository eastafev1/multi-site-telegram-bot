# Multi-site Telegram Bot (Nazar Gift + Amazstar)

Python bot that monitors product availability and supports booking from Telegram for:

- `https://nazar.gift`
- `https://amazstar.club`

## Features

- Monitoring by `Available` column only.
- Sends Telegram alerts only when:
  - `Available` changed `0 -> 1`
  - new product appears with `Available = 1`
- First run is silent state sync (no spam).
- BOOK button in Telegram:
  - Nazar callback: `book:nazar:<product_id>` (legacy `book:<id>` still supported)
  - Amazstar callback: `book:amazstar:<product_id>`
- API-first mode with runtime fallback to Playwright.

## Endpoints used

### Nazar

- Product list: `POST /products/list`
- Open book modal: `POST /book_product_modal`
- Confirm booking: `POST /product/{id}/book`

### Amazstar

- Product list: `POST /getProducts`
- Open book modal: `POST /booking` (`prod_id`)
- Confirm booking: `POST /bookings` (`product_id`)

## Monitoring coverage

- `Nazar` fetches full country list using DataTables pagination loop (`start/length`).
- `Amazstar` fetches full ES list using DataTables pagination loop (`start/length`).

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```bash
python main.py
```

## Telegram commands

- `/start`
- `/status`
- `/scan`
- `/products`

## Config

Edit `config.env`.

Main options:

- `RUN_MODE=auto|api|playwright` (Nazar)
- `AMAZSTAR_RUN_MODE=auto|api|playwright`
- `SCAN_INTERVAL_SEC` (Nazar interval)
- `AMAZSTAR_CHECK_INTERVAL_SEC` (Amazstar interval)
- `PRODUCTS_PAGE_SIZE` (Nazar page size per request)
- `AMAZSTAR_PRODUCTS_PAGE_SIZE` (Amazstar page size per request)
