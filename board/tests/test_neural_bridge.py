import ast
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
import neural_bridge as nb

KNOWN={'сеть','хранит','слова','память','помнит','ответ','почему','что','если','это','важно'}
EDGES={'сеть':{'хранит'},'хранит':{'слова'},'почему':{'сеть'},'что':{'сеть'}}
def packet(last=11,spikes=2):return ''.join('N%d:1:%d:%s|'%(i,spikes,'сеть' if i==last else 'память') for i in range(12))
def frame(seq=1):return {'seq':seq,'fresh':True,'words':[('сеть',1)],'zone':'Память','physical_online':12,'software_neurons':48}
def local_functions(names,values):
 tree=ast.parse((Path(__file__).resolve().parents[1]/'python/main.py').read_text());module=ast.Module(body=[x for x in tree.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x.name in names],type_ignores=[])
 values.setdefault('THOUGHT_CHARS',6000);values.setdefault('LOOP_TURNS',37)
 exec(compile(module,'main:selected','exec'),values);return values

class BridgeTests(unittest.TestCase):
 def test_all_twelve_slots_including_last_are_preserved(self):
  bus=nb.NeuronBus();bus.ingest(packet());s=bus.snapshot();self.assertTrue(s['fresh']);self.assertEqual(len(s['nodes']),12);self.assertEqual(s['nodes'][11]['word'],'сеть');self.assertEqual(s['nodes'][11]['pulse'],1)
 def test_context_accumulates_real_spikes_without_faking_pulses(self):
  bus=nb.NeuronBus();bus.ingest(packet(spikes=2));bus.ingest(packet(spikes=0));node=bus.snapshot()['nodes'][11];self.assertEqual(node['spikes'],0);self.assertEqual(node['context_spikes'],2);self.assertEqual(node['pulse'],1)
  for h in bus.history:
   for i,row in enumerate(h):h[i]=(row[0]-4,row[1],row[2])
  self.assertEqual(bus.snapshot()['nodes'][11]['context_spikes'],0)
 def test_invalid_or_partial_frame_does_not_replace_context(self):
  bus=nb.NeuronBus();bus.ingest(packet());old=bus.snapshot()['seq']
  for text in ['N0:1:3:сеть|',packet()+'N0:1:2:сеть|',packet().replace('N11:','N99:')]:
   with self.assertRaises(ValueError):bus.ingest(text)
  self.assertEqual(bus.snapshot()['seq'],old)
 def test_punctuation_in_neuron_word_does_not_drop_whole_frame(self):
  bus=nb.NeuronBus();text=packet().replace('N3:1:2:память','N3:1:2:что?').replace('N5:1:2:память','N5:1:2:при вет,')
  bus.ingest(text);s=bus.snapshot();self.assertTrue(s['fresh']);self.assertEqual(s['nodes'][3]['word'],'что');self.assertEqual(s['nodes'][5]['word'],'привет');self.assertEqual(len(s['nodes']),12)
 def test_rejected_frame_is_reported(self):
  bus=nb.NeuronBus();bus.reject('Malformed neuron telemetry','N0');s=bus.snapshot();self.assertEqual(s['rejected'],1);self.assertFalse(s['fresh'])
 def test_latin_translit_from_neuron_boards_becomes_russian(self):
  self.assertEqual([nb.to_russian(w) for w in ['privet','zdes','slychu','gotov','tok','volna']],['привет','здесь','слышу','готов','ток','волна'])
  self.assertEqual(nb.to_russian('сеть'),'сеть');self.assertEqual(nb.to_russian('mix сеть'),'')
  bus=nb.NeuronBus();bus.ingest(packet().replace('N2:1:2:память','N2:1:2:gotov'));n=bus.snapshot()['nodes'][2];self.assertEqual(n['word'],'готов');self.assertEqual(n['raw'],'gotov')
 def test_surface_never_outputs_latin(self):
  self.assertEqual(nb.surface('privet сеть хранит слова'),'Сеть хранит слова.')
 def test_fallback_words_instead_of_silence(self):
  n=[0]
  def snapshot(*_):n[0]+=1;return frame(n[0])
  phrase,meta=nb.reflect(snapshot,lambda f:'сеть слова память',Mock(),KNOWN.__contains__,lambda w:EDGES.get(w,set()),fallback_words=True)
  self.assertTrue(phrase);self.assertTrue(meta['unverified']);self.assertEqual(nb.status()['phase'],'words');self.assertNotRegex(phrase,'[a-z]')
 def test_fallback_uses_pool_when_no_context_at_all(self):
  n=[0]
  def snapshot(*_):n[0]+=1;f=frame(n[0]);f.update(words=[]);return f
  phrase,_=nb.reflect(snapshot,lambda f:'',Mock(),KNOWN.__contains__,lambda w:set(),fallback_words=True,pool=lambda:['сеть','слова','x'])
  self.assertEqual(phrase,'Сеть слова.')
  for a in nb.status()['attempts']:self.assertIn('physical_online',a);self.assertIn('software_neurons',a)
 def test_cloud_thought_mail_feeds_neurons_and_never_replies(self):
  from types import SimpleNamespace
  excite=Mock();feed=Mock();add=Mock();http=Mock();rem=Mock()
  env=local_functions({'_brainmail_handle'},{'_cloud_base':lambda:('http://c','t'),'nn_excite':excite,'nn_stems':lambda t:[t],'mn_feed':feed,'add_message':add,'log':Mock(),'_http_json_err':http,'brain_remember':rem})
  env['_brainmail_handle']({'id':'ct1','from':'облако (мысль)','text':'Сеть хранит слова.','kind':'cloud_thought'})
  excite.assert_called_once();feed.assert_called_once();add.assert_called_once();http.assert_not_called();rem.assert_not_called()
  self.assertEqual(add.call_args.kwargs['zone'],'Мысль облака')
 def test_snapshot_waits_for_real_next_frame(self):
  bus=nb.NeuronBus();bus.ingest(packet());old=bus.snapshot()['seq'];thread=threading.Timer(.02,lambda:bus.ingest(packet()));thread.start();s=bus.snapshot(old,.5);thread.join();self.assertGreater(s['seq'],old)
 def test_retries_use_new_context_and_never_publish_failed_candidates(self):
  count=[0];compose=Mock(side_effect=lambda f:'сеть слова память' if f['seq']<3 else 'сеть хранит слова')
  def snapshot(after,timeout):count[0]+=1;return frame(count[0])
  phrase,meta=nb.reflect(snapshot,compose,lambda c,z:(c[0],{'by':'тензор','score':.8}),KNOWN.__contains__,lambda w:EDGES.get(w,set()))
  self.assertEqual(phrase,'Сеть хранит слова.');self.assertEqual(nb.status()['round'],3);self.assertEqual([r['seq'] for r in nb.status()['attempts']],[1,2,3])
 def test_six_round_bound_and_no_self_training(self):
  n=[0]
  def snapshot(*_):n[0]+=1;return frame(n[0])
  phrase,_=nb.reflect(snapshot,lambda f:'сеть слова память',Mock(),KNOWN.__contains__,lambda w:EDGES.get(w,set()),limit=500)
  self.assertEqual(phrase,'');self.assertEqual(n[0],6);self.assertEqual(nb.status()['phase'],'no_phrase')
 def test_no_fresh_context_stops_instead_of_reusing_it(self):
  calls=Mock(return_value=frame());phrase,_=nb.reflect(calls,lambda f:'сеть слова память',Mock(),KNOWN.__contains__,lambda w:EDGES.get(w,set()))
  self.assertEqual(phrase,'');self.assertEqual(calls.call_count,2);self.assertEqual(nb.status()['round'],1)
 def test_untrained_first_candidate_is_not_a_neural_verdict(self):
  n=[0]
  def snapshot(*_):n[0]+=1;return frame(n[0])
  phrase,_=nb.reflect(snapshot,lambda f:'сеть хранит слова',lambda c,z:(c[0],{'by':'первый вариант','score':0}),KNOWN.__contains__,lambda w:EDGES.get(w,set()))
  self.assertEqual(phrase,'')
 def test_punctuation_learned_and_question_termination(self):
  nb.learn_punctuation(['Если сеть, это важно.'])
  self.assertEqual(nb.surface('если сеть это важно'),'Если сеть, это важно.')
  self.assertTrue(nb.surface('почему сеть хранит слова').endswith('?'))
  self.assertFalse(nb.check('Сеть сеть слова.',[('сеть',1)],KNOWN.__contains__,lambda w:EDGES.get(w,set()))[0])
 def test_main_speak_uses_reflection_and_no_hub_seed(self):
  import time
  state={'links':100,'uni':{'сеть':1}}
  fake=Mock();fake.reflect=Mock(return_value=('Сеть хранит слова.',{'by':'тензор','score':.9}));fake.status=Mock(return_value={'phase':'accepted'})
  register=Mock();context=frame();context.update(zone='Память',hot={})
  env=local_functions({'brain_speak','_words_fallback_on'},{'_GEN_STATE':state,'GEN_MIN_LINKS':1,'neural_bridge':fake,'_neuron_context':lambda *a:context,'_gen_compose':Mock(),'_tn_pick_thought':Mock(),'dict_has':lambda w:True,'gen_register':register,'_camai_say':Mock(),'time':time})
  self.assertEqual(env['brain_speak']('HUB RANDOM WORDS'),'Сеть хранит слова.');self.assertEqual(fake.reflect.call_args.kwargs['limit'],6);register.assert_called_once()
 def test_bridge_event_exposes_all_nodes_without_calling_rpc(self):
  import math
  from types import SimpleNamespace
  bus=nb.NeuronBus();fake=SimpleNamespace(BUS=bus);zones=Mock();words=Mock();sh=SimpleNamespace(lock=threading.RLock())
  env=local_functions({'on_neurons'},{'neural_bridge':fake,'nn_excite_zone':zones,'nn_excite':words,'nn_stems':lambda t:[t],'SH':sh,'_bridge_touch':Mock(),'ZONES':[(str(i),[]) for i in range(12)],'math':math})
  env['on_neurons'](packet());self.assertEqual(zones.call_count,12);self.assertEqual(words.call_count,12);self.assertEqual(zones.call_args_list[0].args[0],'0');self.assertEqual(zones.call_args_list[-1].args[0],'11')
 def test_generated_thoughts_are_not_corpus_positive_examples(self):
  from types import SimpleNamespace
  sh=SimpleNamespace(lock=threading.RLock(),memory=[{'role':'bot','text':'💬 «Самосгенерированная фраза»'},{'role':'user','text':'Сеть хранит слова.'}],docs=[],teacher_note='')
  env=local_functions({'_gen_texts'},{'SH':sh,'_read_load':lambda:None,'_READ_LOCK':threading.RLock(),'_READ':{'items':[]},'_advice_load':lambda:{}})
  self.assertEqual(env['_gen_texts'](),['Сеть хранит слова.'])
 def test_feedback_changes_neuron_weights_only_from_external_verdict(self):
  learn=Mock();observe=Mock()
  env=local_functions({'gen_apply_verdicts'},{'_pen_load':lambda:None,'_GEN_PEN':{'pairs':{},'good':0,'bad':0},'_GEN_STATE':{'lead':{'zone':'Память'}},'_norm_word':str.lower,'re':__import__('re'),'_pen_save':lambda:None,'nn_learn':learn,'tn_observe':observe})
  env['gen_apply_verdicts']([{'ok':True,'t':'сеть хранит слова'}]);learn.assert_called_once_with('сеть хранит слова','Память',1);observe.assert_not_called()

if __name__=='__main__':unittest.main()
