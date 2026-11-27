import os, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
_TMP = tempfile.TemporaryDirectory()
os.environ['NEURO_DATA'] = _TMP.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import store
import cloudthought as ct

class CloudThoughtTests(unittest.TestCase):
 def setUp(self):
  for f in ('boards.json','mailboxes.json','thoughts.json','cloudthought.json'):
   p=Path(_TMP.name)/f
   if p.exists():p.unlink()
  store.board_touch('uno1',name='Плата 1')
  store.board_touch('old',name='Старая')
  bs=store.boards();bs['old']['last_seen']=time.time()-9999;store.save_json('boards.json',bs)
 def test_deliver_goes_to_chat_feed_and_online_boards_only(self):
  chat,n=ct.deliver('Сеть хранит слова памяти.',True,True)
  self.assertTrue(chat);self.assertEqual(n,1)
  msgs=store.chat_load('thoughts')['messages'];self.assertEqual(msgs[-1]['brain'],'облако')
  import brain
  mail=brain.mail_take('uno1');self.assertEqual(len(mail),1);self.assertEqual(mail[0]['kind'],'cloud_thought');self.assertEqual(brain.mail_take('old'),[])
  self.assertEqual(store.thoughts_list(1)[0]['board'],'облако')
 def test_switches_respected(self):
  chat,n=ct.deliver('Сеть хранит слова памяти.',False,False);self.assertFalse(chat);self.assertEqual(n,0)
 def test_settings_clamped(self):
  c=ct.set_cfg({'every':1,'on':False});self.assertEqual(c['every'],30);self.assertFalse(c['on'])
  self.assertEqual(ct.think_now()['reason'],'выключено')
 def test_think_now_uses_composed_text(self):
  ct.set_cfg({'on':True})
  fake=MagicMock();fake.compose=lambda tp:('Память хранит слова сети.',{})
  with patch.dict(sys.modules,{'wordmind':fake}):
   r=ct.think_now()
  self.assertTrue(r['ok']);self.assertEqual(r['boards'],1)
 def test_no_thought_is_reported_not_faked(self):
  fake=MagicMock();fake.compose=lambda tp:('',{})
  with patch.dict(sys.modules,{'wordmind':fake}):
   r=ct.think_now(force=True)
  self.assertFalse(r['ok'])
if __name__=='__main__':unittest.main()
