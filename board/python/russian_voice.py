"""Serialized Russian PCM speech. Does not equate MCU tones with spoken words."""
import io
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

MAX_TEXT=3800

def clean_text(text):
    text=re.sub(r'```[^\n]*\n[\s\S]*?```',' ',str(text or ''))
    text=re.sub(r'[_*`#]','',text)
    return ' '.join(text.split())[:MAX_TEXT]


def chunks(text,limit=260):
    text=clean_text(text);out=[];current=''
    for word in text.split():
        if len(current)+len(word)+1>limit and current:out.append(current);current=''
        while len(word)>limit:
            if current:out.append(current);current=''
            out.append(word[:limit]);word=word[limit:]
        current=(current+' '+word).strip()
    if current:out.append(current)
    return out


def pcm_seconds(data):
    with wave.open(io.BytesIO(data),'rb') as w:
        if not w.getframerate() or w.getnframes()<1:raise ValueError('Пустой WAV')
        return w.getnframes()/w.getframerate()


def local_wav(text):
    exe=shutil.which('espeak-ng') or shutil.which('espeak')
    if not exe:raise RuntimeError('На плате не установлен espeak-ng с русским голосом')
    with tempfile.TemporaryDirectory(prefix='neuro12-ru-') as d:
        target=Path(d)/'speech.wav'
        result=subprocess.run([exe,'-v','ru','-s','150','-w',str(target),'--stdin'],input=text.encode('utf-8'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
        if result.returncode:raise RuntimeError(result.stderr.decode('utf-8','replace')[-300:])
        data=target.read_bytes();pcm_seconds(data);return data


class RussianSpeaker:
    def __init__(self,cloud,player,browser,event,enabled=lambda:True,local=local_wav):
        self.cloud=cloud;self.player=player;self.browser=browser;self.event=event;self.enabled=enabled;self.local=local
        self.submit_lock=threading.Lock();self.queue=queue.Queue(maxsize=4);self.last_error='';self.thread=threading.Thread(target=self._worker,daemon=True);self.thread.start()
    def submit(self,text):
        text=clean_text(text)
        if not text:return {'ok':False,'error':'Пустой текст для озвучки'}
        if not self.enabled():return {'ok':False,'error':'Озвучка выключена'}
        with self.submit_lock:
            if self.queue.full():return {'ok':False,'error':'Очередь озвучки заполнена; повторите позже'}
            self.event(phase='queued',chars=len(text),pending=self.queue.qsize()+1,language='ru',completed=False)
            self.queue.put_nowait(text)
        return {'ok':True,'queued':True,'chars':len(text)}
    def _worker(self):
        while True:
            text=self.queue.get()
            try:
                parts=chunks(text);browser_count=0
                for index,part in enumerate(parts,1):
                    if not self.enabled():self.event(phase='cancelled',completed=False);break
                    self.event(phase='synthesizing',chunk=index,chunks=len(parts),chars=len(text),language='ru',completed=False,err='')
                    try:data=self.local(part);engine='espeak-ru-local'
                    except Exception as local_error:
                        data,error=self.cloud(part)
                        if not data:raise RuntimeError(str(error or local_error))
                        engine='russian-cloud'
                    duration=pcm_seconds(data)
                    self.event(phase='playing',engine=engine,duration=duration,chunk=index,chunks=len(parts),completed=False)
                    if not self.player(data):
                        self.browser(data);browser_count+=1
                    self.event(phase='browser_pending' if browser_count else 'speaking',via='browser' if browser_count else 'board',engine=engine,chunk=index,chunks=len(parts),completed=False)
                else:self.event(phase='browser_pending' if browser_count else 'done',via='browser' if browser_count else 'board',completed=not bool(browser_count),available=True,err='')
            except Exception as e:self.last_error=str(e);self.event(phase='error',available=False,completed=False,err=self.last_error)
            finally:self.queue.task_done()
