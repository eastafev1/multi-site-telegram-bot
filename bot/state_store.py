from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from bot.models import Product

LOGGER = logging.getLogger("bot.state_store")


@dataclass(slots=True)
class ScanDiff:
    first_sync: bool
    newly_available: list[Product]
    new_products_available: list[Product]
    known_products: int


class StateStore:
    def __init__(self, path: Path, trace_product_id: int = 0):
        self.path = path
        self.trace_product_id = int(trace_product_id or 0)
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
        current_map: dict[str, int] = {}
        # Keep historical IDs in state to avoid repeated "new product" alerts
        # when transient items disappear/reappear between scans.
        updated_map: dict[str, int] = dict(previous)
        trace_pid = str(self.trace_product_id) if self.trace_product_id and site_key == "nazar" else ""

        for product in products:
            pid = str(product.id)
            current_map[pid] = int(product.available)
            updated_map[pid] = int(product.available)
            old_value = previous.get(pid)

            if trace_pid and pid == trace_pid:
                LOGGER.warning(
                    "trace product in process_scan | site=%s | pid=%s | old_value=%s | new_value=%s | initialized=%s | name=%s | shop=%s",
                    site_key,
                    pid,
                    old_value,
                    product.available,
                    initialized,
                    product.name,
                    product.shop,
                )

            if old_value is None:
                if product.is_available and initialized:
                    new_products_available.append(product)
                continue

            if old_value <= 0 and product.available > 0 and initialized:
                newly_available.append(product)

        if trace_pid:
            in_previous = trace_pid in previous
            in_current = trace_pid in current_map
            in_updated = trace_pid in updated_map
            LOGGER.warning(
                "trace product state decision | site=%s | pid=%s | in_previous=%s | in_current=%s | in_saved_map=%s | previous_value=%s | current_value=%s",
                site_key,
                trace_pid,
                in_previous,
                in_current,
                in_updated,
                previous.get(trace_pid),
                current_map.get(trace_pid),
            )

        site_state["availability"] = updated_map
        if not initialized:
            site_state["initialized"] = True
        self._save()

        return ScanDiff(
            first_sync=not initialized,
            newly_available=newly_available,
            new_products_available=new_products_available,
            known_products=len(current_map),
        )
