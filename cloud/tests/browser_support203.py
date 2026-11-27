"""Real local HTTP + Chromium; signed MAX launch; no MAX sends."""
import os,sys,tempfile,threading,json,urllib.parse,urllib.request,urllib.error,shutil
from pathlib import Path
from unittest.mock import patch
_TEMP=tempfile.TemporaryDirectory();os.environ['NEURO_DATA']=_TEMP.name
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import app,auth,store,aiapi,support_app,conversation
from test_support203 import signed,TOKEN
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright
store.ensure_dirs();store.update_settings({'support_max_bot_token':TOKEN})
server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
base='http://127.0.0.1:%d'%server.server_port

def http(path,body=None,headers=None):
 req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=dict({'Content-Type':'application/json'},**(headers or {})),method='POST' if body is not None else 'GET')
 try:
  with urllib.request.urlopen(req,timeout=10) as r:return r.status,json.load(r),r.headers
 except urllib.error.HTTPError as e:return e.code,json.load(e),e.headers
try:
 assert http('/api/support/tickets')[0]==401
 assert http('/api/support/session',{'init_data':signed()+'&hash=bad'})[0]==401
 with patch.object(conversation,'answer',return_value=('Облако: проверьте доступность почтового сервиса.','Знания',{})),sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
  context=browser.new_context(viewport={'width':390,'height':844})
  context.route('https://st.max.ru/**',lambda route:route.fulfill(content_type='application/javascript',body='window.WebApp={initData:"",ready(){}};'))
  context.add_init_script("Object.defineProperty(navigator,'clipboard',{value:{writeText:async t=>{window.__copied=t;}}});")
  page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(base+'/max-app#WebAppData='+urllib.parse.quote(signed(),safe=''))
  page.wait_for_function('document.querySelector("#connection").textContent.includes("Подключено")')
  page.locator('#service').fill('Почта');page.locator('#description').fill('Не работает <img src=x onerror=alert(1)>')
  page.locator('details').first.evaluate('(d)=>d.open=true')
  page.locator('#remoteId').fill('123-456');page.locator('#remotePassword').fill('test-temporary-42')
  page.locator('#copyRemotePair').click();assert '123-456' in page.evaluate('window.__copied')
  page.locator('#submitTicket').click();page.wait_for_function('document.querySelector("#ticketResult").textContent.includes("REQ-")')
  assert 'сохранена в облаке' in page.locator('#ticketResult').inner_text().lower()
  page.locator('[data-view="requests"]').click();page.locator('.ticket').wait_for()
  assert page.locator('.ticket img').count()==0
  page.get_by_role('button',name='⧉ Данные Ассистента').click();page.wait_for_function('window.__copied.includes("test-temporary-42")')
  rows=store.load_json(support_app.FILE,[]);ticket_id=rows[0]['id'];assert 'test-temporary-42' not in Path(store.p(support_app.FILE)).read_text()
  page.locator('[data-view="assistant"]').click();page.locator('#question').fill('Не открывается почта');page.locator('#askButton').click()
  page.wait_for_function('document.querySelector("#messages").textContent.includes("Облако:")')
  page.locator('#questionToTicket').click();assert page.locator('#description').input_value()=='Не открывается почта'
  assert not errors,errors
  page.screenshot(path='/workspace/artifacts/max-support203.png',full_page=True)
  # Another real signed user cannot read the first user's password.
  code,other,_=http('/api/support/session',{'init_data':signed(uid=456)});assert code==200
  headers={'X-Support-Session':other['session']}
  assert http('/api/support/tickets',headers=headers)[1]['tickets']==[]
  assert http('/api/support/tickets',{'action':'reveal','id':ticket_id},headers)[0]==403
  ordinary,k1=aiapi.key_create('ordinary-test');connector,k2=aiapi.key_create('connector-test')
  store.update_settings({'support_connector_key_id':k2['id']})
  assert http('/api/v1/support/tickets',headers={'Authorization':'Bearer '+ordinary})[1]['tickets']==[]
  code,revealed,head=http('/api/v1/support/tickets',{'action':'reveal','id':ticket_id},{'Authorization':'Bearer '+connector})
  assert code==200 and revealed['remote_password']=='test-temporary-42' and head['Cache-Control']=='no-store'
  assert http('/api/v1/support/tickets',{'action':'update','id':ticket_id,'status':'in_progress','external_id':'EXT-203'},{'Authorization':'Bearer '+connector})[0]==200
  # Owner config flow in the actual panel.
  owner=browser.new_context();sid=auth.create_session('127.0.0.1','test');owner.add_cookies([dict(name=auth.COOKIE,value=sid,url=base)])
  panel=owner.new_page();panel.goto(base);panel.locator('#btnSettings').click();panel.locator('[data-tab="supportapp"]').click();panel.locator('#suURL').wait_for(state='attached')
  panel.evaluate('document.querySelectorAll("#settingsVeil details").forEach(d=>d.open=true)')
  panel.locator('#suURL').fill('https://support.example.test');panel.locator('#suSave').click();panel.wait_for_function('document.querySelector("#suSaved").textContent.includes("Сохранено")')
  assert store.settings()['support_public_url']=='https://support.example.test'
  code,config,_=http('/api/support/config',headers={'Cookie':auth.COOKIE+'='+sid});assert code==200 and TOKEN not in json.dumps(config)
  browser.close()
 print('Browser/HTTP OK: signed MAX login, mobile ticket form/copy, local receipt, XSS-safe list, cloud AI, per-user isolation, connector scope, no-store secrets, settings persistence; no real MAX messages')
finally:
 server.shutdown();server.server_close();_TEMP.cleanup()
