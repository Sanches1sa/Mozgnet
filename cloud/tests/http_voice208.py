"""Real local HTTP transport with a mocked synthesizer, no board/audio hardware."""
import os,sys,tempfile,threading,json,urllib.request,urllib.error,io,wave
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
TEMP=tempfile.TemporaryDirectory();os.environ['NEURO_DATA']=TEMP.name
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import app,store,tts
store.ensure_dirs();store.set_token('voice-test-only')
server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:'+str(server.server_port)
def post(token=True,text='Проверка русской речи.'):
 headers={'Content-Type':'application/json'}
 if token:headers['X-Mozg-Token']='voice-test-only'
 req=urllib.request.Request(base+'/api/tts/ru',data=json.dumps({'text':text}).encode(),headers=headers)
 try:
  with urllib.request.urlopen(req,timeout=5) as r:return r.status,r.read()
 except urllib.error.HTTPError as r:return r.code,r.read()
try:
 b=io.BytesIO()
 with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0'*1600)
 with patch.object(tts,'synth_espeak',return_value=b.getvalue()):
  status,data=post();assert status==200 and data==b.getvalue()
  assert post(False)[0] in (401,403)
  assert post(text='я'*601)[0]==400
 with patch.object(tts,'synth_espeak',return_value=None):assert post()[0]==503
 with urllib.request.urlopen(base+'/api/health',timeout=5) as r:h=json.load(r)
 assert h['version']=='3.208.0' and h['capabilities']['neuron_slots']==12
 print('HTTP voice OK: WAV bytes, token auth, text limit, explicit missing-engine error, cloud version/capabilities')
finally:server.shutdown();server.server_close();TEMP.cleanup()
