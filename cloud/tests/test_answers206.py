import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import test_repairs
import answer_focus,conversation,brain,llmfact,store,webfetch
BAD='Памяти в базе знаний википедии яндекс канал мысли том или году заняла. Сколько оперативной после года однородное описал возможное один из писем человек по.'
Q='Сколько оперативной памяти в моноблоке iRU Planio 235?'
GOOD='В моноблоке iRU Planio 235 установлены 16 ГБ оперативной памяти.'
SOURCE={'url':'https://example.com/planio235','title':'Моноблок iRU Planio 235','snippet':GOOD}
class Answers206(unittest.TestCase):
 def setUp(self):self.settings=patch.object(store,'settings',return_value={'web_on':True,'conversation_web':True});self.settings.start()
 def tearDown(self):self.settings.stop()
 def test_incomplete_model_asks_before_generation_or_search(self):
  for q in ['Сколько оперативной памяти в моноблоке iru 2023?','Моноблок iru 2023']:
   with patch.object(brain,'think') as think,patch.object(webfetch,'alice') as search:
    reply,zone,info=conversation.answer(q)
   think.assert_not_called();search.assert_not_called();self.assertTrue(info['clarification']);self.assertIn('полная модель',reply);self.assertNotIn('16',reply)
 def test_installed_capacity_does_not_prove_upgrade_support(self):
  self.assertFalse(answer_focus.usable('Можно расширить память моноблока iRU Planio 235?',GOOD,grounded=True))
 def test_os_requirements_and_memory_diagnostics_do_not_require_device_model(self):
  for q in ['Сколько оперативной памяти нужно Ubuntu Server?','Как проверить сколько занято оперативной памяти?','Моноблок iRU тормозит из-за нехватки памяти?']:
   self.assertEqual(answer_focus.clarification(q),'')
 def test_sourced_specification_replaces_gibberish(self):
  with patch.object(brain,'think',return_value=(BAD,brain.Z['sys'],{'mem_brain':{'thought':True}})),patch.object(webfetch,'alice',return_value=(GOOD,{'sources':[SOURCE]})):
   reply,zone,info=conversation.answer(Q)
  self.assertIn('16 ГБ',reply);self.assertNotIn('википедии',reply);self.assertTrue(info['focus_rejected'])
 def test_bad_generated_search_summary_uses_source_excerpt(self):
  with patch.object(brain,'think',return_value=(BAD,brain.Z['sys'],{})),patch.object(webfetch,'alice',return_value=(BAD,{'sources':[SOURCE]})):
   reply,zone,info=conversation.answer(Q)
  self.assertIn('16 ГБ',reply);self.assertNotIn('однородное',reply)
 def test_different_device_source_is_not_used(self):
  other={'url':'https://example.com/other','title':'Ноутбук другой модели 999','snippet':'Установлено 64 ГБ оперативной памяти.'}
  with patch.object(brain,'think',return_value=(BAD,brain.Z['sys'],{})),patch.object(webfetch,'alice',return_value=('Установлено 64 ГБ оперативной памяти.',{'sources':[other]})):
   reply,zone,info=conversation.answer(Q)
  self.assertNotIn('64',reply);self.assertTrue(info['focus_no_answer'])
 def test_raw_generated_answer_is_not_streamed_to_delivery(self):
  with patch.object(brain,'think',return_value=(GOOD,brain.Z['kb'],{'sources':[SOURCE]})) as think:
   reply,_,_=conversation.answer(Q,deliver=lambda t:None)
  self.assertIsNone(think.call_args.kwargs['deliver']);self.assertIn('16 ГБ',reply)
 def test_metadata_noise_is_removed_but_creative_output_preserved(self):
  s=GOOD+'\n\n_Своя LLM от себя:_ Оперативной гимн бедных.\n\n_Составлено: мозг и LLM_\n\n**Проверка в интернете**\n? подтверждения не нашла: Доброе утро, Алекс!'
  clean=answer_focus.clean(Q,s);self.assertIn('16 ГБ',clean);self.assertNotIn('гимн',clean);self.assertNotIn('подтверждения',clean)
  self.assertEqual(answer_focus.clean('Придумай стих про оперативную память',BAD),BAD)
 def test_greetings_and_questions_not_checked_as_facts(self):
  for text in ['**Приветствие**\nДоброе утро, Алекс!\n\n**Ответ**\n'+GOOD,'Добрый день, Алекс! '+GOOD]:
   claims=llmfact.claims(text);self.assertTrue(any('16' in s for s in claims));self.assertFalse(any('Алекс' in s for s in claims))
  self.assertFalse(llmfact.claims('Уточни модель своего моноблока, пожалуйста.\nСколько памяти занято сейчас?'))
 def test_offline_does_not_publish_gibberish(self):
  with patch.object(brain,'think',return_value=(BAD,brain.Z['sys'],{})),patch.object(webfetch,'alice') as search:
   reply,_,info=conversation.answer(Q,no_web=True)
  search.assert_not_called();self.assertNotIn('однородное',reply);self.assertTrue(info['focus_no_answer'])
 def test_short_model_followup_resolves_original_memory_question(self):
  ctx=[{'role':'user','text':'Сколько оперативной памяти в моноблоке iRU 2023?'}]
  with patch.object(brain,'think',return_value=(BAD,brain.Z['sys'],{})) as think,patch.object(webfetch,'alice',return_value=(GOOD,{'sources':[SOURCE]})):
   reply,zone,info=conversation.answer('iRU Planio 235',ctx=ctx)
  self.assertIn('оперативной памяти',think.call_args.args[0]);self.assertIn('Planio 235',think.call_args.args[0]);self.assertIn('16 ГБ',reply)
 def test_talk_mode_uses_same_factual_policy(self):
  import talkmind
  with patch.object(brain,'think') as think:
   parts,info=talkmind.respond_parts('Сколько памяти в моноблоке iRU 2023?')
  think.assert_not_called();self.assertTrue(info['clarification']);self.assertIn('полная модель',' '.join(parts))
 def test_builtin_max_sends_clarification_not_a_device_guess(self):
  import maxbot,aiapi
  from contextlib import ExitStack
  with ExitStack() as stack:
   for name,value in [('_status_begin',None),('_status_end',None),('_mem_ctx',[]),('_memory_on',False),('_zone_log',None),('_send_formulas',None),('_send_illustrations',None)]:
    stack.enter_context(patch.object(maxbot,name,return_value=value))
   stack.enter_context(patch.object(maxbot,'_load',return_value={'stats':{'ans':0}}))
   sent=stack.enter_context(patch.object(maxbot,'send'))
   think=stack.enter_context(patch.object(brain,'think'))
   thoughts=stack.enter_context(patch('agent.cloud_thought'))
   stack.enter_context(patch('worddict.learn'));stack.enter_context(patch('dialog.trace'))
   maxbot._deliver_answer(7772023,'Сколько оперативной памяти в моноблоке iRU 2023?','Алекс')
   outgoing=' '.join(str(c.args[1]) for c in sent.call_args_list)
  think.assert_not_called();thoughts.assert_not_called();self.assertIn('полная модель',outgoing);self.assertNotIn('16 ГБ',outgoing);self.assertNotIn('Составлено',outgoing)
if __name__=='__main__':unittest.main()
