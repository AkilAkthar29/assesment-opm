# OPM assessment

Django in `backend/`, Next.js in `frontend/`, and PostgreSQL on Windows.
Redis, two Celery workers, and Celery beat run in WSL Ubuntu. No Docker.

## Verified local demo

Open **http://127.0.0.1:3002**. Django is at `127.0.0.1:8001`.
The isolated demo uses `backend/.env.demo`, database `opm_demo_20261114`,
Redis database 4, and `backend/data-demo`. The working catalog remains in
`backend/.env` and is not used for destructive verification.

### Terminal 1 - PowerShell: Django

```powershell
cd D:\akil\opm\backend
$env:OPM_ENV_FILE='.env.demo'
.\.venv-win\Scripts\python.exe manage.py migrate
.\.venv-win\Scripts\python.exe manage.py check_services
.\.venv-win\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

### Terminal 2 - PowerShell: Next.js

```powershell
cd D:\akil\opm\frontend
$env:BACKEND_URL='http://127.0.0.1:8001'
npm.cmd ci
npm.cmd run build
npm.cmd run start -- --port 3002
```

Install/build once after checkout or source changes. On subsequent starts use
only `npm.cmd run start -- --port 3002` with the same BACKEND_URL.
For development, use `npm.cmd run dev -- --port 3002` instead of `start`.
Keep Django and Next.js in separate PowerShell terminals.

### Terminals 3-5 - WSL Ubuntu

Open Ubuntu using `wsl -d Ubuntu` in three separate terminals.

```bash
# Terminal 3: Redis and catalog worker
cd /mnt/d/akil/opm
sudo service redis-server start
redis-cli ping
bash scripts/celery-wsl.sh catalog .env.demo
```

```bash
# Terminal 4: webhook worker
cd /mnt/d/akil/opm
bash scripts/celery-wsl.sh webhooks .env.demo
```

```bash
# Terminal 5: exactly one scheduler
cd /mnt/d/akil/opm
bash scripts/celery-wsl.sh beat .env.demo
```

Keep all five terminals running. Ctrl+C stops a process. Beat publishes a drain
for each queue every two seconds through Redis. Django writes durable jobs into
PostgreSQL; Celery consumes those jobs. There is no standalone management-command
worker. Without beat or the queue worker, jobs remain queued.

The helper selects `~/.venvs/opm/bin/python`, loads the same environment file as
Django, and uses the WSL NAT gateway as DATABASE_HOST. Relative `DATA_DIR=data-demo`
resolves to the same files on Windows and `/mnt/d/...` in WSL. Never put a Windows
absolute DATA_DIR in the shared env file. DATABASE_HOST replaces only the host in
DATABASE_URL, preserving the database, credentials, and connection options.
For mirrored networking, explicitly set `export DATABASE_HOST=127.0.0.1`.

## One-time setup on another checkout

Install Windows Python 3.12/3.13, Node.js 22, PostgreSQL, and WSL Ubuntu.
The existing Windows environment `backend/.venv-win` was verified; environments
are machine-specific and are not committed. Create it if missing:

```powershell
cd D:\akil\opm\backend
py -3 -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env.demo)) { Copy-Item demo.env.example .env.demo }
```

Using SQL Shell/psql as a PostgreSQL administrator:

```sql
CREATE ROLE opm_demo LOGIN;
\password opm_demo
CREATE DATABASE opm_demo_20261114 OWNER opm_demo;
CREATE DATABASE opm_submission_test OWNER opm_demo;
```

Reuse existing roles/databases. Set the chosen password in `.env.demo`;
URL-encode special characters. Keep credentials out of Git. `.env.demo` already
exists in this local checkout and must not be overwritten.

In WSL Ubuntu, install the separate Linux environment:

```bash
sudo apt update
sudo apt install -y redis-server python3-venv python3-pip
python3 -m venv "$HOME/.venvs/opm"
"$HOME/.venvs/opm/bin/python" -m pip install -r /mnt/d/akil/opm/backend/requirements.txt
```

### Existing Windows PostgreSQL and WSL NAT

PostgreSQL must accept connections on both loopback and the private WSL gateway.
The verified gateway was `172.26.48.1`, with WSL client `172.26.51.27`.
These addresses can change after restarting WSL. Discover them with:

```powershell
wsl -d Ubuntu -- ip route show default
wsl -d Ubuntu -- hostname -I
```

The existing server uses `.local/pgdata` and port 5432. Check before starting:

```powershell
cd D:\akil\opm
.\.local\postgres\pgsql\bin\pg_isready.exe -h 127.0.0.1 -p 5432
```

If it reports accepting connections, do not start a second copy. If stopped,
start it using the current gateway (the following matches this verified session):

```powershell
.\.local\postgres\pgsql\bin\pg_ctl.exe start -D D:/akil/opm/.local/pgdata -l D:/akil/opm/.local/postgres-start.log -o "-p 5432 -h 127.0.0.1,172.26.48.1" -w
```

For a different PostgreSQL installation use its Windows service instead. Configure
`listen_addresses` for loopback plus the private WSL gateway and restart that
server if needed. The verified `pg_hba.conf` adds only this demo-role rule:

```text
host opm_demo_20261114,opm_submission_test opm_demo 172.26.51.27/32 scram-sha-256
```

Update the exact client address if WSL changes, then reload PostgreSQL. Keep
Redis on WSL loopback; PostgreSQL need not listen on public interfaces. If a
Windows firewall rule is necessary, restrict it to the WSL client and port 5432.
Run `manage.py check_services` in Windows and through the WSL helper to diagnose
connections. A successful service check does not by itself prove workers run.

## Five-minute interviewer walkthrough

1. **0:00-0:45:** Open port 3002 and show the separate API, frontend, Celery queue,
   and beat terminals. Explain the demo database is disposable.
2. **0:45-1:45:** Upload `demo/demo.csv`. Three source rows become two SKUs;
   `demo-001` is named Last version. Show upload progress and Activity completion.
   Edit it to inactive, import the same file again, and show it stays inactive.
3. **1:45-2:30:** Upload `demo/invalid.csv`. Show the line-number error and unchanged
   products. Retry with `demo/corrected.csv` and show completion.
4. **2:30-3:30:** Add/edit a product, filter by SKU/name/description/status, refresh,
   navigate Next/Previous on the assessment catalog, and cancel a product deletion.
5. **3:30-4:15:** Add/edit/disable/test/delete a webhook. For this run no controlled
   public receiver was supplied: `https://opm-demo.invalid/webhook` intentionally
   demonstrates DNS failure, `status_code: null`, and measured `response_ms`.
   Successful receipt must be demonstrated with an endpoint you control.
6. **4:15-5:00:** Show the verified 500,000-row results in VERIFICATION.md. In the
   disposable demo only, cancel Delete all once, then type `DELETE ALL` to confirm
   and show Celery completion. This removes the demo assessment rows; reimport
   `.local/assessment.csv` to restore them. Never do this in the working catalog.

## Verification and repeatable checks

See [VERIFICATION.md](VERIFICATION.md) for fresh results and explicit gaps.
Create `.env.test` from `.env.demo`, select `opm_submission_test`, set
`DATA_DIR=data-submission-test`, and add `ALLOW_TEST_DATABASE_RESET=1`.
Never run this destructive suite against the working catalog.

```powershell
cd D:\akil\opm\backend
$env:OPM_ENV_FILE='.env.test'
.\.venv-win\Scripts\python.exe manage.py migrate
.\.venv-win\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-win\Scripts\python.exe manage.py makemigrations --check --dry-run
```

With the five demo services running, repeat live API checks or the assessment:

```powershell
cd D:\akil\opm\backend
$env:OPM_ENV_FILE='.env.demo'
.\.venv-win\Scripts\python.exe scripts/verify_live.py --output ../.local/live-smoke.json
.\.venv-win\Scripts\python.exe scripts/verify_live.py --assessment ../.local/assessment.csv --output ../.local/live-assessment.json
```

The smoke script creates/edits/deletes demo products and clears the demo catalog.
It verifies the API serves the selected disposable database before API writes.
The assessment script uploads through Next.js and waits for real Celery; neither
script executes job functions directly. The old `scripts/benchmark.py` uses a
local child process and is not evidence of Redis/Celery execution.

## Behavior and implementation

- UTF-8 CSV up to 500,000 nonempty rows / 256 MiB; headers `name,sku,description`.
  Comma/semicolon/tab, optional BOM, useful line errors, and malformed-file rollback.
- Trimmed Unicode-casefold SKU uniqueness, last duplicate wins, active status retained.
- Disk staging and PostgreSQL COPY/upsert keep import memory bounded. Validation
  completes before catalog changes; products, webhook outbox, and result commit together.
- Session advisory locks serialize imports/delete-all and recovery across workers.
  Catalog and webhook queues are independent. Interrupted running jobs recover on
  the next drain; completed jobs are not replayed.
- CRUD, literal case-insensitive filters, active filter, and 50-row cursor pagination.
  Full substring filters may scan the catalog; per-page total counts are not computed.
- Public HTTP/HTTPS webhooks on ports 80/443 only; DNS/IP validation and pinned
  connections block private destinations. No redirects. Eight-second socket timeout
  plus a 15-second Linux deadline. Delivery failures report status/timing/error.
  No automatic delivery retry policy; receivers can deduplicate by delivery_id.
- Next.js streams multipart requests/responses to Django on the same browser origin.

## Submission and hosting

Secrets, environments, runtime data, and the large assessment CSV remain ignored;
only the small `demo/*.csv` fixtures are committed. Public deployment requires
Python/Node web processes, Linux Celery/beat, PostgreSQL, Redis, shared persistent
uploads, HTTPS, and process supervision. Set PUBLIC_DEPLOYMENT=1, a unique
SECRET_KEY, strong ACCESS_PASSWORD, and ALLOWED_HOSTS. HTTP Basic username is
`catalog`; serve it only over HTTPS. Do not expose Django's development server.
A deployment URL is not available until authenticated hosting is configured.
