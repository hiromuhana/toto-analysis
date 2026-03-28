"""File-based cache with TTL expiration."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from toto.config import CACHE_DIR, CACHE_TTL_HOURS

logger = logging.getLogger(__name__)


def _cache_path(key: str) -> Path:
    safe_key = key.replace("/", "_").replace(":", "_").replace("?", "_")
    return CACHE_DIR / f"{safe_key}.json"


def get(key: str) -> Any | None:
    """Retrieve a cached value if it exists and hasn't expired.

    Args:
        key: Cache key identifier.

    Returns:
        Cached data or None if miss/expired.
    """
    path = _cache_path(key)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stored_at = data.get("_cached_at", 0)
        ttl_seconds = CACHE_TTL_HOURS * 3600
        if time.time() - stored_at > ttl_seconds:
            logger.debug("Cache expired for key=%s", key)
            path.unlink(missing_ok=True)
            return None
        logger.debug("Cache hit for key=%s", key)
        return data.get("value")
    except (json.JSONDecodeError, KeyError):
        logger.warning("Corrupted cache entry for key=%s, removing", key)
        path.unlink(missing_ok=True)
        return None


def set(key: str, value: Any) -> None:
    """Store a value in the cache.

    Args:
        key: Cache key identifier.
        value: Data to cache (must be JSON-serializable).
    """
    path = _cache_path(key)
    payload = {"_cached_at": time.time(), "value": value}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.debug("Cache set for key=%s", key)


def invalidate(key: str) -> None:
    """Remove a cache entry."""
    path = _cache_path(key)
    path.unlink(missing_ok=True)


def clear_all() -> int:
    """Remove all cache entries. Returns count of removed files."""
    count = 0
    for f in CACHE_DIR.glob("*.json"):
        f.unlink()
        count += 1
    logger.info("Cleared %d cache entries", count)
    return count
