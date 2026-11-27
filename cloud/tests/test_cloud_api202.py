import sys,unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import test_repairs
import aiapi,dialog,maxbot,store,brain,conversation

class CloudAPITests(unittest.TestCase):
 def setUp(self):aiapi._SESS.clear()
 def test_api_uses_same_cloud_service_as_panel(self):
  with patch.object(dialog,'respond',return_value=(['Один','Два'],'Знания',{'sources':[{'url':'https://example.com'}]})) as respond:
   panel=aiapi.cloud_reply('Вопрос',ctx=[],chat_key='chat:1',template=True)
   result=aiapi.answer('Вопрос',{'id':'test','name':'test'},session='s',mode='cloud',ctx=[],template=True)
  self.assertEqual(result['parts'],panel[0]);self.assertEqual(result['answer'],'Один\n\nДва');self.assertEqual(result['engine'],'cloud-dialog')
  self.assertEqual(respond.call_count,2);self.assertIsNone(respond.call_args.kwargs['deliver'])
 def test_context_once_and_sessions_isolated(self):
  with patch.object(aiapi,'cloud_reply',return_value=(['A'],'Знания',{})) as cloud:
   aiapi.answer('Q',{'id':'k'},session='max:1',ctx=[{'role':'user','text':'Q'}])
   self.assertEqual(cloud.call_args.kwargs['ctx'],[{'role':'user','text':'Q'}])
   aiapi.answer('Next',{'id':'k'},session='max:1')
   self.assertIn({'role':'bot','text':'A'},cloud.call_args.kwargs['ctx'])
   aiapi.answer('Other',{'id':'k'},session='max:2')
   self.assertEqual(cloud.call_args.kwargs['ctx'],[{'role':'user','text':'Other'}])
 def test_max_delivers_api_parts_unchanged(self):
  with ExitStack() as stack:
   api=stack.enter_context(patch.object(aiapi,'answer',return_value={'ok':True,'answer':'Первый\n\nВторой','parts':['Первый','Второй'],'zone':'Знания'}))
   for name,value in [('_status_begin',None),('_status_end',None),('_mem_ctx',[{'role':'user','text':'История'}]),('_memory_on',False),('_zone_log',None),('_send_formulas',None),('_send_illustrations',None)]:stack.enter_context(patch.object(maxbot,name,return_value=value))
   stack.enter_context(patch.object(maxbot,'_load',return_value={'stats':{'ans':0}}))
   source=stack.enter_context(patch.object(maxbot,'_src_answer'));extra=stack.enter_context(patch('chatmem.memory_line'));sent=stack.enter_context(patch.object(maxbot,'send'))
   maxbot._deliver_answer(123,'Вопрос','Алекс')
   self.assertEqual([c.args[1] for c in sent.call_args_list],['Первый','Второй'])
   self.assertEqual(api.call_args.kwargs['mode'],'cloud');self.assertEqual(api.call_args.kwargs['session'],'max:123')
   source.assert_not_called();extra.assert_not_called()
 def test_service_error_never_switches_to_other_ai(self):
  with ExitStack() as stack:
   stack.enter_context(patch.object(aiapi,'answer',side_effect=RuntimeError('test')))
   for name,value in [('_status_begin',None),('_status_end',None),('_mem_ctx',[]),('_memory_on',False),('_zone_log',None),('_send_formulas',None),('_send_illustrations',None)]:stack.enter_context(patch.object(maxbot,name,return_value=value))
   stack.enter_context(patch.object(maxbot,'_load',return_value={'stats':{'ans':0}}))
   other=stack.enter_context(patch.object(maxbot,'_think'));sent=stack.enter_context(patch.object(maxbot,'send'))
   maxbot._deliver_answer(123,'Вопрос','Алекс')
   self.assertIn('Не удалось получить ответ облака',sent.call_args.args[1]);other.assert_not_called()
 def test_local_command_stays_before_ai(self):
  state={'bot':{},'stats':{'recv':0,'ans':0}}
  with patch.object(maxbot,'_load',return_value=state),patch.object(maxbot,'_see_chat'),patch('maxsrc.is_source',return_value=False),patch.object(maxbot,'_cmd',return_value='Результат обработчика заявки'),patch.object(maxbot,'send_reply') as send,patch.object(maxbot,'_deliver_answer') as ai:
   maxbot._on_message({'sender':{'name':'Алекс'},'recipient':{'chat_id':123,'chat_type':'dialog'},'body':{'text':'/local_action'}})
  send.assert_called_once();ai.assert_not_called()
 def test_talk_mode_matches_panel_talkmind(self):
  with patch('talkmind.respond_parts',return_value=(['Разговор'],{})) as talk:
   result=aiapi.answer('Привет',{'id':'t'},mode='talk')
  talk.assert_called_once();self.assertEqual(result['engine'],'talkmind');self.assertEqual(result['parts'],['Разговор'])
if __name__=='__main__':unittest.main()
