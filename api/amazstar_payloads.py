from __future__ import annotations


def build_amazstar_products_payload(
    *,
    country: str = "es",
    start: int = 0,
    length: int = 500,
    draw: int = 1,
) -> dict[str, str]:
    columns = [
        "col_state",
        "col_book_order",
        "col_id",
        "col_name",
        "col_image",
        "col_link",
        "col_seller_shop",
        "col_manager",
        "col_total",
        "col_daily",
        "col_available",
        "col_paypal_fee",
        "col_review_type",
        "col_feedback",
        "col_perc_refund",
        "col_note",
        "col_agent_comm",
    ]
    payload: dict[str, str] = {
        "draw": str(draw),
        "start": str(max(start, 0)),
        "length": str(max(length, 1)),
        "search[value]": "",
        "search[regex]": "false",
        "filter": "",
        "all_countries": "false",
        "country": country.lower(),
        "seller_id": "",
        "order[0][column]": "2",
        "order[0][dir]": "desc",
    }

    for idx, name in enumerate(columns):
        payload[f"columns[{idx}][data]"] = str(idx)
        payload[f"columns[{idx}][name]"] = name
        payload[f"columns[{idx}][searchable]"] = "true"
        payload[f"columns[{idx}][orderable]"] = "false" if name in {"col_state", "col_book_order", "col_image", "col_link", "col_available", "col_perc_refund", "col_agent_comm"} else "true"
        payload[f"columns[{idx}][search][value]"] = ""
        payload[f"columns[{idx}][search][regex]"] = "false"

    return payload
