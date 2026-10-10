import threading
import time
import hashlib as hl


class PhotoCache:
    def __init__(self, ttl: float = 24 * 3600, max_items: int = 500):
        self.ttl = ttl
        self.max_items = max_items
        self._items: dict[str, tuple[float, dict]] = {}  # key -> (timestamp, result)
        self._lock = threading.Lock()

    @staticmethod
    def key(image_bytes: bytes) -> str:
        """Return a unique key for the given image bytes."""
        return hl.sha256(image_bytes).hexdigest()

    def get(self, key: str) -> dict | None:
        """Return the cached analysis result for this key, or None if missing or expired."""
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            timestamp, result = item
            if (time.time() - timestamp) > self.ttl:
                del self._items[key]
                return None
            return result

    def set(self, key: str, result: dict):
        """Store an analysis result under this key."""
        with self._lock:
            if len(self._items) >= self.max_items:
                oldest_key = min(self._items, key=lambda k: self._items[k][0])
                del self._items[oldest_key]
            self._items[key] = (time.time(), result)
