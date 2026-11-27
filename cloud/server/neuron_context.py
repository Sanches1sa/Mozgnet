"""Fresh full-neuron context from authenticated /api/link; never oscillator text.
No model weights are modified by trying another generation.
"""
import math
import re
import threading
import time

_LOCK=threading.Condition()
_FRAMES={}
_WORD=re.compile(r'^[а-яёa-z0-9_-]{1,48}$',re.I)


def note(board,frame):
    if not isinstance(frame,dict) or not frame.get('fresh'):return False
    rows=frame.get('nodes')
    if not isinstance(rows,list) or len(rows)!=12:return False
    clean=[];seen=set()
    try:
        seq=int(frame['seq']);epoch=str(frame.get('session') or '')[:48]
        for row in rows:
            i=int(row['id']);spikes=int(row.get('context_spikes',row.get('spikes')) or 0);word=str(row.get('context_word',row.get('word')) or '').lower()
            if i not in range(12) or i in seen or not 0<=spikes<=1000000 or (word and not _WORD.fullmatch(word)):return False
            seen.add(i);clean.append(dict(id=i,online=row.get('online') is True,spikes=spikes,word=word))
    except (ValueError,KeyError,TypeError):return False
    source=str(board)[:40];key=(epoch,seq)
    with _LOCK:
        old=_FRAMES.get(source)
        if old and old['key']==key:return False
        if old and old['key'][0]==epoch and seq<=old['key'][1]:return False
        _FRAMES[source]=dict(key=key,nodes=clean,ts=time.monotonic())
        if len(_FRAMES)>100:
            oldest=min(_FRAMES,key=lambda name:_FRAMES[name]['ts']);del _FRAMES[oldest]
        _LOCK.notify_all()
    return True


def snapshot():
    now=time.monotonic();scores={};keys=[];boards=[]
    with _LOCK:frames=list(_FRAMES.items())
    for source,frame in frames:
        if not 0<=now-frame['ts']<15:continue
        keys.append((source,frame['key']));boards.append(source)
        for row in frame['nodes']:
            if row['online'] and row['spikes']>0 and row['word']:
                scores[row['word']]=scores.get(row['word'],0)+math.log1p(row['spikes'])
    return dict(key=tuple(sorted(keys)),boards=boards,words=[w for w,_ in sorted(scores.items(),key=lambda kv:-kv[1])][:48])


def valid_phrase(text):
    t=str(text or '').strip();words=re.findall(r'[а-яёa-z]+',t.lower())
    return len(words)>=3 and len(set(words))/len(words)>=.55 and bool(re.search(r'[.!?][»"\')]*$',t)) and not re.search(r'[,;:]\s*[.!?]|[,;:]{2}',t)


def generate(topic,call,validator=None,limit=6):
    """Only cold/new board contexts activate this loop; otherwise preserve caller."""
    frame=snapshot()
    if not frame['words']:return call(topic)
    last=None;attempts=[];started=time.monotonic()
    for i in range(max(1,min(6,int(limit)))):
        if i:
            with _LOCK:_LOCK.wait_for(lambda:snapshot()['key']!=last,timeout=1.0)
            frame=snapshot()
            if frame['key']==last or not frame['words']:break
        last=frame['key']
        prompt='Контекст нейронов: '+' '.join(frame['words'])+'. Задача: '+str(topic)
        text,info=call(prompt);info=dict(info or {})
        if info.get('llm')=='cold':return text,info
        overlap={w[:5] for w in frame['words']} & {w[:5] for w in re.findall(r'[а-яёa-z]{3,}',str(text).lower())}
        ok=bool(overlap) and valid_phrase(text) and (validator(text) if validator else True)
        attempts.append(dict(round=i+1,boards=frame['boards'],context=frame['words'],accepted=bool(ok)))
        if ok:
            info['neuron_reflection']=dict(attempts=attempts,round=i+1,limit=6,accepted=True)
            return text,info
        if time.monotonic()-started>12:break
    return '',{'llm':'no_coherent_neuron_phrase','neuron_reflection':dict(attempts=attempts,limit=6,accepted=False)}
