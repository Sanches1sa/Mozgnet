"""Real HTTP boundary, fake answer engine, no actual MAX messages."""
import os,sys,tempfile,threading,json,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
_TEMP=tempfile.TemporaryDirectory();os.environ['NEURO_DATA']=_TEMP.name
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import app,auth,store,aiapi,conversation,maxbot,brain
from http.server import ThreadingHTTPServer
store.ensure_dirs();sid=auth.create_session('127.0.0.1','test');key,_=aiapi.key_create('isolated-test')
server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base='http://127.0.0.1:%d'%server.server_port

def post(path,body,headers=None):
 request=urllib.request.Request(base+path,data=json.dumps(body).encode(),headers=dict({'Content-Type':'application/json'},**(headers or {})),method='POST')
 try:
  with urllib.request.urlopen(request,timeout=10) as r:return r.status,json.load(r)
 except urllib.error.HTTPError as e:return e.code,json.load(e)

try:
 assert post('/api/v1/ask',{'question':'Вопрос'})[0]==401
 with ExitStack() as stack:
  stack.enter_context(patch.object(conversation,'answer',return_value=('Готовый ответ из облака.','Знания',{'sources':[]})))
  stack.enter_context(patch('replytpl.is_new_dialog',return_value=False));stack.enter_context(patch('worddict.learn'));stack.enter_context(patch('desktop.panel_command',return_value=None))
  code,api=post('/api/v1/ask',{'question':'Вопрос','mode':'cloud','template':True,'session':'test'}, {'Authorization':'Bearer '+key})
  assert code==200 and api['engine']=='cloud-dialog',api
  code,panel=post('/api/chat/send',{'text':'Вопрос','route':'cloud'},{'Cookie':auth.COOKIE+'='+sid})
  assert code==200 and panel['ok'],panel
  assert api['answer']==panel['reply'],(api,panel)
  for name,value in [('_status_begin',None),('_status_end',None),('_mem_ctx',[]),('_memory_on',False),('_zone_log',None),('_send_formulas',None),('_send_illustrations',None)]:stack.enter_context(patch.object(maxbot,name,return_value=value))
  stack.enter_context(patch.object(maxbot,'_load',return_value={'stats':{'ans':0}}))
  sent=stack.enter_context(patch.object(maxbot,'send'))
  maxbot._deliver_answer(123,'Вопрос','Алекс')
  assert '\n\n'.join(call.args[1] for call in sent.call_args_list)==panel['reply']
 print('HTTP OK: 401 without key; authenticated API == cloud panel == builtin MAX delivery (deterministic engine); no real MAX sends')
finally:
 server.shutdown();server.server_close();_TEMP.cleanup()
