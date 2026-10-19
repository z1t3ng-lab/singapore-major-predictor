"""Replaceable data-source boundary. HLTV fetching stays deliberately disabled."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class DataProvider(Protocol):
    name: str

    def refresh(self) -> None:
        """Refresh data, respecting the provider's documented access rules."""


@dataclass
class ManualImportProvider:
    name: str = "user-supplied CSV/JSON"

    def refresh(self) -> None:
        raise RuntimeError("Manual imports are submitted through /api/import; there is no remote refresh.")


@dataclass
class HltvProvider:
    name: str = "HLTV"
    enabled: bool = False
    reason: str = (
        "Automated collection is disabled. The public terms page did not establish permission for "
        "automated event-stat collection, and robots.txt could not be verified through the available "
        "check. Import a permitted CSV/JSON export instead. No official HLTV API is assumed."
    )

    def refresh(self) -> None:
        if not self.enabled:
            raise RuntimeError(self.reason)
        raise NotImplementedError("An enabled HLTV provider must implement conservative, cached requests first.")
