import base64
import binascii
import hmac
import json
import os
import shutil
import uuid
from functools import wraps

from django.http import JsonResponse

from .core import Invalid, emit, enqueue, product, webhook
from .db import DATA, connect

MAX_UPLOAD = 256 * 1024 * 1024


def endpoint(*methods):
    def decorate(view):
        @wraps(view)
        def handle(request, *args, **kwargs):
            password = os.getenv("ACCESS_PASSWORD")
            if password:
                authorization = request.headers.get("Authorization", "")
                try:
                    scheme, token = authorization.split(" ", 1)
                    expected = ("catalog:" + password).encode()
                    allowed = scheme.lower() == "basic" and hmac.compare_digest(
                        base64.b64decode(token, validate=True), expected)
                except (ValueError, binascii.Error):
                    allowed = False
                if not allowed:
                    response = JsonResponse({"error": "Sign in as catalog."}, status=401)
                    response["WWW-Authenticate"] = 'Basic realm="Product Ops"'
                    return response
            if request.method not in methods:
                response = JsonResponse({"error": "Method not allowed."}, status=405)
                response["Allow"] = ", ".join(methods)
                return response
            if request.method != "GET" and request.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
                return JsonResponse({"error": "Use the catalog interface to make changes."}, status=403)
            try:
                return view(request, *args, **kwargs)
            except (Invalid, ValueError) as error:
                return JsonResponse({"error": str(error)}, status=400)
            except LookupError:
                return JsonResponse({"error": "Record not found."}, status=404)
        return handle
    return decorate


def body(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeError) as error:
        raise Invalid("Expected a JSON object.") from error
    if not isinstance(data, dict):
        raise Invalid("Expected a JSON object.")
    return data


def found(row):
    if row is None:
        raise LookupError
    return dict(row)


def job_response(job_id):
    return JsonResponse({"id": job_id}, status=202)


def product_json(row):
    return {**dict(row), "active": bool(row["active"])}


@endpoint("GET", "POST", "PATCH", "DELETE")
def products(request, pk=None):
    method = request.method
    with connect() as db:
        if method == "GET":
            if pk is not None:
                return JsonResponse(product_json(found(db.execute(
                    "SELECT id, sku, name, description, active FROM products WHERE id=?", (pk,)).fetchone())))
            filters = ["id > ?"]
            params = [max(0, int(request.GET.get("after", 0)))]
            for key in ("sku", "name", "description"):
                value = request.GET.get(key, "").strip()
                if value:
                    column = "sku_key" if key == "sku" else f"LOWER({key})"
                    pattern = value.casefold() if key == "sku" else value.lower()
                    pattern = pattern.replace("!", "!!").replace("%", "!%").replace("_", "!_")
                    filters.append(f"{column} LIKE ? ESCAPE '!'")
                    params.append("%" + pattern + "%")
            status = request.GET.get("status")
            if status in ("active", "inactive"):
                filters.append("active=?")
                params.append(int(status == "active"))
            where = " AND ".join(filters)
            rows = db.execute(f"SELECT id, sku, name, description, active FROM products WHERE {where} ORDER BY id LIMIT 51", params).fetchall()
            return JsonResponse({"items": [product_json(row) for row in rows[:50]],
                                 "next": rows[49]["id"] if len(rows) > 50 else None})
        if method == "POST" and pk is None:
            values = product(body(request))
            row = db.execute("""
                INSERT INTO products(sku, sku_key, name, description, active) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (sku_key) DO NOTHING RETURNING id, sku, name, description, active
            """, values).fetchone()
            event = "product.created"
        elif method == "PATCH" and pk is not None:
            values = product(body(request))
            from psycopg.errors import UniqueViolation
            try:
                row = db.execute("""
                    UPDATE products SET sku=?, sku_key=?, name=?, description=?, active=?
                    WHERE id=? RETURNING id, sku, name, description, active
                """, (*values, pk)).fetchone()
            except UniqueViolation:
                db.raw.rollback()
                return JsonResponse({"error": "This SKU already exists."}, status=409)
            found(row)
            event = "product.updated"
        elif method == "DELETE" and pk is not None:
            row = found(db.execute(
                "DELETE FROM products WHERE id=? RETURNING id, sku, name, description, active", (pk,)).fetchone())
            event = "product.deleted"
        else:
            return JsonResponse({"error": "Method not allowed for this URL."}, status=405)
        if row is None:
            return JsonResponse({"error": "This SKU already exists."}, status=409)
        item = product_json(row)
        emit(db, event, item)
        return JsonResponse(item, status=201 if method == "POST" else 200)


@endpoint("POST")
def upload(request):
    (DATA / "uploads").mkdir(parents=True, exist_ok=True)
    length = int(request.META.get("CONTENT_LENGTH") or 0)
    if length > MAX_UPLOAD + 65536:
        return JsonResponse({"error": "The CSV exceeds 256 MiB."}, status=413)
    source = request.FILES.get("file")
    if source is None or not source.name.lower().endswith(".csv") or not source.size:
        raise Invalid("Choose a nonempty CSV file.")
    if source.size > MAX_UPLOAD:
        return JsonResponse({"error": "The CSV exceeds 256 MiB."}, status=413)
    job_id = uuid.uuid4().hex
    destination = DATA / "uploads" / (job_id + ".csv")
    try:
        with destination.open("wb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
        with connect() as db:
            enqueue(db, "import", {"size": source.size}, job_id)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return job_response(job_id)


@endpoint("POST")
def clear(request):
    if body(request).get("confirm") is not True:
        raise Invalid("Confirm deletion of all products.")
    with connect() as db:
        return job_response(enqueue(db, "clear", {}))


@endpoint("GET")
def job(request, job_id):
    with connect() as db:
        info = found(db.execute("""
            SELECT id, kind, state, stage, progress, rows_processed, result, error FROM jobs WHERE id=?
        """, (job_id,)).fetchone())
    info["result"] = json.loads(info["result"]) if info["result"] else None
    return JsonResponse(info)


def hook_json(row):
    return {**dict(row), "events": json.loads(row["events"]), "enabled": bool(row["enabled"])}


@endpoint("GET", "POST", "PATCH", "DELETE")
def hooks(request, pk=None):
    method = request.method
    with connect() as db:
        if method == "GET" and pk is None:
            return JsonResponse({"items": [hook_json(row) for row in db.execute("SELECT * FROM webhooks ORDER BY id")]})
        if method == "POST" and pk is None:
            row = db.execute("INSERT INTO webhooks (url, events, enabled) VALUES (?, ?, ?) RETURNING *",
                             webhook(body(request))).fetchone()
        elif method == "PATCH" and pk is not None:
            row = db.execute("UPDATE webhooks SET url=?, events=?, enabled=? WHERE id=? RETURNING *",
                             (*webhook(body(request)), pk)).fetchone()
        elif method == "DELETE" and pk is not None:
            found(db.execute("DELETE FROM webhooks WHERE id=? RETURNING id", (pk,)).fetchone())
            return JsonResponse({"deleted": True})
        else:
            return JsonResponse({"error": "Method not allowed for this URL."}, status=405)
    return JsonResponse(hook_json(found(row)), status=201 if method == "POST" else 200)


@endpoint("POST")
def test_hook(request, pk):
    with connect() as db:
        hook = found(db.execute("SELECT url FROM webhooks WHERE id=?", (pk,)).fetchone())
        return job_response(enqueue(db, "test", {"url": hook["url"], "event": "webhook.test", "data": {}}))

