"""Settings probe through a real local HTTP server and Chromium, fake keys."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch

_TEMP=tempfile.TemporaryDirectory()
os.environ['NEURO_DATA']=_TEMP.name
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import app
import auth
import store
import yandex_search
import breath
import llm_zones
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright

store.ensure_dirs()
breath.note_will('test-board',{'Кирпичики':.8,'Буквы':.6,'Математика':.9})
llm_zones.route('python функция код')
store.update_settings({'yandex_api_key':'saved-fake-key','yandex_folder':'saved-folder','sources_on':False})
sid=auth.create_session('127.0.0.1','test')
server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
base='http://127.0.0.1:%d'%server.server_port
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1280,'height':900})
        context.add_cookies([dict(name=auth.COOKIE,value=sid,url=base)])
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base);page.locator('#btnSettings').click()
        page.locator('[data-tab="photoocr"]').click()
        page.locator('#ocrState').wait_for(state='attached')
        page.wait_for_function('document.querySelector("#ocrState").textContent.includes("architecture")')
        page.evaluate('document.querySelectorAll("#settingsVeil details").forEach(d=>d.open=true)')
        page.locator('#ocrTrain').click()
        page.wait_for_function('document.querySelector("#ocrResult").textContent.includes("3")')
        assert not errors,errors
        page.screenshot(path='/workspace/artifacts/photo-ocr198.png',full_page=True)
        browser.close()
        print('Browser OK: OCR tab, API status, insufficient-data error, no JS errors')
finally:
    server.shutdown();server.server_close();_TEMP.cleanup()
