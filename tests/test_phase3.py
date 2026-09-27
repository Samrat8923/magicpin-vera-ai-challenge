import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import context_store
from app.state import conversation_store, suppression_registry
from app.decision_engine import decision_engine

DATASET_DIR = Path(__file__).parent.parent / "dataset"

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_data():
    context_store.clear()
    conversation_store.clear()
    suppression_registry.clear()

    # Load categories
    cat_dir = DATASET_DIR / "categories"
    if cat_dir.exists():
        for f in cat_dir.glob("*.json"):
            with open(f, encoding="utf-8") as fp:
                cat = json.load(fp)
                context_store.upsert("category", cat["slug"], 1, cat)

    # Load merchants
    with open(DATASET_DIR / "merchants_seed.json", encoding="utf-8") as fp:
        merchants = json.load(fp)["merchants"]
        for m in merchants:
            context_store.upsert("merchant", m["merchant_id"], 1, m)

    # Load customers
    with open(DATASET_DIR / "customers_seed.json", encoding="utf-8") as fp:
        customers = json.load(fp)["customers"]
        for c in customers:
            context_store.upsert("customer", c["customer_id"], 1, c)

    # Load triggers
    with open(DATASET_DIR / "triggers_seed.json", encoding="utf-8") as fp:
        triggers = json.load(fp)["triggers"]
        for t in triggers:
            context_store.upsert("trigger", t["id"], 1, t)

    yield

    context_store.clear()
    conversation_store.clear()
    suppression_registry.clear()


def test_research_digest_trigger():
    # trg_001_research_digest_dentists -> Dr. Meera
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_001_research_digest_dentists"],
        },
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    act = actions[0]
    assert act["merchant_id"] == "m_001_drmeera_dentist_delhi"
    assert act["send_as"] == "vera"
    assert "JIDA" in act["body"]
    assert "Dr. Meera" in act["body"]
    assert "2,100" in act["body"] or "trial" in act["body"]
    assert act["cta"] == "open_ended"
    assert "http" not in act["body"].lower()


def test_customer_recall_due_trigger():
    # trg_003_recall_due_priya -> Priya (patient of Dr. Meera)
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_003_recall_due_priya"],
        },
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    act = actions[0]
    assert act["send_as"] == "merchant_on_behalf"
    assert act["customer_id"] == "c_001_priya_for_m001"
    assert "Priya" in act["body"]
    assert "Dr. Meera" in act["body"]
    assert "cleaning" in act["body"].lower()
    assert act["cta"] == "multi_choice_slot"


def test_perf_dip_trigger():
    # trg_004_perf_dip_bharat -> Bharat Dental Care
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_004_perf_dip_bharat"],
        },
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    act = actions[0]
    assert act["send_as"] == "vera"
    assert "calls" in act["body"].lower()
    assert "50%" in act["body"]
    assert act["cta"] == "binary_yes_no"


def test_perf_spike_trigger():
    # trg_024_perf_spike_zen -> Zen Yoga Studio
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_024_perf_spike_zen"],
        },
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    act = actions[0]
    assert "15%" in act["body"]
    assert "calls" in act["body"].lower()
    assert act["cta"] == "binary_yes_no"


def test_festival_trigger():
    # trg_006_festival_diwali -> Studio11 Family Salon
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_006_festival_diwali"],
        },
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    act = actions[0]
    assert "Diwali" in act["body"]
    assert "188" in act["body"]
    assert "Lakshmi" in act["body"] or "Studio11" in act["body"]


def test_ipl_match_trigger():
    # trg_010_ipl_match_delhi -> SK Pizza Junction
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_010_ipl_match_delhi"],
        },
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    act = actions[0]
    assert "DC vs MI" in act["body"]
    assert "delivery" in act["body"].lower()


def test_chronic_refill_trigger():
    # trg_019_chronic_refill_grandfather -> Apollo Pharmacy
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_019_chronic_refill_grandfather"],
        },
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    act = actions[0]
    assert act["send_as"] == "merchant_on_behalf"
    assert "metformin" in act["body"].lower()
    assert "atorvastatin" in act["body"].lower()


def test_active_offer_preference_over_expired():
    # Dr. Meera has:
    # o_meera_001: "Dental Cleaning @ ₹299" (active)
    # o_meera_002: "Deep Cleaning @ ₹499" (expired)
    # Ensure any generated message picks the active offer, never the expired one
    actions = decision_engine.evaluate_tick(
        "2026-04-26T10:35:00Z",
        ["trg_003_recall_due_priya"],
    )
    assert len(actions) == 1
    assert "Deep Cleaning @ ₹499" not in actions[0].body
    assert "299" in actions[0].body


def test_suppressed_trigger_is_skipped():
    # First tick consumes and suppresses trigger
    resp1 = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_001_research_digest_dentists"],
        },
    )
    assert len(resp1.json()["actions"]) == 1

    # Second tick with same trigger is suppressed
    resp2 = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:36:00Z",
            "available_triggers": ["trg_001_research_digest_dentists"],
        },
    )
    assert len(resp2.json()["actions"]) == 0


def test_merchant_opt_out_suppresses_tick():
    suppression_registry.suppress_merchant("m_001_drmeera_dentist_delhi")

    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_001_research_digest_dentists"],
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["actions"]) == 0


def test_competing_signals_ranked_by_urgency():
    # trg_018_supply_atorvastatin_recall has urgency 5
    # trg_022_cde_webinar_dentists has urgency 1
    # trg_004_perf_dip_bharat has urgency 4
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": [
                "trg_022_cde_webinar_dentists",
                "trg_018_supply_atorvastatin_recall",
                "trg_004_perf_dip_bharat",
            ],
        },
    )
    actions = resp.json()["actions"]
    assert len(actions) == 3
    # Top action must be urgency 5 (atorvastatin recall)
    assert actions[0]["trigger_id"] == "trg_018_supply_atorvastatin_recall"
    # Second action must be urgency 4 (perf dip)
    assert actions[1]["trigger_id"] == "trg_004_perf_dip_bharat"
    # Third action must be urgency 1 (cde webinar)
    assert actions[2]["trigger_id"] == "trg_022_cde_webinar_dentists"


def test_determinism_identical_output():
    # Calling evaluate_tick twice with fresh clear produces identical results
    triggers = ["trg_001_research_digest_dentists", "trg_010_ipl_match_delhi"]

    actions1 = decision_engine.evaluate_tick("2026-04-26T10:35:00Z", triggers)

    # Clear suppression so second run evaluates same inputs
    suppression_registry.clear()
    actions2 = decision_engine.evaluate_tick("2026-04-26T10:35:00Z", triggers)

    assert len(actions1) == len(actions2)
    for a1, a2 in zip(actions1, actions2):
        assert a1.body == a2.body
        assert a1.cta == a2.cta
        assert a1.send_as == a2.send_as
        assert a1.suppression_key == a2.suppression_key
        assert a1.rationale == a2.rationale


def test_no_url_or_taboo_words_in_outputs():
    # Verify across multiple kinds
    all_triggers = [
        "trg_001_research_digest_dentists",
        "trg_002_compliance_dci_radiograph",
        "trg_003_recall_due_priya",
        "trg_004_perf_dip_bharat",
        "trg_006_festival_diwali",
        "trg_010_ipl_match_delhi",
        "trg_018_supply_atorvastatin_recall",
    ]
    actions = decision_engine.evaluate_tick("2026-04-26T10:35:00Z", all_triggers)
    for act in actions:
        assert "http://" not in act.body
        assert "https://" not in act.body
        # Check taboos
        for taboo in ["guaranteed", "100% safe", "miracle"]:
            assert taboo not in act.body.lower()
        # Exactly one question mark or CTA
        assert act.cta in ("binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "open_ended")


def test_customer_opted_out_is_suppressed():
    # Customer c_001_priya_for_m001 opted out in preferences
    cust = context_store.get("customer", "c_001_priya_for_m001")
    assert cust is not None
    # Temporarily set reminder_opt_in to False
    updated_cust = json.loads(json.dumps(cust.payload))
    updated_cust["preferences"]["reminder_opt_in"] = False
    context_store.upsert("customer", "c_001_priya_for_m001", 2, updated_cust)

    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_003_recall_due_priya"],
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["actions"]) == 0

    # Also test suppression registry customer opt-out
    updated_cust["preferences"]["reminder_opt_in"] = True
    context_store.upsert("customer", "c_001_priya_for_m001", 3, updated_cust)
    suppression_registry.suppress_customer("c_001_priya_for_m001")

    resp2 = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_003_recall_due_priya"],
        },
    )
    assert resp2.status_code == 200
    assert len(resp2.json()["actions"]) == 0


def test_regulation_change_action():
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_002_compliance_dci_radiograph"],
        },
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    act = actions[0]
    assert "DCI" in act["body"]
    assert "radiograph" in act["body"].lower()
    assert act["cta"] == "binary_yes_no"
    assert act["send_as"] == "vera"


def test_supply_alert_action():
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_018_supply_atorvastatin_recall"],
        },
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    act = actions[0]
    assert "Atorvastatin" in act["body"] or "atorvastatin" in act["body"].lower()
    assert "recall" in act["body"].lower() or "quarantine" in act["body"].lower()
    assert act["cta"] == "binary_yes_no"


def test_no_useful_signal_returns_empty():
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": [],
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["actions"]) == 0


def test_category_differentiation():
    # Evaluate across different categories and ensure voice & vocabulary differ
    triggers = [
        "trg_001_research_digest_dentists",   # Dentists
        "trg_006_festival_diwali",            # Salons
        "trg_010_ipl_match_delhi",            # Restaurants
        "trg_024_perf_spike_zen",             # Gyms
        "trg_019_chronic_refill_grandfather", # Pharmacies
    ]
    resp = client.post(
        "/v1/tick",
        json={
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": triggers,
        },
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 5

    # Check that dentists use clinical/professional terms (e.g. Dr. Meera, study, fluoride)
    dentist_act = next(a for a in actions if a["merchant_id"] == "m_001_drmeera_dentist_delhi")
    assert "Dr." in dentist_act["body"]

    # Check salon uses festive/styling/package context
    salon_act = next(a for a in actions if a["merchant_id"] == "m_003_studio11_salon_hyderabad")
    assert "Diwali" in salon_act["body"] or "booking" in salon_act["body"].lower()

    # Check restaurant mentions match / orders / delivery
    rest_act = next(a for a in actions if a["merchant_id"] == "m_005_pizzajunction_restaurant_delhi")
    assert "DC vs MI" in rest_act["body"] or "order" in rest_act["body"].lower()

    # Check gym mentions sessions / members / trial
    gym_act = next(a for a in actions if a["merchant_id"] == "m_008_zenyoga_gym_chennai")
    assert "calls" in gym_act["body"].lower() or "slot" in gym_act["body"].lower()

    # Check pharmacy mentions refill / medication / prescription
    pharm_act = next(a for a in actions if a["merchant_id"] == "m_009_apollo_pharmacy_jaipur")
    assert "refill" in pharm_act["body"].lower() or "medicine" in pharm_act["body"].lower()

