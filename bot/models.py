from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class Product:
    id: int
    country: str
    state: int
    image: str
    review_type: str
    name: str
    link: str
    manager: str
    shop: str
    daily: int
    total: int
    available: int
    agent_comm: str
    paypal_fees: str
    note: str
    raw_row: list[Any]
    source: str = "NAZAR"

    @property
    def is_available(self) -> bool:
        return self.available > 0

    def image_url(self, domain: str) -> str | None:
        if not self.image:
            return None
        if self.image.startswith("http://") or self.image.startswith("https://"):
            return self.image
        return f"{domain}{self.image}"

    @classmethod
    def from_row(cls, row: list[Any], default_country: str = "es") -> "Product":
        return cls.from_nazar_row(row=row, default_country=default_country)

    @classmethod
    def from_nazar_row(cls, row: list[Any], default_country: str = "es") -> "Product":
        if len(row) < 15:
            raise ValueError(f"Unexpected row format: {row!r}")

        offset = 0
        country = default_country
        if isinstance(row[0], str) and len(row) >= 16:
            country = row[0].lower()
            offset = 1

        return cls(
            id=_to_int(row[offset + 0]),
            country=country,
            state=_to_int(row[offset + 1]),
            image=str(row[offset + 3] or ""),
            review_type=str(row[offset + 4] or ""),
            name=str(row[offset + 5] or ""),
            link=str(row[offset + 6] or ""),
            manager=str(row[offset + 7] or ""),
            shop=str(row[offset + 8] or ""),
            daily=_to_int(row[offset + 9]),
            total=_to_int(row[offset + 10]),
            available=_to_int(row[offset + 11]),
            agent_comm=str(row[offset + 12] or ""),
            paypal_fees=str(row[offset + 13] or ""),
            note=str(row[offset + 14] or ""),
            raw_row=list(row),
            source="NAZAR",
        )

    @classmethod
    def from_amazstar_row(cls, row: list[Any], default_country: str = "es") -> "Product":
        if len(row) < 17:
            raise ValueError(f"Unexpected Amazstar row format: {row!r}")

        offset = 0
        country = default_country.lower()
        if isinstance(row[0], str) and len(row[0]) <= 3:
            country = str(row[0]).lower()
            offset = 1

        # Amazstar row layout (ES table):
        # [state, book_order, id, name, image, link, shop, manager, total, daily, available, paypal, review_type, feedback, refund, note, agent_comm]
        return cls(
            id=_to_int(row[offset + 2]),
            country=country,
            state=_to_int(row[offset + 0]),
            image=str(row[offset + 4] or ""),
            review_type=str(row[offset + 12] or ""),
            name=str(row[offset + 3] or ""),
            link=str(row[offset + 5] or ""),
            manager=str(row[offset + 7] or ""),
            shop=str(row[offset + 6] or ""),
            daily=_to_int(row[offset + 9]),
            total=_to_int(row[offset + 8]),
            available=_to_int(row[offset + 10]),
            agent_comm=str(row[offset + 16] or ""),
            paypal_fees=str(row[offset + 11] or ""),
            note=str(row[offset + 15] or ""),
            raw_row=list(row),
            source="AMAZSTAR",
        )
