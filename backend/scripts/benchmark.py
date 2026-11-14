"""Disposable benchmark: runs worker in a child and polls real HTTP during import."""
import argparse
import csv
import json
import multiprocessing
import os
import time
import urllib.request
import http.client
import uuid
from urllib.parse import urlsplit
from pathlib import Path


def run_job(job, pipe):
    from catalog.worker import process
    started=time.perf_counter()
    process(job)
    try:
        import resource
        peak_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    except ImportError:
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in
                ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
                 'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
        counters=Counters(); counters.cb=ctypes.sizeof(counters)
        ctypes.windll.kernel32.GetCurrentProcess.restype=wintypes.HANDLE
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
        if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.byref(counters),counters.cb):
            raise ctypes.WinError()
        peak_mib=counters.PeakWorkingSetSize/1024/1024
    pipe.send({'seconds':round(time.perf_counter()-started,3),
               'peak_worker_mib':round(peak_mib,2)})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--csv'); parser.add_argument('--url',default='http://127.0.0.1:8000/api/products/')
    parser.add_argument('--http-upload', action='store_true', help='Upload through HTTP first, including the Next.js streaming route when --url uses port 3000')
    args=parser.parse_args()
    from catalog.db import DATA, initialize, connect
    from catalog.core import enqueue
    from catalog.worker import claim
    initialize()
    if args.csv:
        source=Path(args.csv)
    else:
        source=DATA/'fixture-500000.csv'
        with source.open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(['name','sku','description'])
            for i in range(500000):w.writerow([f'Product {i}',f'fixture-{i}',f'Generated description {i}'])
    import shutil
    upload_seconds=None
    if args.http_upload:
        started=time.perf_counter()
        url=urlsplit(args.url)
        connection=(http.client.HTTPSConnection if url.scheme=='https' else http.client.HTTPConnection)(url.hostname,url.port,timeout=300)
        boundary=uuid.uuid4().hex
        prefix=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="import.csv"\r\nContent-Type: text/csv\r\n\r\n').encode()
        suffix=f'\r\n--{boundary}--\r\n'.encode()
        connection.putrequest('POST','/api/imports/')
        connection.putheader('Content-Type',f'multipart/form-data; boundary={boundary}')
        connection.putheader('Content-Length',str(len(prefix)+source.stat().st_size+len(suffix)))
        if os.getenv('ACCESS_PASSWORD'):
            import base64
            connection.putheader('Authorization','Basic '+base64.b64encode(('catalog:'+os.environ['ACCESS_PASSWORD']).encode()).decode())
        connection.endheaders(); connection.send(prefix)
        with source.open('rb') as file:
            while chunk:=file.read(1024*1024):connection.send(chunk)
        connection.send(suffix)
        response=connection.getresponse(); value=json.loads(response.read());connection.close()
        if response.status!=202:raise RuntimeError(value)
        job_id=value['id']
        upload_seconds=round(time.perf_counter()-started,3)
    else:
        with connect() as db:job_id=enqueue(db,'import',{'size':source.stat().st_size})
        shutil.copyfile(source,DATA/'uploads'/(job_id+'.csv'))
    job=claim(('import',))
    if not job or job['id']!=job_id:raise RuntimeError('Use an idle disposable database without catalog worker/scheduler')
    parent,child=multiprocessing.Pipe()
    process=multiprocessing.Process(target=run_job,args=(job,child));process.start()
    samples=[]; errors=[]
    while process.is_alive():
        start=time.perf_counter()
        try:
            request=urllib.request.Request(args.url)
            if os.getenv('ACCESS_PASSWORD'):
                import base64
                request.add_header('Authorization','Basic '+base64.b64encode(('catalog:'+os.environ['ACCESS_PASSWORD']).encode()).decode())
            with urllib.request.urlopen(request,timeout=10) as response:response.read()
            samples.append(time.perf_counter()-start)
        except Exception as error:errors.append(str(error))
        time.sleep(.25)
    process.join()
    if not parent.poll(2):raise RuntimeError('Benchmark worker exited without metrics')
    result=parent.recv()
    with connect() as db:result['job']=dict(db.execute('SELECT state,rows_processed,error FROM jobs WHERE id=?',(job_id,)).fetchone())
    result.update(http_samples=len(samples),max_api_ms=round(max(samples,default=0)*1000,2),http_errors=errors)
    result.update(upload_seconds=upload_seconds,input_bytes=source.stat().st_size)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
