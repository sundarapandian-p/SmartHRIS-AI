# SmartHRIS-AI — Project Requirements

## Vision

A learning-oriented **Human Resource Information System (HRIS)** that demonstrates production-style patterns: large-scale employee data ingestion, salary management, learning management (LMS), and a **hybrid RAG** assistant for HR policy and operational questions—all built incrementally so each layer is understandable.

## Goals

| Goal | Success criteria |
|------|------------------|
| Bulk employee operations | Import and validate up to **100,000** rows from CSV/Excel with clear error reporting |
| Core HR data | Employees, departments, roles, compensation records with audit-friendly design |
| LMS | Courses, enrollments, completion tracking, certifications |
| AI assistant | Local LLM (Ollama) + **semantic (FAISS)** + **keyword (BM25)** retrieval fused with **RRF** |
| Operability | Docker-based local deployment; automated tests with **pytest** |
| Learning | Small, reviewable steps; PHP developer ramping on Python, FastAPI, and RAG |

## Non-goals (initial phases)

- Multi-tenant SaaS billing and SSO
- Full payroll tax compliance for every jurisdiction
- Replacing a commercial HRIS in production
- Cloud-hosted LLM as the default inference path

## Stakeholders and users (conceptual)

- **HR admin**: bulk upload, corrections, salary updates, course assignment
- **Manager**: team visibility (later phase)
- **Employee**: course progress, certifications, policy Q&A via assistant
- **Developer (you)**: readable modules, tests, and docs at each milestone

## Functional requirements

### 1. Authentication and authorization (phased)

- Phase 1: API key or simple JWT + role enum (`admin`, `hr`, `employee`)
- Phase 2: password hashing, refresh tokens, route-level dependencies
- All mutating HR endpoints require authenticated HR/admin roles

### 2. Employee master data

- CRUD for employee records (employee number, name, contact, department, job title, status, hire date)
- Unique constraints on business keys (e.g. employee number, work email)
- Soft delete or `status` field preferred over hard delete for audit trails
- Pagination and filtering on list endpoints

### 3. Bulk upload (CSV / Excel)

- Upload endpoint accepting `.csv`, `.xlsx`
- **Streaming or chunked processing** to support ~100k rows without loading entire file into memory
- Row-level validation with downloadable error report (row number, field, message)
- Idempotent options: upsert by employee number vs. reject duplicates
- Background job or async task queue for long imports (Celery/RQ or FastAPI `BackgroundTasks` for MVP—decide in architecture)

### 4. Salary management

- Salary history per employee (effective date, amount, currency, pay frequency, reason)
- No single mutable “current salary” without history (append-only history table)
- HR-only write; read scoped by role

### 5. LMS

- **Courses**: title, description, duration, mandatory flag, active flag
- **Enrollments**: employee ↔ course, assigned date, due date, status
- **Progress / completion**: percent or milestone-based
- **Certifications**: issued on completion, expiry optional, verifiable certificate id

### 6. Hybrid RAG assistant

- Ingest HR policies, handbook snippets, and optionally course descriptions into a **document store**
- Chunking strategy documented (size, overlap, metadata: department, doc type)
- **Embeddings**: Sentence Transformers (local)
- **Vector index**: FAISS (persisted to disk, versioned with embedding model name)
- **Keyword index**: BM25 over same chunks
- **Fusion**: Reciprocal Rank Fusion (RRF) to merge ranked lists
- **Generation**: Ollama local model; prompts include retrieved context and refusal when context insufficient
- Admin endpoint to trigger re-index after document updates

### 7. Frontends

- **Streamlit**: dashboards for upload status, employee search, LMS views, chat UI for assistant
- **FastAPI**: REST (and optional OpenAPI) for all business logic; Streamlit calls API (no business logic duplicated in UI)

## Non-functional requirements

| Area | Requirement |
|------|-------------|
| Performance | Bulk import target: process 100k rows within acceptable wall time on dev hardware (benchmark later) |
| Data | MySQL 8.4 LTS; migrations via Alembic |
| Security | Secrets in environment variables; no credentials in repo |
| Observability | Structured logging; correlation id on API requests |
| Testing | pytest unit + integration tests; test DB or containers for CI |
| Packaging | Docker Compose: API, Streamlit, MySQL, optional Ollama service |

## Data volume assumptions

- Employees: up to **100,000**
- Documents for RAG: hundreds to low thousands of chunks initially
- Concurrent users: low (learning / demo)

## Technology stack (confirmed)

- Python **3.12**
- **FastAPI** backend
- **Streamlit** frontend
- **MySQL 8.4** LTS
- **Ollama** (local LLM)
- **Sentence Transformers**, **FAISS**, **BM25**, **RRF**
- **Docker** deployment
- **pytest**

## Constraints and policies for this repo

- Incremental delivery; no big-bang codegen
- Do not commit `.env`, keys, or production data dumps
- Prefer explicit types (Pydantic v2) and thin route handlers with service layer

## Open decisions (to resolve during implementation)

1. Async import: in-process background tasks vs. dedicated worker
2. Excel library: `openpyxl` vs. `pandas` (pandas convenient but heavier)
3. Embedding model default (e.g. `all-MiniLM-L6-v2` vs. larger model)
4. Ollama model tag (e.g. `llama3.2`, `mistral`) and minimum VRAM/RAM
5. Whether Streamlit uses server-side session or passes JWT from login form

## Acceptance criteria for “MVP complete”

- Docker Compose brings up MySQL + API + Streamlit
- HR can upload a CSV, see validation errors, and query employees via API/UI
- At least one salary history record per employee workflow
- One course enrollment lifecycle demonstrated end-to-end
- RAG answers a policy question using retrieved chunks with citations
- pytest suite runs green for core services
