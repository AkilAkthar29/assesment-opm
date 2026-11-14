# Verification — native Windows/WSL layout

Results below apply to the reorganized `backend/` and `frontend/` sources. Prior infrastructure-specific measurements are not used as evidence for the new startup flow.

## Checks

- Fresh PostgreSQL application/test databases: `backend/manage.py migrate` applied `catalog.0001_initial`; Django system check passed.
- **14 PostgreSQL tests passed**, covering duplicate last-row replacement/status preservation, rollback, CRUD/all filters, cursor pagination/literal wildcard filtering, confirmation, queue exclusion, same-drain recovery, authentication, transactional import events, webhook lifecycle/results/failure timing, private destinations and redirects. Runtime: 7.818 seconds. `makemigrations --check --dry-run` reported no changes; Python compilation passed.
- Next.js production build and TypeScript passed with the streaming `/api/[...path]` route. Frontend HTTP GET returned 200.
- Actual source CSV headers re-inspected: **`name,sku,description`**. File size: **90,569,310 bytes**, 500,000 rows. It was downloaded from the supplied assessment link during the earlier session and reused here.

## Live flows and benchmarks

The Django development server and a production Next.js server ran separately on Windows. Live HTTP checks used `http://127.0.0.1:3000/api/`, exercising the new streaming route and real PostgreSQL state: product create/edit/delete, duplicate rejection, all filters, clear-confirmation rejection, cross-site write rejection, duplicate CSV replacement/inactive preservation, malformed-file rollback, webhook create/edit/toggle/test/delete, and job results. Jobs were executed directly by the verification process because Linux/Redis transport is unavailable. A real synthetic HTTPS webhook test returned **204 in 1,318 ms**.

Both benchmark runs started with an empty disposable catalog and no enabled webhooks. They streamed the entire multipart file through Next.js to Django/shared storage, then ran actual worker import code in a child process while polling the product API through Next.js. Import time excludes upload and child startup. Peak memory is the Windows child process peak working set; it excludes PostgreSQL, Next.js, Redis, and Celery parent/process overhead.

| Input | Rows | Upload seconds | Import seconds | Peak worker MiB | Concurrent API requests | Maximum API latency | HTTP failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Supplied CSV, 90,569,310 bytes | 500,000 | 0.767 | 54.034 | 39.03 | 150 | 321.36 ms | 0 |
| Generated fixture, 29,666,692 bytes | 500,000 | 0.272 | 27.979 | 37.82 | 88 | 180.04 ms | 0 |

These are single-machine/single-run measurements without webhook fan-out, not capacity guarantees. Successful API polling demonstrates availability during imports, not proof of interactive browser responsiveness. Raw results/fixtures remain in ignored `.local/`.

## Outstanding access/dependencies

- `wsl --list --verbose` reports **Windows Subsystem for Linux is not installed**. Ubuntu installation and first-launch user setup are needed for the documented PostgreSQL/Redis services and Linux Celery workers/beat. No Windows features were modified automatically.
- Verification uses portable Python 3.13.9 and PostgreSQL 17.11 under ignored `.local/`, with PostgreSQL on loopback port 55432. This establishes database/application behavior but does not prove Windows-to-WSL forwarding or shared-mount access.
- Actual Redis → Linux Celery transport, Linux delivery deadline signals, and cross-OS file access remain pending WSL setup. README gives each service/terminal command.
- `check_services` successfully verified PostgreSQL/migrations/writable storage and correctly reported Redis unavailable. Windows Python is not on PATH; verification used the portable runtime. Install Windows Python for the documented `py`/virtual-environment setup.
- Browser automation still reports **the browser access host is unavailable**. Interactive upload progress, confirmation dialogs, and visual/mobile responsiveness require manual browser testing or restored browser access. Backend/API behavior and the real large-file HTTP upload route were verified.
- No repository remote or authenticated compatible hosting is configured, so push/deployment URLs are unavailable. Public configuration requires API authentication and a unique secret.
