# Staging deployment preparation (Phase 6.7.1)

Controlled checklist for deploying SmartHRIS-AI to **staging**. This document does not replace day-to-day development setup in the root [README](../README.md); it focuses on auth, RBAC, migrations, and RAG constraints after Phases 6.1–6.5.

**Scope:** preparation and runbooks only. Operators execute commands on the staging host; nothing here runs automatically from CI.

---

## 1. Staging environment configuration

1. Copy [`.env.staging.example`](../.env.staging.example) to a **host-local** `.env` or inject the same keys via your secrets manager.
2. Set at minimum:

| Variable | Staging value |
|----------|----------------|
| `APP_ENV` | `staging` |
| `DEBUG` | `false` |
| `JWT_SECRET_KEY` | Strong random string, **≥ 32 characters** (see §2) |
| `DATABASE_URL` | Staging MySQL URL (verify database **name** before any migration) |
| `API_BASE_URL` | Base URL of the FastAPI service (set on the **Streamlit** host/process) |
| `OLLAMA_BASE_URL` | Reachable Ollama HTTP API for RAG |
| `RAG_DOCUMENT_DIR` | Writable private directory for uploaded files |
| `RAG_INDEX_DIR` | Writable private directory for FAISS/BM25 artifacts |

3. **Do not set** `SMART_HRIS_STREAMLIT_PAGE_GUARD_TEST` on staging (page-guard bypass is disabled when `APP_ENV` is `staging` or `production`; see `streamlit_app/auth.py`).
4. Optional: `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `EMBEDDING_MODEL_NAME`, import/RAG tuning — defaults are in `src/smarthris/config.py` and [`.env.example`](../.env.example).

For RBAC rules and API behavior, see README sections *Authentication*, *Authorization and RBAC*, and *Streamlit login*.

---

## 2. Secure secret generation

Never commit real secrets. `.env`, staging copies, and password literals belong only on the host or in a vault.

**JWT signing key (≥ 32 characters)** — from the project venv on the staging or ops workstation:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Store the output in your secrets manager and set `JWT_SECRET_KEY` in the API process environment. Rotate by issuing a new key and updating env (existing tokens invalidate).

**MySQL passwords** — use your organization’s policy (long random password; not the `changeme` placeholder from dev examples).

**Admin login password** — choose at provisioning time; hash with `hash_password()` (§5). Do not store plaintext in files, tickets, or chat.

---

## 3. Migration execution runbook

Target chain (after existing history through `20261109_0008`):

`20261109_0008` → `20261114_0009` (users) → `20261114_0010` (HR document visibility)

Alembic reads `DATABASE_URL` from app settings (`alembic/env.py` → `get_settings()`). **Wrong env file = wrong database.**

### 3.1 Verify database identity

Before any Alembic command, confirm the URL points at **staging** only (not production, not `smarthris_test`):

- Inspect `DATABASE_URL` in the active environment (redact password in logs).
- From MySQL client: `SELECT DATABASE();` and confirm the database name matches staging.

### 3.2 Check current revision

From repo root with venv activated and staging `DATABASE_URL` loaded:

```powershell
alembic current
alembic history --verbose
```

Note the current revision id. Fresh staging after all prior phases should be at or before `20261109_0008` until auth migrations are applied.

### 3.3 Back up the database

Take a full logical backup before upgrade, e.g.:

```powershell
# Adjust host, user, and database name for staging
mysqldump -h CHANGE_ME_HOST -u CHANGE_ME_USER -p --single-transaction --routines CHANGE_ME_DATABASE > staging-pre-migration-YYYYMMDD.sql
```

Store the dump off-host. For HR documents already indexed, also back up `RAG_DOCUMENT_DIR` and `RAG_INDEX_DIR` (§7).

### 3.4 Apply pending migrations

```powershell
alembic upgrade head
```

Applies `20261114_0009` then `20261114_0010` in order when pending.

### 3.5 Verify final revision

```powershell
alembic current
```

Expected head: **`20261114_0010`**.

Confirm schema expectations:

- Table `users` with unique `email`, optional unique `employee_id`, `user_role` enum.
- Column `hr_documents.visibility` with default `hr_only`.

### 3.6 Rollback considerations

| Action | Data impact |
|--------|-------------|
| `alembic downgrade 20261109_0008` | Drops `users` table and enum — **all application accounts lost** |
| `alembic downgrade 20261114_0009` | Drops `visibility` column — **visibility values lost** |

Only downgrade after restoring from backup or with explicit acceptance of data loss. Prefer **restore from mysqldump** over downgrade in staging drills.

---

## 4. First admin provisioning

There is **no** bootstrap HTTP API or checked-in provisioning script. After migrations, create one admin row manually.

### 4.1 Generate password hash (bcrypt)

On a trusted machine with the project venv (do not log the password):

```powershell
python -c "from smarthris.core.security import hash_password; print(hash_password('REPLACE_WITH_STRONG_PASSWORD'))"
```

Copy the printed hash only; discard shell history if it contained the plaintext password.

### 4.2 Insert admin user

Use normalized email (lowercase). **`employee_id` must be NULL** for a standalone admin service account.

```sql
INSERT INTO users (email, password_hash, role, employee_id, is_active)
VALUES (
  'admin@staging.example',
  'PASTE_BCRYPT_HASH_HERE',
  'admin',
  NULL,
  1
);
```

Duplicate email or `employee_id` violates unique constraints (API returns 409 on similar conflicts during future automation).

### 4.3 Verify login

**API:**

```powershell
curl -s -X POST http://CHANGE_ME_API_HOST:8000/auth/login `
  -H "Content-Type: application/json" `
  -d '{\"email\":\"admin@staging.example\",\"password\":\"YOUR_PASSWORD\"}'
```

Expect HTTP 200 with `access_token`, `token_type`, `expires_in`.

**Profile:**

```powershell
curl -s http://CHANGE_ME_API_HOST:8000/auth/me `
  -H "Authorization: Bearer ACCESS_TOKEN_FROM_LOGIN"
```

Expect `role`: `admin`, `is_active`: true.

**Streamlit:** sign in on the staging UI with the same credentials; confirm sidebar shows admin and HR-only navigation entries.

If login returns **503** with `jwt_misconfigured`, `JWT_SECRET_KEY` is missing or shorter than 32 characters (`src/smarthris/core/security.py`).

---

## 5. Deployment commands

### 5.1 FastAPI (exactly one worker)

HR document indexing and FAISS/BM25 updates use **process-local** locks (`src/smarthris/rag/faiss_store.py`, `src/smarthris/services/hr_document_processing_service.py`). **Do not** use `uvicorn --workers 2` (or more).

With `APP_ENV=staging` and `DEBUG=false`, the console entry point does **not** enable reload:

```powershell
cd E:\projects\python\SmartHRIS-AI
.\.venv\Scripts\Activate.ps1
# Load staging .env into the shell (your org’s method)
smarthris-api
```

Equivalent explicit uvicorn (still single process, no reload):

```powershell
uvicorn smarthris.main:app --host 0.0.0.0 --port 8000
```

Do **not** pass `--reload` on staging.

### 5.2 Streamlit frontend

Start after the API is healthy:

```powershell
$env:API_BASE_URL = "http://CHANGE_ME_STAGING_API_HOST:8000"
$env:APP_ENV = "staging"
python -m streamlit run streamlit_app/app.py
```

Use HTTPS termination at a reverse proxy when exposing staging beyond localhost.

### 5.3 Ollama connectivity

- Ensure `OLLAMA_BASE_URL` on the API host reaches Ollama (firewall/DNS).
- Pull the configured model on the Ollama host (default tag in settings: `llama3.2`):

```powershell
ollama pull llama3.2
```

RAG (`POST /rag/query`) returns **503** when Ollama is unreachable; search without generation may still work.

### 5.4 Keyword / hybrid search (optional)

If staging tests include keyword or hybrid retrieval, install the optional dependency on the API host (`rank-bm25`) per README *HR document search*.

---

## 6. Backup and recovery

### 6.1 What to back up

| Asset | Why |
|-------|-----|
| MySQL staging database | Users, employees, import jobs, document metadata and chunks |
| `RAG_DOCUMENT_DIR` | Original uploaded PDF/DOCX/TXT files |
| `RAG_INDEX_DIR` | FAISS and BM25 on-disk indexes per embedding model |

Back up before migrations, before bulk document uploads, and before index-heavy maintenance.

### 6.2 MySQL restore

Restore from mysqldump into a **dedicated** staging database; verify `DATABASE_URL` before starting the API.

### 6.3 File restore

Restore `RAG_DOCUMENT_DIR` and `RAG_INDEX_DIR` together with a DB backup from the same point in time when possible. Paths must match `Settings` on the API host.

### 6.4 Index reconciliation after restart or crash

MySQL may show documents as `indexed` while on-disk FAISS/BM25 is stale after a crash or partial promote (documented in `src/smarthris/rag/faiss_store.py`).

**Recovery options:**

1. **Per document:** HR/Admin `POST /documents/{document_id}/reindex` for affected ids.
2. **Programmatic full rebuild:** From a Python shell on the API host with staging settings and DB session, call `reconcile_vector_index_from_database` in `src/smarthris/services/hr_document_processing_service.py` (rebuilds FAISS and BM25 from `INDEXED` chunk rows in MySQL). Use after major restore or when indexes are missing but MySQL chunk data is intact.

After reconciliation, run search/RAG smoke tests (§7).

---

## 7. Staging smoke-test checklist

Perform manually after deploy. Expected results assume correct data and at least one `all_staff` and one `hr_only` indexed document for visibility tests.

| # | Action | Expected result | Security condition |
|---|--------|-----------------|-------------------|
| 1 | `GET /health` | `status: ok`, `database.status: ok` | API and DB reachability |
| 2 | Admin Streamlit/API login | Token + `role: admin` | JWT + bcrypt auth |
| 3 | Invalid password | 401 / generic failure | No account enumeration |
| 4 | HR user login | Access employees, imports, LMS, document upload | HR RBAC |
| 5 | Employee user login | Limited nav; token issued | Employee RBAC |
| 6 | Employee `GET /employees/{own_id}` | 200 | Self-scope |
| 7 | Employee list employees or `POST /imports` | 403 | Denied admin paths |
| 8 | Employee `GET /analytics/lms/summary` | 403 | LMS denied |
| 9 | HR `GET /employees` | 200 | HR/admin listing |
| 10 | HR search on `hr_only` content | Hits allowed | HR visibility |
| 11 | Employee search same corpus | No `hr_only` chunks in results | Visibility filter |
| 12 | Employee `POST /rag/query` | Citations/excerpts only from `all_staff`; no HR-only text in prompt | RAG + visibility |
| 13 | Expired token or logout | 401 clears Streamlit session; expiry message | Session teardown |
| 14 | Change role in DB; wait profile refresh (default 30s) or reload | Nav matches DB role | `/auth/me` authority |
| 15 | Restart API; repeat search | Consistent results if indexes on disk | Persistence |

Provision HR and employee test users via SQL using the same `hash_password` pattern as admin, with `employee_id` set and `role` matching `validate_user_employee_link` rules (`src/smarthris/core/user_validation.py`).

---

## 8. Related references

- [`.env.example`](../.env.example) — full variable list for development
- [`.env.staging.example`](../.env.staging.example) — staging placeholders
- [README](../README.md) — RBAC matrix, single-worker requirement, Streamlit login behavior
- Phase 6.6 audit (conversation/report) — risk register and deployment assumptions