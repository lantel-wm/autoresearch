from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "DownloadConfig",
    "download_stock_pool",
    "download_symbols",
    "fetch_symbol_minute",
    "save_symbol_minute",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    module = import_module(".akshare_sina", __name__)
    return getattr(module, name)
