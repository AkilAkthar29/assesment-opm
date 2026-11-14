# Verification — 14 November 2026

## Environment and checks

Windows, portable Python 3.13.9, PostgreSQL 17.11 binaries on loopback port 55432, Django 5.2.19, Celery 5.7.0, psycopg 3.3.9; Node 22 and Next.js 16.4.6. Downloaded tools, databases, source CSVs, generated fixtures, and raw measurement output live under ignored `.local/`.

- **12 tests passed** against a separate disposable PostgreSQL database, including real COPY and bulk upsert. Coverage: case-insensitive replacement/last-row-wins, inactive preservation/new-active default, malformed CSV rollback, CRUD and all four filters, server-side delete-all confirmation, simultaneous drain exclusion, auth, import event outbox, webhook CRUD/test results, transport failure timing, private destinations, and redirect refusal.
- Django system check: no issues. Python compilation: passed.
- Next.js production compilation and TypeScript: passed. Local frontend HTTP GET: 200.
- Both Compose configurations passed the official standalone Docker Compose v2.40.3 `config --quiet` validation.
- A real HTTPS webhook POST of synthetic empty test data to `https://httpbin.org/status/204` returned **204 in 816 ms**. Public address resolution, pinned connection, and TLS hostname verification were exercised. Mocked tests separately verified rejection and failure paths.

## Import measurements

Both final runs started with an empty catalog and no enabled webhooks. Each executed the actual `catalog.worker.process` import code in an isolated child Python process, using the same PostgreSQL database as the concurrently polled Django HTTP API. Time excludes upload, fixture creation, and child startup. Peak memory is Windows peak working set for that process; it excludes PostgreSQL, Redis, and Celery parent/process overhead.

| File | Input rows | Import seconds | Peak worker MiB | HTTP samples during import | Maximum list API latency | HTTP errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Supplied assessment CSV | 500,000 | 37.828 | 39.26 | 109 | 235.44 ms | 0 |
| Generated fixture | 500,000 | 34.687 | 37.91 | 103 | 330.06 ms | 0 |

The supplied file's exact headers are `name,sku,description`; size is 90,569,310 bytes. It produced **466,693 unique products**, confirming that duplicate input SKUs were consolidated. The generated fixture uses unique `fixture-{i}` SKUs, names, and descriptions. Measurements are single runs on this machine, without subscribed product-event delivery load; they are not capacity guarantees.

The API remained available during both imports. The Next.js page served successfully, but browser interaction and visual/mobile responsiveness could not be verified: the browser access host was unavailable, and the native fallback stopped because the current browser URL could not be established safely. API availability alone does not prove interactive UI responsiveness.

## Outstanding verification and access

- **Full `docker compose up --build`, Celery/Redis transport, Linux 15-second signal deadline, and nginx upload streaming:** configuration validated, but no Docker engine/Desktop is installed in this environment. Run the documented commands on Docker Desktop with WSL 2/Linux containers. PostgreSQL/API/job behavior was tested directly; that is not a substitute for the full container startup check.
- **Browser upload progress, retry interaction, confirmation dialogs, and responsive rendering:** implemented and build-checked; require working browser automation or manual testing at `http://localhost:3000`. Backend upload, validation, job, CRUD, and clear APIs were tested.
- **Push:** the supplied folder had no Git repository or remote. Local commits were created. An authenticated repository URL/remote is required for push; none is configured.
- **Public deployment:** requires authenticated access to a Docker-capable host with persistent PostgreSQL/shared upload storage, Redis, Celery processes, a public domain, and TLS. Available static/Worker hosting cannot run this stack. No public endpoint was created. The public-deployment configuration guard requires API authentication and a unique application secret.

## Manual end-to-end checklist on Docker Desktop

1. Run `docker compose up --build`; open `http://localhost:3000`.
2. Import the supplied CSV. Observe browser byte progress, then parsing/validation/writing/completion in Activity. Search products while it runs.
3. Edit a product inactive and import its SKU in different casing with two rows. The last row should win and the product should stay inactive.
4. Upload a file with a valid first row and malformed later row. Confirm failure and unchanged catalog; select a corrected file and retry.
5. Exercise all filters, Next/Previous, create/edit/delete, and cancel an individual deletion.
6. Cancel delete-all, then type `DELETE ALL`; observe queued/progress/result. Queue it behind an import to verify serialized behavior.
7. Add a public webhook, select events, edit and toggle it, and run Test. Inspect status/time/error; test a redirect endpoint and private destination rejection. Delete the webhook.
