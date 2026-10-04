# LoanAssist v3.5 (Enterprise Banking Capstone Edition)

A **neuro-symbolic, RAG-based loan pre-qualification and advisory assistant** that delivers consistent,
policy-grounded eligibility assessments for **Personal Loans, Home Loans, and Auto Loans** via a
professional streaming chat interface with deterministic financial underwriting and automated adverse-action remediation.

> **Preliminary assessment only.** This system does not constitute a loan offer,
> commitment, or final approval.

---

## What it does (v3.5 Enterprise Highlights)

| Capability | Detail |
|:---|:---|
| **Semantic Loan Detection** | Inferred naturally from conversation without buttons or tags — "building loan" / "flat loan" → Home Loan; "car" / "scooter" / "bus" / "bike" / "EV" → Auto Loan; "cash loan" → Personal Loan |
| **Auto Loan End-to-End** | 11-section policy (`AL-AGE-001` to `AL-EXC-001`), on-road price LTV calculator (85% new, 70% used), vehicle age & tenure limits, 9 rule evaluators |
| **Joint / Co-Applicant Pooling** | Supports joint applicants across retail loans — pools household net incomes and existing EMIs to satisfy FOIR and income threshold criteria |
| **Adverse-Action Remediation Engine** | Pure-Python deterministic solver that generates actionable alternatives (affordable loan reduction, tenure extension solving amortization for target EMI, collateral down payment top-up, co-borrower addition) complying with RBI Fair Practice Code & US ECOA |
| **Conversational FAQ Side-Routing** | Intercepts policy questions (interest rates, foreclosure, prepayment, property eligibility) mid-assessment, answers with verified RAG context, and gently reminds the user of missing profile fields without getting trapped in missing-field loops |
| **Hybrid RAG Retriever (RRF + BM25)** | Reciprocal Rank Fusion ($k=60$) combining ChromaDB dense vector embeddings with BM25 sparse keyword search (Robertson-Spärck Jones IDF) and exact Rule-ID priority boosting ($+0.25$) |
| **Sub-15ms Semantic FAQ Cache** | Ultra-fast token similarity cache providing sub-15ms responses to high-frequency bank policy inquiries, cutting inference cost and latency |
| **Sliding-TTL Distributed Session Store** | Memory-managed session store with sliding TTL eviction, with seamless zero-code fallback to distributed Redis via `REDIS_URL` |
| **DPDP Act (2023) Compliance** | Ephemeral in-session processing, PII masking (PAN, Aadhaar, account numbers), and explicit privacy consent disclosure in UI |
| **Deterministic Rules Engine** | Pure-Python `PASS` / `FAIL` / `MANUAL_REVIEW` / `INSUFFICIENT_INFORMATION` — LLM never decides eligibility or computes math |
| **RAG over Versioned Policies** | Per-product ChromaDB collections (`personal_loan`, `home_loan`, `auto_loan`), metadata-filtered by active policy version |
| **Grounded Citations** | Every assessment cites exact Rule IDs and versioned policy documents |
| **Financial Calculators** | Pure-Python reducing-balance EMI, FOIR, LTV (property / on-road price), tenure solver, and max affordable loan |
| **Quantitative RAG Triad Benchmarks** | Comprehensive test suite testing Context Relevance ($100\%$), Groundedness / Faithfulness ($100\%$), and Answer Relevance ($100\%$) |
| **96 Automated Tests** | Zero-regression test suite covering calculators, co-applicant pooling, remediation solvers, rules engine, and RAG Triad |
| **3-Layer Safety Defence** | Layer 1 Input Guard → Layer 2 Grounded Prompts (temp=0) → Layer 3 Per-Sentence Streaming Output Guard |

---

## Architecture

```
User (Browser :8501)
  └─► Streamlit Chat UI v3.5 (DPDP Consent + Remediation Cards + What-If Sliders)
       └─► POST /chat (SSE Stream)
             ├─► Layer 1: Input Guard (injection blocklist, PII detection, topic filter)
             ├─► Layer 1.5: Semantic FAQ Cache (<15ms instant cache for frequent bank FAQs)
             ├─► Profile Extractor (LLM temp=0 + Python alias fallback + profile merge + co-applicant)
             │
             ├─ Incomplete profile + Policy Question ─► Informational Side-Routing (RAG answer + missing reminder)
             ├─ Incomplete profile ──────────────────► Conversation Agent (LLM, asks missing fields only)
             ├─ Document request  ───────────────────► Document Agent (on-demand checklist)
             │
             └─ Complete profile  ───────────────────► Hybrid RAG Retriever (Dense + BM25 + RRF + Rule-ID Boost)
                                                       Deterministic Rules Engine (pure Python, 25+ rules)
                                                       Household Financial Calculators (EMI, FOIR, LTV, Co-Applicant)
                                                       Adverse-Action Remediation Engine (deterministic alternatives)
                                                       Explanation Agent (LLM, grounded explanation)
                                                       Layer 3: Output Guard (per-sentence buffer)
                                                       Sliding-TTL Session Store (Memory / Redis)
                                                       Audit Logger (JSONL + compliance record)
```

### Services (Docker Compose)

| Service | URL | Purpose |
|:---|:---|:---|
| **API** | `http://localhost:8001` | FastAPI: `/chat` (SSE), `/ask`, `/audit/{id}`, `/scenario`, `/health`, `/metrics`, `/versions` |
| **LiteLLM Admin UI & Proxy** | `http://localhost:4000/ui` (Proxy: `http://localhost:4000`) | OpenAI-compatible proxy & dashboard (Master Key: `sk-master-key-loanassist`) |
| **Local model** | `http://localhost:8090` | Qwen 2.5-1.5B local inference server |
| **Prometheus** | `http://localhost:9090` | Metrics scraping target |
| **Grafana** | `http://localhost:3000` | Golden signals dashboard (`admin` / `admin`) |

The Streamlit UI runs at `http://localhost:8501`.  
ChromaDB is embedded and persists to `rag/chroma/`.

---

## Supported Loan Products

### 1. Personal Loan
- **Age:** 21 to 60 years
- **Income:** $\ge$ ₹25,000 / month (or combined household income with co-applicant)
- **Employment:** Salaried $\ge$ 12 months, Self-employed $\ge$ 24 months
- **Credit Score:** $\ge$ 700
- **FOIR:** $\le$ 50%
- **Loan Amount:** ₹50,000 to ₹25,00,000 (capped at $30\times$ monthly income)
- **Tenure:** 12 to 60 months
- **Indicative Interest:** 12.0% p.a.

### 2. Home Loan
- **Age:** $\ge$ 21 years; loan closure before age 70
- **Income:** $\ge$ ₹40,000 / month (or combined household income with co-applicant)
- **Employment:** Salaried $\ge$ 24 months, Self-employed $\ge$ 36 months (24–35 months triggers `MANUAL_REVIEW`)
- **Credit Score:** $\ge$ 700
- **FOIR:** $\le$ 55% (pooled household obligations)
- **LTV:** $\le$ 85% for loans $\le$ ₹30L; $\le$ 80% for loans > ₹30L
- **Property:** Apartment, Villa, or Residential House (Commercial / Agricultural strictly ineligible)
- **Loan Amount:** ₹5,00,000 to ₹5,00,00,000
- **Tenure:** 12 to 360 months
- **Indicative Interest:** 8.5% p.a.

### 3. Auto Loan
- **Age:** 21 to 65 years; loan closure before age 70
- **Income:** $\ge$ ₹20,000 / month
- **Employment:** Salaried $\ge$ 12 months, Self-employed $\ge$ 24 months
- **Credit Score:** $\ge$ 680
- **FOIR:** $\le$ 50%
- **LTV (on On-Road Price):** $\le$ 85% for new vehicles; $\le$ 70% for used vehicles
- **Vehicle Eligibility:** Two-wheelers, four-wheelers, commercial vehicles (new), electric vehicles
- **Used Vehicle Limits:** Max age 10 years at application, max tenure 60 months
- **Loan Amount:** ₹50,000 to ₹50,00,000
- **Tenure:** 12 to 84 months (new), up to 48 months (commercial)
- **Indicative Interest:** 9.0% p.a.

---

## Python Environment Setup

### 1. Clone & Navigate to Project
```bash
cd Loan_Eligibility_Assistant
```

### 2. Configure Environment File
Copy `.env.example` to `.env`:

```bash
# Windows (PowerShell)
Copy-Item .env.example .env

# Linux / macOS
cp .env.example .env
```

Key configuration options in `.env`:
```dotenv
API_KEY=local-dev-key
LLM_BASE_URL=http://localhost:4000/v1
LLM_MODEL=qwen-local
PROMPT_VERSION=v1

# Active policy versions (ChromaDB filters by active version)
ACTIVE_PERSONAL_LOAN_VERSION=v2
ACTIVE_HOME_LOAN_VERSION=v1
ACTIVE_AUTO_LOAN_VERSION=v1

# Optional distributed session backend
# REDIS_URL=redis://localhost:6379/0
```

### 3. Create & Activate Python Virtual Environment

#### On Windows (PowerShell):
```powershell
# Create virtual environment
python -m venv .venv

# Activate environment
.\.venv\Scripts\Activate.ps1
```

*(If execution policy blocks script activation, run: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`)*

#### On Linux / macOS:
```bash
# Create virtual environment
python3 -m venv .venv

# Activate environment
source .venv/bin/activate
```

### 4. Install Dependencies
```bash
python -m pip install --upgrade pip
pip install -r api/requirements.txt
pip install -r ui/requirements.txt
```

### 5. Create Log & Data Directories
```bash
# Windows (PowerShell)
New-Item -ItemType Directory -Force -Path logs, rag/chroma

# Linux / macOS
mkdir -p logs rag/chroma
```

---

## Policy Ingestion (RAG Setup)

Run this step to chunk and index the markdown policies into ChromaDB before running the application:

```bash
python rag/ingest.py
```

Expected output:
```
LoanAssist RAG Ingestion
Policies dir : .../rag/policies
Chroma dir   : .../rag/chroma
Files found  : 4

  auto_loan_v1.md         product=auto_loan     version=v1  sections=11
  home_loan_v1.md         product=home_loan     version=v1  sections=11
  personal_loan_v1.md     product=personal_loan version=v1  sections=9
  personal_loan_v2.md     product=personal_loan version=v2  sections=9

Total chunks: 40
  personal_loan: 18 chunks
  home_loan: 11 chunks
  auto_loan: 11 chunks

Building per-product collections ...
  [OK] 'personal_loan' -- 18 chunks stored
  [OK] 'home_loan' -- 11 chunks stored
  [OK] 'auto_loan' -- 11 chunks stored
Building generic 'eligibility' collection (backward compat) ...
  [OK] 'eligibility' -- 40 chunks stored

Ingestion complete.
```

---

## Running the Application

### Option A: Full Stack with Docker Compose (Recommended)

1. **Start all backend services:**
   ```bash
   docker compose up -d --build
   ```

2. **Verify running containers:**
   ```bash
   docker compose ps
   ```

3. **Check API Health:**
   ```bash
   curl http://localhost:8001/health
   ```

4. **Launch Streamlit Chat UI:**
   ```bash
   streamlit run ui/chat_app.py
   ```
   Open `http://localhost:8501` in your browser.

---

### Option B: Local Python Execution (Development / Debugging)

1. **Start the API directly:**
   ```bash
   # From project root with .venv activated
   cd api
   python -m uvicorn app:app --host 0.0.0.0 --port 8001 --reload
   ```

2. **Run Streamlit in a separate terminal:**
   ```bash
   # From project root with .venv activated
   streamlit run ui/chat_app.py
   ```

---

## Running Tests & Evaluation Benchmarks

### 1. Automated Test Suite (96 Tests Passing)
Run all 96 unit, integration, and RAG Triad benchmark tests across the repository:

```bash
pytest api/tests/ tests/ -v
```

Expected output:
```
============================= 96 passed in 5.23s ==============================
```

Breakdown of Test Suites:
- `api/tests/test_calculators.py` (24 tests): Standard reducing-balance EMI, FOIR, LTV, zero-interest edge cases, tenure solving for target EMI.
- `api/tests/test_co_applicant.py` (4 tests): Joint household income pooling, joint obligations, required field dynamic checks.
- `api/tests/test_remediation.py` (7 tests): Adverse-action remediation calculations, affordable loan reductions, tenure extension solving, collateral top-ups, co-borrower recommendations.
- `api/tests/test_rules_engine.py` (56 tests): Boundary conditions across Personal, Home, and Auto Loans (`PASS`, `FAIL`, `MANUAL_REVIEW`, `INSUFFICIENT_INFORMATION`).
- `tests/test_rag_triad.py` (5 tests): Quantitative evaluation of **Context Relevance**, **Groundedness / Faithfulness**, and **Answer Relevance**.

### 2. CI/CD Promptfoo Evaluation Gate
To execute the automated regression and safety evaluation gate locally (requires running API):

```bash
bash scripts/eval-gate.sh
```

Evaluates:
- Personal, Home, and Auto loan eligibility boundary enforcement
- Prompt injection resistance and refusal handling
- Adverse action remediation notes generation
- Semantic loan intent detection

---

## API Reference

### `POST /chat` — Streaming Eligibility Chat (Primary)
Server-Sent Events (SSE) streaming endpoint:

| Event Type | Description |
|:---|:---|
| `token` | Streamed text chunk (buffered by sentence through Layer 3 output guard) |
| `profile_update` | Extracted profile snapshot and missing required fields |
| `decision` | Structured decision object (`POTENTIALLY_ELIGIBLE`, `NOT_ELIGIBLE`, `MANUAL_REVIEW`, `INSUFFICIENT_INFORMATION`) |
| `calculations` | Financial outputs: reducing EMI, FOIR, LTV, and max affordable loan |
| `remediation` | Structured adverse-action alternatives (suggested loan, tenure, down payment, co-applicant income) |
| `citations` | Retrieved policy clauses with document name, rule ID, and version |
| `audit_ref` | Unique application ID for tracking and compliance audit |
| `error` | Error or safety refusal notification |

Example request:
```bash
curl -N -X POST http://localhost:8001/chat \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: local-dev-key' \
  -d '{"session_id":"sess-1","message":"I want to check home loan eligibility. Salary is Rs.50,000, age 32, need 50 Lakhs for 20 years."}'
```

### `POST /scenario` — What-If Simulator
Override profile fields to simulate alternative terms or financial adjustments:
```bash
curl -X POST http://localhost:8001/scenario \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: local-dev-key' \
  -d '{
    "session_id": "sess-1",
    "overrides": {
      "co_applicant_income": 40000,
      "requested_amount": 4000000
    }
  }'
```

### `GET /audit/{application_id}` — Compliance Audit Record
Retrieve full underwriting logs including input snapshot, rule checks, calculations, remediation notes, and policy IDs.

---

## Project Layout

```
Loan_Eligibility_Assistant/
├── api/
│   ├── app.py                 # FastAPI application (/chat SSE, /scenario, /audit, /health)
│   ├── calculators.py         # Financial calculators, tenure solver & adverse-action remediation
│   ├── rules_engine.py        # Pure-Python eligibility rules engine with co-applicant pooling
│   ├── profile_extractor.py   # LLM extraction, semantic aliases, informational query detector
│   ├── guardrails.py          # 3-layer security (injection blocklist, PII, topic gate)
│   ├── session_store.py       # Sliding-TTL session abstraction (In-Memory / Redis)
│   ├── semantic_cache.py      # Sub-15ms semantic FAQ cache for policy queries
│   ├── redact.py              # PII detection & masking
│   ├── requirements.txt       # Backend dependencies
│   └── tests/
│       ├── test_calculators.py   # Calculator unit tests (24 tests)
│       ├── test_co_applicant.py  # Co-applicant household pooling tests (4 tests)
│       ├── test_remediation.py   # Adverse-action remediation tests (7 tests)
│       └── test_rules_engine.py  # Rules engine unit tests (56 tests)
├── prompts/
│   ├── registry.yaml          # Versioned prompt registry (v1 production, v2 canary)
│   └── loader.py              # YAML prompt loader
├── rag/
│   ├── policies/
│   │   ├── personal_loan_v1.md # Personal loan policy v1
│   │   ├── personal_loan_v2.md # Personal loan policy v2 (active)
│   │   ├── home_loan_v1.md     # Home loan policy v1 (active)
│   │   └── auto_loan_v1.md     # Auto loan policy v1 (active)
│   ├── hybrid_retriever.py    # Hybrid RAG (Dense ChromaDB + Sparse BM25 + RRF)
│   ├── ingest.py              # ChromaDB chunking and indexing script
│   └── chroma/                # ChromaDB vector index directory
├── ui/
│   ├── chat_app.py            # Streamlit v3.5 professional chat interface
│   ├── conversation_manager.py# Persistent multi-turn session storage
│   └── requirements.txt       # Streamlit UI dependencies
├── tests/
│   ├── test_rag_triad.py      # Quantitative RAG Triad evaluation benchmark (5 tests)
│   └── labelled_set.json      # 25 labelled ground-truth test cases
├── .github/workflows/
│   └── eval-gate.yml          # GitHub Actions CI/CD eval gate pipeline
├── scripts/
│   └── eval-gate.sh           # Promptfoo runner script
├── docker-compose.yml         # Container orchestration
├── promptfooconfig.yaml       # Promptfoo automated test suites
├── .env.example               # Template environment configuration
└── README.md                  # Comprehensive system documentation
```