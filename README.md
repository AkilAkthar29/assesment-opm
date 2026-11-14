# Product Ops — native Windows + WSL2

Django/PostgreSQL/Celery/Redis backend in **`backend/`**, Next.js TypeScript frontend in **`frontend/`**. The existing validation, COPY/upsert pipeline, durable job/event outbox, and interface are retained.

## Service layout

| Service | Where it runs | Connection |
| --- | --- | --- |
| Django API | Windows PowerShell terminal 1 | `http://127.0.0.1:8000`; PostgreSQL via WSL localhost forwarding |
| Next.js | Windows PowerShell terminal 2 | **http://localhost:3000**; streams `/api/*` to Django |
| PostgreSQL | WSL2 Ubuntu service | `127.0.0.1:5432`, database `catalog` |
| Redis | WSL2 Ubuntu service | `127.0.0.1:6379/0`, Celery broker |
| Catalog worker | WSL2 Ubuntu terminal 3 | Consumes queue `catalog` |
| Webhook worker | WSL2 Ubuntu terminal 4 | Consumes queue `webhooks` |
| Celery beat | WSL2 Ubuntu terminal 5 | Schedules both queue drains every two seconds |

Use the **same checkout** in Windows and WSL. `DATA_DIR=data` resolves relative to `backend/`, giving Windows `C:\...\backend\data` and Linux `/mnt/c/.../backend/data` (or `/mnt/d/...` on D:). These are the same files. Keep this relative path in the shared `.env`; do not put a Windows absolute path there. Use separate Windows and Linux Python environments.

## One-time Windows prerequisites — PowerShell

Install Windows Python 3.12/3.13 and Node.js 22 LTS. Verify `py --version`, `node --version`, and `npm.cmd --version`.

If WSL2 Ubuntu is missing, use **Administrator PowerShell**, restart if requested, and complete Ubuntu's first-launch username/password setup:

```powershell
wsl --install -d Ubuntu
wsl --set-version Ubuntu 2
wsl --list --verbose
```

Celery workers run in Linux; see the [Celery Windows support FAQ](https://docs.celeryq.dev/en/main/faq.html#does-celery-support-windows).

## Install services — WSL2 Ubuntu

From PowerShell **at the project root**, open Ubuntu in this checkout:

```powershell
wsl -d Ubuntu --cd "$((Get-Location).Path)"
```

The following are **WSL2 Ubuntu / Bash** commands:

```bash
sudo apt update
sudo apt install -y postgresql postgresql-client redis-server python3 python3-venv python3-pip
sudo service postgresql start
sudo service redis-server start
redis-cli ping
sudo -u postgres createuser --pwprompt catalog
sudo -u postgres createdb --owner=catalog catalog
sudo -u postgres createdb --owner=catalog catalog_test
sudo -u postgres createdb --owner=catalog catalog_bench
psql -h 127.0.0.1 -U catalog -d catalog -W -c 'SELECT version();'
python3 -m venv "$HOME/.venvs/product-ops"
"$HOME/.venvs/product-ops/bin/python" -m pip install -r backend/requirements.txt
```

Choose a database password at the prompt. Put it in `backend/.env` below; `change-me-local` in the example is only a placeholder. If the role/databases already exist, reuse them. To change the role password, run `sudo -u postgres psql`, then `\password catalog`. Ubuntu's default localhost host authentication uses passwords. Keep database/Redis listeners on loopback.

Windows normally accesses WSL services through localhost; see [Microsoft's networking documentation](https://learn.microsoft.com/windows/wsl/networking). Avoid a conflicting Windows PostgreSQL/Redis listener on the same port. No public service exposure is required. Do not use the Windows `.venv` in Linux; the Linux environment above lives under your WSL home.

## Install backend and apply migrations — PowerShell

From the project root:

```powershell
Set-Location backend
Copy-Item .env.example .env
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Edit `backend/.env` in VS Code: set the password in `DATABASE_URL` to the one chosen above (URL-encode special password characters), and keep `DATA_DIR=data`. Then:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py check_services
```

The first versioned migration creates/adopts compatible existing products, webhooks, and jobs without deleting data. Future schema changes should be new migrations. `check_services` checks PostgreSQL, migration state, shared storage, and Redis and prints the storage location without credentials. Environment variables override `.env`; `OPM_ENV_FILE` selects another file relative to `backend/`.

## Daily startup — five separate VS Code terminals

After a Windows/WSL restart, start services in **WSL2 Ubuntu**:

```bash
sudo service postgresql start
sudo service redis-server start
```

### Terminal 1 — PowerShell: Django

From the project root:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

API: **http://127.0.0.1:8000/api/products/**.

### Terminal 2 — PowerShell: Next.js

From the project root:

```powershell
Set-Location frontend
Copy-Item .env.example .env.local
npm.cmd ci
npm.cmd run dev
```

Open **http://localhost:3000**. Copy/install only once; do not overwrite customized environment files on later starts. `BACKEND_URL=http://127.0.0.1:8000` is server-only. The API route streams multipart request bodies and responses, forwards auth challenges, and keeps browser calls on one origin. No CORS configuration is needed.

### Terminals 3–5 — WSL2 Ubuntu: workers and scheduler

For **each** terminal, begin in PowerShell at the project root and open Ubuntu:

```powershell
wsl -d Ubuntu --cd "$((Get-Location).Path)"
```

In Ubuntu run `cd backend`, then **one command per terminal**:

```bash
# Terminal 3 — WSL2 Ubuntu, from backend/
"$HOME/.venvs/product-ops/bin/python" -m celery -A config.celery worker -Q catalog --concurrency=1 --loglevel=INFO --hostname='catalog@%h'
```

```bash
# Terminal 4 — WSL2 Ubuntu, from backend/
"$HOME/.venvs/product-ops/bin/python" -m celery -A config.celery worker -Q webhooks --concurrency=1 --loglevel=INFO --hostname='webhooks@%h'
```

```bash
# Terminal 5 — WSL2 Ubuntu, from backend/
"$HOME/.venvs/product-ops/bin/python" -m celery -A config.celery beat --loglevel=INFO --schedule /tmp/product-ops-beat
```

Keep all five terminals running. Run exactly one beat scheduler; without it jobs stay queued. Ctrl+C stops a process. Workers receive tasks through Redis and claim durable jobs from PostgreSQL. In another **WSL** terminal from `backend/`, check worker connectivity with:

```bash
"$HOME/.venvs/product-ops/bin/python" -m celery -A config.celery inspect ping --timeout=5
```

## Features and guarantees

- Assessment file inspected: **`name,sku,description`**, 500,000 rows, 90,569,310 bytes. Source files and generated fixtures stay out of Git.
- Upload/retry: UTF-8 CSV (optional BOM), comma/semicolon/tab, maximum 500,000 nonempty rows / 256 MiB. SKU/name required; description optional. Duplicate headers, malformed quoting/encoding, incorrect column counts, missing/overlong values, null bytes and excess rows fail clearly. Extra columns are ignored.
- Browser upload progress is separate from background parsing, validation, writing, and completion/error progress. Activity IDs persist locally. Retry by reselecting the CSV and clicking **Import / Retry CSV**. Processed source uploads are removed on success/failure.
- Both manual edits and import trim and Unicode-casefold SKUs. PostgreSQL enforces normalized uniqueness and a `LOWER(TRIM(sku))` unique index. Last duplicate row wins, existing active status is preserved, new products default active. CSV status fields are deliberately ignored.
- All validation precedes live changes. A disk spool bounds memory. PostgreSQL COPY loads a temporary staging table; a bulk upsert, event outbox entries, and job result commit atomically. Malformed files leave the catalog unchanged.
- Product create/edit/delete, confirmation, literal case-insensitive SKU/name/description filters, active filter, and cursor pagination (50/page) are implemented. Full substring filters can scan a large catalog; exact total counts are not computed per page.
- Delete-all is separate, requires typing `DELETE ALL` and API `{"confirm":true}`, runs in the background and reports a result. A PostgreSQL **session advisory lock** serializes imports/clears throughout validation, writing, and recovery across workers. Manual CRUD remains available.
- Webhook CRUD, enable/disable/test and a separate delivery queue support `product.created`, `product.updated`, `product.deleted`, `products.cleared`, and `import.completed`. Bulk product events are transactional; subscribe only to `import.completed` for a summary rather than potentially hundreds of thousands of deliveries.
- Tests show HTTP status (null if no response), milliseconds, and error. Private/loopback/link-local/reserved/multicast destinations are blocked. Every resolved address is checked; connections pin the checked address and verify the original TLS hostname. Only HTTP/HTTPS ports 80/443, no redirects, eight-second socket timeout and an additional 15-second Linux deadline including DNS. Failed HTTP attempts complete with `ok:false`; infrastructure failures use `state:failed`. No automatic delivery retry policy is configured.
- PostgreSQL jobs form a durable outbox. A queue-lock holder recovers interrupted running jobs on restart. Completed jobs are not replayed. Receivers can use `delivery_id` to deduplicate a delivery interrupted after they accepted it.

## Tests — PowerShell

From `backend/`, copy `.env` to `.env.test`; change the database name to **`catalog_test`**, `DATA_DIR=data-test`, add `ALLOW_TEST_DATABASE_RESET=1`, and leave `ACCESS_PASSWORD` empty. Then:

```powershell
$env:OPM_ENV_FILE='.env.test'
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Remove-Item Env:OPM_ENV_FILE
```

The suite resets its configured database's products/jobs/webhooks. **Never use the working catalog.** It covers duplicate replacement/status preservation, rollback, CRUD/filters, confirmation, queue exclusion/recovery, auth, import events, webhook lifecycle/results, private destinations and redirects. These tests call job functions directly; Redis/Linux transport is an additional integration check.

Frontend checks, from `frontend/` in **PowerShell**:

```powershell
npm.cmd run build
npm.cmd run check
```

## Benchmarks — PowerShell

Copy `backend/.env` to `.env.bench`; use database **`catalog_bench`**, `DATA_DIR=data-bench`, and blank `ACCESS_PASSWORD`. Stop the regular API and **all workers/beat**. In a PowerShell from `backend/`, start a benchmark API:

```powershell
$env:OPM_ENV_FILE='.env.bench'
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000 --noreload
```

Leave Next.js running. In another **PowerShell** from `backend/`:

```powershell
$env:OPM_ENV_FILE='.env.bench'
.\.venv\Scripts\python.exe scripts/benchmark.py --http-upload --url http://127.0.0.1:3000/api/products/
```

This generates 500,000 rows, streams them through Next.js/Django, executes real import code in a child process, and measures upload time, import time, peak memory, and concurrent proxied product API latency. It deliberately bypasses Celery transport and excludes Celery process overhead; it must not race queue consumers. Repeated runs measure upserts unless the benchmark database is fresh.

For the supplied CSV, in the same benchmark terminal:

```powershell
New-Item -ItemType Directory -Force data-bench | Out-Null
curl.exe -L --fail 'https://drive.google.com/uc?export=download&id=1ZbhNUj88sqZ_lwkLluHZ_b00aUBW5StV' -o data-bench/assessment.csv
Get-Content data-bench/assessment.csv -TotalCount 1
.\.venv\Scripts\python.exe scripts/benchmark.py --csv data-bench/assessment.csv --http-upload --url http://127.0.0.1:3000/api/products/
```

Afterward stop the benchmark API and run `Remove-Item Env:OPM_ENV_FILE` in both benchmark terminals before restarting normal services. Results and unverified steps: [VERIFICATION.md](VERIFICATION.md).

## Repository and public hosting

No remote repository or authenticated compatible hosting account is configured. Focused local commits are available; pushing needs your repository URL. Hosting needs Python and Node web processes, PostgreSQL, Redis, Linux Celery/beat, persistent shared uploads, and HTTPS.

For public use set `PUBLIC_DEPLOYMENT=1`, strong `ACCESS_PASSWORD`, unique `SECRET_KEY`, and `ALLOWED_HOSTS` in the backend environment. Public mode refuses startup without a password and nondefault secret. Basic auth username **catalog** protects reads and writes; the frontend forwards its challenge. Use HTTPS, process supervision, and the same persistent DATA_DIR for API/workers. Do not publicly use Django's development server. Waitress is included for Windows WSGI: `python -m waitress --listen=127.0.0.1:8000 config.wsgi:application` from `backend/`; workers stay on Linux.

Secrets, `.env*` except examples, virtual environments, CSVs, runtime data, downloaded tools and builds are ignored. No C: or D: project location is hard-coded.
