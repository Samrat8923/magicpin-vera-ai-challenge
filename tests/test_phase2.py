import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import context_store
from app.state import conversation_store, suppression_registry
from conversation_handlers import (
    classify_message,
    detect_auto_reply,
    detect_commitment,
    detect_opt_out,
    detect_out_of_scope,
    respond,
    Intent,
)


@pytest.fixture(autouse=True)
def clean_state():
    context_store.clear()
    conversation_store.clear()
    suppression_registry.clear()
    yield
    context_store.clear()
    conversation_store.clear()
    suppression_registry.clear()


client = TestClient(app)


# --- Unit Classification Tests ---

def test_detect_opt_out():
    assert detect_opt_out("Stop messaging me. This is useless spam.") is True
    assert detect_opt_out("STOP") is True
    assert detect_opt_out("Please unsubscribe my number") is True
    assert detect_opt_out("Not interested anymore") is True
    assert detect_opt_out("Leave me alone") is True
    assert detect_opt_out("Why are you bothering me?") is True
    # Non opt-outs
    assert detect_opt_out("No problem, what time?") is False
    assert detect_opt_out("I don't have time today but tomorrow is fine") is False
    assert detect_opt_out("We don't do deep cleaning, only regular scaling") is False


def test_detect_auto_reply():
    assert detect_auto_reply("Thank you for contacting Dr. Meera's clinic! Our team will respond shortly.") is True
    assert detect_auto_reply("Thanks for reaching out! We are currently outside of business hours.") is True
    assert detect_auto_reply("I am an automated assistant") is True
    # Non auto-replies
    assert detect_auto_reply("Thank you, that sounds very helpful") is False
    assert detect_auto_reply("Our team will review this internally and let you know") is False


def test_detect_out_of_scope():
    assert detect_out_of_scope("Can you help me file GST this month?") is True
    assert detect_out_of_scope("Can you fix my computer?") is True
    assert detect_out_of_scope("Please book a flight to Mumbai for me") is True
    assert detect_out_of_scope("Can I get a personal loan through this?") is True
    # In scope
    assert detect_out_of_scope("Can you draft a Google post for my salon?") is False
    assert detect_out_of_scope("How do I update my business hours?") is False


def test_detect_commitment():
    assert detect_commitment("Ok lets do it. Whats next?") is True
    assert detect_commitment("Yes, go ahead") is True
    assert detect_commitment("Send the abstract please") is True
    assert detect_commitment("Confirmed, proceed") is True
    assert detect_commitment("Approved, make it live") is True
    assert detect_commitment("Yes please") is True
    # Non commitments
    assert detect_commitment("Yes or no, what do you think?") is False
    assert detect_commitment("Yesterday was a busy day") is False


def test_classify_message():
    assert classify_message("Stop messaging me") == Intent.OPT_OUT
    assert classify_message("Thank you for contacting us! We will respond shortly.") == Intent.AUTO_REPLY
    assert classify_message("Can you help me with my GST return?") == Intent.OUT_OF_SCOPE
    assert classify_message("Yes, go ahead and draft it") == Intent.COMMITMENT
    assert classify_message("Not now, maybe next month") == Intent.NEGATIVE_RESPONSE
    assert classify_message("How long does it take for Google changes to show?") == Intent.QUESTION
    assert classify_message("Got it, thanks!") == Intent.POSITIVE_ACK
    assert classify_message("We had 20 patients today.") == Intent.NORMAL


# --- End-to-End Replay & Route Tests ---

def test_a_first_auto_reply():
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_ar_1",
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "Thank you for contacting Studio11! Our team will respond shortly.",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "send"
    assert "automated reply" in data["body"].lower() or "auto" in data["body"].lower()
    assert "rationale" in data


def test_b_second_auto_reply_wait():
    auto_msg = "Thank you for contacting us! Our team will respond shortly."
    # Turn 1
    client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_ar_2",
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": auto_msg,
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2,
        },
    )
    # Turn 2: Second consecutive auto-reply
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_ar_2",
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": auto_msg,
            "received_at": "2026-04-26T10:01:00Z",
            "turn_number": 3,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "wait"
    assert data["wait_seconds"] == 14400
    assert "rationale" in data


def test_c_third_auto_reply_end():
    auto_msg = "Thank you for contacting us! Our team will respond shortly."
    # Turns 1 & 2
    for t in (2, 3):
        client.post(
            "/v1/reply",
            json={
                "conversation_id": "conv_ar_3",
                "merchant_id": "m_001",
                "from_role": "merchant",
                "message": auto_msg,
                "received_at": f"2026-04-26T10:0{t}:00Z",
                "turn_number": t,
            },
        )
    # Turn 3: Third consecutive auto-reply
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_ar_3",
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": auto_msg,
            "received_at": "2026-04-26T10:04:00Z",
            "turn_number": 4,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "end"
    assert "repeated" in data["rationale"].lower() or "auto-reply" in data["rationale"].lower()


def test_d_explicit_stop_end():
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_stop_1",
            "merchant_id": "m_stop",
            "from_role": "merchant",
            "message": "STOP",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "end"
    assert suppression_registry.is_merchant_suppressed("m_stop")


def test_e_not_interested_opt_out():
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_ni_1",
            "merchant_id": "m_ni",
            "from_role": "merchant",
            "message": "Not interested anymore. Remove my number.",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "end"
    assert suppression_registry.is_merchant_suppressed("m_ni")


def test_f_yes_go_ahead_action_mode():
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_commit_f",
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "Yes, go ahead. What's next?",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "send"
    body_lower = data["body"].lower()
    # Must contain action keywords
    assert any(w in body_lower for w in ["done", "sending", "draft", "here", "confirm", "proceed", "next"])
    # Must NOT contain qualifying questions
    assert not any(w in body_lower for w in ["would you", "do you", "can you tell", "what if", "how about"])


def test_g_send_it_action_mode():
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_commit_g",
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "Send it",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "send"
    body_lower = data["body"].lower()
    assert any(w in body_lower for w in ["done", "sending", "draft", "confirm", "proceed"])


def test_h_normal_business_question_not_opt_out():
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_q_1",
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "How long does it take for Google profile updates to reflect?",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "send"
    assert not suppression_registry.is_merchant_suppressed("m_001")
    assert "hours" in data["body"].lower() or "google" in data["body"].lower()


def test_i_out_of_scope_request():
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_oos_1",
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "Btw can you also help me with my GST filing this month?",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "send"
    body_lower = data["body"].lower()
    assert "specialist" in body_lower or "outside" in body_lower or "focus" in body_lower
    assert "google business" in body_lower or "campaign" in body_lower


def test_j_conversation_history_preserved():
    conv_id = "conv_history_test"
    # Send turn 2
    client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id,
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "What is missing on my profile?",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2,
        },
    )
    # Send turn 3
    client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id,
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "Yes, go ahead and update it.",
            "received_at": "2026-04-26T10:02:00Z",
            "turn_number": 3,
        },
    )

    conv = conversation_store.get(conv_id)
    assert conv is not None
    # 2 merchant turns recorded
    assert len(conv.turns) == 2
    assert conv.turns[0].turn_number == 2
    assert conv.turns[1].turn_number == 3
    assert conv.last_intent == Intent.COMMITMENT.value


def test_k_merchant_remains_suppressed_after_opt_out():
    client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_opt_suppress",
            "merchant_id": "m_suppress_test",
            "from_role": "merchant",
            "message": "Stop messaging me. This is useless spam.",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2,
        },
    )
    assert suppression_registry.is_merchant_suppressed("m_suppress_test") is True

    # Subsequent reply on same or another conversation from this merchant returns end
    resp2 = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_opt_suppress_2",
            "merchant_id": "m_suppress_test",
            "from_role": "merchant",
            "message": "Hello?",
            "received_at": "2026-04-26T10:05:00Z",
            "turn_number": 2,
        },
    )
    assert resp2.status_code == 200
    assert resp2.json()["action"] == "end"


def test_l_anti_repetition_applies():
    conv_id = "conv_anti_rep"
    # Send two general acknowledgments in the same conversation
    resp1 = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id,
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "Noted.",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2,
        },
    )
    resp2 = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id,
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "Okay.",
            "received_at": "2026-04-26T10:01:00Z",
            "turn_number": 3,
        },
    )
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    body1 = resp1.json()["body"]
    body2 = resp2.json()["body"]
    # Bodies must not be identical verbatim
    assert body1 != body2


def test_context_aware_commitment_with_prior_vera_turn():
    conv_id = "conv_prior_context"
    # Simulate an earlier turn where Vera proposed a research abstract
    conversation_store.add_turn(
        conversation_id=conv_id,
        turn_number=1,
        from_role="vera",
        message="JIDA Oct issue landed: 3-month recall cuts caries by 38%. Want me to send the abstract + draft a patient WhatsApp?",
    )
    # Merchant commits
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id,
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "Yes please send it",
            "received_at": "2026-04-26T10:02:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "send"
    body_lower = data["body"].lower()
    # Confirms it grounded into the research abstract topic
    assert "abstract" in body_lower or "research" in body_lower or "cleaning" in body_lower
    assert any(w in body_lower for w in ["done", "sending", "draft", "here", "confirm", "proceed", "next"])
    assert not any(w in body_lower for w in ["would you", "do you", "can you tell", "what if"])


def test_respond_module_function():
    # Test challenge-brief §7.4 `respond(state, merchant_message)` function
    conv = conversation_store.get_or_create("conv_test_mod", merchant_id="m_001")
    result = respond(conv, "Ok lets do it. Whats next?")
    assert isinstance(result, dict)
    assert result["action"] == "send"
    assert "done" in result["body"].lower()
