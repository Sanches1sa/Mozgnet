"""Real HTTP transport, shared bot path and factual filtering; source data is mocked."""
import os,sys,tempfile,threading,json,urllib.request
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
_TEMP=tempfile.TemporaryDirectory();os.environ['NEURO_DATA']=_TEMP.name
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import app,auth,store,aiapi,brain,maxbot,webfetch
from http.server import ThreadingHTTPServer
store.ensure_dirs();store.update_settings({'web_on':True,'conversation_web':True,'llm_free':True})
sid=auth.create_session('127.0.0.1','test');key,_=aiapi.key_create('answer-test')
server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:'+str(server.server_port)
def post(path,body,headers):
 req=urllib.request.Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**headers})
 with urllib.request.urlopen(req,timeout=10) as r:return json.load(r)
BAD='Оперативной гимн бедных. Памяти в базе знаний википедии мысли том или году заняла.'
GOOD='В моноблоке iRU Planio 235 установлено 16 ГБ оперативной памяти.'
S={'title':'Моноблок iRU Planio 235','snippet':GOOD,'url':'https://example.com/test-specifications'}
try:
 with ExitStack() as stack:
  stack.enter_context(patch.object(brain,'think',return_value=(BAD,brain.Z['sys'],{'mem_brain':{'thought':True}})))
  stack.enter_context(patch.object(webfetch,'alice',return_value=(GOOD,{'sources':[S]})))
  stack.enter_context(patch('replytpl.is_new_dialog',return_value=False));stack.enter_context(patch('worddict.learn'));stack.enter_context(patch('desktop.panel_command',return_value=None))
  for mode in ('cloud','talk'):
   result=post('/api/v1/ask',{'question':'Сколько памяти в моноблоке iRU 2023?','mode':mode,'session':'clarify-'+mode},{'Authorization':'Bearer '+key})
   assert result['clarification'] and 'полная модель' in result['answer'] and '16 ГБ' not in result['answer'],result
   found=post('/api/v1/ask',{'question':'Сколько оперативной памяти в моноблоке iRU Planio 235?','mode':mode,'session':'found-'+mode},{'Authorization':'Bearer '+key})
   assert '16 ГБ' in found['answer'] and 'гимн' not in found['answer'],found
   panel=post('/api/chat/send',{'text':'Сколько памяти в моноблоке iRU 2023?','route':mode},{'Cookie':auth.COOKIE+'='+sid})
   assert 'полная модель' in panel['reply'] and '16 ГБ' not in panel['reply'],panel
  for name,value in [('_status_begin',None),('_status_end',None),('_mem_ctx',[]),('_memory_on',False),('_zone_log',None),('_send_formulas',None),('_send_illustrations',None)]:stack.enter_context(patch.object(maxbot,name,return_value=value))
  stack.enter_context(patch.object(maxbot,'_load',return_value={'stats':{'ans':0}}));sent=stack.enter_context(patch.object(maxbot,'send'))
  maxbot._deliver_answer(777206,'Сколько оперативной памяти в моноблоке iRU Planio 235?','Алекс')
  reply=' '.join(c.args[1] for c in sent.call_args_list);assert '16 ГБ' in reply and 'гимн' not in reply and 'Составлено' not in reply,reply
 print('HTTP/MAX OK: cloud API, cloud/talk panel and builtin MAX use clarification and source-backed technical answers; no real MAX sends or live hardware claims')
finally:server.shutdown();server.server_close();_TEMP.cleanup()
