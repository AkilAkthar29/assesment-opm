# Verification - 14 November 2026

These results supersede the earlier Windows-only child-process benchmarks.
No Docker was used. Django and Next.js ran as separate Windows processes;
Redis, both Celery queues, and beat ran in WSL Ubuntu.

## Environment and data protection

- Existing Windows Python: `backend/.venv-win`, Python 3.13.17.
- WSL Ubuntu: Python 3.14.4, Celery 5.7.0, prefork concurrency 1 per queue.
- Existing Windows PostgreSQL 17.11 on port 5432; demo role `opm_demo`.
- Disposable demo database `opm_demo_20261114`; independent regression database
  `opm_submission_test`. Separate `data-demo` / `data-submission-test` directories.
- Windows `.env.demo` and WSL use the same database credentials and relative
  storage. WSL DATABASE_HOST resolves to the private Windows gateway. A marker
  written by Windows was read successfully from `/mnt/d/.../backend/data-demo`.
- The working `catalog` database contained **466,693 products**. An ordered content
  fingerprint covering ID, SKU, normalized SKU, name, description, and active status
  matched before and after destructive demo tests. The working catalog was preserved.
- Environment files, credentials, raw output, and the 90 MB assessment CSV remain
  ignored. Small interview CSV fixtures are committed under `demo/`.

## Automated and live results

| Check | Result | Evidence / limit |
| --- | --- | --- |
| Windows regression suite | PASS | 15 tests, 5.854 seconds, disposable PostgreSQL |
| Django system checks | PASS | No issues |
| Migration drift | PASS | No changes detected |
| Next.js production build / TypeScript | PASS | Next.js 16.4.6 production build completed |
| Frontend API proxy | PASS | Live requests through `http://127.0.0.1:3002/api/` |
| Redis and separate Celery queues | PASS | `catalog@KADHARNIYAZ` and `webhooks@KADHARNIYAZ`, Redis DB 4; beat publishes both drains |
| Shared Windows/WSL storage | PASS | Windows-written marker read from WSL; actual uploaded files processed by Linux workers |
| Case-only duplicate import | PASS via API | Three rows; last row wins; inactive product stays inactive |
| Invalid CSV and retry | PASS via API | Line 3 SKU error, unchanged product rows, corrected file completes |
| Product CRUD, filters, pagination | PASS via API | Create/edit/read/delete, SKU/name/description/status filters, disjoint cursor pages |
| Import/delete serialization | PASS | Regression tests prove concurrent drains cannot interleave; interrupted job recovered in same drain |
| Webhook controls and failure reporting | PASS via API | Create/edit/test/disable/delete; subscribed product event queued and consumed; disabled hook creates no delivery |
| Delete-all confirmation / completion | PASS via API | Missing/false confirmation rejected, no clear job created; true confirmation completed by Celery; empty catalog verified |
| Browser upload progress and dialogs | NOT VERIFIED | Browser connector host unavailable; native Brave control repeatedly reported minimized window/user input and failed geometry |
| Browser CRUD refresh/cancel/pagination | NOT VERIFIED | API persistence and cancellation guards passed; interactive clicks were not completed |
| Successful public webhook receipt | NOT VERIFIED | User supplied no controlled endpoint and requested this limitation be recorded |
| Public deployment | NOT VERIFIED | No authenticated hosting configured |

The webhook endpoint `https://opm-demo.invalid/edited` intentionally fails DNS.
The Test job completed with `ok:false`, `status_code:null`, `response_ms:47`, and
`[Errno -2] Name or service not known`. A subscribed `product.created` delivery
was also consumed by the webhook worker and reported failure in 16 ms. This
proves queue routing/failure reporting, not successful external delivery.
Regression tests mock a 204 response to check result handling; that is not a live
HTTP success. A controlled receiver is still required to verify actual status
and subscribed-event receipt end to end.

The confirmed clear job `fb340af957224d60b41103cbd70bb604` deleted 466,751 demo
products and reached Complete in 1.096 seconds of observed polling. The working
catalog's fingerprint remained unchanged. Browser cancellation remains unverified.

## Supplied assessment CSV through real Celery

Reused local `.local/assessment.csv`; headers `name,sku,description`.
The live verifier streamed multipart HTTP through the running Next.js proxy to
Django, then only polled jobs/products. It did not call claim/process/drain locally.

| Measurement | Result |
| --- | ---: |
| Source size | 90,569,310 bytes |
| Processed source rows | 500,000 |
| Upload time | 0.916 seconds |
| Total upload-to-observed-completion | 56.200 seconds |
| After-upload completion wait | 55.284 seconds |
| Celery task duration | 55.120 seconds |
| Concurrent filtered API requests | 130 |
| Maximum filtered API latency | 272.08 ms |
| HTTP failures / job errors | 0 / none |

Job: `8187d44b779247a79f8fc9be0168a03d`.
Celery task: `0a2c4239-e4e3-475c-9530-5189f1cae1ce`.
Worker logs show Processing and Finished for that exact job in the catalog
prefork worker, followed by task success in 55.119666 seconds. Observed stages:
Queued, Validating data, Writing products, Complete; result `{"rows":500000}`.
The source contains duplicate SKUs, so source-row count is not unique-product count.

This was a single run, without enabled webhook fan-out. Timings include the
actual Redis/Celery path and Windows/WSL shared-file access. No peak memory was
measured in this run. Filtered HTTP success establishes API availability, not
interactive browser responsiveness. The demo database retains the final imported
assessment catalog for interview browsing.

## Reproduction and submission

Use the five terminal commands and five-minute walkthrough in README.md.
`backend/scripts/verify_live.py` is the live Celery verifier; the older
`backend/scripts/benchmark.py` bypasses Celery and must not be used to claim a
Redis/Celery pass. The regression suite intentionally invokes worker functions
in-process for deterministic locking/rollback tests; the live run above provides
the separate transport evidence.

GitHub publication and repository URL are pending selection of an authenticated
GitHub account/repository. No public application URL has been verified.
