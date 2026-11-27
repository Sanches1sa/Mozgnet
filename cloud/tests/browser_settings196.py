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
    with patch.object(yandex_search,'search',return_value=[dict(title='Example',url='https://example.com',snippet='Search result',src='')]) as search,sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1280,'height':900})
        context.add_cookies([dict(name=auth.COOKIE,value=sid,url=base)])
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base)
        page.locator('#btnSettings').click()
        page.locator('#setYFolder').wait_for(state='attached')
        page.evaluate('document.querySelectorAll("#settingsVeil details").forEach(d=>d.open=true)')
        page.locator('#setYKey').fill('new-test-key')
        page.locator('#setYFolder').fill('new-test-folder')
        page.locator('#yandexTest').click()
        page.wait_for_function('document.querySelector("#yandexState").textContent.includes("1 ссылок")')
        assert search.call_args.args[:2]==('new-test-key','new-test-folder'),search.call_args
        assert store.settings()['yandex_api_key']=='saved-fake-key'
        assert store.settings()['yandex_folder']=='saved-folder'
        assert not errors,errors
        page.locator('#setEngines').select_option('yandex')
        assert page.locator('#setNegFreq').count()==1
        assert page.locator('#setCamPin').count()==1
        def duplicate_ids():
            return page.evaluate("""() => {const counts={};document.querySelectorAll('#settingsVeil [id]').forEach(e=>counts[e.id]=(counts[e.id]||0)+1);return Object.keys(counts).filter(k=>counts[k]>1);} """)
        assert not duplicate_ids(),duplicate_ids()
        page.locator('[data-tab="communication"]').click()
        page.locator('#convStyle').wait_for(state='visible')
        assert not duplicate_ids(),duplicate_ids()
        page.locator('#convStyle').select_option('short')
        page.locator('#saveConversation').click()
        page.wait_for_function("document.querySelector('#conversationSaved').textContent==='Сохранено'")
        assert store.settings()['conversation_style']=='short'
        page.locator('[data-tab="common"]').click()
        page.locator('#setEngines').wait_for(state='attached')
        page.evaluate('document.querySelectorAll("#settingsVeil details").forEach(d=>d.open=true)')
        assert not duplicate_ids(),duplicate_ids()
        assert page.locator('#setNegFreq').count()==1
        assert page.locator('#partForm').count()==1
        page.locator('[data-tab="coupling"]').click()
        page.locator('.zone-table-wrap tr').nth(16).wait_for(state='attached')
        assert page.locator('.zone-table-wrap tr').count()>=17
        page.locator('#zoneMix').fill('75')
        page.locator('#zoneSave').click()
        page.wait_for_function("document.querySelector('#zoneSaveState').textContent==='Сохранено'")
        assert store.settings()['coupling_zone_mix']==75
        page.locator('[data-tab="instruct"]').click()
        page.wait_for_function("document.querySelector('[data-pane=instruct]').dataset.grouped==='1'")
        assert page.locator('[data-pane="instruct"] details.sgrp').count()>=2
        page.locator('[data-tab="common"]').click()
        page.locator('#setEngines').wait_for(state='attached')
        page.evaluate('document.querySelectorAll("#settingsVeil details").forEach(d=>d.open=true)')
        search.side_effect=yandex_search.SearchError('HTTP 403: проверьте роль search-api.webSearch.user')
        page.locator('#yandexTest').click()
        page.wait_for_function('document.querySelector("#yandexState").textContent.includes("403")')
        assert not page.locator('#yandexTest').is_disabled()
        Path('/workspace/artifacts').mkdir(exist_ok=True)
        page.screenshot(path='/workspace/artifacts/settings196.png',full_page=True)
        assert not errors,errors
        print('Browser OK: Yandex API selector/probe, communication persistence, unique controls after navigation, quantum/D6/D4 retained, all-zone coupling and grouped tabs.')
        browser.close()
finally:
    server.shutdown();server.server_close();thread.join()
