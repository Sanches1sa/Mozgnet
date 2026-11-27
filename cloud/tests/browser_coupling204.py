"""Real local HTTP/UI and gradient steps on an isolated tiny model, no board required."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading

_TEMP=tempfile.TemporaryDirectory()
os.environ['NEURO_DATA']=_TEMP.name
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import numpy as np
import app, auth, store, breath, tinyllm, llm_activity
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright

store.ensure_dirs()
store.update_settings({'llm_disk_state':False,'llm_zones_on':True,'sources_on':False})
breath.note_will('тестовая плата',{'Кирпичики':.02,'Обучение':.02,'Буквы':.02})
c=dict(tinyllm.cfg(),llm_d=32,llm_heads=2,llm_layers=1,llm_ctx=32,llm_vocab=128,llm_disk_state=False)
m=tinyllm.GPT(c);m.tok.build(['Код python функция возвращает число.']*3,128);m.init_params(m.tok.size)
x=np.ones((4,32),dtype=np.int64);stop=threading.Event();errors=[]
def train():
    try:
        while not stop.is_set():m.train_step(x,x,.00001)
    except Exception as e:errors.append(str(e))
worker=threading.Thread(target=train,daemon=True)
sid=auth.create_session('127.0.0.1','test')
server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base='http://127.0.0.1:%d'%server.server_port
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':1600})
        context.add_cookies([dict(name=auth.COOKIE,value=sid,url=base)])
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base);page.locator('#btnSettings').click();worker.start()
        page.locator('[data-tab="coupling"]').click()
        page.locator('#cupWork').wait_for(state='visible')
        page.wait_for_function('document.querySelector("#cupWork").textContent.includes("Обучение")')
        assert page.get_by_role('columnheader',name='Контекст LLM',exact=True).count()==1
        assert page.get_by_role('columnheader',name='Работа LLM · 30 с',exact=True).count()==1
        body=context.request.get(base+'/api/coupling').json()
        work=body['unified']['activity'];assert work['duty']>.02,work
        row=next(r for r in body['unified']['zones'] if r['zone']=='Обучение')
        assert row['board']==.02 and row['llm_duty']>.02,row
        page.wait_for_function('Number((document.querySelector("#cupWork").textContent.match(/с: (\\d+)%/) || [0,0])[1]) > 2')
        page.screenshot(path='/workspace/artifacts/cloud-coupling-v3_204.png',full_page=True)
        stop.set();worker.join(5);assert not worker.is_alive()
        assert not context.request.get(base+'/api/coupling').json()['unified']['activity']['busy']
        assert not errors,errors
        browser.close()
    print('Browser/API OK: actual training >2% measured wall-clock occupancy, board raw .02 preserved, separate semantic/work columns, idle after stop; no user model touched')
finally:
    stop.set()
    if worker.ident:worker.join(5)
    server.shutdown();server.server_close();_TEMP.cleanup()
