from __future__ import annotations


def build_products_payload(
    country: str = "es",
    length: int = 200,
    *,
    start: int = 0,
    draw: int = 1,
) -> dict[str, str]:
    columns = [
        "id",
        "state",
        "actions",
        "image",
        "review_type",
        "name",
        "link",
        "manager_name",
        "seller_shop",
        "daily_quantity",
        "total_quantity",
        "available",
        "agent_comm",
        "paypal_fees",
        "note",
    ]
    payload: dict[str, str] = {
        "draw": str(draw),
        "start": str(max(start, 0)),
        "length": str(max(length, 1)),
        "search[value]": "",
        "search[regex]": "false",
        "country": country,
        "filter": "",
        "order[0][column]": "0",
        "order[0][dir]": "desc",
        "order[0][name]": "id",
    }
    for idx, name in enumerate(columns):
        payload[f"columns[{idx}][data]"] = str(idx)
        payload[f"columns[{idx}][name]"] = name
        payload[f"columns[{idx}][searchable]"] = "true"
        payload[f"columns[{idx}][orderable]"] = "false" if name in {"actions", "image", "link"} else "true"
        payload[f"columns[{idx}][search][value]"] = ""
        payload[f"columns[{idx}][search][regex]"] = "false"
    return payload
