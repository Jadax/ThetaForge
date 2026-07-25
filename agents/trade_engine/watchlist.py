"""
Favorites / Watchlist persistence.
Per-user stock lists stored in JSON file.
"""
import json
import os
from typing import List, Dict, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlists.json")


@dataclass
class WatchlistItem:
    symbol: str
    added_at: str = ""
    notes: str = ""
    tags: List[str] = field(default_factory=list)
    custom_delta: float = 0.3
    custom_dte: int = 45
    custom_strategies: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.added_at:
            self.added_at = datetime.utcnow().isoformat()


class FavoritesStore:
    """Simple JSON-backed watchlist store."""

    def __init__(self, filepath: str = WATCHLIST_FILE):
        self.filepath = filepath
        self._ensure_file()

    def _ensure_file(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        if not os.path.exists(self.filepath):
            self._write({"default": []})

    def _read(self) -> Dict[str, List[Dict]]:
        with open(self.filepath, "r") as f:
            return json.load(f)

    def _write(self, data: Dict[str, List[Dict]]):
        with open(self.filepath, "w") as f:
            json.dump(data, f, indent=2)

    def list_symbols(self, user: str = "default") -> List[WatchlistItem]:
        data = self._read()
        items = data.get(user, [])
        return [WatchlistItem(**item) for item in items]

    def add_symbol(
        self, symbol: str, user: str = "default",
        notes: str = "", tags: List[str] = None,
        custom_delta: float = 0.3, custom_dte: int = 45,
        custom_strategies: List[str] = None,
    ) -> WatchlistItem:
        data = self._read()
        if user not in data:
            data[user] = []

        existing = [i for i in data[user] if i["symbol"] == symbol.upper()]
        if existing:
            return WatchlistItem(**existing[0])

        item = WatchlistItem(
            symbol=symbol.upper(),
            notes=notes,
            tags=tags or [],
            custom_delta=custom_delta,
            custom_dte=custom_dte,
            custom_strategies=custom_strategies or [],
        )
        data[user].append(asdict(item))
        self._write(data)
        return item

    def remove_symbol(self, symbol: str, user: str = "default") -> bool:
        data = self._read()
        if user not in data:
            return False
        before = len(data[user])
        data[user] = [i for i in data[user] if i["symbol"] != symbol.upper()]
        self._write(data)
        return len(data[user]) < before

    def update_symbol(
        self, symbol: str, user: str = "default",
        notes: str = None, tags: List[str] = None,
        custom_delta: float = None, custom_dte: int = None,
        custom_strategies: List[str] = None,
    ) -> Optional[WatchlistItem]:
        data = self._read()
        if user not in data:
            return None

        for item in data[user]:
            if item["symbol"] == symbol.upper():
                if notes is not None:
                    item["notes"] = notes
                if tags is not None:
                    item["tags"] = tags
                if custom_delta is not None:
                    item["custom_delta"] = custom_delta
                if custom_dte is not None:
                    item["custom_dte"] = custom_dte
                if custom_strategies is not None:
                    item["custom_strategies"] = custom_strategies
                self._write(data)
                return WatchlistItem(**item)
        return None

    def symbol_exists(self, symbol: str, user: str = "default") -> bool:
        data = self._read()
        return any(i["symbol"] == symbol.upper() for i in data.get(user, []))
