# SmartHRIS-AI — Development Roadmap

Incremental milestones. Each phase should end with something runnable, tested, and documented in a short PR-sized change set.

---

## Phase 0 — Foundation (recommended next)

**Outcome**: Empty application skeleton, tooling, and database connectivity—no business features yet.

| Step | Task | Deliverable |
|------|------|-------------|
| 0.1 | `pyproject.toml` (or requirements), `src/smarthris`, `pytest` smoke test | `pip install -e ".[dev]"` works locally |
| 0.2 | `config.py` + `.env.example` | Settings load from environment |
| 0.3 | SQLAlchemy engine + session; Alembic init | `alembic upgrade head` on empty DB |
| 0.4 | FastAPI `main.py` + `/health` | Returns `{ "status": "ok", "db": "ok" }` when DB up |
| 0.5 | `README.md` quickstart | Clone → env → run API |

**Learning focus**: Python package layout, FastAPI lifespan, SQLAlchemy 2.0 session pattern.

**Do not start yet until you approve**—this doc is planning only.

---

## Phase 1 — Employee core + API

| Step | Task |
|------|------|
| 1.1 | Models: `Department`, `Employee` |
| 1.2 | Pydantic schemas + CRUD service + repository |
| 1.3 | Router: list (pagination), get, create, update |
| 1.4 | pytest: repository + API tests with test DB |

**Exit criteria**: Create/list employees via curl or OpenAPI UI.

---

## Phase 2 — Bulk CSV import (MVP scale first)

| Step | Task |
|------|------|
| 2.1 | `ImportJob` + `ImportError` models |
| 2.2 | CSV streaming parser (stdlib `csv` first) |
| 2.3 | Row validation; batch insert (start with 1k rows in tests) |
| 2.4 | Job status endpoint + error export |
| 2.5 | Scale test script in `scripts/` toward 100k (benchmark) |

**Exit criteria**: Upload sample CSV; job completes; invalid rows reported.

---

## Phase 3 — Excel + hardening

| Step | Task |
|------|------|
| 3.1 | `.xlsx` support (`openpyxl` streaming read) |
| 3.2 | Upsert strategy documented and implemented |
| 3.3 | Indexes on `employee_number`, email |
| 3.4 | Optional background worker if imports exceed HTTP timeout |

---

## Phase 4 — Salary history

| Step | Task |
|------|------|
| 4.1 | `SalaryRecord` model (effective-dated) |
| 4.2 | Service: add salary, list history, current computed view |
| 4.3 | Authorization: HR-only writes |

---

## Phase 5 — LMS

| Step | Task |
|------|------|
| 5.1 | `Course`, `Enrollment`, `Certification` models |
| 5.2 | Enroll employee, update progress, complete → certification |
| 5.3 | Streamlit page: course list + enrollment status |

---

## Phase 6 — Streamlit shell

| Step | Task |
|------|------|
| 6.1 | `streamlit_app/app.py` + `api_client.py` |
| 6.2 | Pages: Employees, Import status, LMS |
| 6.3 | Shared layout and API error handling |

---

## Phase 7 — RAG: indexing

| Step | Task |
|------|------|
| 7.1 | `Document` + chunk storage; markdown/text ingest |
| 7.2 | Chunking + Sentence Transformers embed |
| 7.3 | Build/persist FAISS index; BM25 corpus |
| 7.4 | CLI or admin route: `POST /rag/reindex` |

---

## Phase 8 — RAG: query + Ollama

| Step | Task |
|------|------|
| 8.1 | Query embedding + dual retrieval |
| 8.2 | RRF merge + top-k selection |
| 8.3 | Ollama client + prompt with citations |
| 8.4 | Streamlit chat page |
| 8.5 | pytest with mocked Ollama |

---

## Phase 9 — Docker production-like stack

| Step | Task |
|------|------|
| 9.1 | `docker-compose.yml`: MySQL, API, Streamlit |
| 9.2 | Optional Compose profile for Ollama |
| 9.3 | Volume mounts for FAISS data and MySQL |
| 9.4 | Document Windows vs Docker MySQL choice |

---

## Phase 10 — Quality and polish

| Step | Task |
|------|------|
| 10.1 | CI workflow: lint (ruff), pytest |
| 10.2 | API rate limits on upload/RAG |
| 10.3 | Observability: request timing middleware |
| 10.4 | Performance notes for 100k import |

---

## Suggested dependency list (not installed yet)

When you approve Phase 0, pin versions in `pyproject.toml`:

**Runtime**

- `fastapi`, `uvicorn[standard]`
- `sqlalchemy`, `alembic`, `pymysql` or `aiomysql` + `cryptography`
- `pydantic-settings`
- `python-multipart` (uploads)
- `httpx` (Streamlit → API, Ollama client)
- `streamlit`
- `sentence-transformers`, `faiss-cpu`
- `rank-bm25` (or equivalent)
- `openpyxl` (Excel phase)

**Dev**

- `pytest`, `pytest-asyncio`, `httpx` (TestClient)
- `ruff`, `mypy` (optional)

**System / external**

- MySQL 8.4 server (available locally)
- Ollama (install or Docker—not currently on PATH)
- Docker Desktop (available)

---

## Definition of done per phase

- Code merged in small commits you can explain
- At least one new test or manual test script documented
- `docs/` updated only when behavior or ADR-level decisions change

---

## Immediate next step after your approval

**Phase 0.1–0.4**: scaffold package, configuration, Alembic, and health check—still no employee CRUD until Phase 1.
