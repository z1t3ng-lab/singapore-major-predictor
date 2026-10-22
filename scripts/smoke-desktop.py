"""Exercise packaged runtimes with a clean environment and a disposable database."""
import json
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import urllib.request

root = Path(__file__).resolve().parents[1]
workspace = root / 'desktop/dist/Major Forecast.app/Contents/Resources/workspace'
python = workspace/'runtime/python/bin/python3.12'
node = workspace/'runtime/node'
env = {'PATH':'/usr/bin:/bin', 'PYTHONHOME':str(workspace/'runtime/python'), 'PYTHONNOUSERSITE':'1', 'PYTHONDONTWRITEBYTECODE':'1'}
subprocess.run([str(python),'-c','import fastapi, uvicorn, numpy, pandas, sklearn, sqlite3; print("Bundled Python imports passed")'],env=env,cwd='/tmp',check=True)
with tempfile.TemporaryDirectory(prefix='major-bundle-check-') as temp:
    env['MAJOR_DB_PATH']=str(Path(temp)/'test.sqlite3')
    process=subprocess.Popen([str(node),str(workspace/'desktop/server.mjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,cwd='/tmp',env=env)
    lines=queue.Queue()
    def read():
        for line in process.stdout: lines.put(line)
        lines.put(None)
    threading.Thread(target=read,daemon=True).start()
    try:
        while True:
            line=lines.get(timeout=60)
            if line is None: raise RuntimeError('Desktop supervisor stopped before readiness')
            print(line.rstrip())
            if line.startswith('MAJOR_ERROR'): raise RuntimeError(line)
            if line.startswith('MAJOR_READY '):
                url=json.loads(line.split(' ',1)[1])['url']; break
        def request(path,body=None):
            with urllib.request.urlopen(urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=60) as response:
                return json.load(response)
        assert request('/api/health')['status']=='ok'
        assert request('/api/teams')['teams']==[]
        assert urllib.request.urlopen(url,timeout=20).status==200
        request('/api/demo',{})
        forecast=request('/api/simulate',{'simulations':8,'seed':73})
        assert abs(forecast['championship_probability_sum']-1)<1e-9
        request('/api/workspace/scenarios',{'name':'Package smoke check','run_id':forecast['run_id']})
        assert len(request('/api/workspace/scenarios'))==1
        assert len(request('/api/workspace/history'))==1
        print('Bundled UI, API, simulation, and persistence passed with no development runtimes on PATH.')
    finally:
        process.stdin.close()
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired: process.kill();process.wait();raise
