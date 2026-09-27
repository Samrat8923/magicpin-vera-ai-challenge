# Magicpin VERA AI Assistant — Phase 1 Submission

A high-performance, deterministic FastAPI service implementing the 4-context composition framework for the Magicpin VERA AI Challenge.

---

## 1. Project Structure

```
magicpin-ai-challenge/
├── app/
│   ├── __init__.py           # Package initializer
│   ├── main.py               # FastAPI application setup, global error handling, router mounting
│   ├── models.py             # Strict Pydantic models for all 5 endpoint contracts
│   ├── store.py              # Thread-safe in-memory ContextStore (category, merchant, customer, trigger)
│   ├── state.py              # ConversationStore & SuppressionRegistry state engines
│   ├── config.py             # Environment configuration (PORT, HOST, team metadata)
│   ├── health.py             # GET /v1/healthz router
│   ├── metadata.py           # GET /v1/metadata router
│   ├── context_routes.py     # POST /v1/context router (idempotent, atomic versioning)
│   ├── tick_routes.py        # POST /v1/tick router (proactive dispatch interface)
│   └── reply_routes.py       # POST /v1/reply router (delegates to conversation_handlers)
├── conversation_handlers.py  # Multi-turn deterministic state machine & respond() interface
├── tests/
│   ├── test_phase1.py        # Phase 1 test suite (health, metadata, context versioning)
│   └── test_phase2.py        # Phase 2 test suite (intent classification, auto-reply, commitments, opt-outs)
├── bot.py                    # Root entrypoint for uvicorn/judge compatibility
├── requirements.txt          # Production dependencies
└── README.md                 # System documentation
```

---

## 2. API Endpoints Implemented

| Endpoint | Method | Purpose | Response Code | Key Behavior |
|---|---|---|---|---|
| `/v1/healthz` | GET | Liveness probe & context counts | 200 | Returns status `"ok"`, real uptime seconds, and context counts for all 4 scopes |
| `/v1/metadata` | GET | Bot & submission metadata | 200 | Returns team name, model, approach, version, and submission timestamp |
| `/v1/context` | POST | Ingest/update contexts | 200 / 409 / 400 | Atomic insertion & upgrade; 409 for stale/same versions; 400 for invalid scope |
| `/v1/tick` | POST | Periodic simulated time wake-up | 200 | Validates triggers; returns `{"actions": []}` for Phase 1 |
| `/v1/reply` | POST | Inbound turn reply handler | 200 | Records conversation history; handles auto-replies, hostility, commitment |

---

## 3. How to Run the Server

Start the server using `uvicorn` (default port: `8080`):

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Alternatively, run `bot.py`:

```bash
python bot.py
```

Configurable environment variables:
- `PORT`: Server port (default: `8080`)
- `HOST`: Server host (default: `0.0.0.0`)
- `TEAM_NAME`: Team display name
- `TEAM_MEMBERS`: Comma-separated member names
- `MODEL`: Model identifier
- `CONTACT_EMAIL`: Contact email address

---

## 4. Running the Tests

Run the full automated test suite:

```bash
python -m pytest tests/test_phase1.py -v
```
