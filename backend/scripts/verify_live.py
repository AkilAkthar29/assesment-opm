"""Exercise an isolated running demo through Next.js; never execute jobs directly.

Run from backend with OPM_ENV_FILE=.env.demo. Requires real workers and beat.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog.db import connect, DATABASE_URL


def request(path, data=None, method='GET', expected=200):
    payload = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, payload, method=method,
                                 headers={'Content-Type': 'application/json'} if payload else {})
    try:
        response = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        value = json.loads(response.read())
        assert response.status == expected, (path, response.status, value)
        return value


def wait(job_id, timeout=300):
    started = time.perf_counter()
    stages = []
    while time.perf_counter() - started < timeout:
        job = request(f'jobs/{job_id}/')
        if not stages or stages[-1] != job['stage']:
            stages.append(job['stage'])
        if job['state'] in ('complete', 'failed'):
            return {**job, 'observed_seconds': round(time.perf_counter() - started, 3), 'stages': stages}
        time.sleep(.25)
    raise TimeoutError(f'Job {job_id} did not finish; check Redis, workers and beat')


def upload(source):
    url = urlsplit(BASE)
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=300)
    boundary = uuid.uuid4().hex
    prefix = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
              'filename="import.csv"\r\nContent-Type: text/csv\r\n\r\n').encode()
    suffix = f'\r\n--{boundary}--\r\n'.encode()
    started = time.perf_counter()
    connection.putrequest('POST', url.path + 'imports/')
    connection.putheader('Content-Type', f'multipart/form-data; boundary={boundary}')
    connection.putheader('Content-Length', str(len(prefix) + source.stat().st_size + len(suffix)))
    connection.endheaders()
    connection.send(prefix)
    with source.open('rb') as file:
        while chunk := file.read(1024 * 1024):
            connection.send(chunk)
    connection.send(suffix)
    response = connection.getresponse()
    result = json.loads(response.read())
    assert response.status == 202, result
    connection.close()
    # Prove that the HTTP server writes into this script's isolated database.
    with connect() as db:
        assert db.execute('SELECT id FROM jobs WHERE id=?', (result['id'],)).fetchone()
    return result['id'], round(time.perf_counter() - started, 3)


def smoke():
    demo = Path(__file__).resolve().parents[2] / 'demo'
    result = {}
    old = request('products/', {'sku': 'DEMO-001', 'name': 'Before', 'active': False}, 'POST', 201)
    job_id, _ = upload(demo / 'demo.csv')
    result['duplicate_import'] = wait(job_id)
    assert result['duplicate_import']['state'] == 'complete'
    item = request(f"products/{old['id']}/")
    assert item['name'] == 'Last version' and item['active'] is False
    before = request('products/')['items']
    job_id, _ = upload(demo / 'invalid.csv')
    result['invalid_import'] = wait(job_id)
    assert result['invalid_import']['state'] == 'failed'
    assert 'line 3' in result['invalid_import']['error']
    assert request('products/')['items'] == before
    job_id, _ = upload(demo / 'corrected.csv')
    result['retry'] = wait(job_id)
    assert result['retry']['state'] == 'complete'
    item.update(name='Edited lamp', description='Oak desk', active=False)
    request(f"products/{item['id']}/", item, 'PATCH')
    for query in ('sku=demo-001', 'name=EDITED', 'description=oak', 'status=inactive'):
        assert any(p['id'] == item['id'] for p in request('products/?' + query)['items'])
    assert request(f"products/{item['id']}/")['name'] == 'Edited lamp'
    request(f"products/{item['id']}/", method='DELETE')
    request(f"products/{item['id']}/", expected=404)
    for i in range(53):
        request('products/', {'sku': f'pagination-{i}', 'name': f'Page {i}'}, 'POST', 201)
    first = request('products/')
    second = request('products/?after=' + str(first['next']))
    assert len(first['items']) == 50 and second['items']
    assert not ({p['id'] for p in first['items']} & {p['id'] for p in second['items']})
    hook = {'url': 'https://opm-demo.invalid/webhook', 'events': ['product.created'], 'enabled': True}
    saved = request('webhooks/', hook, 'POST', 201)
    hook['url'] = 'https://opm-demo.invalid/edited'
    request(f"webhooks/{saved['id']}/", hook, 'PATCH')
    job = request(f"webhooks/{saved['id']}/test/", {}, 'POST', 202)
    result['webhook_test'] = wait(job['id'])
    assert result['webhook_test']['result']['ok'] is False
    assert result['webhook_test']['result']['status_code'] is None
    assert isinstance(result['webhook_test']['result']['response_ms'], int)
    request('products/', {'sku': 'event-proof', 'name': 'Subscribed event'}, 'POST', 201)
    with connect() as db:
        delivery = db.execute("SELECT id FROM jobs WHERE kind='delivery' ORDER BY created_at DESC LIMIT 1").fetchone()
    assert delivery
    result['event_delivery_failure'] = wait(delivery['id'])
    hook['enabled'] = False
    request(f"webhooks/{saved['id']}/", hook, 'PATCH')
    with connect() as db:
        count = db.execute("SELECT count(*) AS n FROM jobs WHERE kind='delivery'").fetchone()['n']
    request('products/', {'sku': 'disabled-proof', 'name': 'No event'}, 'POST', 201)
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM jobs WHERE kind='delivery'").fetchone()['n'] == count
    request(f"webhooks/{saved['id']}/", method='DELETE')
    with connect() as db:
        queued_before = db.execute("SELECT count(*) AS n FROM jobs WHERE kind='clear'").fetchone()['n']
    request('clear/', {}, 'POST', 400)
    request('clear/', {'confirm': False}, 'POST', 400)
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM jobs WHERE kind='clear'").fetchone()['n'] == queued_before
    job = request('clear/', {'confirm': True}, 'POST', 202)
    result['clear'] = wait(job['id'])
    assert result['clear']['state'] == 'complete'
    assert request('products/')['items'] == []
    result['smoke'] = 'passed'
    return result


def assessment(source):
    started = time.perf_counter()
    job_id, upload_seconds = upload(source)
    print(f'Assessment accepted: job={job_id} upload_seconds={upload_seconds}', flush=True)
    latencies, errors, stages = [], [], []
    while time.perf_counter() - started < 900:
        job = request(f'jobs/{job_id}/')
        if not stages or stages[-1] != job['stage']:
            stages.append(job['stage'])
            print(f"{job['stage']}: {job['rows_processed']} rows", flush=True)
        sample = time.perf_counter()
        try:
            request('products/?name=james')
            latencies.append((time.perf_counter() - sample) * 1000)
        except Exception as error:
            errors.append(str(error))
        if job['state'] in ('complete', 'failed'):
            return {'job': job, 'bytes': source.stat().st_size, 'upload_seconds': upload_seconds,
                    'total_seconds': round(time.perf_counter() - started, 3),
                    'after_upload_seconds': round(time.perf_counter() - started - upload_seconds, 3),
                    'stages': stages, 'api_requests': len(latencies), 'api_errors': errors,
                    'max_api_ms': round(max(latencies), 2) if latencies else None}
        time.sleep(.25)
    raise TimeoutError('Assessment did not finish in 15 minutes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:3002/api/')
    parser.add_argument('--assessment', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    BASE = args.base_url.rstrip('/') + '/'
    # No destructive operations or fixture writes may target the working catalog.
    assert urlsplit(DATABASE_URL).path.rsplit('/', 1)[-1].startswith('opm_demo_'), 'Use a disposable opm_demo_* database'
    assert os.environ.get('OPM_ENV_FILE') == '.env.demo', 'Select .env.demo explicitly'
    # Before any API writes, prove this URL serves the selected disposable DB.
    # A wrong --base-url must not be able to clear the user's working catalog.
    probe = 'verification-probe-' + uuid.uuid4().hex
    with connect() as db:
        row = db.execute('INSERT INTO products(sku,sku_key,name,description,active) VALUES(?,?,?,?,?) RETURNING id',
                         (probe, probe, probe, '', 1)).fetchone()
    try:
        assert any(p['sku'] == probe and p['id'] == row['id'] for p in
                   request('products/?sku=' + probe)['items']), 'API URL serves a different database; refusing writes'
    finally:
        with connect() as db:
            db.execute('DELETE FROM products WHERE sku_key=?', (probe,))
    result = assessment(args.assessment) if args.assessment else smoke()
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
