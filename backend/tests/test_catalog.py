"""Run against a separate PostgreSQL test database; never the working catalog."""
import base64
import csv
import json
import os
import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.test import Client
from django.core.files.uploadedfile import SimpleUploadedFile
from catalog.core import product, Invalid
from catalog.core import webhook
from catalog import worker
from catalog.db import connect, initialize, DATA, DATABASE_URL


class ValidationTests(unittest.TestCase):
    def test_private_webhook_configuration_rejected(self):
        for url in ['http://localhost/x','http://127.0.0.1','http://[::1]/','http://169.254.169.254/','https://example.org:8443']:
            with self.subTest(url=url), self.assertRaises(Invalid):
                webhook({'url':url,'events':['import.completed']})
    def test_normalization(self):
        self.assertEqual(product({'sku':' AbC ', 'name':'Name'})[1], 'abc')
        self.assertEqual(product({'sku':'Straße', 'name':'Name'})[1], 'strasse')

    def test_private_delivery_blocked(self):
        for ip in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1']:
            with self.subTest(ip=ip), patch('socket.getaddrinfo', return_value=[(2,1,6,'',(ip,80))]):
                with self.assertRaises(Invalid):
                    worker.send_webhook('http://example.org', 'webhook.test', {}, 'test')

    def test_redirect_is_not_followed(self):
        conn=MagicMock(); conn.getresponse.return_value.status=302
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',80))]), patch('http.client.HTTPConnection',return_value=conn):
            result=worker.send_webhook('http://example.org', 'webhook.test', {}, 'test')
        self.assertEqual(result['status_code'],302)
        self.assertFalse(result['ok'])
        self.assertEqual(conn.request.call_count,1)


@unittest.skipUnless(DATABASE_URL, 'Requires disposable PostgreSQL database')
class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Never point this suite at a live catalog.
        if not os.getenv('ALLOW_TEST_DATABASE_RESET') == '1':
            raise RuntimeError('Set ALLOW_TEST_DATABASE_RESET=1 only for a disposable database')
        initialize()

    def setUp(self):
        self.client=Client(HTTP_HOST='localhost')
        with connect() as db:
            db.execute('TRUNCATE products, jobs, webhooks RESTART IDENTITY')

    def request(self,path,data,method='post'):
        return getattr(self.client,method)('/api/'+path,json.dumps(data),content_type='application/json')

    def import_rows(self, content):
        response=self.client.post('/api/imports/',{'file':SimpleUploadedFile('test.csv',content.encode())})
        self.assertEqual(response.status_code,202)
        job=worker.claim(('import',))
        worker.process(job)
        return self.client.get('/api/jobs/'+job['id']+'/').json()

    def test_duplicate_last_wins_and_status(self):
        self.request('products/',{'sku':'ABc','name':'Old','active':False})
        j=self.import_rows('name,sku,description\nFirst,abc,one\nLast,ABC,two\nNew,new,three\n')
        self.assertEqual(j['state'],'complete')
        rows=self.client.get('/api/products/').json()['items']
        self.assertEqual(len(rows),2)
        old=next(p for p in rows if p['sku']=='ABC')
        self.assertEqual(old['name'],'Last'); self.assertFalse(old['active'])
        self.assertTrue(next(p for p in rows if p['sku']=='new')['active'])

    def test_malformed_rolls_back(self):
        self.request('products/',{'sku':'old','name':'Original'})
        j=self.import_rows('name,sku,description\nChanged,old,x\nBroken,missing\n')
        self.assertEqual(j['state'],'failed')
        self.assertEqual(self.client.get('/api/products/').json()['items'][0]['name'],'Original')

    def test_pagination_and_literal_filter(self):
        with connect() as db:
            db.many('INSERT INTO products(sku,sku_key,name,description,active) VALUES(?,?,?,?,?)',
                    (product({'sku':f'page-{i}','name':'Literal %_ name'}) for i in range(53)))
        first=self.client.get('/api/products/').json()
        second=self.client.get('/api/products/?after='+str(first['next'])).json()
        self.assertEqual(len(first['items']),50); self.assertEqual(len(second['items']),3)
        self.assertIsNone(second['next'])
        self.assertFalse({x['id'] for x in first['items']} & {x['id'] for x in second['items']})
        self.assertEqual(len(self.client.get('/api/products/?name=%25_').json()['items']),50)

    def test_crud_filters_confirmation(self):
        r=self.request('products/',{'sku':'SKU-A','name':'Chair','description':'Oak','active':False})
        self.assertEqual(r.status_code,201); pk=r.json()['id']
        self.assertEqual(self.request('products/',{'sku':'sku-a','name':'Duplicate'}).status_code,409)
        for query in ['sku=sku-a','name=CHAIR','description=oak','status=inactive']:
            self.assertEqual(len(self.client.get('/api/products/?'+query).json()['items']),1)
        self.assertEqual(self.client.get('/api/products/?status=active').json()['items'],[])
        self.assertEqual(self.request(f'products/{pk}/',{'sku':'SKU-A','name':'Table'},'patch').status_code,200)
        self.assertEqual(self.request('clear/',{}).status_code,400)
        self.assertEqual(self.request('clear/',{'confirm':'true'}).status_code,400)
        self.assertEqual(self.client.delete(f'/api/products/{pk}/').status_code,200)
        self.request('products/',{'sku':'B','name':'Book'})
        self.assertEqual(self.request('clear/',{'confirm':True}).status_code,202)
        worker.process(worker.claim(('clear',)))
        self.assertEqual(self.client.get('/api/products/').json()['items'],[])

    def test_webhook_lifecycle_and_test_result(self):
        data={'url':'https://example.org/hook','events':['product.created'],'enabled':True}
        r=self.request('webhooks/',data); self.assertEqual(r.status_code,201); pk=r.json()['id']
        self.request('products/',{'sku':'event','name':'Event'})
        with connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) AS n FROM jobs WHERE kind='delivery'").fetchone()['n'],1)
        data['enabled']=False
        self.assertEqual(self.request(f'webhooks/{pk}/',data,'patch').status_code,200)
        self.assertEqual(self.request(f'webhooks/{pk}/test/',{}).status_code,202)
        job=worker.claim(('test',))
        with patch.object(worker,'send_webhook',return_value={'status_code':204,'response_ms':12,'ok':True,'error':None}):
            worker.process(job)
        result=self.client.get('/api/jobs/'+job['id']+'/').json()['result']
        self.assertEqual(result['status_code'],204); self.assertEqual(result['response_ms'],12)
        self.assertEqual(self.client.delete(f'/api/webhooks/{pk}/').status_code,200)

    def test_auth(self):
        with patch.dict(os.environ, {'ACCESS_PASSWORD':'test-secret'}):
            self.assertEqual(self.request('clear/',{'confirm':True}).status_code,401)
            auth=base64.b64encode(b'catalog:test-secret').decode()
            self.assertEqual(self.client.get('/api/products/',HTTP_AUTHORIZATION='Basic '+auth).status_code,200)

    def test_parallel_drains_do_not_interleave(self):
        from config.celery import drain
        self.request('clear/',{'confirm':True})
        entered=threading.Event(); release=threading.Event(); failures=[]
        def hold(job):
            entered.set()
            if not release.wait(10):raise RuntimeError('Timed out waiting for second drain')
        def first():
            try:drain.run('catalog')
            except BaseException as error:failures.append(error)
        with patch.object(worker,'process',side_effect=hold) as process:
            thread=threading.Thread(target=first);thread.start()
            try:
                self.assertTrue(entered.wait(5))
                drain.run('catalog')
                self.assertEqual(process.call_count,1)
                with connect() as db:
                    self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()['state'],'running')
            finally:
                release.set();thread.join(10)
        self.assertFalse(thread.is_alive());self.assertEqual(failures,[])

    def test_interrupted_job_is_recovered_in_same_drain(self):
        from config.celery import drain
        response=self.request('clear/',{'confirm':True})
        job_id=response.json()['id']
        with connect() as db:
            db.execute("UPDATE jobs SET state='running' WHERE id=?",(job_id,))
        drain.run('catalog')
        self.assertEqual(self.client.get('/api/jobs/'+job_id+'/').json()['state'],'complete')

    def test_import_product_events_are_transactional(self):
        self.request('webhooks/',{'url':'https://example.org','events':['product.created','product.updated','import.completed']})
        self.request('products/',{'sku':'old','name':'Before'})
        with connect() as db:db.execute('DELETE FROM jobs')
        self.assertEqual(self.import_rows('name,sku,description\nAfter,OLD,x\nNew,new,x\n')['state'],'complete')
        with connect() as db:
            events=[json.loads(row['payload'])['event'] for row in db.execute("SELECT payload FROM jobs WHERE kind='delivery'")]
        self.assertCountEqual(events,['product.created','product.updated','import.completed'])

    def test_webhook_failure_has_elapsed_time(self):
        self.request('webhooks/',{'url':'https://example.org','events':['import.completed']})
        self.request('webhooks/1/test/',{})
        job=worker.claim(('test',))
        with patch.object(worker,'send_webhook',side_effect=TimeoutError('Deadline exceeded')):
            worker.process(job)
        result=self.client.get('/api/jobs/'+job['id']+'/').json()['result']
        self.assertFalse(result['ok']);self.assertIsNone(result['status_code'])
        self.assertIsInstance(result['response_ms'],int);self.assertIn('Deadline',result['error'])


if __name__=='__main__':
    unittest.main()
