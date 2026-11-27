#!/usr/bin/env python3
"""Headless Ubuntu launcher. UI runs in a browser via an SSH local tunnel."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import tempfile
import threading
from urllib.parse import urlsplit
import webbrowser

sys.path.insert(0,str(Path(__file__).resolve().parent/'linux'))
from manager import Manager,port


def make_server(manager,listen_port,token=None):
    token=token or secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,code,value,kind='application/json; charset=utf-8'):
            body=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
            self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(body)
        def host_ok(self):
            try:hostname=urlsplit('http://'+self.headers.get('Host','')).hostname
            except ValueError:return False
            return hostname in ('127.0.0.1','localhost','::1')
        def authorized(self):
            if not self.host_ok():self.send(403,{'ok':False,'error':'Только локальный адрес/SSH-туннель'});return False
            supplied=self.headers.get('Authorization','')
            if not secrets.compare_digest(supplied,'Bearer '+token):self.send(401,{'ok':False,'error':'Откройте ссылку лаунчера с #token из терминала или журнала службы'});return False
            origin=self.headers.get('Origin')
            if origin and origin!='http://'+self.headers.get('Host',''):self.send(403,{'ok':False,'error':'Чужой Origin'});return False
            return True
        def do_GET(self):
            if self.path=='/':
                if not self.host_ok():return self.send(403,{'ok':False})
                return self.send(200,(Path(__file__).parent/'linux/index.html').read_bytes(),'text/html; charset=utf-8')
            if not self.authorized():return
            if self.path=='/api/status':return self.send(200,manager.status())
            return self.send(404,{'ok':False})
        def do_POST(self):
            if not self.authorized():return
            uploaded=None
            try:
                size=int(self.headers.get('Content-Length','0'))
                if self.path=='/api/import':
                    if not 0<size<=256*1024*1024:raise ValueError('ZIP должен быть не больше 256 МБ')
                    fd,name=tempfile.mkstemp(suffix='.zip',prefix='upload-',dir=str(manager.state));uploaded=Path(name)
                    self.connection.settimeout(60)
                    with os.fdopen(fd,'wb') as f:
                        left=size
                        while left:
                            chunk=self.rfile.read(min(left,1024*1024))
                            if not chunk:raise ValueError('Загрузка прервана')
                            f.write(chunk);left-=len(chunk)
                    jid=manager.submit('board-import',uploaded);uploaded=None
                    return self.send(202,{'ok':True,'id':jid})
                if not 0<size<=8192:raise ValueError('Размер запроса не поддерживается')
                b=json.loads(self.rfile.read(size))
                if not isinstance(b,dict):raise ValueError('Ожидается объект JSON')
                if self.path=='/api/config':
                    if manager.queue_lock.locked():raise ValueError('Дождитесь завершения текущего действия')
                    if any(k in b and int(b[k])==self.server.server_address[1] for k in ('cloud_port','board_port')):raise ValueError('Порт уже используется лаунчером')
                    return self.send(200,{'ok':True,'config':manager.configure(b)})
                if self.path=='/api/action':return self.send(202,{'ok':True,'id':manager.submit(str(b.get('action','')))})
                return self.send(404,{'ok':False})
            except (ValueError,RuntimeError,OSError) as e:return self.send(400,{'ok':False,'error':str(e)})
            finally:
                if uploaded:
                    try:uploaded.unlink()
                    except OSError:pass
    server=ThreadingHTTPServer(('127.0.0.1',listen_port),Handler)
    return server,token


def main():
    parser=argparse.ArgumentParser(description='neuro12: веб-лаунчер Ubuntu Server + UNO Q по USB. GUI на сервере не нужен.')
    parser.add_argument('--root',default=str(Path(__file__).resolve().parent.parent),help='Каталог облака с server/app.py')
    parser.add_argument('--port',type=port,default=8076,help='Локальный порт панели (только 127.0.0.1)')
    parser.add_argument('--start-cloud',action='store_true',help='Запустить облако при старте лаунчера')
    parser.add_argument('--open',action='store_true',help='Открыть браузер на этом компьютере, если есть GUI')
    parser.add_argument('--check',action='store_true',help='Только проверка зависимостей')
    args=parser.parse_args();manager=Manager(args.root)
    if args.check:
        import shutil
        status={'root':str(manager.root),'python':sys.version.split()[0],'adb':shutil.which('adb'),'display':bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))}
        try:import numpy;status['numpy']=numpy.__version__
        except ImportError:status['numpy']='Не установлен: sudo apt install python3-numpy'
        print(json.dumps(status,ensure_ascii=False,indent=2));return
    if args.port in (manager.config['cloud_port'],manager.config['board_port']):parser.error('Порт лаунчера должен отличаться от портов облака и платы')
    server,token=make_server(manager,args.port)
    url='http://127.0.0.1:%d/#token=%s'%(args.port,token)
    print('Лаунчер: '+url,flush=True)
    print('Без GUI: откройте SSH-туннель с другого компьютера, затем эту ссылку. Не публикуйте токен.',flush=True)
    print('Облако и плата работают отдельно. Плата: Arduino App CLI через ADB по USB.',flush=True)
    if args.start_cloud:manager.submit('cloud-start')
    if args.open:webbrowser.open(url)
    def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:server.serve_forever(poll_interval=.3)
    finally:server.server_close();manager.close()


if __name__=='__main__':main()
