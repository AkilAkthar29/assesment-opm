# Product Ops — OPM assessment

Django API, PostgreSQL catalog/webhooks/job status, Celery with Redis, and a responsive Next.js/TypeScript frontend. nginx streams API requests directly to Django; uploads never pass through Next.js in Compose.

## Run on Windows

Install Docker Desktop with the WSL 2 Linux-container engine and Docker Compose v2. From PowerShell in this folder:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open **http://localhost:3000**. The Products tab contains upload/retry, CRUD, filters, pagination, activity, and delete-all. Webhook settings contains add/edit, enable/disable, test, and delete. Individual deletion requires confirmation; delete-all requires typing `DELETE ALL`, then the API additionally requires JSON `{"confirm":true}`.

```powershell
docker compose logs -f api catalog-worker webhook-worker scheduler
docker compose down
```

`down` keeps PostgreSQL and shared upload volumes. Only the gateway publishes a host port; the default bind address is loopback. There is no need to install Python, Node, PostgreSQL, or Redis on Windows for Compose.

## CSV behavior

The supplied assessment file was downloaded and its actual headers inspected: **`name,sku,description`**. It contains 500,000 rows and is 90,569,310 bytes (see verification report for measurements). The file is deliberately ignored by Git.

- UTF-8 (optional BOM), comma/semicolon/tab delimiter; SKU and name required; description optional. Common descriptive header aliases are accepted. Duplicate headers, bad encoding, malformed quoting, wrong field counts, missing required values, null bytes, and over-limit fields/row counts fail the job.
- Maximum 500,000 nonempty product rows and 256 MiB per file. Name limit 500 characters, SKU 255, description 30,000. Additional columns are ignored. CSV status fields are intentionally ignored.
- Shared-volume temporary upload handling streams to disk. Once the complete upload is stored under an opaque job ID, the API returns 202. Browser upload progress is separate from asynchronous parsing, validation, writing, and completion progress.
- Both manual edits and imports trim SKUs and use Python Unicode `casefold()` for `sku_key`. PostgreSQL enforces a unique normalized key and an additional unique `LOWER(TRIM(sku))` index.
- All rows are validated into a temporary disk spool before changing the catalog. Validated rows enter a transaction-local PostgreSQL staging table using COPY. `DISTINCT ON (sku_key) ... ORDER BY seq DESC` selects the last occurrence. One bulk upsert preserves existing active status and defaults new products to active. An error rolls back all catalog changes and associated event records.
- Import/clear jobs share a PostgreSQL advisory lock held throughout processing, even across validation and separate database sessions. Multiple workers cannot interleave those jobs. Manual CRUD remains available; it is not part of this job serialization contract.
- Failed uploads/imports can be retried by selecting the source file and using Import / Retry CSV. Processed source uploads are deleted on success or failure; retry uploads a new copy with a new job ID. Activity IDs are retained in browser local storage.

## Jobs and webhooks

PostgreSQL job records form a durable outbox. Celery beat schedules queue drains every two seconds; separate Celery workers consume `catalog` and `webhooks`. Redis carries task messages, while PostgreSQL remains the source of job status. Queue locks also recover interrupted running jobs when a worker restarts; a catalog transaction interrupted before commit can be replayed safely. Completed jobs are not replayed.

Events: `product.created`, `product.updated`, `product.deleted`, `products.cleared` (catalog cleared), and `import.completed`. Imports emit product events and one completion event transactionally. A webhook subscribed to product events can therefore produce many delivery jobs on a large import; subscribe to `import.completed` alone for a summary.

Delivery resolves and checks **every** returned address and rejects private, loopback, reserved, link-local, and multicast destinations. Connections are pinned to the validated address while preserving TLS hostname/certificate verification. Literal private addresses and localhost are also rejected at configuration time. Only HTTP/HTTPS ports 80/443 are accepted. Redirects are returned as errors and never followed. Socket timeout is eight seconds; Linux Celery delivery has a 15-second wall-clock deadline covering DNS and the request. Responses are not buffered. Tests show HTTP status (or null if no response), elapsed milliseconds, success, and error in Activity. A transport failure is a completed attempt with `ok:false`; infrastructure/job failures use `state:failed`. Delivery is one attempt, with no automatic retry policy.

## Tests

Run the independent test Compose project; it uses an ephemeral PostgreSQL database and never connects to the development catalog:

```powershell
docker compose -p product-ops-test -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p product-ops-test -f compose.test.yaml down
```

The suite requires `ALLOW_TEST_DATABASE_RESET=1` and resets only the database explicitly supplied to it. Never run it against a catalog you want to retain. Tests cover duplicate replacement/status preservation, malformed-file rollback, CRUD/filters, clear confirmation, concurrent drain exclusion, auth, transactional import events, webhook lifecycle/results, private destinations, and redirects.

Frontend checks, if Node 22 is installed:

```powershell
Push-Location frontend
npm ci
npm run build
npm run check
Pop-Location
```

## Reproduce benchmarks

Use a separate Compose project and run only the database, Redis, and API; the benchmark itself executes the same worker job code in a child process, without competing queue consumers. It generates a 500,000-row fixture under the ignored shared data directory, measures child import time/peak memory, and polls the HTTP product list concurrently. Upload time is excluded; these measurements do not include Celery process overhead. API response latency is a proxy for availability, not proof of interactive browser responsiveness.

```powershell
docker compose -p product-ops-bench up -d --build db redis api
docker compose -p product-ops-bench exec api python scripts/benchmark.py
```

For the supplied file (downloaded into ignored `.local` storage):

```powershell
New-Item -ItemType Directory -Force .local | Out-Null
curl.exe -L --fail 'https://drive.google.com/uc?export=download&id=1ZbhNUj88sqZ_lwkLluHZ_b00aUBW5StV' -o .local/assessment.csv
Get-Content .local/assessment.csv -TotalCount 1
docker compose -p product-ops-bench cp .local/assessment.csv api:/tmp/assessment.csv
docker compose -p product-ops-bench exec api python scripts/benchmark.py --csv /tmp/assessment.csv
docker compose -p product-ops-bench down
```

The benchmark adds/updates catalog products in its separate project. Repeated runs measure upserts into an existing catalog. Measured clean-catalog results from this workspace are in [VERIFICATION.md](VERIFICATION.md).

## API

| Endpoint | Methods / behavior |
| --- | --- |
| `/api/products/` | GET, POST; GET accepts `sku`, `name`, `description`, `status=active/inactive`, `after` cursor; returns at most 50 items and a `next` cursor |
| `/api/products/{id}/` | GET, PATCH (full product fields), DELETE |
| `/api/imports/` | POST multipart `file`; returns job ID |
| `/api/clear/` | POST `{"confirm":true}`; returns job ID |
| `/api/jobs/{id}/` | GET stage, progress, row count, result, error |
| `/api/webhooks/` | GET, POST |
| `/api/webhooks/{id}/` | PATCH (full configuration), DELETE |
| `/api/webhooks/{id}/test/` | POST; returns job ID |

Filters use case-insensitive literal substring matching, with SQL wildcard characters escaped. Cursor pagination avoids deep OFFSET scans. Full substring searches over a large catalog can still require scans; no exact total count is calculated on each page.

## Public deployment and Git delivery

This workspace originally had no `.git` directory or configured remote. Local focused commits were created; no repository URL can be inferred. To publish to your authenticated repository:

```powershell
git remote add origin https://github.com/YOUR-ACCOUNT/YOUR-REPOSITORY.git
git push -u origin master
```

Deploy the full Compose stack to a Docker-capable host with persistent volumes and HTTPS termination. Static-site/Cloudflare Worker hosting alone cannot run Django, PostgreSQL, Redis, and Celery. Configure `.env` on that host:

```dotenv
PUBLIC_DEPLOYMENT=1
ACCESS_PASSWORD=<a-strong-private-password>
SECRET_KEY=<a-unique-random-secret>
ALLOWED_HOSTS=your-public-domain,api
```

Set a suitable `BIND_IP` only behind the host's TLS proxy and firewall. `PUBLIC_DEPLOYMENT=1` refuses startup without a password and nondefault secret. The API uses HTTP Basic authentication, username **catalog**, protecting reads and writes; the browser prompts when it first requests the API. HTTPS is required publicly. The local `.env`, credentials, source CSVs, generated fixtures, and downloaded tools are ignored. Replace the development database credentials for a real hosted environment.

No remote repository or compatible authenticated Docker hosting account was available, so push/public deployment remain unverified. Docker Desktop is also absent in this execution environment; the exact Compose startup needs verification on a Docker-enabled Windows machine. See the verification report for the tests that did run.
