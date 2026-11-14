import csv
import http.client
import ipaddress
import json
import logging
import socket
import ssl
import signal
import time
from urllib.parse import urlsplit

from .core import Invalid, emit, product
from .db import DATA, DATABASE_URL, connect, initialize

MAX_ROWS = 500_000
UPSERT = """
INSERT INTO products (sku, sku_key, name, description, active) VALUES (?, ?, ?, ?, ?)
ON CONFLICT (sku_key) DO UPDATE SET
sku = excluded.sku, name = excluded.name, description = excluded.description
"""
ALIASES = {"sku": ("sku", "productsku", "stockkeepingunit"),
           "name": ("name", "productname", "title"),
           "description": ("description", "productdescription", "desc")}


def progress(job_id, stage, percent, rows=0):
    with connect() as db:
        db.execute("UPDATE jobs SET stage=?, progress=?, rows_processed=? WHERE id=?",
                   (stage, percent, rows, job_id))


def finish(db, job_id, result):
    db.execute("UPDATE jobs SET state='complete', stage='Complete', progress=100, result=? WHERE id=?",
               (json.dumps(result), job_id))


def clean_header(header):
    return "".join(c for c in header.strip().casefold() if c.isalnum())


def import_csv(job_id, payload):
    path = DATA / "uploads" / (job_id + ".csv")
    progress(job_id, "Parsing CSV", 1)
    with path.open("rb") as raw:
        from io import TextIOWrapper
        source = TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        sample = source.read(8192)
        source.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(source, dialect, strict=True)
        headers = [clean_header(header) for header in next(reader, [])]
        if len(headers) != len(set(headers)):
            raise Invalid("CSV contains duplicate column headers.")
        columns = {key: next((headers.index(alias) for alias in aliases if alias in headers), None)
                   for key, aliases in ALIASES.items()}
        if columns["sku"] is None or columns["name"] is None:
            raise Invalid("CSV must include SKU and Name columns.")
        progress(job_id, "Validating data", 3)
        # Stage validated rows on disk so validation never holds a catalog write lock.
        from tempfile import TemporaryFile
        with TemporaryFile(mode="w+t", encoding="utf-8", dir=DATA) as staged:
            count = 0
            for row in reader:
                if not row:
                    continue
                count += 1
                if count > MAX_ROWS:
                    raise Invalid("CSV exceeds the 500,000-row limit.")
                if len(row) != len(headers):
                    raise Invalid(f"CSV line {reader.line_num} has the wrong number of columns.")
                try:
                    values = product({key: row[index] if index is not None else ""
                                      for key, index in columns.items()})
                except Invalid as error:
                    raise Invalid(f"CSV line {reader.line_num}. {error}") from error
                staged.write(json.dumps(values) + "\n")
                if count % 2000 == 0:
                    progress(job_id, "Validating data", min(84, 3 + 81 * raw.tell() // payload["size"]), count)
            if not count:
                raise Invalid("The CSV has no product rows.")
            progress(job_id, "Writing products", 85, count)
            staged.seek(0)
            with connect() as db:
                if DATABASE_URL:
                    db.execute("CREATE TEMP TABLE stage_products (seq BIGINT GENERATED ALWAYS AS IDENTITY, sku TEXT, sku_key TEXT, name TEXT, description TEXT, active INTEGER) ON COMMIT DROP")
                    with db.raw.cursor() as cursor:
                        with cursor.copy("COPY stage_products (sku, sku_key, name, description, active) FROM STDIN") as copy:
                            for line in staged:
                                copy.write_row(json.loads(line))
                    db.execute("""CREATE TEMP TABLE final_products ON COMMIT DROP AS
                        SELECT DISTINCT ON (s.sku_key) s.*, p.id IS NOT NULL AS existed
                        FROM stage_products s LEFT JOIN products p ON p.sku_key=s.sku_key
                        ORDER BY s.sku_key, s.seq DESC""")
                    db.execute("""
                        INSERT INTO products (sku, sku_key, name, description, active)
                        SELECT sku, sku_key, name, description, active
                        FROM final_products
                        ON CONFLICT (sku_key) DO UPDATE SET sku=excluded.sku,
                            name=excluded.name, description=excluded.description
                    """)
                    # Transactional outbox: one delivery per subscribed product event.
                    db.execute("""INSERT INTO jobs(id,kind,state,stage,payload,created_at)
                        SELECT md5(random()::text || p.id::text || h.id::text),
                          'delivery','queued','Queued',
                          jsonb_build_object('url',h.url,'event',
                            CASE WHEN f.existed THEN 'product.updated' ELSE 'product.created' END,
                            'data',jsonb_build_object('id',p.id,'sku',p.sku,'name',p.name,
                              'description',p.description,'active',p.active=1))::text,
                          (extract(epoch FROM clock_timestamp()) * 1000000000)::bigint
                        FROM final_products f JOIN products p ON p.sku_key=f.sku_key
                        CROSS JOIN webhooks h WHERE h.enabled=1 AND jsonb_exists(h.events::jsonb,
                          CASE WHEN f.existed THEN 'product.updated' ELSE 'product.created' END)
                    """)
                else:
                    db.many(UPSERT, (json.loads(line) for line in staged))
                result = {"rows": count}
                emit(db, "import.completed", result)
                finish(db, job_id, result)


def send_webhook(url, event, data, job_id):
    parts = urlsplit(url)
    host = parts.hostname.encode("idna").decode("ascii")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in (80, 443):
        raise Invalid("Webhook delivery supports ports 80 and 443.")
    started = time.monotonic()
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global or ip.is_multicast:
            raise Invalid("Webhook destination must resolve to a public address.")
    # Pin the connection to the checked address while preserving TLS host verification.
    target = addresses[0][4]
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = cls(host, port, timeout=8)
    connection._create_connection = lambda _address, timeout, **_: socket.create_connection(
        (target[0], target[1]), timeout
    )
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        connection.request("POST", path, json.dumps({"event": event, "data": data, "delivery_id": job_id}),
                           {"Content-Type": "application/json", "User-Agent": "ProductOps/1.0"})
        response = connection.getresponse()
        return {"status_code": response.status, "response_ms": round((time.monotonic() - started) * 1000),
                "ok": 200 <= response.status < 300,
                "error": None if 200 <= response.status < 300 else f"HTTP {response.status}; redirects are not followed."}
    finally:
        connection.close()


def claim(kinds):
    placeholders = ",".join("?" for _ in kinds)
    locking = " FOR UPDATE SKIP LOCKED" if DATABASE_URL else ""
    with connect() as db:
        if not DATABASE_URL:
            db.execute("BEGIN IMMEDIATE")
        row = db.execute(f"SELECT id, kind, payload FROM jobs WHERE state='queued' AND kind IN ({placeholders}) ORDER BY created_at LIMIT 1{locking}", kinds).fetchone()
        if row:
            db.execute("UPDATE jobs SET state='running', stage='Starting' WHERE id=?", (row["id"],))
            return dict(row)


def fail(job_id, message):
    with connect() as db:
        db.execute("UPDATE jobs SET state='failed', stage='Failed', error=? WHERE id=?", (message, job_id))


def process(job):
    job_id, kind = job["id"], job["kind"]
    payload = json.loads(job["payload"])
    try:
        if kind == "import":
            import_csv(job_id, payload)
        elif kind == "clear":
            progress(job_id, "Deleting products", 25)
            with connect() as db:
                count = db.execute("DELETE FROM products").rowcount
                emit(db, "products.cleared", {"deleted": count})
                finish(db, job_id, {"deleted": count})
        else:
            progress(job_id, "Sending request", 25)
            started = time.monotonic()
            def timeout_handler(*_):
                raise TimeoutError("Webhook exceeded the 15-second deadline.")
            previous = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(15)
            try:
                result = send_webhook(payload["url"], payload["event"], payload["data"], job_id)
            except (Invalid, OSError, ValueError, http.client.HTTPException) as error:
                result = {"status_code": None, "response_ms": round((time.monotonic()-started)*1000),
                          "ok": False, "error": str(error)[:500]}
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous)
            with connect() as db:
                finish(db, job_id, result)
    except (Invalid, UnicodeError, csv.Error, OSError, http.client.HTTPException) as error:
        fail(job_id, str(error)[:500])
    except Exception:
        logging.exception("Job %s failed", job_id)
        fail(job_id, "The job could not finish. Check the worker log and try again.")
    finally:
        if kind == "import":
            (DATA / "uploads" / (job_id + ".csv")).unlink(missing_ok=True)


def run(kinds):
    initialize()
    with connect() as db:
        placeholders = ",".join("?" for _ in kinds)
        interrupted = db.execute(f"""
            UPDATE jobs SET state='failed', stage='Failed', error='Worker restarted. Please try again.'
            WHERE state='running' AND kind IN ({placeholders}) RETURNING id, kind
        """, kinds).fetchall()
    for job in interrupted:
        if job["kind"] == "import":
            (DATA / "uploads" / (job["id"] + ".csv")).unlink(missing_ok=True)
    while True:
        job = claim(kinds)
        if job:
            process(job)
        else:
            time.sleep(.5)


if __name__ == "__main__":
    import sys
    run(("delivery", "test") if sys.argv[1:] == ["webhooks"] else ("import", "clear"))

