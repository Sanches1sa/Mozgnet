import sys,unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import answer_focus,conversation,brain,webfetch,chatmem,maxbot,store
Q='Какая команда на виндовс для перезагрузки системы?'
BAD='Для своих крупнейших века король бела сделал карьеру в город жителей расположен. Команда не остановить свой след нем была написана еще девочка были обнаружены.'
GOOD='Для перезагрузки Windows выполните:\n```cmd\nshutdown /r /t 0\n```\n/r — перезагрузка, /t 0 — без задержки.'
class FocusTests(unittest.TestCase):
 def setUp(self):
  self.s=patch.object(store,'settings',return_value={'web_on':True,'conversation_web':True});self.s.start()
 def tearDown(self):self.s.stop()
 def test_gibberish_replaced_by_sourced_answer(self):
  with patch.object(brain,'think',return_value=(BAD,brain.Z['sys'],{'mem_brain':{'thought':True}})),patch.object(webfetch,'alice',return_value=(GOOD,{'sources':[{'url':'https://example.com/windows'}]})) as search:
   reply,zone,info=conversation.answer(Q)
  search.assert_called_once();self.assertIn('shutdown /r /t 0',reply);self.assertNotIn('король',reply);self.assertTrue(info['focus_rejected'])
 def test_mixed_answer_keeps_command_drops_noise(self):
  text=GOOD+'\n\n'+BAD+'\n\n_Своя LLM от себя:_ Системы польской области.\n\n_Составлено: мозг + LLM_'
  clean=answer_focus.clean(Q,text)
  self.assertIn('shutdown /r /t 0',clean);self.assertNotIn('король',clean);self.assertNotIn('Составлено',clean);self.assertNotIn('польской',clean)
 def test_no_web_never_returns_gibberish(self):
  with patch.object(brain,'think',return_value=(BAD,brain.Z['sys'],{})),patch.object(webfetch,'alice') as search:
   reply,_,info=conversation.answer(Q,no_web=True)
  search.assert_not_called();self.assertTrue(reply);self.assertNotIn('король',reply);self.assertTrue(info['focus_no_answer'])
 def test_unrelated_source_rejected(self):
  self.assertFalse(answer_focus.usable(Q,'apt-get install yandex-browser-stable — установка Linux',grounded=True))
 def test_correct_memory_and_unrelated_memory(self):
  rows=[dict(id=1,title='Установка Яндекса',text='В Linux выполните apt-get install yandex-browser-stable. Для установки используйте терминал и пароль.',ts=1),dict(id=2,title='Коды оборудования',text='Система магнитно-резонансной томографии. Коды оборудования по направлениям исследований.',ts=2),dict(id=3,title='Перезагрузка Windows',text=GOOD,ts=3)]
  with patch.object(chatmem,'_load',return_value={'items':rows}):
   hits=chatmem.match(Q)
  self.assertEqual([r['id'] for r in hits],[3])
 def test_complete_answer_not_padded_with_memory(self):
  with patch.object(chatmem,'memory_line') as memory:
   self.assertEqual(maxbot._with_instructions(GOOD,Q),GOOD)
  memory.assert_not_called()
 def test_explanation_of_a_command_is_not_an_execution_request(self):
  self.assertFalse(answer_focus.command_question('Что делает команда shutdown?'))
 def test_creative_text_preserved(self):
  self.assertEqual(answer_focus.clean('Придумай сказку о короле',BAD),BAD)
 def test_empty_search_is_not_sourced_success(self):
  with patch.object(brain,'think',return_value=(BAD,brain.Z['sys'],{})),patch.object(webfetch,'alice',return_value=('Текст без источника',{})):
   reply,_,info=conversation.answer(Q)
  self.assertTrue(info['focus_no_answer']);self.assertNotIn(BAD,reply)
 def test_real_max_delivery_path_no_unrelated_appendices(self):
  with ExitStack() as stack:
   stack.enter_context(patch.object(brain,'think',return_value=(BAD,brain.Z['sys'],{'mem_brain':{'thought':True}})))
   stack.enter_context(patch.object(webfetch,'alice',return_value=(GOOD,{'sources':[{'url':'https://example.com/windows'}]})))
   stack.enter_context(patch.object(maxbot,'_src_answer',return_value={'level':'high','text':'Установка Linux: apt-get install yandex-browser-stable'}))
   for name,value in [('_status_begin',None),('_status_end',None),('_mem_ctx',[]),('_memory_on',False),('_tpl_on',False),('_zone_log',None),('_send_formulas',None),('_send_illustrations',None)]:
    stack.enter_context(patch.object(maxbot,name,return_value=value))
   stack.enter_context(patch.object(maxbot,'_load',return_value={'stats':{'ans':0}}))
   stack.enter_context(patch('worddict.learn'));stack.enter_context(patch('dialog.trace'))
   sent=stack.enter_context(patch.object(maxbot,'send'))
   memory=stack.enter_context(patch.object(chatmem,'memory_line',return_value='Посторонняя инструкция'))
   maxbot._deliver_answer(123,Q,'Алекс')
   outgoing='\n'.join(str(c.args[1]) for c in sent.call_args_list)
  self.assertIn('shutdown /r /t 0',outgoing);self.assertNotIn('король',outgoing);self.assertNotIn('apt-get',outgoing);self.assertNotIn('Посторонняя',outgoing)
  memory.assert_not_called()
 def test_source_page_used_if_search_summary_has_no_command(self):
  with patch.object(brain,'think',return_value=(BAD,brain.Z['sys'],{})),patch.object(webfetch,'alice',return_value=('Использование команды в Windows',{'sources':[{'url':'https://example.com/windows'}]})),patch.object(webfetch,'fetch_page',return_value=(True,{'text':'Для перезагрузки Windows выполните shutdown /r /t 0'})):
   reply,_,info=conversation.answer(Q)
  self.assertIn('shutdown /r /t 0',reply);self.assertTrue(info['command_extracted_from_source'])
 def test_command_from_source_not_canned(self):
  q='Какая команда для перезапуска сервиса?'
  page={'text':'Для перезапуска сервиса выполните:\nservice-tool --restart demo\nСостояние сервиса обновится.'}
  self.assertIn('service-tool --restart demo',answer_focus.command_from_page(q,page))
if __name__=='__main__':unittest.main()
