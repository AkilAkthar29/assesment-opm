import json
import time
import uuid
import ipaddress
from urllib.parse import urlsplit

EVENTS = ("product.created", "product.updated", "product.deleted",
          "products.cleared", "import.completed")


class Invalid(ValueError):
    pass


def field(value, label, maximum, required=False):
    if not isinstance(value, str):
        raise Invalid(f"{label} must be text.")
    value = value.strip()
    if (required and not value) or len(value) > maximum or "\0" in value:
        raise Invalid(f"{label} must contain {'1' if required else '0'}–{maximum:,} characters without null bytes.")
    return value


def boolean(value, label):
    if type(value) is not bool:
        raise Invalid(f"{label} must be true or false.")
    return int(value)


def product(data):
    sku = field(data.get("sku"), "SKU", 255, True)
    return (sku, sku.casefold(), field(data.get("name"), "Name", 500, True),
            field(data.get("description", ""), "Description", 30000),
            boolean(data.get("active", True), "Active"))


def webhook(data):
    url = field(data.get("url"), "URL", 2048, True)
    try:
        parts = urlsplit(url)
        valid = (parts.scheme in ("http", "https") and parts.hostname and not parts.username
                 and not parts.password and not parts.fragment and not any(c.isspace() for c in url)
                 and parts.port != 0)
    except ValueError as error:
        raise Invalid("Enter a valid webhook URL.") from error
    if not valid:
        raise Invalid("Use an HTTP or HTTPS URL without credentials or a fragment.")
    if (parts.port or (443 if parts.scheme == 'https' else 80)) not in (80, 443):
        raise Invalid("Webhook delivery supports ports 80 and 443.")
    host = parts.hostname.rstrip('.').lower()
    if host == 'localhost' or host.endswith('.localhost'):
        raise Invalid("Webhook destination must be public.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (not address.is_global or address.is_multicast):
        raise Invalid("Webhook destination must be public.")
    events = data.get("events")
    if not isinstance(events, list) or not events or any(event not in EVENTS for event in events):
        raise Invalid("Select at least one supported event.")
    return url, json.dumps(list(dict.fromkeys(events))), boolean(data.get("enabled", True), "Enabled")


def enqueue(db, kind, payload, job_id=None):
    job_id = job_id or uuid.uuid4().hex
    db.execute(
        "INSERT INTO jobs (id, kind, state, stage, payload, created_at) VALUES (?, ?, 'queued', 'Queued', ?, ?)",
        (job_id, kind, json.dumps(payload), time.time_ns()),
    )
    return job_id


def emit(db, event, data):
    for hook in db.execute("SELECT url, events FROM webhooks WHERE enabled = 1"):
        if event in json.loads(hook["events"]):
            enqueue(db, "delivery", {"url": hook["url"], "event": event, "data": data})

