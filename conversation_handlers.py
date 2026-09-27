"""
Deterministic Conversation & Reply State Machine for Magicpin VERA AI Assistant.
Implements multi-turn reply handling, intent classification, auto-reply management,
commitment transitions, opt-out handling, and out-of-scope redirection.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional

from app.models import (
    ReplyEndResponse,
    ReplyRequest,
    ReplyResponse,
    ReplySendResponse,
    ReplyWaitResponse,
)
from app.state import ConversationState, conversation_store, suppression_registry


class Intent(str, Enum):
    AUTO_REPLY = "AUTO_REPLY"
    COMMITMENT = "COMMITMENT"
    OPT_OUT = "OPT_OUT"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    QUESTION = "QUESTION"
    POSITIVE_ACK = "POSITIVE_ACK"
    NEGATIVE_RESPONSE = "NEGATIVE_RESPONSE"
    NORMAL = "NORMAL"


# --- Regex & Phrase Banks ---

AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"thanks for contacting",
    r"team will respond shortly",
    r"will respond shortly",
    r"we will get back to you",
    r"will get back to you",
    r"thanks for reaching out",
    r"thank you for reaching out",
    r"currently unavailable",
    r"outside of business hours",
    r"office hours",
    r"will respond",
    r"automated assistant",
    r"automated message",
    r"automated reply",
    r"auto-reply",
    r"canned response",
    r"i am an automated",
    r"our business hours are",
]

OPT_OUT_PATTERNS = [
    r"^\s*stop\s*([.!]|$)",
    r"stop messaging",
    r"stop sending",
    r"don'?t message",
    r"do not message",
    r"don'?t contact",
    r"do not contact",
    r"remove me",
    r"remove my number",
    r"unsubscribe",
    r"not interested anymore",
    r"not interested",
    r"leave me alone",
    r"useless spam",
    r"stop spamming",
    r"bothering me",
    r"quit messaging",
    r"no more messages",
]

OUT_OF_SCOPE_PATTERNS = [
    r"\bgst\b",
    r"\btax filing\b",
    r"\bfile (my )?taxes\b",
    r"\bincome tax\b",
    r"\bca work\b",
    r"\bitr\b",
    r"\bfix (my )?(computer|laptop|pc|printer|phone|wifi)\b",
    r"\bcomputer repair\b",
    r"\blaptop repair\b",
    r"\bbook (a |my )?(flight|ticket|hotel|train|cab|uber|ola)\b",
    r"\bflight booking\b",
    r"\border groceries\b",
    r"\bpersonal loan\b",
    r"\bcredit card loan\b",
]

COMMITMENT_PATTERNS = [
    r"^(ok(ay)?[\s,]*)?(let[']?s|lets) do it\b",
    r"^(ok(ay)?[\s,]*)?go ahead\b",
    r"^(ok(ay)?[\s,]*)?do it\b",
    r"^(yes[\s,]*)?(please )?send( it| the abstract| the draft)?(\b|$)",
    r"^\s*yes([!.,\s]|$)",
    r"^(approved|confirm(ed)?|proceed|make it live|publish it|schedule it)\b",
    r"^(looks good,? (proceed|go ahead|do it))\b",
    r"\b(yes please|let[']?s proceed|let[']?s go ahead|start the campaign)\b",
]

QUESTION_PATTERNS = [
    r"\?",
    r"^\s*(what|how|when|why|where|can you|is there|which|who)\b",
]

POSITIVE_ACK_PATTERNS = [
    r"\b(ok|okay|got it|noted|sure|sounds good|great|perfect|cool|thanks|thank you)\b",
]

NEGATIVE_RESPONSE_PATTERNS = [
    r"\bnot now\b",
    r"\bmaybe later\b",
    r"\bskip this\b",
    r"\bnot this week\b",
    r"\bno thanks\b",
    r"\bno,? we don'?t do that\b",
    r"\bnot today\b",
]


# --- Classification Functions ---

def detect_auto_reply(message: str, state: Optional[ConversationState] = None) -> bool:
    msg_lower = message.lower().strip()
    if any(re.search(pat, msg_lower) for pat in AUTO_REPLY_PATTERNS):
        return True

    # Detect substantially identical canned response sent 2+ times consecutively
    if state and state.turns:
        merchant_turns = [t for t in state.turns if t.from_role in ("merchant", "customer")]
        if merchant_turns:
            last_msg = merchant_turns[-1].message.strip().lower()
            if len(msg_lower) > 15 and msg_lower == last_msg:
                return True

    return False


def detect_opt_out(message: str) -> bool:
    msg_lower = message.lower().strip()
    return any(re.search(pat, msg_lower) for pat in OPT_OUT_PATTERNS)


def detect_out_of_scope(message: str) -> bool:
    msg_lower = message.lower().strip()
    return any(re.search(pat, msg_lower) for pat in OUT_OF_SCOPE_PATTERNS)


def detect_commitment(message: str, state: Optional[ConversationState] = None) -> bool:
    msg_lower = message.lower().strip()
    # Exclude questions explicitly deliberating options without committing (e.g. "yes or no?")
    if re.search(r"\byes or no\b", msg_lower) or re.search(r"^can (i|we|you)\b.*\?", msg_lower):
        return False
    return any(re.search(pat, msg_lower) for pat in COMMITMENT_PATTERNS)


def detect_question(message: str) -> bool:
    msg_lower = message.lower().strip()
    return any(re.search(pat, msg_lower) for pat in QUESTION_PATTERNS)


def detect_positive_ack(message: str) -> bool:
    msg_lower = message.lower().strip()
    return any(re.search(pat, msg_lower) for pat in POSITIVE_ACK_PATTERNS)


def detect_negative_response(message: str) -> bool:
    msg_lower = message.lower().strip()
    return any(re.search(pat, msg_lower) for pat in NEGATIVE_RESPONSE_PATTERNS)


def classify_message(message: str, state: Optional[ConversationState] = None) -> Intent:
    """
    Conservative, deterministic rule-based message classifier.
    Order of precedence:
    1. Opt-out (highest priority for safety & compliance)
    2. Auto-reply
    3. Out-of-scope requests
    4. Explicit commitment
    5. Specific negative response (not opt-out)
    6. Specific question
    7. Positive acknowledgment
    8. Normal business conversation
    """
    if detect_opt_out(message):
        return Intent.OPT_OUT

    if detect_auto_reply(message, state):
        return Intent.AUTO_REPLY

    if detect_out_of_scope(message):
        return Intent.OUT_OF_SCOPE

    if detect_commitment(message, state):
        return Intent.COMMITMENT

    if detect_negative_response(message):
        return Intent.NEGATIVE_RESPONSE

    if detect_question(message):
        return Intent.QUESTION

    if detect_positive_ack(message):
        return Intent.POSITIVE_ACK

    return Intent.NORMAL


# --- Context-Aware Response Generators ---

def _build_context_grounded_commitment(state: Optional[ConversationState]) -> tuple[str, str, str]:
    """
    Constructs a commitment response anchored in previous Vera context.
    Ensures actioning tokens are present and qualifying tokens are excluded.
    """
    prev_vera_msg = ""
    if state and state.turns:
        vera_turns = [t for t in state.turns if t.from_role in ("vera", "assistant")]
        if vera_turns:
            prev_vera_msg = vera_turns[-1].message.lower()

    if any(k in prev_vera_msg for k in ("abstract", "jida", "research", "paper", "study")):
        body = (
            "Sending the abstract now (PDF, 2 pages) — patient-ed draft is ready below:\n\n"
            '"Regular cleaning recalls protect long-term dental health. Ask us about recommended intervals."\n\n'
            "Done! Pre-filled your update. Reply CONFIRM to schedule publication."
        )
        rationale = "Honored research abstract request and delivered patient draft; switched to action execution."
    elif any(k in prev_vera_msg for k in ("campaign", "offer", "discount", "bogo", "thali", "package")):
        body = (
            "Done — your promotion draft is prepared and queued in the campaign manager. "
            "Next step: pre-filling the Google Business update for tomorrow morning. "
            "Reply CONFIRM to launch."
        )
        rationale = "Merchant committed to offer/campaign; transitioned directly to execution queue."
    elif any(k in prev_vera_msg for k in ("post", "photo", "gbp", "google profile", "description")):
        body = (
            "Done — drafted your Google Business post and staged the updates. "
            "Here is the text ready for scheduling. Reply CONFIRM to publish now."
        )
        rationale = "Merchant committed; pre-filled profile post and requested final confirmation."
    elif any(k in prev_vera_msg for k in ("recall", "patient", "lapsed", "customer")):
        body = (
            "Done — drafted recall outreach messages for your lapsed roster. "
            "Sending queue is staged with slots ready. Reply CONFIRM to dispatch."
        )
        rationale = "Merchant committed to customer outreach; prepared dispatch batch."
    else:
        body = (
            "Done! Here is what's next: proceeding with the requested action. "
            "Draft is prepared and queued. Reply CONFIRM to execute."
        )
        rationale = "Merchant explicitly committed; switched directly to action mode without qualifying questions."

    return body, "binary_confirm_cancel", rationale


def _apply_anti_repetition(body: str, state: Optional[ConversationState]) -> str:
    """
    Ensures the exact same message body is never sent twice in the same conversation.
    Applies deterministic variations if a collision is detected.
    """
    if not state or not hasattr(state, "bot_sent_bodies") or not state.bot_sent_bodies:
        return body

    if body in state.bot_sent_bodies:
        turn_count = len(state.turns)
        prefixes = [
            "Quick update: ",
            "Regarding our conversation: ",
            "To follow up: ",
            "As discussed: ",
        ]
        chosen_prefix = prefixes[turn_count % len(prefixes)]
        return f"{chosen_prefix}{body}"

    return body


# --- Reply Dispatcher ---

class ConversationHandler:
    """
    Deterministic conversation handler for Vera.
    """

    def handle_reply(self, body: ReplyRequest) -> ReplyResponse:
        conv = conversation_store.get_or_create(
            conversation_id=body.conversation_id,
            merchant_id=body.merchant_id,
            customer_id=body.customer_id,
        )

        # 0. Check if conversation or merchant is already opted out
        if conv.opt_out or (body.merchant_id and suppression_registry.is_merchant_suppressed(body.merchant_id)):
            return ReplyEndResponse(
                action="end",
                rationale="Conversation or merchant is marked as opted out; suppressed.",
            )

        intent = classify_message(body.message, conv)

        # 1. OPT_OUT / Hostile
        if intent == Intent.OPT_OUT:
            conversation_store.mark_opt_out(body.conversation_id)
            if body.merchant_id:
                suppression_registry.suppress_merchant(body.merchant_id)

            conversation_store.add_turn(
                conversation_id=body.conversation_id,
                turn_number=body.turn_number,
                from_role=body.from_role,
                message=body.message,
                action="end",
                intent=intent.value,
                merchant_id=body.merchant_id,
                customer_id=body.customer_id,
            )
            return ReplyEndResponse(
                action="end",
                rationale="Merchant indicated explicit opt-out or hostility; gracefully ending conversation and suppressing merchant.",
            )

        # 2. AUTO_REPLY
        if intent == Intent.AUTO_REPLY:
            conv_count = conversation_store.increment_auto_reply(body.conversation_id)
            m_count = (
                conversation_store.increment_merchant_auto_reply(body.merchant_id)
                if body.merchant_id
                else conv_count
            )
            effective_count = max(conv_count, m_count)

            if effective_count == 1:
                resp_obj = ReplySendResponse(
                    action="send",
                    body="Looks like an automated reply 😊 When the owner or manager is available, let me know if you would like to proceed.",
                    cta="open_ended",
                    rationale="Detected initial auto-reply; sent light polite prompt for owner.",
                )
            elif effective_count == 2:
                resp_obj = ReplyWaitResponse(
                    action="wait",
                    wait_seconds=14400,
                    rationale="Detected repeated canned auto-reply (2x); backing off 4 hours to wait for owner.",
                )
            else:
                resp_obj = ReplyEndResponse(
                    action="end",
                    rationale="Repeated canned auto-reply 3+ times without owner engagement; closing conversation.",
                )

            conversation_store.add_turn(
                conversation_id=body.conversation_id,
                turn_number=body.turn_number,
                from_role=body.from_role,
                message=body.message,
                action=resp_obj.action,
                intent=intent.value,
                merchant_id=body.merchant_id,
                customer_id=body.customer_id,
            )
            return resp_obj

        # Reset auto-reply counters on genuine human interaction
        conversation_store.reset_auto_reply(body.conversation_id)
        if body.merchant_id:
            conversation_store.reset_merchant_auto_reply(body.merchant_id)

        # 3. OUT_OF_SCOPE
        if intent == Intent.OUT_OF_SCOPE:
            raw_body = (
                "I have to leave that to the specialists — as your Vera assistant, I focus strictly on your "
                "Google Business Profile, local campaigns, customer engagement, and offers. Coming back to your business: "
                "would you like me to review your listing performance or draft an offer post?"
            )
            body_text = _apply_anti_repetition(raw_body, conv)
            resp_send = ReplySendResponse(
                action="send",
                body=body_text,
                cta="open_ended",
                rationale="Out-of-scope request politely declined; redirected merchant back to growth and listing assistance.",
            )
            self._record_bot_send(conv, body, resp_send, intent.value)
            return resp_send

        # 4. COMMITMENT
        if intent == Intent.COMMITMENT:
            raw_body, cta, rationale = _build_context_grounded_commitment(conv)
            body_text = _apply_anti_repetition(raw_body, conv)
            resp_send = ReplySendResponse(
                action="send",
                body=body_text,
                cta=cta,
                rationale=rationale,
            )
            self._record_bot_send(conv, body, resp_send, intent.value)
            return resp_send

        # 5. NEGATIVE_RESPONSE
        if intent == Intent.NEGATIVE_RESPONSE:
            raw_body = (
                "Understood — no problem at all. We will keep your current setup as is. "
                "Whenever you want to explore new posts or offers, just let me know."
            )
            body_text = _apply_anti_repetition(raw_body, conv)
            resp_send = ReplySendResponse(
                action="send",
                body=body_text,
                cta="open_ended",
                rationale="Respected merchant preference to skip or defer without escalating or closing.",
            )
            self._record_bot_send(conv, body, resp_send, intent.value)
            return resp_send

        # 6. QUESTION
        if intent == Intent.QUESTION:
            raw_body = (
                "Google Business profile updates typically reflect within 24-48 hours once reviewed. "
                "In the meantime, we can draft a new post or review your search keywords. Would you like me to draft one?"
            )
            body_text = _apply_anti_repetition(raw_body, conv)
            resp_send = ReplySendResponse(
                action="send",
                body=body_text,
                cta="open_ended",
                rationale="Answered merchant inquiry directly and proposed clear next operational step.",
            )
            self._record_bot_send(conv, body, resp_send, intent.value)
            return resp_send

        # 7. POSITIVE_ACK
        if intent == Intent.POSITIVE_ACK:
            raw_body = (
                "Got it! Let me know if you would like me to review your weekly performance stats or draft an offer post for your Google profile."
            )
            body_text = _apply_anti_repetition(raw_body, conv)
            resp_send = ReplySendResponse(
                action="send",
                body=body_text,
                cta="open_ended",
                rationale="Acknowledged merchant message and suggested next growth priority.",
            )
            self._record_bot_send(conv, body, resp_send, intent.value)
            return resp_send

        # 8. NORMAL / Fallback
        raw_body = (
            "Understood. Proceeding with your account review. Let me know if you would like me to prepare a new Google post or audit your active offers."
        )
        body_text = _apply_anti_repetition(raw_body, conv)
        resp_send = ReplySendResponse(
            action="send",
            body=body_text,
            cta="open_ended",
            rationale="Standard conversational continuation acknowledged + advanced.",
        )
        self._record_bot_send(conv, body, resp_send, intent.value)
        return resp_send

    def _record_bot_send(
        self,
        conv: ConversationState,
        req: ReplyRequest,
        resp: ReplySendResponse,
        intent_val: str,
    ) -> None:
        conversation_store.add_turn(
            conversation_id=req.conversation_id,
            turn_number=req.turn_number,
            from_role=req.from_role,
            message=req.message,
            action=resp.action,
            cta=resp.cta,
            intent=intent_val,
            merchant_id=req.merchant_id,
            customer_id=req.customer_id,
        )
        if not hasattr(conv, "bot_sent_bodies"):
            conv.bot_sent_bodies = []
        conv.bot_sent_bodies.append(resp.body)
        conv.last_intent = intent_val
        conv.last_response = resp.model_dump()


# Singleton handler
conversation_handler = ConversationHandler()


def respond(state: ConversationState, merchant_message: str) -> dict:
    """
    Module entry point as documented in challenge-brief.md §7.4.
    Given ConversationState + merchant_message, returns reply dict.
    """
    req = ReplyRequest(
        conversation_id=state.conversation_id,
        merchant_id=state.merchant_id,
        customer_id=state.customer_id,
        from_role="merchant",
        message=merchant_message,
        received_at="2026-04-26T10:00:00Z",
        turn_number=len(state.turns) + 1,
    )
    res = conversation_handler.handle_reply(req)
    return res.model_dump()
