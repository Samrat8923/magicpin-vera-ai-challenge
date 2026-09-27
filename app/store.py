from dataclasses import dataclass
from datetime import datetime, timezone
import threading
from typing import Any, Dict, List, Optional, Tuple

SCOPES = ("category", "merchant", "customer", "trigger")


@dataclass
class StoredContext:
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    stored_at: datetime


class ContextStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._store: Dict[Tuple[str, str], StoredContext] = {}

    def upsert(
        self, scope: str, context_id: str, version: int, payload: Dict[str, Any]
    ) -> Tuple[bool, int]:
        """
        Atomically insert or update a context.
        Returns:
            (accepted, current_version)
            If accepted is False, current_version is the already-stored version.
        """
        with self._lock:
            key = (scope, context_id)
            existing = self._store.get(key)
            if existing is not None and version <= existing.version:
                return False, existing.version

            self._store[key] = StoredContext(
                scope=scope,
                context_id=context_id,
                version=version,
                payload=payload,
                stored_at=datetime.now(timezone.utc),
            )
            return True, version

    def get(self, scope: str, context_id: str) -> Optional[StoredContext]:
        with self._lock:
            return self._store.get((scope, context_id))

    def get_counts(self) -> Dict[str, int]:
        with self._lock:
            counts = {s: 0 for s in SCOPES}
            for (scope, _), _ in self._store.items():
                if scope in counts:
                    counts[scope] += 1
                else:
                    counts[scope] = 1
            return counts

    def list_by_scope(self, scope: str) -> List[StoredContext]:
        with self._lock:
            return [ctx for (s, _), ctx in self._store.items() if s == scope]

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# Global singleton instance
context_store = ContextStore()
