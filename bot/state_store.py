from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from bot.models import Product


@dataclass(slots=True)
class ScanDiff:
    first_sync: bool
    newly_available: list[Product]
    new_products_available: list[Product]
    known_products: int


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"sites": {}}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and "sites" in loaded:
                return loaded
            # Backward compatibility for old single-site state format.
            if isinstance(loaded, dict) and "availability" in loaded:
                return {
                    "sites": {
                        "nazar": {
                            "initialized": bool(loaded.get("initialized", False)),
                            "availability": loaded.get("availability", {}),
                        }
                    }
                }
            return {"sites": {}}
        except Exception:
            return {"sites": {}}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _site_state(self, site_key: str) -> dict:
        sites = self._state.setdefault("sites", {})
        site = sites.setdefault(site_key, {"initialized": False, "availability": {}})
        site.setdefault("initialized", False)
        site.setdefault("availability", {})
        return site

    def process_scan(self, products: list[Product], site_key: str) -> ScanDiff:
        site_state = self._site_state(site_key)
        previous: dict[str, int] = {
            str(k): int(v) for k, v in site_state.get("availability", {}).items()
        }
        initialized = bool(site_state.get("initialized", False))

        newly_available: list[Product] = []
        new_products_available: list[Product] = []
        next_map: dict[str, int] = {}

        for product in products:
            pid = str(product.id)
            next_map[pid] = int(product.available)
            old_value = previous.get(pid)

            if old_value is None:
                if product.is_available and initialized:
                    new_products_available.append(product)
                continue

            if old_value <= 0 and product.available > 0 and initialized:
                newly_available.append(product)

        site_state["availability"] = next_map
        if not initialized:
            site_state["initialized"] = True
        self._save()

        return ScanDiff(
            first_sync=not initialized,
            newly_available=newly_available,
            new_products_available=new_products_available,
            known_products=len(next_map),
        )
