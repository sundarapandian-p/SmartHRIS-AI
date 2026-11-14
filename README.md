# SmartHRIS-AI

Learning project: HRIS with bulk uploads, LMS, and hybrid RAG (later phases).  
**Phase 0** — FastAPI, MySQL, Alembic, `/health`. **Phase 1** — `departments` / `employees` tables and employee CRUD API.  
**Frontend** — Streamlit shell (`streamlit_app/`) as a thin client over the API.

## Prerequisites (Windows)

| Tool | Version (tested) |
|------|------------------|
| Python | 3.12+ |
| MySQL Server | 8.4 LTS (service e.g. `MySQL84`) |
| Git | optional |

## 1. Clone and open the project

```powershell
cd E:\projects\python\SmartHRIS-AI
```

## 2. Create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

## 3. Create the MySQL database and user

Connect as an administrator (adjust client path if needed):

```powershell
& "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe" -u root -p
```

In the MySQL shell:

```sql
CREATE DATABASE IF NOT EXISTS smarthris
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'smarthris'@'localhost' IDENTIFIED BY 'changeme';
GRANT ALL PRIVILEGES ON smarthris.* TO 'smarthris'@'localhost';
FLUSH PRIVILEGES;
```

Use a strong password in real environments.

## 4. Configure environment variables

```powershell
Copy-Item .env.example .env
# Edit .env — set DATABASE_URL or MYSQL_* values to match step 3.
```

Never commit `.env`; only `.env.example` belongs in git.

## 5. Run database migrations

From the project root with the venv activated:

```powershell
alembic upgrade head
```

Phase 0 ships a no-op baseline revision so Alembic history is ready for Phase 1 tables.

## 6. Run the API

```powershell
smarthris-api
# or
uvicorn smarthris.main:app --reload --host 127.0.0.1 --port 8000
```

HR document upload runs background indexing against on-disk FAISS. Use a **single API worker** only (default for `smarthris-api` and `uvicorn` without `--workers`). Do not run `uvicorn ... --workers 2` (or more) with document indexing or **semantic search** enabled—the FAISS/BM25 file locks and the document **indexing pipeline lock** are process-local and cannot serialize across workers or OS processes. See `src/smarthris/rag/faiss_store.py` and `src/smarthris/services/hr_document_processing_service.py`.

### HR document search (`POST /documents/search`)

Keyword search requires the **rank-bm25** package (not installed in the base optional set). From the project root with venv activated:

```powershell
pip install "rank-bm25>=0.2.2"
# or: pip install -e ".[rag-keyword]"
```

Send a JSON body with `query` (required), optional `top_k` (1–20, default `RAG_TOP_K`), optional `doc_type`, and optional `retrieval_mode` (`semantic` | `keyword` | `hybrid`, default **`semantic`** for backward compatibility).

**Examples**

```json
{ "query": "annual leave policy", "retrieval_mode": "semantic" }
```

```json
{ "query": "annual leave policy", "retrieval_mode": "keyword" }
```

```json
{ "query": "annual leave policy", "retrieval_mode": "hybrid", "top_k": 5 }
```

- **semantic** — FAISS vector search (cosine-like inner product on normalized embeddings).
- **keyword** — BM25 over the persisted chunk corpus (`rank-bm25`).
- **hybrid** — runs both retrievers with `RAG_RETRIEVAL_CANDIDATES` (default 60) per channel, then **Reciprocal Rank Fusion** with `RAG_RRF_CONSTANT` (default 60). `score` is the raw retriever score for single-mode search and the **RRF score** for hybrid (not comparable across modes).

**Index sync** — After a document is successfully indexed, the global **FAISS** and **BM25** indexes are rebuilt from the same MySQL `INDEXED` chunk rows. Database reconciliation rebuilds both. Hydration still returns only **INDEXED** documents and applies `doc_type`.

**HTTP semantics**

- **HTTP 422** — malformed JSON or invalid request fields (e.g. empty `query`, `top_k` out of range) rejected by FastAPI/Pydantic.
- **HTTP 400** — whitespace-only `query` after trimming (handler validation).
- **HTTP 200** — ranked hits, or `index_ready: false` when the required on-disk index for that mode is missing (`keyword` / `hybrid` may still return results if the other index is ready).

During **reindexing**, searches may briefly use the **previous in-memory FAISS or BM25 index** until on-disk files change and the process reloads them. Use a **single API worker** (same requirement as indexing).

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for OpenAPI UI.

### Health check

```powershell
curl http://127.0.0.1:8000/health
```

Example when MySQL is reachable:

```json
{"status":"ok","database":{"status":"ok"}}
```

`database.status` is `"ok"` only after the API runs `SELECT 1` against MySQL. If the server is down or credentials are wrong, you will see `"error"` while the API process itself still returns `"status":"ok"`.

## 7. Run the Streamlit frontend

Start the API first (section 6), then from the repository root with the venv activated:

```powershell
python -m streamlit run streamlit_app/app.py
```

Optional: point the UI at a non-default API base URL:

```powershell
$env:API_BASE_URL = "http://127.0.0.1:8000"
python -m streamlit run streamlit_app/app.py
```

The shell provides sidebar pages **Dashboard**, **Employees**, and **Bulk Import**. Dashboard can check `GET /health`. Employee listing and CSV bulk-upload workflows are not implemented in this foundation phase.

## 8. Run tests

```powershell
pytest
```

Tests mock the health check’s DB verification where appropriate. Employee API tests use a separate database (`smarthris_test` or `TEST_DATABASE_URL`).

Create the test database once (same credentials as `.env`, different database name):

```sql
CREATE DATABASE IF NOT EXISTS smarthris_test
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON smarthris_test.* TO 'smarthris'@'localhost';
```

## Project layout

| Path | Purpose |
|------|---------|
| `src/smarthris/config.py` | Pydantic Settings from `.env` |
| `src/smarthris/main.py` | FastAPI app factory and uvicorn entry |
| `src/smarthris/models/` | SQLAlchemy ORM (`Department`, `Employee`) |
| `src/smarthris/schemas/` | Pydantic API contracts |
| `src/smarthris/repositories/` | SQL queries (no HTTP) |
| `src/smarthris/services/` | Business rules and transactions |
| `src/smarthris/api/routers/` | HTTP routes (`health`, `employees`, `imports`) |
| `src/smarthris/core/` | Domain errors and exception handlers |
| `streamlit_app/` | Streamlit UI shell (`app.py`, `api_client.py`, `pages/`) |
| `alembic/` | Migration scripts; `env.py` reads `DATABASE_URL` |
| `tests/` | pytest (config, health, employee/import API, Streamlit client) |
| `docs/` | Requirements, architecture, roadmap |

## Next phase

See `docs/development-roadmap.md` for planned milestones. Frontend bulk-import UI and employee listing pages build on this Streamlit shell.
