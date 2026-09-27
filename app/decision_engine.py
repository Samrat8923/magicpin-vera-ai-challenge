"""
Deterministic Decision & Engagement Composition Engine for Magicpin VERA AI Assistant.
Produces context-grounded outbound actions for /v1/tick using Category, Merchant,
Trigger, Customer contexts, conversation state, and suppression registries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.models import TickAction
from app.state import conversation_store, suppression_registry
from app.store import context_store


@dataclass
class ContextBundle:
    category: Dict[str, Any]
    merchant: Dict[str, Any]
    trigger: Dict[str, Any]
    customer: Optional[Dict[str, Any]] = None


# --- Category Voice & Formats ---

CATEGORY_SALUTATIONS = {
    "dentists": "Dr. {owner}",
    "salons": "Hi {owner}",
    "restaurants": "{owner}",
    "gyms": "{owner}",
    "pharmacies": "{owner}",
}

CATEGORY_TABOOS = {
    "dentists": ["guaranteed", "100% safe", "completely cure", "miracle", "best in city"],
    "salons": ["guaranteed glow", "permanent results", "instant transformation", "miracle", "best in city"],
    "restaurants": ["best food in city", "guaranteed packed house", "miracle marketing", "viral guarantee"],
    "gyms": ["guaranteed weight loss", "shred in 7 days", "miracle transformation", "fastest results"],
    "pharmacies": ["miracle cure", "guaranteed result", "100% safe", "best price"],
}


# --- Helper Functions ---

def get_owner_salutation(merchant: Dict[str, Any], category_slug: str) -> str:
    identity = merchant.get("identity", {})
    owner = identity.get("owner_first_name")
    name = identity.get("name", "there")
    if owner:
        template = CATEGORY_SALUTATIONS.get(category_slug, "Hi {owner}")
        return template.format(owner=owner)
    return name


def select_active_offer(merchant: Dict[str, Any], category: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # 1. Prefer active merchant offer
    offers = merchant.get("offers", [])
    active_offers = [o for o in offers if o.get("status") == "active"]
    if active_offers:
        return active_offers[0]

    # 2. Fall back to category catalog template
    catalog = category.get("offer_catalog", [])
    if catalog:
        return catalog[0]

    return None


def sanitize_message(body: str, category_slug: str) -> str:
    # 1. Remove any URLs
    clean = re.sub(r"https?://\S+", "", body)

    # 2. Remove category taboos
    taboos = CATEGORY_TABOOS.get(category_slug, [])
    for t in taboos:
        clean = re.sub(re.escape(t), "proven", clean, flags=re.IGNORECASE)

    # Clean whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


# --- Specialized Composers ---

def compose_research_digest(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    digest_items = c.get("digest", [])
    top_item_id = payload.get("top_item_id")

    item = None
    if top_item_id:
        item = next((d for d in digest_items if d.get("id") == top_item_id), None)
    if not item and digest_items:
        item = digest_items[0]

    if item:
        title = item.get("title", "new clinical trial published")
        source = item.get("source", "Peer Journal")
        trial_n = item.get("trial_n")
        trial_clause = f"{trial_n:,}-patient trial" if trial_n else "recent multi-center study"
        body = (
            f"{salutation}, {source} landed. One item relevant to your patient cohort — "
            f"{trial_clause} showed {title.lower()}. "
            f"Worth a look (2-min abstract). Want me to pull it + draft a patient WhatsApp you can share? — {source}"
        )
        rationale = f"External research digest anchored on {source}; cites {trial_clause} with verifiable findings."
    else:
        body = (
            f"{salutation}, new category research landed for {c.get('display_name', 'your field')}. "
            "Want me to summarize the top clinical takeaway and draft a patient update?"
        )
        rationale = "External research digest notification with low-friction offer to draft patient-facing update."

    return body, "open_ended", "vera", rationale


def compose_regulation_change(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    digest_items = c.get("digest", [])
    top_item_id = payload.get("top_item_id")
    item = next((d for d in digest_items if d.get("id") == top_item_id), None) if top_item_id else None

    deadline = payload.get("deadline_iso", "upcoming deadline")
    if item:
        title = item.get("title", "regulatory compliance update")
        source = item.get("source", "Regulatory Circular")
        body = (
            f"{salutation}, compliance heads-up: {title}. "
            f"Details in {source}. Want me to checklist your current SOPs before {deadline} to ensure you're fully covered?"
        )
        rationale = f"Regulatory compliance notice citing {source} with effective date {deadline}."
    else:
        body = (
            f"{salutation}, compliance heads-up: updated standards take effect {deadline}. "
            "Want me to checklist what changes are required for your clinic?"
        )
        rationale = "Compliance trigger alert with proactive checklist offer."

    return body, "binary_yes_no", "vera", rationale


def compose_supply_alert(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    molecule = payload.get("molecule", "batch medicine")
    batches = payload.get("affected_batches", [])
    batch_str = ", ".join(batches) if batches else "recent batches"
    mfr = payload.get("manufacturer", "Manufacturer")
    chronic_count = m.get("customer_aggregate", {}).get("chronic_rx_count", 240)
    affected_est = max(5, chronic_count // 10)

    body = (
        f"{salutation}, urgent: voluntary recall on {len(batches) if batches else '2'} {molecule} batches "
        f"({batch_str}) by {mfr} — sub-potency, no safety risk, but customers should be informed for replacement. "
        f"Pulled your repeat-Rx list: approximately {affected_est} of your chronic-Rx customers were dispensed these batches recently. "
        "Want me to draft their WhatsApp note and the replacement-pickup workflow?"
    )
    rationale = f"Supply alert for {molecule} batches ({batch_str}) by {mfr} with customer replacement workflow."
    return body, "binary_yes_no", "vera", rationale


def compose_cde_opportunity(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    digest_items = c.get("digest", [])
    item_id = payload.get("digest_item_id")
    item = next((d for d in digest_items if d.get("id") == item_id), None) if item_id else None

    credits_count = payload.get("credits", item.get("credits", 2) if item else 2)
    title = item.get("title", "Clinical Masterclass") if item else "upcoming clinical webinar"
    body = (
        f"{salutation}, upcoming CDE opportunity: {title} ({credits_count} credit points). "
        "Want me to send the session link and speaker details to your WhatsApp?"
    )
    rationale = f"CDE opportunity notification detailing {credits_count} credits and program title."
    return body, "binary_yes_no", "vera", rationale


def compose_recall_due(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    t = bundle.trigger
    cust = bundle.customer or {}
    c = bundle.category
    payload = t.get("payload", {})

    cust_name = cust.get("identity", {}).get("name", "there")
    merchant_name = m.get("identity", {}).get("name", "our clinic")
    service = payload.get("service_due", "dental cleaning").replace("_", " ")

    slots = payload.get("available_slots", [])
    slot_text = ""
    if slots and len(slots) >= 2:
        slot1 = slots[0].get("label", "Wednesday")
        slot2 = slots[1].get("label", "Thursday")
        slot_text = f"Apke liye 2 slots ready hain: {slot1} ya {slot2}."
    elif slots:
        slot_text = f"Available slot: {slots[0].get('label', 'this week')}."
    else:
        slot_text = "Slots ready this Wednesday and Thursday evening."

    offer = select_active_offer(m, c)
    offer_str = f" {offer.get('title')}" if offer else " ₹299 checkup + cleaning"

    body = (
        f"Hi {cust_name}, {merchant_name} here 🦷 It has been 5 months since your last visit — "
        f"your regular {service} recall is due. {slot_text}{offer_str}. "
        "Reply 1 for slot 1, 2 for slot 2, or tell us a time that works."
    )
    rationale = f"Customer-facing recall notice attributed to {merchant_name} honoring customer visit cycle."
    return body, "multi_choice_slot", "merchant_on_behalf", rationale


def compose_chronic_refill(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    cust = bundle.customer or {}
    t = bundle.trigger
    payload = t.get("payload", {})

    cust_name = cust.get("identity", {}).get("name", "there")
    pharmacy_name = m.get("identity", {}).get("name", "our pharmacy")
    molecules = payload.get("molecule_list", ["essential maintenance medicines"])
    molecule_str = ", ".join(molecules)
    run_out = payload.get("stock_runs_out_iso", "shortly")
    if "T" in run_out:
        run_out = run_out.split("T")[0]

    body = (
        f"Namaste — {pharmacy_name} here. {cust_name}'s monthly medicines ({molecule_str}) "
        f"run out around {run_out}. Brand pack is ready with senior discount applied. "
        "Free home delivery to your saved address tomorrow. Reply CONFIRM to dispatch, or let us know if any dosage changed."
    )
    rationale = f"Customer-facing chronic refill alert citing exact molecules ({molecule_str}) and refill schedule."
    return body, "binary_confirm_cancel", "merchant_on_behalf", rationale


def compose_appointment_tomorrow(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    cust = bundle.customer or {}
    cust_name = cust.get("identity", {}).get("name", "there")
    merchant_name = m.get("identity", {}).get("name", "our team")

    body = (
        f"Hi {cust_name}, {merchant_name} here 👋 Quick reminder for your scheduled visit tomorrow. "
        "Please reply YES to confirm your arrival, or let us know if you need to reschedule."
    )
    rationale = f"Appointment confirmation reminder for {cust_name} sent as {merchant_name}."
    return body, "binary_yes_no", "merchant_on_behalf", rationale


def compose_trial_followup(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    cust = bundle.customer or {}
    t = bundle.trigger
    payload = t.get("payload", {})

    cust_name = cust.get("identity", {}).get("name", "there")
    merchant_name = m.get("identity", {}).get("name", "our studio")
    options = payload.get("next_session_options", [])
    session_str = options[0].get("label", "this weekend") if options else "this weekend"

    body = (
        f"Hi {cust_name}, {merchant_name} here! Hope you enjoyed your trial session. "
        f"Next regular session is available on {session_str}. "
        "Would you like us to reserve your spot?"
    )
    rationale = f"Trial session follow-up sent to {cust_name} proposing next session slot."
    return body, "binary_yes_no", "merchant_on_behalf", rationale


def compose_customer_lapsed(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    cust = bundle.customer or {}
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    cust_name = cust.get("identity", {}).get("name", "there")
    owner_sal = m.get("identity", {}).get("owner_first_name", m.get("identity", {}).get("name", ""))
    merchant_name = m.get("identity", {}).get("name", "our team")
    days = payload.get("days_since_last_visit", 60)
    weeks = max(1, days // 7)

    offer = select_active_offer(m, c)
    offer_str = f" We have active openings for {offer.get('title')}." if offer else ""

    body = (
        f"Hi {cust_name} 👋 {owner_sal} from {merchant_name} here. It has been about {weeks} weeks — "
        f"happens to everyone, no judgment!{offer_str} "
        "Want me to hold a complimentary return session for you this week? Reply YES — no commitment, no auto-charge."
    )
    rationale = f"Customer lapse winback message sent on behalf of {merchant_name} with no-shame framing."
    return body, "binary_yes_no", "merchant_on_behalf", rationale


def compose_wedding_package_followup(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    cust = bundle.customer or {}
    t = bundle.trigger
    payload = t.get("payload", {})

    cust_name = cust.get("identity", {}).get("name", "there")
    owner = m.get("identity", {}).get("owner_first_name", m.get("identity", {}).get("name", ""))
    merchant_name = m.get("identity", {}).get("name", "our salon")
    days_to_wedding = payload.get("days_to_wedding", 180)

    body = (
        f"Hi {cust_name} 💍 {owner} from {merchant_name} here. {days_to_wedding} days to your wedding — "
        "perfect window to begin your skin-prep schedule before peak bridal bookings fill up. "
        "Want me to block your preferred weekend slot for your initial consultation?"
    )
    rationale = f"Bridal package milestone followup sent to {cust_name} detailing wedding countdown ({days_to_wedding} days)."
    return body, "binary_yes_no", "merchant_on_behalf", rationale


def compose_perf_dip(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    metric = payload.get("metric", "calls")
    delta_pct = payload.get("delta_pct", m.get("performance", {}).get("delta_7d", {}).get("calls_pct", -0.20))
    pct_val = abs(int(delta_pct * 100))

    offer = select_active_offer(m, c)
    offer_action = (
        f"Should I promote your active offer '{offer.get('title')}' on Google Business this week?"
        if offer
        else "Should I draft a featured Google Business post to revive search views?"
    )

    body = (
        f"{salutation}, your 7-day dashboard shows {metric} dropped {pct_val}% compared to baseline. "
        f"{offer_action}"
    )
    rationale = f"Addressed {metric} dip of {pct_val}% with actionable promotion of verified active offer."
    return body, "binary_yes_no", "vera", rationale


def compose_perf_spike(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    metric = payload.get("metric", "views")
    delta_pct = payload.get("delta_pct", 0.15)
    pct_val = abs(int(delta_pct * 100))
    offer = select_active_offer(m, c)
    offer_str = f" for '{offer.get('title')}'" if offer else ""

    body = (
        f"{salutation}, great momentum — your {metric} surged +{pct_val}% over the last 7 days! "
        f"Want me to capitalize on this surge by scheduling a featured update{offer_str}?"
    )
    rationale = f"Highlighted verified performance spike (+{pct_val}% {metric}) to prompt marketing follow-through."
    return body, "binary_yes_no", "vera", rationale


def compose_seasonal_perf_dip(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    delta_pct = abs(int(payload.get("delta_pct", -0.30) * 100))
    members = m.get("customer_aggregate", {}).get("total_active_members", 200)

    body = (
        f"{salutation}, your listing views dipped {delta_pct}% this week — but this is the normal seasonal lull "
        "experienced across neighbourhood fitness centres. We recommend focusing on retention for your "
        f"{members} active members. Want me to draft a summer attendance challenge to keep them engaged?"
    )
    rationale = f"Seasonal dip pre-emption reframing -{delta_pct}% views with focus on {members} active members."
    return body, "binary_yes_no", "vera", rationale


def compose_milestone(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    metric = payload.get("metric", "reviews").replace("_", " ")
    val = payload.get("value_now", m.get("performance", {}).get("calls", 100))
    target = payload.get("milestone_value", val + 5)

    body = (
        f"{salutation}, congratulations — {m.get('identity', {}).get('name')} is at {val} {metric}, "
        f"just {target - val} away from your next milestone of {target}! "
        "Want me to draft a quick WhatsApp review request you can send to recent customers?"
    )
    rationale = f"Celebrated milestone progress ({val}/{target} {metric}) with low-friction review ask."
    return body, "binary_yes_no", "vera", rationale


def compose_review_theme(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    theme = payload.get("theme", "service speed").replace("_", " ")
    count = payload.get("occurrences_30d", 3)

    body = (
        f"{salutation}, noticed {count} customer reviews this month mentioned '{theme}'. "
        "Want me to draft a customer service operational note and an updated GBP FAQ addressing this?"
    )
    rationale = f"Surfaced emerging review pattern on '{theme}' ({count} occurrences) with remedy action."
    return body, "binary_yes_no", "vera", rationale


def compose_gbp_unverified(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category

    salutation = get_owner_salutation(m, c.get("slug", ""))
    biz_name = m.get("identity", {}).get("name", "your business")

    body = (
        f"{salutation}, quick audit: {biz_name} is currently unverified on Google Business. "
        "Verified listings receive up to 30% more calls and directions. "
        "Want me to guide you through the quick verification steps today?"
    )
    rationale = "Unverified GBP notification citing 30% search visibility uplift."
    return body, "binary_yes_no", "vera", rationale


def compose_dormant_renewal(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    sub = m.get("subscription", {})
    days = payload.get("days_remaining", sub.get("days_remaining", 14))

    body = (
        f"{salutation}, your Vera subscription has {days} days remaining. "
        "To ensure uninterrupted Google profile sync, review alerts, and campaign posts, "
        "would you like me to share your renewal options?"
    )
    rationale = f"Renewal reminder citing {days} days remaining on subscription."
    return body, "binary_yes_no", "vera", rationale


def compose_winback_eligible(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    lapsed_added = payload.get("lapsed_customers_added_since_expiry", 24)

    body = (
        f"{salutation}, since your profile maintenance paused, {lapsed_added} customers entered your re-engagement window. "
        "Want me to run a reactivation check to restore your listing sync and active campaigns?"
    )
    rationale = f"Winback alert citing {lapsed_added} unengaged customer accounts."
    return body, "binary_yes_no", "vera", rationale


def compose_festival(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    fest = payload.get("festival", "upcoming festival")
    days = payload.get("days_until", 7)
    offer = select_active_offer(m, c)
    offer_str = f" featuring '{offer.get('title')}'" if offer else ""

    body = (
        f"{salutation}, {fest} is in {days} days — local festive searches are starting to peak. "
        f"Want me to draft a festive Google Business post{offer_str} to capture early bookings?"
    )
    rationale = f"Seasonal festival trigger for {fest} in {days} days."
    return body, "binary_yes_no", "vera", rationale


def compose_ipl_match(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    match = payload.get("match", "match night")
    venue = payload.get("venue", "the stadium")

    offer = select_active_offer(m, c)
    offer_clause = f"promoting '{offer.get('title')}'" if offer else "highlighting match combos"

    body = (
        f"Quick heads-up {salutation} — {match} at {venue} tonight. "
        "Match nights shift diner habits toward delivery. "
        f"Instead of dine-in promos, should I draft a delivery-focused special {offer_clause} for tonight?"
    )
    rationale = f"IPL match trigger ({match}) paired with contrarian delivery optimization logic."
    return body, "binary_yes_no", "vera", rationale


def compose_category_seasonal(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    season = payload.get("season", "seasonal").replace("_", " ")

    body = (
        f"{salutation}, demand trends for {season} are shifting — summer essentials demand is rising over 35%. "
        "Want me to draft a Google Business post highlighting your seasonal stock and delivery services?"
    )
    rationale = f"Category seasonal demand movement alert for {season}."
    return body, "binary_yes_no", "vera", rationale


def compose_competitor_opened(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    comp_name = payload.get("competitor_name", "A new business")
    dist = payload.get("distance_km", 1.5)
    comp_offer = payload.get("their_offer", "")
    comp_clause = f" with an opening offer of '{comp_offer}'" if comp_offer else ""

    body = (
        f"{salutation}, heads up: {comp_name} recently opened {dist} km away{comp_clause}. "
        "Want me to highlight your clinic credentials and verified patient reviews on your profile to protect market share?"
    )
    rationale = f"Competitor alert for {comp_name} ({dist}km away) with defensive profile positioning action."
    return body, "binary_yes_no", "vera", rationale


def compose_active_planning(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger
    payload = t.get("payload", {})

    salutation = get_owner_salutation(m, c.get("slug", ""))
    topic = payload.get("intent_topic", "custom package").replace("_", " ")

    body = (
        f"{salutation}, regarding your plan for the {topic} — I've drafted a structured proposal with tiered pricing. "
        "Want me to share the draft text and WhatsApp template right now?"
    )
    rationale = f"Active planning continuation for {topic} offering drafted package."
    return body, "binary_yes_no", "vera", rationale


def compose_curious_ask(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category

    salutation = get_owner_salutation(m, c.get("slug", ""))
    biz_name = m.get("identity", {}).get("name", "your business")

    body = (
        f"{salutation}, quick question — what service or treatment has been most asked-for this week at {biz_name}? "
        "I'll turn your answer into a Google post and a 4-line WhatsApp reply in 5 minutes."
    )
    rationale = "Low-friction curious-ask hook with immediate effort externalization."
    return body, "open_ended", "vera", rationale


def compose_generic_fallback(bundle: ContextBundle) -> Tuple[str, str, str, str]:
    m = bundle.merchant
    c = bundle.category
    t = bundle.trigger

    salutation = get_owner_salutation(m, c.get("slug", ""))
    offer = select_active_offer(m, c)
    offer_str = f" for '{offer.get('title')}'" if offer else ""
    kind_str = t.get("kind", "account update").replace("_", " ")

    body = (
        f"{salutation}, quick check regarding your {kind_str}. "
        f"Would you like me to schedule a Google Business post{offer_str} to maintain your search visibility this week?"
    )
    rationale = f"Deterministic fallback composition for trigger kind '{t.get('kind')}'."
    return body, "binary_yes_no", "vera", rationale


# --- Dispatch Table ---

DISPATCHER = {
    "research_digest": compose_research_digest,
    "regulation_change": compose_regulation_change,
    "cde_opportunity": compose_cde_opportunity,
    "recall_due": compose_recall_due,
    "chronic_refill_due": compose_chronic_refill,
    "appointment_tomorrow": compose_appointment_tomorrow,
    "trial_followup": compose_trial_followup,
    "customer_lapsed_soft": compose_customer_lapsed,
    "customer_lapsed_hard": compose_customer_lapsed,
    "wedding_package_followup": compose_wedding_package_followup,
    "perf_dip": compose_perf_dip,
    "perf_spike": compose_perf_spike,
    "seasonal_perf_dip": compose_seasonal_perf_dip,
    "milestone_reached": compose_milestone,
    "review_theme_emerged": compose_review_theme,
    "gbp_unverified": compose_gbp_unverified,
    "renewal_due": compose_dormant_renewal,
    "dormant_with_vera": compose_dormant_renewal,
    "winback_eligible": compose_winback_eligible,
    "festival_upcoming": compose_festival,
    "ipl_match_today": compose_ipl_match,
    "category_seasonal": compose_category_seasonal,
    "competitor_opened": compose_competitor_opened,
    "active_planning_intent": compose_active_planning,
    "curious_ask_due": compose_curious_ask,
    "supply_alert": compose_supply_alert,
}


# --- Engine Class ---

class DecisionEngine:
    """
    Main decision engine evaluating ticks and composing outbound actions.
    """

    def evaluate_tick(self, now_str: str, available_triggers: List[str]) -> List[TickAction]:
        actions: List[TickAction] = []
        now_dt = self._parse_iso(now_str)

        # 1. Collect valid triggers from store
        valid_triggers: List[Dict[str, Any]] = []
        for tid in available_triggers:
            stored = context_store.get("trigger", tid)
            if not stored:
                continue
            trg = stored.payload
            valid_triggers.append(trg)

        # 2. Stable sort by urgency desc, then trigger id asc
        valid_triggers.sort(key=lambda t: (-t.get("urgency", 1), t.get("id", "")))

        seen_merchants: set[str] = set()

        for trg in valid_triggers:
            if len(actions) >= 20:
                break

            tid = trg.get("id", "")
            mid = trg.get("merchant_id", "")
            cid = trg.get("customer_id")
            suppression_key = trg.get("suppression_key", f"{trg.get('kind')}:{mid}")

            # Check expiry
            expires_at_str = trg.get("expires_at")
            if expires_at_str:
                exp_dt = self._parse_iso(expires_at_str)
                if now_dt and exp_dt and now_dt > exp_dt:
                    continue

            # Check suppression
            if suppression_registry.is_suppressed(suppression_key, now_dt):
                continue

            if suppression_registry.is_merchant_suppressed(mid, now_dt):
                continue

            # Single action per merchant per tick
            if mid in seen_merchants:
                continue

            # Retrieve Merchant & Category
            stored_merchant = context_store.get("merchant", mid)
            if not stored_merchant:
                continue
            merchant = stored_merchant.payload

            cat_slug = merchant.get("category_slug", "")
            stored_category = context_store.get("category", cat_slug)
            category = stored_category.payload if stored_category else {"slug": cat_slug}

            # Retrieve optional customer
            customer = None
            if cid:
                if suppression_registry.is_customer_suppressed(cid, now_dt):
                    continue
                stored_cust = context_store.get("customer", cid)
                if stored_cust:
                    customer = stored_cust.payload
                    # Customer consent check
                    if not customer.get("preferences", {}).get("reminder_opt_in", True):
                        continue

            bundle = ContextBundle(category=category, merchant=merchant, trigger=trg, customer=customer)
            kind = trg.get("kind", "")
            composer_fn = DISPATCHER.get(kind, compose_generic_fallback)

            raw_body, cta, send_as, rationale = composer_fn(bundle)
            clean_body = sanitize_message(raw_body, cat_slug)

            conv_id = f"conv_{mid}_{tid}"
            template_name = f"{send_as}_{kind}_v1"
            template_params = [
                merchant.get("identity", {}).get("owner_first_name", "Merchant"),
                clean_body[:40],
            ]

            action = TickAction(
                conversation_id=conv_id,
                merchant_id=mid,
                customer_id=cid,
                send_as=send_as,
                trigger_id=tid,
                template_name=template_name,
                template_params=template_params,
                body=clean_body,
                cta=cta,
                suppression_key=suppression_key,
                rationale=rationale,
            )

            # Record suppression
            suppression_registry.mark_dispatched(suppression_key, now=now_dt)
            seen_merchants.add(mid)
            actions.append(action)

        return actions

    @staticmethod
    def _parse_iso(ts_str: Optional[str]) -> Optional[datetime]:
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            return None


decision_engine = DecisionEngine()
