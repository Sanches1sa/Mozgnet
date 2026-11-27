"""Full board UI in Chromium, synthetic API telemetry; no physical neuron claim."""
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import json,shutil,threading
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
class Static(SimpleHTTPRequestHandler):
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Static,directory=str(root/'assets')));threading.Thread(target=server.serve_forever,daemon=True).start()
seq=[0]
def state():
 seq[0]+=1
 return {'ok':True,'cfg':{},'bridge_alive':True,'bridge_seen':True,'neurons_online':12,'net':'1'*12,'dict':{'ready':True,'count':30000},'nn':{'n':12,'neurons':[]},'mem':{'device':'тест UNO Q'},'neuron_activity':{'fresh':True,'seq':seq[0],'nodes':[{'id':i,'online':True,'spikes':i+1,'word':['сеть','слово','память'][i%3],'pulse':seq[0]} for i in range(12)]},'reflection':{'round':3,'limit':6,'phase':'assembling','reason':'Свежий контекст всех 12 нейронов'},'gen':{'ready':True,'vocab':30000,'links':1000}}
try:
 with sync_playwright() as p:
  b=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  def route(r):r.fulfill(content_type='application/json',body=json.dumps(state() if '/api/state' in r.request.url else {'ok':True,'items':[],'job':None,'audio':[],'cfg':{}}))
  page.route('**/api/**',route);page.goto('http://127.0.0.1:'+str(server.server_port));page.locator('.neuron-live').nth(11).wait_for();page.wait_for_function('neuronPulseSeen.get(11)>=2')
  assert page.locator('.neuron-live').count()==12
  assert page.locator('.neuron-live.spiking').count()==12
  page.evaluate('renderNeuronActivity(lastState)')
  page.screenshot(path='/workspace/artifacts/neuro12-neurons-v2_97.png',full_page=True)
  page.click('#btn-panel');page.locator('#s-toutput').wait_for();page.wait_for_function("document.getElementById('s-toutput').value === 'pwm9'")
  assert page.locator('#s-toutput option').count()==2
  page.screenshot(path='/workspace/artifacts/neuro12-voice-v2_97.png',full_page=True)
  assert not errors,errors;b.close()
 print('Board browser OK: 12 hardware slots rendered, new spike packets trigger pulses, loop status shown; API data synthetic')
finally:server.shutdown();server.server_close()
