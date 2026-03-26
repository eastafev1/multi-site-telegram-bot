from __future__ import annotations

from bot.models import Product


def render_product_caption(product: Product, site_label: str) -> str:
    lines = [
        f"Source: {site_label}",
        f"{product.name}",
        f"ID: {product.id}",
        f"Shop: {product.shop}",
        f"Manager: {product.manager}",
        f"Review type: {product.review_type}",
        f"Daily / Total / Available: {product.daily} / {product.total} / {product.available}",
        f"Agent comm.: {product.agent_comm or '-'}",
        f"Paypal fees: {product.paypal_fees or '-'}",
        f"Link: {product.link or '-'}",
    ]
    text = "\n".join(lines)
    # Telegram caption limit.
    return text[:1024]
