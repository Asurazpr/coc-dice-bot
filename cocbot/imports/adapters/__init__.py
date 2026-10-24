"""Registered spreadsheet templates. Add an adapter here (plus tests) to support a new layout."""
from __future__ import annotations

from typing import List, Optional

from cocbot.imports.adapters.coc7_zh_v135 import Coc7ZhV135Adapter
from cocbot.imports.model import SheetAdapter

ADAPTERS: List[SheetAdapter] = [Coc7ZhV135Adapter()]


def detect(book) -> Optional[SheetAdapter]:
    """The adapter whose exact signature matches, or None (no guessing)."""
    matches = [a for a in ADAPTERS if a.detect(book)]
    return matches[0] if len(matches) == 1 else None


def supported_labels() -> List[str]:
    return [a.label for a in ADAPTERS]
