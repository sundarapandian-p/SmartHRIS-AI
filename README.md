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

### Staging deployment

For a **controlled staging** rollout (env template, migration runbook, first-admin provisioning, backups, and smoke tests), see [`docs/staging-deployment.md`](docs/staging-deployment.md) and [`.env.staging.example`](.env.staging.example). Use `APP_ENV=staging`, `DEBUG=false`, a strong `JWT_SECRET_KEY`, and a **single** API worker.

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

### Authentication (Phase 6.1)

Users live in the `users` table (separate from `employees`). Passwords are stored as **bcrypt** hashes only. Login returns a **JWT access token** (HS256) signed with `JWT_SECRET_KEY`.

**Required environment** (see `.env.example`):

- `JWT_SECRET_KEY` — at least 32 characters (generate a long random value; never commit it).
- `JWT_ALGORITHM` — default `HS256`.
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` — default `60`.

**Endpoints**

- `POST /auth/login` — JSON body `{"email": "...", "password": "..."}` → `access_token`, `token_type`, `expires_in`.
- `GET /auth/me` — header `Authorization: Bearer <access_token>` → current user profile (role loaded from the database).

Run `alembic upgrade head` after pulling auth migrations so the `users` table exists. Phase **6.2** also requires migration **`20261114_0010`** (`hr_documents.visibility`: `hr_only` | `all_staff`, server default `hr_only`).

**Provisioning administrators** — There is no bootstrap endpoint in Phase 6.1. After migration, create an admin row in MySQL (or a future ops script) with a bcrypt hash of the chosen password—for example using a one-off Python shell that calls `smarthris.core.security.hash_password`. Link `employee_id` when the account represents a specific employee; admin/HR service accounts may leave `employee_id` NULL.

### Authorization and RBAC (Phase 6.2)

Send `Authorization: Bearer <access_token>` on protected routes (except `GET /health` and `POST /auth/login`). Roles are always loaded from the database after JWT validation (`admin`, `hr`, `employee`).

| Area | Admin | HR | Employee |
|------|-------|----|----------|
| Employees (list, create, update, hierarchy) | Yes | Yes | **Own record only** (`GET /employees/{id}` when `users.employee_id` matches) |
| Bulk import (`/imports/*`) | Yes | Yes | Denied |
| LMS analytics (`/analytics/lms/*`) | Yes | Yes | Denied |
| Document upload / list / get / reindex | Yes | Yes | Denied |
| Document search (`POST /documents/search`) | Yes | Yes | **Indexed `all_staff` documents only** |
| RAG (`POST /rag/query`) | Yes | Yes | **Indexed `all_staff` documents only** |

**Document visibility** — Uploads default to `hr_only`. HR/Admin may set `visibility` on upload (`hr_only` or `all_staff`). Existing rows become `hr_only` via migration default. Semantic, keyword, hybrid retrieval, and RAG **filter by visibility before** chunks are hydrated, cited, or sent to Ollama.

**Employee self-scope** — Employees cannot list colleagues, change org fields, or guess another employee’s id (foreign ids return **404**). Employees without a linked `employee_id` receive **403** on self-service routes.

### Streamlit login (Phase 6.3)

Run the UI from the repo root: `python -m streamlit run streamlit_app/app.py` (with the API up and `JWT_SECRET_KEY` configured).

1. Open the app — you are prompted to **Sign in** (`POST /auth/login`).
2. The access token is stored only in **Streamlit session state** (not files, URLs, or browser local storage).
3. Protected API calls send `Authorization: Bearer <token>` via `SmartHrisApiClient`.
4. **Log out** from the sidebar clears the token and profile; **401** responses also clear the session (expired/invalid token). **403** means “no permission” — you stay signed in.
5. **Navigation by role** (hiding a page is UX only; the API still enforces RBAC):

| Page | Admin | HR | Employee |
|------|-------|-----|----------|
| Dashboard | ✓ | ✓ | ✓ (personal summary) |
| Employees | ✓ | ✓ | — (use **My Profile**) |
| My Profile | — | — | ✓ |
| Bulk Import | ✓ | ✓ | — |
| LMS Analytics | ✓ | ✓ | — |
| HR Document Q&A | ✓ | ✓ | ✓ (`all_staff` content via API) |

**Deployment:** apply Alembic migrations **`20261114_0009`** (users) then **`20261114_0010`** (document visibility) before using authenticated Streamlit or API RBAC in a shared environment.

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

### HR document Q&A (`POST /rag/query`)

Answers HR policy questions using retrieved document excerpts and a local **Ollama** model. **Default `retrieval_mode` is `hybrid`** (unlike `/documents/search`, which defaults to `semantic`).

**Configuration** (see `.env.example`):

- `OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`)
- `OLLAMA_MODEL` (default `llama3.2`)
- `OLLAMA_REQUEST_TIMEOUT_SECONDS` (default `120`)

**Install Ollama later (not required for pytest):** install [Ollama](https://ollama.com/) on your machine or use a future Docker Compose profile, then run `ollama pull llama3.2` (or your chosen tag). Pytest mocks the Ollama HTTP API and does **not** need a running server.

**Example request**

```json
{
  "question": "How many days of annual leave do new hires receive?",
  "retrieval_mode": "hybrid",
  "top_k": 5
}
```

**Example response**

```json
{
  "question": "How many days of annual leave do new hires receive?",
  "answer": "New hires receive fifteen days per year [S1].",
  "answer_status": "answered",
  "retrieval_mode": "hybrid",
  "index_ready": true,
  "sources": [
    {
      "rank": 1,
      "chunk_id": 501,
      "document_id": "00000000-0000-4000-8000-000000000501",
      "document_title": "Leave Policy",
      "original_filename": "leave-policy.txt",
      "doc_type": "policy",
      "chunk_index": 0,
      "page_number": 2,
      "section_title": "Annual leave",
      "excerpt": "Employees receive fifteen days of annual leave.",
      "score": 0.031
    }
  ],
  "model": "llama3.2"
}
```

**Refusal (deterministic, no Ollama call)**

- `answer_status`: `index_not_ready` when indexes required for retrieval are missing (`index_ready: false`).
- `answer_status`: `insufficient_context` when retrieval returns no usable chunks (empty fusion/list after hydration).
- Fixed refusal text: *I don't have enough information in the indexed HR documents to answer that.*

**HTTP semantics**

- **HTTP 422** — invalid JSON or field validation (e.g. empty `question` at the schema layer).
- **HTTP 400** — whitespace-only `question` after trimming.
- **HTTP 200** — answered or deterministic refusal as above.
- **HTTP 503** — Ollama unreachable, timeout, missing model, or invalid upstream response (`code`: `ollama_unavailable`, `ollama_timeout`, or `ollama_model_error`). Upstream bodies are not exposed.

**Security** — Protected routes reject missing/invalid tokens (**401**), inactive users (**403**), and wrong roles (**403**). Do not log prompts, tokens, or document excerpts at INFO level. Uploaded policies may contain sensitive HR text; citations return short excerpts only, not full chunk bodies or storage paths.

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

The shell provides sidebar pages **Dashboard**, **Employees**, **Bulk Import**, **LMS Analytics**, and **HR Document Q&A**. Dashboard can check `GET /health`.

### HR Document Q&A (Streamlit)

The **HR Document Q&A** page calls `POST /rag/query` on the FastAPI backend. It does **not** upload or index documents (use the API `POST /documents` flow for that).

**Prerequisites**

1. FastAPI API running (`smarthris-api` or uvicorn) with MySQL and indexed HR documents (`status: indexed`).
2. Search indexes on disk (FAISS / optional BM25 for hybrid) built during indexing.
3. **Ollama** running locally with the model configured on the API (`OLLAMA_MODEL`, default `llama3.2`). Pull the model first (`ollama pull llama3.2`).
4. Optional: `$env:API_BASE_URL` if the API is not on `http://127.0.0.1:8000`.

**UI behavior**

- **Retrieval mode** — `hybrid` (default), `semantic`, or `keyword` (passed through to the API).
- **Top K** — 1–20 chunks used for context (default 5).
- **Document type** — optional filter: All, `policy`, `handbook`, or `other`.
- **Chat history** — stored in the Streamlit session until you clear it or restart the app.
- **Sources** — each answer can expand to show rank, title, filename, type, page, section, score, and a short **excerpt** (not full chunk text).
- **Refusals** — when indexes are missing (`index_not_ready`) or no relevant chunks are found (`insufficient_context`), the API returns a fixed refusal message without calling Ollama.
- **Errors** — API **503** usually means Ollama is down, timed out, or the model is missing; **400** / **422** mean validation or retrieval errors.

**Security** — Document and RAG routes require authentication and enforce document visibility (Phase 6.2+). Sensitive policy text may appear in short excerpts in the UI; use staging access controls and HTTPS for shared environments.

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
| `docs/` | Requirements, architecture, roadmap, [`staging-deployment.md`](docs/staging-deployment.md) |

## Next phase

See `docs/development-roadmap.md` for planned milestones. Frontend bulk-import UI and employee listing pages build on this Streamlit shell.
