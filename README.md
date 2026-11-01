# SmartHRIS-AI

Learning project: HRIS with bulk uploads, LMS, and hybrid RAG (later phases).  
**Phase 0** — FastAPI, MySQL, Alembic, `/health`. **Phase 1** — `departments` / `employees` tables and employee CRUD API.

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

## 7. Run tests

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
| `src/smarthris/api/routers/` | HTTP routes (`health`, `employees`) |
| `src/smarthris/core/` | Domain errors and exception handlers |
| `alembic/` | Migration scripts; `env.py` reads `DATABASE_URL` |
| `tests/` | pytest (config, health, employee API) |
| `docs/` | Requirements, architecture, roadmap |

## Next phase

See `docs/development-roadmap.md` — **Phase 2**: Bulk CSV import.
