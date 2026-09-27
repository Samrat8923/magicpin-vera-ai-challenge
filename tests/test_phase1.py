import concurrent.futures
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import context_store
from app.state import conversation_store, suppression_registry


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


def test_healthz_initial_state():
    resp = client.get("/v1/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert data["uptime_seconds"] >= 0
    assert data["contexts_loaded"] == {
        "category": 0,
        "merchant": 0,
        "customer": 0,
        "trigger": 0,
    }


def test_metadata():
    resp = client.get("/v1/metadata")
    assert resp.status_code == 200
    data = resp.json()
    assert "team_name" in data
    assert isinstance(data["team_members"], list)
    assert len(data["team_members"]) > 0
    assert "model" in data
    assert "approach" in data
    assert "contact_email" in data
    assert "version" in data
    assert "submitted_at" in data


def test_first_context_insertion():
    payload = {
        "scope": "category",
        "context_id": "dentists",
        "version": 1,
        "payload": {"slug": "dentists", "voice": {"tone": "peer_clinical"}},
        "delivered_at": "2026-04-26T09:45:00Z",
    }
    resp = client.post("/v1/context", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert data["ack_id"] == "ack_dentists_v1"
    assert "stored_at" in data

    # Verify healthz reflects 1 category
    h_resp = client.get("/v1/healthz")
    assert h_resp.json()["contexts_loaded"]["category"] == 1


def test_same_version_rejection():
    payload = {
        "scope": "merchant",
        "context_id": "m_001_drmeera",
        "version": 1,
        "payload": {"merchant_id": "m_001_drmeera", "identity": {"name": "Dr. Meera"}},
        "delivered_at": "2026-04-26T09:45:00Z",
    }
    # First push succeeds
    resp1 = client.post("/v1/context", json=payload)
    assert resp1.status_code == 200
    assert resp1.json()["accepted"] is True

    # Same version push fails with 409
    resp2 = client.post("/v1/context", json=payload)
    assert resp2.status_code == 409
    data2 = resp2.json()
    assert data2["accepted"] is False
    assert data2["reason"] == "stale_version"
    assert data2["current_version"] == 1


def test_lower_version_rejection():
    payload_v2 = {
        "scope": "merchant",
        "context_id": "m_002_bharat",
        "version": 2,
        "payload": {"merchant_id": "m_002_bharat"},
        "delivered_at": "2026-04-26T09:45:00Z",
    }
    resp1 = client.post("/v1/context", json=payload_v2)
    assert resp1.status_code == 200

    payload_v1 = {
        "scope": "merchant",
        "context_id": "m_002_bharat",
        "version": 1,
        "payload": {"merchant_id": "m_002_bharat"},
        "delivered_at": "2026-04-26T09:46:00Z",
    }
    resp2 = client.post("/v1/context", json=payload_v1)
    assert resp2.status_code == 409
    data2 = resp2.json()
    assert data2["accepted"] is False
    assert data2["reason"] == "stale_version"
    assert data2["current_version"] == 2


def test_higher_version_replacement():
    payload_v1 = {
        "scope": "customer",
        "context_id": "c_001_priya",
        "version": 1,
        "payload": {"name": "Priya", "state": "active"},
        "delivered_at": "2026-04-26T09:45:00Z",
    }
    resp1 = client.post("/v1/context", json=payload_v1)
    assert resp1.status_code == 200

    payload_v2 = {
        "scope": "customer",
        "context_id": "c_001_priya",
        "version": 2,
        "payload": {"name": "Priya", "state": "lapsed_soft"},
        "delivered_at": "2026-04-26T10:00:00Z",
    }
    resp2 = client.post("/v1/context", json=payload_v2)
    assert resp2.status_code == 200
    assert resp2.json()["accepted"] is True
    assert resp2.json()["ack_id"] == "ack_c_001_priya_v2"

    # Verify context in store was updated
    stored = context_store.get("customer", "c_001_priya")
    assert stored is not None
    assert stored.version == 2
    assert stored.payload["state"] == "lapsed_soft"

    # Count in healthz should remain 1, not duplicate
    h_resp = client.get("/v1/healthz")
    assert h_resp.json()["contexts_loaded"]["customer"] == 1


def test_all_contexts_counts():
    for slug in ["dentists", "salons", "gyms", "restaurants", "pharmacies"]:
        client.post(
            "/v1/context",
            json={
                "scope": "category",
                "context_id": slug,
                "version": 1,
                "payload": {"slug": slug},
                "delivered_at": "2026-04-26T09:00:00Z",
            },
        )

    for i in range(3):
        client.post(
            "/v1/context",
            json={
                "scope": "merchant",
                "context_id": f"m_{i}",
                "version": 1,
                "payload": {},
                "delivered_at": "2026-04-26T09:00:00Z",
            },
        )

    client.post(
        "/v1/context",
        json={
            "scope": "trigger",
            "context_id": "trg_001",
            "version": 1,
            "payload": {},
            "delivered_at": "2026-04-26T09:00:00Z",
        },
    )

    h_resp = client.get("/v1/healthz").json()
    assert h_resp["contexts_loaded"]["category"] == 5
    assert h_resp["contexts_loaded"]["merchant"] == 3
    assert h_resp["contexts_loaded"]["customer"] == 0
    assert h_resp["contexts_loaded"]["trigger"] == 1


def test_empty_tick_response():
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_001_nonexistent"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "actions" in data
    assert data["actions"] == []


def test_reply_hostile():
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_hostile_1",
            "merchant_id": "m_001",
            "customer_id": None,
            "from_role": "merchant",
            "message": "Stop messaging me. This is useless spam.",
            "received_at": "2026-04-26T10:42:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "end"
    assert "rationale" in data

    # Verify merchant is marked suppressed
    assert suppression_registry.is_merchant_suppressed("m_001")


def test_reply_auto_reply_sequence():
    auto_msg = "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly."
    
    # Turn 2: First auto-reply -> light prompt/send
    resp1 = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_auto_test",
            "merchant_id": "m_001",
            "customer_id": None,
            "from_role": "merchant",
            "message": auto_msg,
            "received_at": "2026-04-26T10:42:00Z",
            "turn_number": 2,
        },
    )
    assert resp1.status_code == 200
    assert resp1.json()["action"] == "send"

    # Turn 3: Second auto-reply -> wait
    resp2 = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_auto_test",
            "merchant_id": "m_001",
            "customer_id": None,
            "from_role": "merchant",
            "message": auto_msg,
            "received_at": "2026-04-26T10:43:00Z",
            "turn_number": 3,
        },
    )
    assert resp2.status_code == 200
    assert resp2.json()["action"] == "wait"
    assert resp2.json()["wait_seconds"] > 0

    # Turn 4: Third auto-reply -> end
    resp3 = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_auto_test",
            "merchant_id": "m_001",
            "customer_id": None,
            "from_role": "merchant",
            "message": auto_msg,
            "received_at": "2026-04-26T10:44:00Z",
            "turn_number": 4,
        },
    )
    assert resp3.status_code == 200
    assert resp3.json()["action"] == "end"


def test_reply_commitment_transition():
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_commit_1",
            "merchant_id": "m_001",
            "customer_id": None,
            "from_role": "merchant",
            "message": "Ok, let's do it. What's next?",
            "received_at": "2026-04-26T10:42:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "send"
    assert "body" in data
    # Must contain action words and not qualification words
    body_lower = data["body"].lower()
    assert any(w in body_lower for w in ["done", "sending", "draft", "here", "confirm", "proceed", "next"])
    assert not any(w in body_lower for w in ["would you", "do you", "can you tell", "what if"])


def test_reply_general_acknowledgment():
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_gen_1",
            "merchant_id": "m_001",
            "customer_id": None,
            "from_role": "merchant",
            "message": "Got your note. Will discuss with team.",
            "received_at": "2026-04-26T10:42:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "send"
    assert "body" in data
    assert "cta" in data
    assert "rationale" in data


def test_malformed_scope_validation():
    resp = client.post(
        "/v1/context",
        json={
            "scope": "invalid_scope_value",
            "context_id": "dentists",
            "version": 1,
            "payload": {},
            "delivered_at": "2026-04-26T09:45:00Z",
        },
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["accepted"] is False
    assert data["reason"] == "invalid_scope"


def test_concurrent_store_upsert():
    """Verify thread-safety when multiple threads update contexts simultaneously."""
    def worker(idx):
        return context_store.upsert(
            scope="merchant",
            context_id="m_shared",
            version=idx,
            payload={"worker": idx},
        )

    # Launch 20 concurrent threads trying to update the same context with ascending versions
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(1, 21)]
        results = [f.result() for f in futures]

    # At least version 20 must have been evaluated, final version should be 20
    final = context_store.get("merchant", "m_shared")
    assert final is not None
    assert final.version == 20
    assert final.payload["worker"] == 20
