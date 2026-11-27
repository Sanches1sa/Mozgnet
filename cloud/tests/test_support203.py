import hashlib,hmac,json,sys,tempfile,time,unittest,urllib.parse
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import store,support_app

TOKEN='fake-max-app-token-for-tests'
def signed(uid=123,stamp=None):
 data={'auth_date':str(int(time.time()) if stamp is None else stamp),'user':json.dumps({'id':uid,'first_name':'Алекс'},ensure_ascii=False,separators=(',',':')),'chat':'{"id":999,"type":"DIALOG"}'}
 secret=hmac.new(b'WebAppData',TOKEN.encode(),hashlib.sha256).digest()
 data['hash']=hmac.new(secret,'\n'.join(k+'='+data[k] for k in sorted(data)).encode(),hashlib.sha256).hexdigest()
 return urllib.parse.urlencode(data)

class SupportTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(store,'DATA_DIR',self.tmp.name);self.p.start()
  store.update_settings({'support_max_bot_token':TOKEN,'support_connector_key_id':'connector'})
  support_app._SESS.clear();support_app._PASSWORDS.clear();support_app._IDEMP.clear()
  self.a={'id':'user-a','user_id':'1','name':'Алекс','admin':False};self.b={'id':'user-b','user_id':'2','name':'Борис','admin':False};self.admin={'id':'owner','admin':True}
  self.body={'service':'Почта','description':'Не открывается почтовый сервис','remote_id':'123-456','remote_password':'temporary-test-password','request_id':'n-1'}
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def test_signed_login_and_tampering(self):
  result=support_app.login(signed());self.assertEqual(support_app.session(result['session'])['user_id'],'123')
  with self.assertRaises(ValueError):support_app.validate_init(signed().replace('auth_date=','auth_date=9'))
 def test_duplicate_and_expired_init_rejected(self):
  with self.assertRaises(ValueError):support_app.validate_init(signed()+'&hash=another')
  with self.assertRaises(ValueError):support_app.validate_init(signed(stamp=int(time.time())-3601))
 def test_rotated_bot_token_revokes_session(self):
  result=support_app.login(signed());store.update_settings({'support_max_bot_token':'other-token'})
  self.assertIsNone(support_app.session(result['session']))
 def test_password_never_saved_or_listed(self):
  result=support_app.create(self.a,self.body);saved=Path(store.p(support_app.FILE)).read_text()
  self.assertNotIn('temporary-test-password',saved);self.assertNotIn('remote_password',saved)
  self.assertNotIn('temporary-test-password',json.dumps(support_app.tickets(self.a)))
  self.assertTrue(result['ticket']['password_available'])
 def test_owner_access_and_foreign_denial(self):
  ticket=support_app.create(self.a,self.body)['ticket'];self.assertEqual(support_app.tickets(self.b),[])
  with self.assertRaises(PermissionError):support_app.reveal(self.b,ticket['id'])
  self.assertEqual(support_app.reveal(self.a,ticket['id'])['remote_password'],'temporary-test-password')
  self.assertEqual(len(support_app.tickets(self.admin)),1)
 def test_only_designated_connector_gets_operator_access(self):
  support_app.create(self.a,self.body)
  own=support_app.platform_actor({'id':'random','name':'Other'},'1');operator=support_app.platform_actor({'id':'connector','name':'Platform'},'1')
  self.assertFalse(own['admin']);self.assertEqual(support_app.tickets(own),[]);self.assertTrue(operator['admin']);self.assertEqual(len(support_app.tickets(operator)),1)
 def test_idempotence_and_conflicting_reuse(self):
  a=support_app.create(self.a,self.body);b=support_app.create(self.a,self.body)
  self.assertEqual(a['ticket']['id'],b['ticket']['id']);self.assertTrue(b['duplicate'])
  with self.assertRaises(ValueError):support_app.create(self.a,dict(self.body,service='Другая'))
 def test_request_id_survives_process_memory_reset(self):
  first=support_app.create(self.a,self.body)
  support_app._IDEMP.clear()
  repeated=support_app.create(self.a,self.body)
  self.assertEqual(first['ticket']['id'],repeated['ticket']['id']);self.assertTrue(repeated['duplicate'])
 def test_password_expiry_and_closed_ticket(self):
  ticket=support_app.create(self.a,self.body)['ticket'];support_app._PASSWORDS[ticket['id']]['expires']=time.time()-1
  with self.assertRaises(ValueError):support_app.reveal(self.a,ticket['id'])
  self.assertFalse(support_app.tickets(self.a)[0]['password_available'])
  support_app.update(self.admin,ticket['id'],{'status':'closed'});self.assertEqual(support_app.tickets(self.a)[0]['status'],'closed')
 def test_delivery_truth_and_external_ack(self):
  ticket=support_app.create(self.a,self.body)['ticket'];self.assertEqual(ticket['delivery'],'cloud')
  with self.assertRaises(PermissionError):support_app.update(self.a,ticket['id'],{'status':'resolved'})
  updated=support_app.update(self.admin,ticket['id'],{'status':'in_progress','external_id':'EXT-789'})
  self.assertEqual(updated['ticket']['delivery'],'external');self.assertEqual(updated['ticket']['external_id'],'EXT-789')
 def test_ai_receives_question_only_not_credentials(self):
  with patch('aiapi.answer',return_value={'ok':True,'answer':'Ответ'}) as ai:
   support_app.ask(self.a,dict(self.body,question='Почему не работает почта?',session='max:123'))
  payload=str(ai.call_args)
  self.assertNotIn('temporary-test-password',payload);self.assertNotIn('123-456',payload);self.assertEqual(ai.call_args.kwargs['mode'],'cloud');self.assertEqual(ai.call_args.kwargs['session'],'max:123')
 def test_unknown_ai_is_not_fake_resolution(self):
  with patch('aiapi.answer',return_value={'ok':True,'answer':'Нет подтвержденной памяти','unanswered':True}):result=support_app.ask(self.a,{'question':'Почта не работает'})
  self.assertNotIn('памяти',result['answer']);self.assertIn('Подать заявку',result['actions'][0]['label']);self.assertEqual(support_app.tickets(self.a),[])
if __name__=='__main__':unittest.main()
