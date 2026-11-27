import io
from pathlib import Path
import sys
import unittest
import wave
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
import russian_voice as rv

def wav():
 b=io.BytesIO()
 with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0'*1600)
 return b.getvalue()

class VoiceTests(unittest.TestCase):
 def speaker(self,**kw):
  self.events=[];self.spoken=[];self.browser=[]
  args=dict(cloud=lambda t:(None,'Нет движка'),player=lambda d:self.spoken.append(d) or True,browser=self.browser.append,event=lambda **e:self.events.append(e),local=lambda t:wav())
  args.update(kw);return rv.RussianSpeaker(**args)
 def test_full_long_reply_is_serialized_and_not_cut_at_220(self):
  seen=[];s=self.speaker(local=lambda t:seen.append(t) or wav());text=' '.join(['Длинный русский ответ с пояснениями.']*70)
  self.assertTrue(s.submit(text)['queued']);s.queue.join()
  self.assertEqual(' '.join(seen),text);self.assertGreater(len(seen),4);self.assertEqual(len(self.spoken),len(seen));self.assertTrue(self.events[-1]['completed']);self.assertEqual(self.events[0]['phase'],'queued')
 def test_browser_fallback_does_not_claim_board_spoke(self):
  s=self.speaker(player=lambda d:False);s.submit('Проверка русской речи.');s.queue.join()
  self.assertEqual(len(self.browser),1);self.assertEqual(self.events[-1]['phase'],'browser_pending');self.assertFalse(self.events[-1]['completed'])
 def test_no_engine_reports_error_no_fake_tones(self):
  def fail(t):raise RuntimeError('Нет eSpeak')
  s=self.speaker(local=fail);s.submit('Проверка речи.');s.queue.join();self.assertEqual(self.events[-1]['phase'],'error');self.assertEqual(self.spoken,[])
 def test_disabled_and_invalid_audio(self):
  s=self.speaker(enabled=lambda:False);self.assertFalse(s.submit('Привет')['ok'])
  s=self.speaker(local=lambda t:b'not wav');s.submit('Проверка речи.');s.queue.join();self.assertEqual(self.events[-1]['phase'],'error')
 def test_russian_engine_stdin_and_private_output(self):
  paths=[]
  def run(args,**kw):
   self.assertEqual(args[args.index('-v')+1],'ru');self.assertIn('--stdin',args);self.assertEqual(kw['input'],'Русский текст.'.encode())
   path=Path(args[args.index('-w')+1]);paths.append(path);path.write_bytes(wav());return type('R',(),{'returncode':0})()
  with patch.object(rv.shutil,'which',return_value='/bin/espeak-ng'),patch.object(rv.subprocess,'run',side_effect=run):rv.local_wav('Русский текст.');rv.local_wav('Русский текст.')
  self.assertNotEqual(paths[0],paths[1]);self.assertTrue(all(not p.exists() for p in paths))
if __name__=='__main__':unittest.main()

class LongReflectionTests(unittest.TestCase):
 def test_collects_six_distinct_sentences_without_fabricated_padding(self):
  import neural_bridge as nb
  n=[0]
  def snapshot(*args):n[0]+=1;return {'seq':n[0],'fresh':True,'words':[('контекст',1)]}
  sentences=[' '.join('слово%d_%d'%(i,j) for j in range(12))+'.' for i in range(6)]
  with patch.object(nb,'surface',side_effect=lambda t,*a:t),patch.object(nb,'check',return_value=(True,'')):
   text,_=nb.reflect(snapshot,lambda f:sentences[f['seq']-1],lambda c,z:(c[0],{'by':'тензор','score':.8}),lambda w:True,lambda w:set(),target_chars=1600)
  self.assertEqual(text,' '.join(sentences));self.assertGreater(len(text),220);self.assertEqual(n[0],6)
