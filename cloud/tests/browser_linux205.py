"""Real launcher HTTP and Chromium; ADB commands are captured, not sent to hardware."""
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from unittest.mock import patch
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('linux_launcher',ROOT/'launcher/neuro12-linux.py');launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
from manager import Manager
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp);(root/'server').mkdir();(root/'server/app.py').write_text('')
 calls=[]
 def run(args,timeout):
  calls.append(args);time.sleep(.05)
  if args[1:]==['devices','-l']:return 'List of devices attached\nUNOQ-DEMO-001 device product:uno_q model:Arduino_UNO_Q\n'
  if 'app list' in args[-1]:return 'ID: user:neuro_12\nName: neuro_12\nStatus: stopped'
  return 'Команда выполнена (тест ADB без физической платы)'
 with patch.dict(os.environ,{'NEURO_LAUNCHER_DATA':str(root/'state')}):m=Manager(root,runner=run)
 m.health=lambda:(False,{})
 server,token=launcher.make_server(m,0,'browser-test-token');threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:'+str(server.server_port)
 try:
  with sync_playwright() as pw:
   browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
   page=browser.new_page(viewport={'width':1500,'height':1100});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(base+'/#token='+token);page.get_by_text('Лаунчер доступен',exact=True).wait_for()
   page.get_by_role('button',name='Найти UNO Q по USB').click();page.wait_for_function('document.querySelector("#log").textContent.includes("UNOQ-DEMO-001")')
   page.locator('#serial').fill('UNOQ-DEMO-001');page.get_by_role('button',name='Список приложений',exact=True).click();page.wait_for_function('document.querySelector("#log").textContent.includes("Name: neuro_12")')
   page.get_by_role('button',name='Подключить интерфейс платы',exact=True).click();page.wait_for_function('document.querySelector("#log").textContent.includes("[Готово] board-forward")')
   archive=root/'source.zip'
   with zipfile.ZipFile(archive,'w') as z:z.writestr('app.yaml','name: neuro_12');z.writestr('python/main.py','print(1)')
   page.get_by_text('Установить приложение из ZIP',exact=True).click();page.locator('#zip').set_input_files(archive);page.get_by_role('button',name='Загрузить на UNO Q и импортировать',exact=True).click();page.wait_for_function('document.querySelector("#log").textContent.includes("[Готово] board-import")')
   page.screenshot(path='/workspace/artifacts/neuro12-linux-launcher-v3_205.png',full_page=True)
   assert any('app import' in args[-1] for args in calls);assert any('--no-rebind' in args for args in calls);assert not errors,errors
   assert 'token=' not in page.url
   browser.close()
  print('Launcher browser OK: authenticated access, device listing, persisted serial, app list, port forwarding, ZIP upload/import queue; ADB mocked, no physical UNO Q used')
 finally:server.shutdown();server.server_close();m.close()
