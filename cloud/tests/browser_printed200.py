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
import photo_ocr
import ocr_dataset
import printed_dataset
import io
from PIL import Image,ImageDraw,ImageFont
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright

store.ensure_dirs()
breath.note_will('test-board',{'Кирпичики':.8,'Буквы':.6,'Математика':.9})
llm_zones.route('python функция код')
store.update_settings({'yandex_api_key':'saved-fake-key','yandex_folder':'saved-folder','sources_on':False})
sid=auth.create_session('127.0.0.1','test')
# Locally prepared page fixture; no real API secrets or messages.
im=Image.new('RGB',(150,50),'white');draw=ImageDraw.Draw(im)
draw.text((5,4),'A B A B',font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',26),fill='black')
raw=io.BytesIO();im.save(raw,format='PNG')
folder=Path(store.p('datasets','printed'));folder.mkdir(parents=True,exist_ok=True);(folder/'1.image').write_bytes(raw.getvalue())
store.save_json(printed_dataset.MANIFEST,[dict(id=1,text='ABAB',width=150,height=50,prepared=False,reason='Нужна обрезка',crops=0)])
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
        photo_ocr._STATE.update(running=True,phase='training',percent=42,epoch=4,epochs=10,loss=.7,eta_seconds=12)
        page.wait_for_function('document.querySelector("#ocrProgress").value===42')
        assert page.locator('#ocrTrain').is_disabled()
        assert '4/10' in page.locator('#ocrProgressInfo').inner_text()
        photo_ocr._STATE.update(running=False,phase='done',percent=100)
        page.wait_for_function('document.querySelector("#ocrProgress").value===100')
        assert 'CoMNIST' in page.locator('[data-pane="photoocr"]').inner_text()
        assert 'Russian OCR Small' in page.locator('[data-pane="photoocr"]').inner_text()
        page.wait_for_function('document.querySelector("#printedCanvas").width===150')
        page.locator('#printedCanvas').scroll_into_view_if_needed()
        box=page.locator('#printedCanvas').bounding_box()
        page.mouse.move(box['x']+4,box['y']+4);page.mouse.down();page.mouse.move(box['x']+26,box['y']+34);page.mouse.up()
        page.locator('#printedText').fill('A');page.locator('#printedSingle').check();page.locator('#printedAdd').click()
        page.wait_for_function('document.querySelector("#printedResult").textContent.includes("добавлено")')
        assert len(store.load_json('photo_ocr_samples.json',[]))==1
        assert store.load_json('photo_ocr_samples.json',[])[0]['split']=='train'
        page.locator('[data-tab="loopllm"]').click()
        page.locator('#loopOn').wait_for(state='attached')
        page.evaluate('document.querySelectorAll("#settingsVeil details").forEach(d=>d.open=true)')
        page.locator('#loopOn').check();page.locator('#loopPasses').fill('3');page.locator('#loopMix').fill('0.35')
        page.locator('#loopSave').click()
        page.wait_for_function('document.querySelector("#loopSaved").textContent.includes("Сохранено")')
        assert store.settings()['llm_loop_on'] is True
        assert store.settings()['llm_loop_passes']==3
        page.locator('[data-tab="common"]').click()
        page.locator('[data-tab="loopllm"]').click()
        page.locator('#loopOn').wait_for(state='attached')
        assert page.locator('#loopOn').is_checked()
        assert page.locator('#loopPasses').input_value()=='3'
        assert not errors,errors
        page.screenshot(path='/workspace/artifacts/photo-ocr200.png',full_page=True)
        browser.close()
        print('Browser OK: printed dataset panel, authenticated page image, drag crop, save training example, progress and loop settings persist')
finally:
    server.shutdown();server.server_close();_TEMP.cleanup()
