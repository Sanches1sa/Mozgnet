"""Local, unprivileged manager for neuro12-cloud and USB-connected UNO Q.
No user-supplied shell scripts or sudo commands are accepted by the web API.
"""
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile


def valid_serial(value):
    value=str(value or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',value) or value.startswith('-'):
        raise ValueError('Выберите серийный номер UNO Q из списка ADB')
    return value


def valid_app(value):
    value=str(value or '').strip()
    if not re.fullmatch(r'user:[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}',value):
        raise ValueError('Укажите ID приложения из списка: user:neuro_12')
    return value


def port(value):
    value=int(value)
    if not 1024<=value<=65535:raise ValueError('Порт должен быть 1024–65535')
    return value


def adb_command(serial,*args):
    # adb shell accepts one safely quoted remote command string.
    return ['adb','-s',valid_serial(serial),'shell',shlex.join(list(args))]


def validate_zip(path):
    with zipfile.ZipFile(path) as z:
        if len(z.infolist())>30000:raise ValueError('Слишком много файлов в ZIP')
        total=0;manifests=[]
        for info in z.infolist():
            name=info.filename.replace('\\','/');p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\x00' in name or (p.parts and ':' in p.parts[0]):
                raise ValueError('Недопустимый путь в ZIP')
            if info.flag_bits&1:raise ValueError('ZIP с паролем не поддерживается')
            if (info.external_attr>>16)&0o170000==0o120000:raise ValueError('Символические ссылки в ZIP не разрешены')
            total+=info.file_size
            if total>1024**3:raise ValueError('Распакованный проект больше 1 ГБ')
            if p.name=='app.yaml':manifests.append(name)
        if len(manifests)!=1:raise ValueError('Нужен ZIP одного приложения App Lab с одним app.yaml')
        if z.testzip() is not None:raise ValueError('ZIP повреждён')
    return manifests[0]


class Manager:
    def __init__(self,root,runner=None):
        self.root=Path(root).resolve()
        if not (self.root/'server/app.py').is_file():raise ValueError('Не найден server/app.py в каталоге облака')
        self.state=Path(os.environ.get('NEURO_LAUNCHER_DATA') or self.root/'data/linux_launcher')
        self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.runner=runner or self.run
        self.config={'serial':'','app':'user:neuro_12','cloud_port':8077,'board_port':7000}
        try:self.configure(json.loads((self.state/'config.json').read_text()),save=False)
        except (OSError,ValueError,TypeError):pass
        self.process=None;self.lock=threading.RLock();self.jobs={};self.commands=set();self.queue_lock=threading.Lock();self.closed=False

    def configure(self,b,save=True):
        c=dict(self.config)
        for k in ('serial','app','cloud_port','board_port'):
            if k not in b:continue
            c[k]=valid_serial(b[k]) if k=='serial' and b[k] else '' if k=='serial' else valid_app(b[k]) if k=='app' else port(b[k])
        if c['cloud_port']==c['board_port']:raise ValueError('Порты облака и платы должны отличаться')
        if getattr(self,'process',None) is not None and self.process.poll() is None and c['cloud_port']!=self.config['cloud_port']:
            raise ValueError('Сначала остановите облако перед сменой порта')
        self.config=c
        if save:
            tmp=self.state/'config.tmp';tmp.write_text(json.dumps(c,ensure_ascii=False,indent=2));os.chmod(tmp,0o600);tmp.replace(self.state/'config.json')
        return c

    def run(self,args,timeout=60):
        if self.closed:raise RuntimeError('Лаунчер завершает работу')
        with tempfile.TemporaryFile() as out:
            try:
                proc=subprocess.Popen(args,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
            except FileNotFoundError as e:raise RuntimeError('Не найдена программа '+args[0]+'. Установите зависимости по инструкции.') from e
            with self.lock:self.commands.add(proc)
            try:code=proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                raise RuntimeError('Истёк срок ожидания команды; проверьте состояние платы перед повтором')
            finally:
                with self.lock:self.commands.discard(proc)
            out.seek(0,2);length=out.tell();out.seek(max(0,length-64000));text=out.read().decode('utf-8','replace')
            if code:raise RuntimeError(('Команда завершилась с кодом %d\n'%code)+text)
            return text

    def health(self):
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:%s/api/health'%self.config['cloud_port'],timeout=2) as r:
                data=json.loads(r.read(256000))
            return bool(data.get('ok')) and data.get('service')=='neuro12-cloud',data
        except Exception:return False,{}

    def status(self):
        ok,health=self.health()
        with self.lock:
            return {'ok':True,'config':dict(self.config),'cloud':{'online':ok,'starting':bool(health.get('starting')) if ok else False,'owned':self.process is not None and self.process.poll() is None,'pid':self.process.pid if self.process is not None and self.process.poll() is None else None},'adb':bool(shutil.which('adb')),'jobs':list(self.jobs.values())[-20:]}

    def cloud_start(self,c):
        with self.lock:
            if self.process is not None and self.process.poll() is None:return 'Процесс облака уже запущен этим лаунчером'
            if self.health()[0]:return 'Облако уже отвечает. Новый процесс не запущен; внешней службой этот лаунчер не управляет.'
            import socket
            with socket.socket() as sock:
                try:sock.bind(('127.0.0.1',c['cloud_port']))
                except OSError:raise RuntimeError('Порт облака занят. Проверьте существующий процесс.')
            env=dict(os.environ,NEURO_PORT=str(c['cloud_port']),NEURO_DATA=str(self.root/'data'))
            python=self.root/'.venv/bin/python3';python=str(python) if python.exists() else sys.executable
            with (self.state/'cloud.log').open('ab') as log:
                self.process=subprocess.Popen([python,str(self.root/'server/app.py')],cwd=self.root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        for _ in range(40):
            healthy,details=self.health()
            if healthy:return 'Облако отвечает на /api/health'+(' · сервер ещё инициализируется; готовность видна в панели' if details.get('starting') else ' · сервер готов')
            if self.process.poll() is not None:raise RuntimeError('Процесс облака завершился. Откройте журнал.')
            time.sleep(.25)
        return 'Процесс запущен; сервер пока не ответил за 10 секунд. Ожидайте изменения статуса и проверьте журнал. Второй процесс не создан.'

    def cloud_stop(self,c):
        with self.lock:
            if self.process is None or self.process.poll() is not None:return 'Нет собственного процесса для остановки. Если облако запущено как служба: sudo systemctl stop neuro12-cloud'
            os.killpg(self.process.pid,signal.SIGTERM)
            try:self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:raise RuntimeError('Облако ещё завершает работу. Принудительное завершение не выполнялось.')
        return 'Процесс облака остановлен'

    def execute(self,action,c,upload=None):
        if action=='cloud-start':return self.cloud_start(c)
        if action=='cloud-stop':return self.cloud_stop(c)
        if action=='cloud-log':
            p=self.state/'cloud.log'
            if not p.exists():return 'Журнал управляемого облака пока пуст. Для службы: journalctl -u neuro12-cloud -n 100'
            with p.open('rb') as f:f.seek(0,2);f.seek(max(0,f.tell()-64000));return f.read().decode('utf-8','replace')
        if action=='devices':return self.runner(['adb','devices','-l'],timeout=20)
        serial=valid_serial(c['serial'])
        if action=='board-check':return self.runner(adb_command(serial,'sh','-lc','id; command -v arduino-app-cli; arduino-app-cli version'),timeout=30)
        if action=='board-apps':return self.runner(adb_command(serial,'arduino-app-cli','app','list'),timeout=30)
        if action in ('board-start','board-stop'):
            return self.runner(adb_command(serial,'arduino-app-cli','app','start' if action=='board-start' else 'stop',valid_app(c['app'])),timeout=900)
        if action=='board-autostart':return self.runner(adb_command(serial,'arduino-app-cli','properties','set','default',valid_app(c['app'])),timeout=30)
        if action=='board-forward':
            # --no-rebind refuses an existing mapping, including one owned by another board.
            return self.runner(['adb','-s',serial,'forward','--no-rebind','tcp:'+str(c['board_port']),'tcp:7000'],timeout=20) or 'Порт интерфейса платы проброшен на Ubuntu'
        if action=='cloud-reverse':return self.runner(['adb','-s',serial,'reverse','--no-rebind','tcp:'+str(c['cloud_port']),'tcp:'+str(c['cloud_port'])],timeout=20) or 'USB reverse создан. Доступ из контейнера приложения нужно проверить отдельно.'
        if action=='board-import':
            if upload is None:raise ValueError('Выберите ZIP приложения платы')
            validate_zip(upload);remote='/tmp/neuro12-'+uuid.uuid4().hex+'.zip'
            try:
                self.runner(['adb','-s',serial,'push',str(upload),remote],timeout=180)
                return self.runner(adb_command(serial,'arduino-app-cli','app','import',remote),timeout=900)
            finally:
                try:self.runner(adb_command(serial,'rm','-f','--',remote),timeout=20)
                except Exception:pass
        raise ValueError('Неизвестное действие')

    def submit(self,action,upload=None):
        allowed={'cloud-start','cloud-stop','cloud-log','devices','board-check','board-apps','board-start','board-stop','board-autostart','board-forward','cloud-reverse','board-import'}
        if action not in allowed:raise ValueError('Неизвестное действие')
        if not self.queue_lock.acquire(blocking=False):raise ValueError('Предыдущее действие ещё выполняется; дождитесь результата')
        jid=uuid.uuid4().hex;config=dict(self.config)
        with self.lock:
            self.jobs[jid]={'id':jid,'action':action,'status':'running','output':'Выполняется…'}
            for old in list(self.jobs)[:-20]:del self.jobs[old]
        def work():
            try:
                result=self.execute(action,config,upload)
                with self.lock:self.jobs[jid].update(status='done',output=result)
            except Exception as e:
                with self.lock:self.jobs[jid].update(status='error',output=str(e))
            finally:
                if upload:
                    try:Path(upload).unlink()
                    except OSError:pass
                self.queue_lock.release()
        threading.Thread(target=work,daemon=True).start()
        return jid

    def close(self):
        # Only the process created by this manager may be stopped.
        self.closed=True
        with self.lock:
            for proc in list(self.commands):
                if proc.poll() is None:
                    try:os.killpg(proc.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
        if self.process is not None and self.process.poll() is None:self.cloud_stop(self.config)
