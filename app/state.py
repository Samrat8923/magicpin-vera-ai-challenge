from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
from typing import Any, Dict, List, Optional


@dataclass
class TurnRecord:
    turn_number: int
    from_role: str
    message: str
    timestamp: datetime
    action: Optional[str] = None
    cta: Optional[str] = None
    intent: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    turns: List[TurnRecord] = field(default_factory=list)
    auto_reply_count: int = 0
    opt_out: bool = False
    bot_sent_bodies: List[str] = field(default_factory=list)
    last_intent: Optional[str] = None
    last_response: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._conversations: Dict[str, ConversationState] = {}
        self._merchant_auto_replies: Dict[str, int] = {}

    def get_or_create(
        self,
        conversation_id: str,
        merchant_id: Optional[str] = None,
        customer_id: Optional[str] = None,
    ) -> ConversationState:
        with self._lock:
            if conversation_id not in self._conversations:
                self._conversations[conversation_id] = ConversationState(
                    conversation_id=conversation_id,
                    merchant_id=merchant_id,
                    customer_id=customer_id,
                )
            conv = self._conversations[conversation_id]
            if merchant_id and not conv.merchant_id:
                conv.merchant_id = merchant_id
            if customer_id and not conv.customer_id:
                conv.customer_id = customer_id
            return conv

    def get(self, conversation_id: str) -> Optional[ConversationState]:
        with self._lock:
            return self._conversations.get(conversation_id)

    def add_turn(
        self,
        conversation_id: str,
        turn_number: int,
        from_role: str,
        message: str,
        action: Optional[str] = None,
        cta: Optional[str] = None,
        intent: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        merchant_id: Optional[str] = None,
        customer_id: Optional[str] = None,
    ) -> TurnRecord:
        with self._lock:
            conv = self.get_or_create(conversation_id, merchant_id, customer_id)
            record = TurnRecord(
                turn_number=turn_number,
                from_role=from_role,
                message=message,
                timestamp=datetime.now(timezone.utc),
                action=action,
                cta=cta,
                intent=intent,
                metadata=metadata or {},
            )
            conv.turns.append(record)
            conv.updated_at = datetime.now(timezone.utc)
            return record

    def mark_opt_out(self, conversation_id: str) -> None:
        with self._lock:
            conv = self.get(conversation_id)
            if conv:
                conv.opt_out = True
                conv.updated_at = datetime.now(timezone.utc)

    def increment_auto_reply(self, conversation_id: str) -> int:
        with self._lock:
            conv = self.get_or_create(conversation_id)
            conv.auto_reply_count += 1
            conv.updated_at = datetime.now(timezone.utc)
            return conv.auto_reply_count

    def reset_auto_reply(self, conversation_id: str) -> None:
        with self._lock:
            conv = self.get(conversation_id)
            if conv:
                conv.auto_reply_count = 0
                conv.updated_at = datetime.now(timezone.utc)

    def increment_merchant_auto_reply(self, merchant_id: str) -> int:
        with self._lock:
            cur = self._merchant_auto_replies.get(merchant_id, 0) + 1
            self._merchant_auto_replies[merchant_id] = cur
            return cur

    def reset_merchant_auto_reply(self, merchant_id: str) -> None:
        with self._lock:
            if merchant_id in self._merchant_auto_replies:
                self._merchant_auto_replies[merchant_id] = 0

    def clear(self) -> None:
        with self._lock:
            self._conversations.clear()
            self._merchant_auto_replies.clear()


@dataclass
class SuppressionEntry:
    key: str
    dispatched_at: datetime
    expires_at: Optional[datetime] = None
    status: str = "active"


class SuppressionRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._registry: Dict[str, SuppressionEntry] = {}
        self._merchant_opt_outs: Dict[str, datetime] = {}
        self._customer_opt_outs: Dict[str, datetime] = {}

    def is_suppressed(self, key: str, now: Optional[datetime] = None) -> bool:
        with self._lock:
            entry = self._registry.get(key)
            if not entry:
                return False
            check_time = now or datetime.now(timezone.utc)
            if entry.expires_at and check_time > entry.expires_at:
                del self._registry[key]
                return False
            return entry.status == "active"

    def mark_dispatched(
        self,
        key: str,
        expires_at: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> SuppressionEntry:
        with self._lock:
            dispatch_time = now or datetime.now(timezone.utc)
            entry = SuppressionEntry(
                key=key,
                dispatched_at=dispatch_time,
                expires_at=expires_at,
                status="active",
            )
            self._registry[key] = entry
            return entry

    def clear_expired(self, now: Optional[datetime] = None) -> int:
        with self._lock:
            check_time = now or datetime.now(timezone.utc)
            expired_keys = [
                k for k, v in self._registry.items() if v.expires_at and check_time > v.expires_at
            ]
            for k in expired_keys:
                del self._registry[k]
            return len(expired_keys)

    def suppress_merchant(self, merchant_id: str, until: Optional[datetime] = None) -> None:
        with self._lock:
            self._merchant_opt_outs[merchant_id] = until or datetime.max.replace(tzinfo=timezone.utc)

    def is_merchant_suppressed(self, merchant_id: str, now: Optional[datetime] = None) -> bool:
        with self._lock:
            until = self._merchant_opt_outs.get(merchant_id)
            if not until:
                return False
            check_time = now or datetime.now(timezone.utc)
            if check_time > until:
                del self._merchant_opt_outs[merchant_id]
                return False
            return True

    def suppress_customer(self, customer_id: str, until: Optional[datetime] = None) -> None:
        with self._lock:
            self._customer_opt_outs[customer_id] = until or datetime.max.replace(tzinfo=timezone.utc)

    def is_customer_suppressed(self, customer_id: str, now: Optional[datetime] = None) -> bool:
        with self._lock:
            until = self._customer_opt_outs.get(customer_id)
            if not until:
                return False
            check_time = now or datetime.now(timezone.utc)
            if check_time > until:
                del self._customer_opt_outs[customer_id]
                return False
            return True

    def clear(self) -> None:
        with self._lock:
            self._registry.clear()
            self._merchant_opt_outs.clear()
            self._customer_opt_outs.clear()


# Global singleton instances
conversation_store = ConversationStore()
suppression_registry = SuppressionRegistry()
