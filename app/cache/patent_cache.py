"""Local cache for patent data with TTL-based invalidation.

Supports caching of:
- Patent search results
- Legal status lookups
- Patent details

Structure:
- data/cache/patents/ru/{hash}.json
- data/cache/patents/eapo/{hash}.json
- data/cache/patents/international/{hash}.json
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("pharm_agent.cache")


class PatentCache:
    """Local cache for patent data.

    TTL-based invalidation for legal status data.
    Cache entries are stored as JSON files with metadata.
    """

    def __init__(self, cache_dir: Path, ttl_days: int = 7) -> None:
        """Initialize the cache.

        Args:
            cache_dir: Directory to store cache files.
            ttl_days: Time-to-live in days for cache entries.
        """
        self.cache_dir = Path(cache_dir)
        self.ttl = timedelta(days=ttl_days)
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        """Create cache directory if it doesn't exist."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Failed to create cache directory %s: %s", self.cache_dir, e)

    def _key_to_path(self, key: str) -> Path:
        """Convert cache key to file path."""
        hash_key = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{hash_key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        """Get cached entry if exists and not expired.

        Args:
            key: Cache key (e.g., "rospatent:search:ibuprofen").

        Returns:
            Cached data dict or None if not found/expired.
        """
        path = self._key_to_path(key)
        if not path.exists():
            return None

        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(entry["cached_at"])

            # Make cached_at timezone-aware if it isn't
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)

            if now - cached_at > self.ttl:
                logger.debug("Cache expired for key: %s", key)
                return None

            logger.debug("Cache hit for key: %s", key)
            return entry.get("data")

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Failed to read cache entry %s: %s", key, e)
            return None

    def set(self, key: str, data: dict[str, Any]) -> bool:
        """Store entry in cache.

        Args:
            key: Cache key.
            data: Data to cache.

        Returns:
            True if successfully cached, False otherwise.
        """
        self._ensure_dir()
        path = self._key_to_path(key)

        entry = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "key": key,
            "data": data,
        }

        try:
            path.write_text(
                json.dumps(entry, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug("Cached data for key: %s", key)
            return True
        except Exception as e:
            logger.warning("Failed to cache data for key %s: %s", key, e)
            return False

    def delete(self, key: str) -> bool:
        """Delete a cache entry.

        Args:
            key: Cache key to delete.

        Returns:
            True if deleted, False if not found or error.
        """
        path = self._key_to_path(key)
        try:
            if path.exists():
                path.unlink()
                logger.debug("Deleted cache entry: %s", key)
                return True
            return False
        except Exception as e:
            logger.warning("Failed to delete cache entry %s: %s", key, e)
            return False

    def clear(self) -> int:
        """Clear all cache entries.

        Returns:
            Number of entries deleted.
        """
        count = 0
        try:
            for f in self.cache_dir.glob("*.json"):
                try:
                    f.unlink()
                    count += 1
                except Exception:
                    pass
            logger.info("Cleared %d cache entries from %s", count, self.cache_dir)
        except Exception as e:
            logger.warning("Failed to clear cache: %s", e)
        return count

    def clear_expired(self) -> int:
        """Remove expired cache entries.

        Returns:
            Number of expired entries removed.
        """
        count = 0
        now = datetime.now(timezone.utc)

        try:
            for f in self.cache_dir.glob("*.json"):
                try:
                    entry = json.loads(f.read_text(encoding="utf-8"))
                    cached_at = datetime.fromisoformat(entry["cached_at"])
                    if cached_at.tzinfo is None:
                        cached_at = cached_at.replace(tzinfo=timezone.utc)

                    if now - cached_at > self.ttl:
                        f.unlink()
                        count += 1
                except Exception:
                    pass
            logger.debug("Removed %d expired cache entries", count)
        except Exception as e:
            logger.warning("Failed to clear expired entries: %s", e)
        return count

    def stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with cache stats: total entries, expired count, total size.
        """
        total = 0
        expired = 0
        size_bytes = 0
        now = datetime.now(timezone.utc)

        try:
            for f in self.cache_dir.glob("*.json"):
                total += 1
                size_bytes += f.stat().st_size

                try:
                    entry = json.loads(f.read_text(encoding="utf-8"))
                    cached_at = datetime.fromisoformat(entry["cached_at"])
                    if cached_at.tzinfo is None:
                        cached_at = cached_at.replace(tzinfo=timezone.utc)
                    if now - cached_at > self.ttl:
                        expired += 1
                except Exception:
                    expired += 1  # Treat unreadable as expired
        except Exception as e:
            logger.warning("Failed to get cache stats: %s", e)

        return {
            "cache_dir": str(self.cache_dir),
            "total_entries": total,
            "expired_entries": expired,
            "valid_entries": total - expired,
            "total_size_bytes": size_bytes,
            "total_size_kb": round(size_bytes / 1024, 2),
            "ttl_days": self.ttl.days,
        }


def make_cache_key(connector_name: str, operation: str, *args: str) -> str:
    """Create a standardized cache key.

    Args:
        connector_name: Name of the connector (e.g., "rospatent").
        operation: Type of operation (e.g., "search", "status").
        *args: Additional key components (e.g., search term, patent number).

    Returns:
        Cache key string.

    Example:
        >>> make_cache_key("rospatent", "search", "ibuprofen")
        "rospatent:search:ibuprofen"
    """
    components = [connector_name, operation] + list(args)
    # Normalize: lowercase, strip whitespace
    normalized = [c.lower().strip() for c in components if c]
    return ":".join(normalized)
