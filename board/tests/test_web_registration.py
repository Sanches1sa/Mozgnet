"""Execute actual route registration without board or Arduino runtime imports."""
import ast
from pathlib import Path
import re
import unittest
from unittest.mock import Mock

SOURCE=(Path(__file__).resolve().parents[1]/'python/main.py').read_text()
TREE=ast.parse(SOURCE)
def load(names,namespace):
 selected=[n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name in names]
 exec(compile(ast.Module(body=selected,type_ignores=[]),'board:actual-functions','exec'),namespace)
 return namespace

class WebRegistrationTests(unittest.TestCase):
 def test_every_exposed_handler_exists_and_register_web_executes(self):
  register=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='register_web')
  routes=[n for n in ast.walk(register) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='expose_api']
  names={n.args[2].id for n in routes};functions={n.name for n in TREE.body if isinstance(n,ast.FunctionDef)}
  self.assertFalse(names-functions,'Undefined route handlers: '+str(names-functions))
  ui=Mock();env=load(names|{'register_web'},{'ui':ui,'APP_VERSION':'test'})
  env['register_web']();self.assertEqual(ui.expose_api.call_count,len(routes))
  registered={(c.args[0],c.args[1]):c.args[2] for c in ui.expose_api.call_args_list}
  self.assertIs(registered['POST','/api/cloud/search'],env['api_cloud_search'])
  self.assertTrue(all(callable(f) for f in registered.values()))
  ui.on_connect.call_args.args[0]('browser');ui.send_message.assert_called_once_with('hello',{'app':'neuro_12','version':'test'})
 def search(self,result):
  return load({'api_cloud_search'},{'re':re,'cloud_search':Mock(return_value=result),'add_message':Mock(),'dict_has':lambda w:True,'dict_learn_words':Mock(),'brain_remember':Mock(),'dictation_queue':Mock()})
 def test_empty_query_does_not_call_cloud(self):
  env=self.search(({},''));self.assertFalse(env['api_cloud_search']({'q':'  '})['ok']);env['cloud_search'].assert_not_called()
 def test_actual_cloud_results_reach_ui(self):
  results=[{'title':'Проверка поиска','snippet':'Достоверные сведения из источника','url':'https://example.com'}]
  env=self.search(({'ok':True,'results':results},''));r=env['api_cloud_search']({'q':'  русский запрос  '})
  env['cloud_search'].assert_called_once_with('русский запрос');self.assertTrue(r['ok']);self.assertEqual(r['results'],results);env['brain_remember'].assert_called_once()
 def test_cloud_failure_is_reported(self):
  env=self.search((None,'нет соединения'));r=env['api_cloud_search']({'q':'поиск'});self.assertFalse(r['ok']);self.assertEqual(r['error'],'нет соединения')
if __name__=='__main__':unittest.main()
