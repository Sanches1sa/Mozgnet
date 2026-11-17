"""Real Chromium smoke test; run separately with python tests/browser_project.py."""
from pathlib import Path
import io
import json
import os
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch
import zipfile

TEMP = tempfile.TemporaryDirectory()
os.environ['NEURO_DATA'] = TEMP.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import app
import auth
import codewrite
import store
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright

store.ensure_dirs()
sid = auth.create_session('127.0.0.1', 'browser-test')
server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
base = 'http://127.0.0.1:%d' % server.server_port
thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
archive = io.BytesIO()
with zipfile.ZipFile(archive, 'w') as z:
    z.writestr('main.py', 'def answer():\n    return 1\n')
    z.writestr('README.md', 'Original\n')
actions = [dict(tool='read', path='main.py'), dict(tool='write', path='main.py', content='def answer():\n    return 42\n'),
           dict(tool='write', path='README.md', content='Returns 42\n'), dict(tool='check'),
           dict(tool='finish', summary='Исправлены main.py и README.md. Тесты не запускались.')]
try:
    with patch.object(codewrite, 'own_ready', return_value=False), patch.object(codewrite, 'available', return_value=True), \
         patch.object(codewrite, '_call', side_effect=[json.dumps(a) for a in actions]), sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE') or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
        artifacts = Path(os.environ.get('NEURO_TEST_ARTIFACTS') or str(Path(__file__).resolve().parents[1] / 'test-artifacts'))
        artifacts.mkdir(parents=True, exist_ok=True)
        context = browser.new_context(viewport={'width': 1366, 'height': 900})
        context.add_cookies([dict(name=auth.COOKIE, value=sid, url=base)])
        page = context.new_page()
        errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(base)
        page.locator('#modeBox [data-route="code"]').click()
        page.locator('#projectFile').set_input_files(dict(name='demo.zip', mimeType='application/zip', buffer=archive.getvalue()))
        page.wait_for_function("document.querySelector('#projectName').textContent.includes('2 файлов')")
        page.locator('#projectInternet').uncheck()
        page.locator('#input').fill('Измени ответ на 42 и обнови README')
        page.locator('#btnSend').click()
        page.locator('#projectResult').wait_for(state='visible', timeout=10000)
        # Verify both indicators with actual computed SVG visibility, also
        # when the OS requests reduced motion (the user explicitly wants it).
        page.evaluate("typingOn('Проверка индикатора')")
        for motion in ('no-preference', 'reduce'):
            page.emulate_media(reduced_motion=motion)
            for selector in ('#typingRow .activity-triangle', '#projectStatus .activity-triangle'):
                if 'projectStatus' in selector:
                    page.evaluate("document.querySelector('#projectStatus').classList.remove('finished')")
                page.evaluate("""sel => {
                    if (window.edgeObserver) window.edgeObserver.disconnect();
                    const svg=document.querySelector(sel);
                    window.edgeSamples=[];
                    const capture=()=>window.edgeSamples.push([...svg.querySelectorAll('path')].map(p=>getComputedStyle(p).visibility==='hidden'));
                    capture();
                    window.edgeObserver=new MutationObserver(capture);
                    window.edgeObserver.observe(svg,{attributes:true,attributeFilter:['data-missing']});
                }""", selector)
                page.wait_for_function('window.edgeSamples.length >= 4')
                samples=page.evaluate('window.edgeSamples.slice(0,4)')
                assert all(sum(flags)==1 for flags in samples), samples
                indices=[flags.index(True) for flags in samples]
                assert all(b==(a+1)%3 for a,b in zip(indices,indices[1:])), indices
                page.evaluate('window.edgeObserver.disconnect()')
        page.evaluate("typingOff(); document.querySelector('#projectStatus').classList.add('finished')")
        page.locator('#projectStatus summary').click()
        assert page.locator('#projectEvents').is_visible()
        assert 'main.py' in page.locator('#projectEvents').inner_text()
        assert 'README.md' in page.locator('#projectResult').inner_text()
        link = page.locator('#projectResult a').get_attribute('href')
        response = context.request.get(base + link)
        assert response.ok
        with zipfile.ZipFile(io.BytesIO(response.body())) as z:
            assert 'return 42' in z.read('project/main.py').decode()
        assert not errors, errors
        page.screenshot(path=str(artifacts / 'project-agent-desktop.png'), full_page=True)
        page.reload()
        page.locator('#projectResult').wait_for(state='visible', timeout=10000)
        assert 'README.md' in page.locator('#projectResult').inner_text()
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_timeout(400)
        page.screenshot(path=str(artifacts / 'project-agent-mobile.png'), full_page=True)
        page.locator('#codeCourse summary').click()
        page.locator('#courseStart').click()
        page.wait_for_function("document.querySelector('#courseStatus').textContent.includes('Нужна существующая модель')")
        assert not page.locator('#courseStart').is_disabled()
        page.locator('#projectNew').click()
        page.wait_for_function("document.querySelector('#projectName').textContent.includes('Новый проект')")
        assert page.locator('#projectResult').is_hidden()
        print('Browser passed: upload ZIP, start job, expand log, download result, restore after reload, mobile, new project, triangle sequence in normal/reduced motion.')
        browser.close()
finally:
    server.shutdown(); server.server_close(); thread.join()
