import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('linux_launcher',ROOT/'launcher/neuro12-linux.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
from manager import Manager,adb_command,valid_app,valid_serial,validate_zip

class LauncherTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'server').mkdir();(self.root/'server/app.py').write_text('')
  self.env=patch.dict(os.environ,{'NEURO_LAUNCHER_DATA':str(self.root/'state')});self.env.start();self.calls=[]
  def runner(args,timeout):self.calls.append(args);return 'test result'
  self.m=Manager(self.root,runner=runner)
  self.m.configure({'serial':'UNOQ-TEST','app':'user:neuro_12'})
 def tearDown(self):self.m.close();self.env.stop();self.temp.cleanup()
 def archive(self,name='app.yaml'):
  p=self.root/'source.zip'
  with zipfile.ZipFile(p,'w') as z:z.writestr(name,'name: test');z.writestr('python/main.py','print(1)')
  return p
 def test_command_arguments_cannot_inject_shell(self):
  for s in ['-s','x;id','$(id)','a\nb']:
   with self.assertRaises(ValueError):valid_serial(s)
  for s in ['user:x;id','/tmp/app','--help','user:$(id)']:
   with self.assertRaises(ValueError):valid_app(s)
  self.assertEqual(adb_command('TEST','arduino-app-cli','app','start','user:neuro_12'),['adb','-s','TEST','shell','arduino-app-cli app start user:neuro_12'])
 def test_zip_requires_one_manifest_and_safe_paths(self):
  self.assertEqual(validate_zip(self.archive()),'app.yaml')
  for name in ['../app.yaml','C:/app.yaml','/app.yaml','random.txt']:
   with self.assertRaises(ValueError):validate_zip(self.archive(name))
 def test_deployment_uses_adb_import_and_cleans_remote_zip(self):
  self.assertEqual(self.m.execute('board-import',self.m.config,self.archive()),'test result')
  self.assertEqual(self.calls[0][:4],['adb','-s','UNOQ-TEST','push'])
  self.assertIn('arduino-app-cli app import /tmp/neuro12-',self.calls[1][-1]);self.assertIn('rm -f -- /tmp/neuro12-',self.calls[2][-1])
 def test_import_failure_still_cleans_remote_zip(self):
  def runner(args,timeout):
   self.calls.append(args)
   if 'app import' in args[-1]:raise RuntimeError('CLI refused existing app')
   return ''
  self.m.runner=runner
  with self.assertRaises(RuntimeError):self.m.execute('board-import',self.m.config,self.archive())
  self.assertIn('rm -f --',self.calls[-1][-1])
 def test_port_forward_never_rebinds_other_devices(self):
  self.m.execute('board-forward',self.m.config);self.assertIn('--no-rebind',self.calls[-1])
  self.m.execute('cloud-reverse',self.m.config);self.assertIn('--no-rebind',self.calls[-1])
 def test_app_controls_use_verified_cli_syntax(self):
  for action in ['board-start','board-stop','board-autostart','board-apps']:
   self.m.execute(action,self.m.config)
  self.assertIn('app start user:neuro_12',self.calls[0][-1]);self.assertIn('app stop user:neuro_12',self.calls[1][-1]);self.assertIn('properties set default user:neuro_12',self.calls[2][-1])
 def test_starting_cloud_is_distinct_from_ready(self):
  with patch.object(self.m,'health',return_value=(True,{'starting':True})):
   self.assertTrue(self.m.status()['cloud']['starting'])
  with patch.object(self.m,'health',return_value=(True,{'starting':False})):
   self.assertFalse(self.m.status()['cloud']['starting'])
 def test_existing_cloud_not_spawned_or_stopped(self):
  with patch.object(self.m,'health',return_value=(True,{})),patch('subprocess.Popen') as popen:
   self.m.cloud_start(self.m.config);self.m.cloud_stop(self.m.config);popen.assert_not_called()
 def test_config_roundtrip_and_invalid_ports(self):
  self.assertEqual(Manager(self.root).config,self.m.config)
  with self.assertRaises(ValueError):self.m.configure({'cloud_port':7000})
  with self.assertRaises(ValueError):self.m.configure({'cloud_port':80})
 def test_no_arbitrary_command_actions(self):
  with self.assertRaises(ValueError):self.m.submit('sudo reboot')
 def test_owned_process_health_and_stop(self):
  with socket.socket() as sock:sock.bind(('127.0.0.1',0));free=sock.getsockname()[1]
  self.m.configure({'cloud_port':free})
  (self.root/'server/app.py').write_text('import os\nfrom http.server import BaseHTTPRequestHandler,HTTPServer\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  b=b\'{"ok":true,"service":"neuro12-cloud"}\';self.send_response(200);self.end_headers();self.wfile.write(b)\nHTTPServer(("127.0.0.1",int(os.environ["NEURO_PORT"])),H).serve_forever()\n')
  result=self.m.cloud_start(self.m.config);self.assertIn('/api/health',result)
  self.assertTrue(self.m.status()['cloud']['owned'])
  self.m.cloud_stop(self.m.config);self.assertFalse(self.m.status()['cloud']['owned'])
 def test_http_auth_origin_host_and_async_tasks(self):
  server,token=launcher.make_server(self.m,0,'test-launcher-token')
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
  def call(path,body=None,headers=None):
   req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body else None,headers=headers or {})
   return urllib.request.urlopen(req,timeout=5)
  try:
   with self.assertRaises(urllib.error.HTTPError) as e:call('/api/status')
   self.assertEqual(e.exception.code,401)
   headers={'Authorization':'Bearer '+token}
   self.assertTrue(json.load(call('/api/status',headers=headers))['ok'])
   with self.assertRaises(urllib.error.HTTPError) as e:call('/api/action',{'action':'devices'},{**headers,'Origin':'https://evil.example'})
   self.assertEqual(e.exception.code,403)
   with self.assertRaises(urllib.error.HTTPError) as e:call('/',headers={'Host':'evil.example'})
   self.assertEqual(e.exception.code,403)
   d=json.load(call('/api/action',{'action':'devices'},headers));self.assertTrue(d['ok']);self.assertIn(d['id'],self.m.jobs)
  finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
