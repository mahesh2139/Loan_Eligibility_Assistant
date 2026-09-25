# LoanAssist v2

A **RAG-based loan pre-qualification assistant** that delivers consistent,
policy-grounded eligibility assessments for Personal Loans and Home Loans via a
streaming chat interface.

> **Preliminary assessment only.** This system does not constitute a loan offer,
> commitment, or final approval.

---

## What it does

| Capability | Detail |
|:---|:---|
| Streaming chat | SSE token-by-token with sentence-buffered output guard |
| RAG over versioned policies | Two ChromaDB collections (`personal_loan`, `home_loan`), filtered by active policy version |
| Grounded citations | Every answer cites the Rule ID and document version it is based on |
| Deterministic rules engine | Pure-Python PASS/FAIL/MANUAL_REVIEW — LLM never decides eligibility |
| Financial calculators | EMI (reducing-balance), FOIR, LTV, max affordable loan |
| Conversational profile collection | Asks only for missing fields; maps "house loan" → home loan |
| Audit logging | Full record: input snapshot, policy version, rule results, calculations, decision |
| What-if simulator | Override any profile field; re-run the rules engine instantly |
| Policy version control | Activate a new policy by changing one env var — no code change |
| Promptfoo eval gate | 11 labelled test cases block a merge when eligibility accuracy regresses |
| 3-layer safety | Input guard → grounded LLM (temp=0) → output guard (per-sentence) |

---

## Architecture

```
User
 └─► Streamlit UI :8501
      └─► POST /chat (SSE)  ─►  Input Guard
                                Profile Extractor (LLM, temp=0)
                                │
                                ├─ incomplete ─► Conversation Agent (LLM, streaming)
                                │
                                └─ complete ──► Policy RAG (ChromaDB, version-filtered)
                                               Rules Engine (pure Python)
                                               Calculators (pure Python)
                                               Explanation Agent (LLM, streaming)
                                               Output Guard (per sentence)
                                               Audit Log (JSONL + memory)
```

### Services (Docker Compose)

| Service | URL | Purpose |
|:---|:---|:---|
| API | http://localhost:8001 | FastAPI: `/chat`, `/ask`, `/audit/{id}`, `/scenario`, `/health`, `/metrics`, `/versions` |
| LiteLLM | http://localhost:4000 | OpenAI-compatible provider proxy with cloud fallback |
| Local model | http://localhost:8090 | Qwen 2.5-1.5B server |
| Prometheus | http://localhost:9090 | Metrics scrape |
| Grafana | http://localhost:3000 | Dashboard (`admin` / `admin`) |

The Streamlit UI runs on the host at http://localhost:8501.  
ChromaDB is embedded and persists to `rag/chroma/`.

---

## Prerequisites

- Docker Engine and Docker Compose v2
- Python 3.11 or newer
- ~1 GB disk space for the local model weights

```bash
docker --version
docker compose version
python --version
```

---

## First-time setup

### 1. Environment file

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Key variables:

```dotenv
API_KEY=local-dev-key
PROMPT_VERSION=v1

# Active policy version per product (change to activate a new policy)
ACTIVE_PERSONAL_LOAN_VERSION=v2
ACTIVE_HOME_LOAN_VERSION=v1

# Cloud fallback (optional)
OPENROUTER_API_KEY=
```

### 2. Python environment

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r api/requirements.txt
pip install -r ui/requirements.txt
```

### 3. Create directories

```bash
mkdir -p logs rag/chroma
```

On Linux/macOS, export your UID/GID so volume mounts stay writable:

```bash
export HOST_UID=$(id -u)
export HOST_GID=$(id -g)
```

### 4. Ingest policy documents

Run this after first checkout and after any change to `rag/policies/*.md`:

```bash
python rag/ingest.py
```

This parses YAML front-matter from each policy file and builds three ChromaDB
collections: `personal_loan`, `home_loan`, and `eligibility` (backward compat).
The first run downloads an ~80 MB embedding model.

Expected output:

```
Files found  : 3
  home_loan_v1.md     product=home_loan     version=v1  sections=11
  personal_loan_v1.md product=personal_loan version=v1  sections=9
  personal_loan_v2.md product=personal_loan version=v2  sections=9

Total chunks: 29
  personal_loan: 18 chunks
  home_loan: 11 chunks
```

---

## Running the application

Start the backend:

```bash
docker compose up -d --build
docker compose ps
```

Check readiness:

```bash
curl http://localhost:8001/health
```

A healthy response includes:

```json
{
  "status": "ok",
  "version": "2.0.0",
  "rag_collections": {"personal_loan": 18, "home_loan": 11, "eligibility": 29},
  "active_policy_versions": {"personal_loan": "v2", "home_loan": "v1"}
}
```

Start the UI:

```bash
streamlit run ui/chat_app.py
```

Open http://localhost:8501.

---

## API endpoints

### `POST /chat` — streaming eligibility chat (primary)

Returns Server-Sent Events. Event types:

| Event | When | Payload |
|:---|:---|:---|
| `token` | Each streamed sentence | `{"type":"token","content":"..."}` |
| `profile_update` | After profile extraction | `{"profile":{...},"missing_fields":[...]}` |
| `decision` | After rules engine | `{"data":{"decision":"...","rule_checks":[...],...}}` |
| `calculations` | After calculators | `{"data":{"emi":...,"foir":...,"ltv":...}}` |
| `citations` | After RAG retrieval | `{"sources":[{"rule_id":"HL-LTV-001","version":"v1",...}]}` |
| `audit_ref` | After audit write | `{"application_id":"APP-XXXXXXXXXXXX"}` |
| `error` | On any error | `{"type":"error","content":"..."}` |

```bash
curl -N -X POST http://localhost:8001/chat \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: local-dev-key' \
  -d '{"session_id":"test-1","message":"I want a home loan"}'
```

### `POST /ask` — non-streaming (backward compatible)

```bash
curl -X POST http://localhost:8001/ask \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: local-dev-key' \
  -d '{"question":"Am I eligible for a personal loan? Age 35, income 60000, CIBIL 750."}'
```

### `GET /audit/{application_id}` — retrieve audit record

```bash
curl http://localhost:8001/audit/APP-XXXXXXXXXXXX \
  -H 'X-API-Key: local-dev-key'
```

### `POST /scenario` — what-if re-score

Override any profile field and get an instant re-evaluation:

```bash
curl -X POST http://localhost:8001/scenario \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: local-dev-key' \
  -d '{"session_id":"test-1","overrides":{"credit_score":750,"monthly_net_income":80000}}'
```

### `GET /versions` — active policy versions

```bash
curl http://localhost:8001/versions
```

---

## Policy versioning

Each policy document in `rag/policies/` has YAML front-matter:

```yaml
---
policy_id: LPQ-PL-2026-02
version: v2
effective_from: "2026-07-01"
effective_to: null
product: personal_loan
status: active
---
```

To activate a new policy version, update `.env` and restart the API:

```dotenv
ACTIVE_PERSONAL_LOAN_VERSION=v2
```

```bash
docker compose restart api
```

No code change is required. The RAG retrieval automatically filters chunks with
`where={"version": "v2"}`.

### Personal Loan: v1 vs v2

| Criterion | v1 (superseded) | v2 (active from 2026-07-01) |
|:---|:---|:---|
| Minimum monthly income | ₹25,000 | ₹22,000 |
| Minimum credit score | 700 | 680 |
| Maximum FOIR | 50% | 55% |
| Maximum loan amount | ₹25,00,000 | ₹30,00,000 |

---

## Running tests

### Unit tests (rules engine + calculators)

```bash
cd api
python -m pytest tests/ -v
```

Expected: **60 passed**.

### Eval gate (Promptfoo)

Requires the API stack to be running:

```bash
bash scripts/eval-gate.sh
```

Tests 11 scenarios across Personal Loan (eligible, credit fail, FOIR fail,
income fail, employment fail), Home Loan (eligible, LTV fail, manual review,
property fail), prompt injection refusal, and general greeting.

### Labelled test set

`tests/labelled_set.json` contains 20 fully labelled cases. Each record has:
- `profile` — complete `ApplicantProfile` field values
- `expected_decision` — ground-truth decision
- `expected_failed_rules` — which rule IDs should fail

---

## CI/CD evaluation gate

`.github/workflows/eval-gate.yml` runs on every PR to `main`:

1. **Unit tests** — `pytest tests/` must be 100% green
2. **Promptfoo eval** — starts the Docker stack, runs 11 eligibility tests, exits 1 on any failure

A failing eval gate **blocks the merge** and uploads results as a workflow artifact.

---

## Project layout

```
api/
  app.py                 FastAPI application (v2: /chat, /scenario, /audit)
  calculators.py         Deterministic EMI / FOIR / LTV / max_loan
  rules_engine.py        Pure-Python eligibility rules engine
  profile_extractor.py   LLM profile extraction from conversation
  guardrails.py          3-layer input + output safety
  redact.py              PII masking (email, Aadhaar, PAN, phone)
  Dockerfile
  requirements.txt
  tests/
    test_calculators.py  23 unit tests
    test_rules_engine.py 37 unit tests

model_server/            OpenAI-compatible Qwen server
litellm/                 Provider proxy configuration (local + cloud fallback)

prompts/
  registry.yaml          Versioned prompts (profile_extractor, conversation_agent,
                         explanation_agent, answer_grounded)
  loader.py              Cached YAML loader; fails fast on unknown version

rag/
  policies/
    personal_loan_v1.md  Personal Loan policy v1 (superseded)
    personal_loan_v2.md  Personal Loan policy v2 (active)
    home_loan_v1.md      Home Loan policy v1 (active)
  ingest.py              RAG ingestion: YAML frontmatter + per-product collections
  chroma/                ChromaDB persistent index (git-ignored)

tests/
  labelled_set.json      20 labelled synthetic test cases

ui/
  chat_app.py            Streamlit UI v2: streaming, result card, what-if,
                         document checklist, policy comparison, audit viewer

prometheus/              Metrics scrape configuration
grafana/                 Provisioned dashboards
scripts/
  eval-gate.sh           Promptfoo eval runner (used by CI/CD)

.github/
  workflows/
    eval-gate.yml        CI/CD pipeline: unit tests → eval gate → block merge

.env.example             All environment variables with comments
promptfooconfig.yaml     11-case Promptfoo test suite
```

---

## Troubleshooting

| Problem | Fix |
|:---|:---|
| API unavailable | `docker compose ps` and `docker compose logs api litellm model` |
| Model still starting | Wait for its health check — weights download on first startup |
| Zero RAG chunks | `python rag/ingest.py` then `docker compose restart api` |
| UI cannot connect | Check sidebar API URL is `http://localhost:8001` |
| Cloud fallback errors | Set a valid `OPENROUTER_API_KEY` in `.env` and restart |
| `/chat` returns no events | Check `X-API-Key` header matches `API_KEY` in `.env` |
| Scenario 404 | Start a `/chat` session first — the profile must exist in memory |