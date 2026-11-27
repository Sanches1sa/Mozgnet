"""Bounded reflection over fresh neuron telemetry. Bridge transport never waits.
The local zonal/tensor networks score candidates; punctuation/chain checks are
structural checks, not proof of semantics or an unbounded self-training loop.
"""
import collections
import copy
import math
import re
import threading
import time
import uuid

# v2.107.0: нейрон-платы (классические Arduino) умеют только ASCII и шлют слова
# ТРАНСЛИТОМ (privet, gotov, volna...). Без перевода в русский контекст пуст,
# а программные нейроны учатся на латинице — мозг «пишет на латыни».
_FIXED_RU={'privet':'привет','zdes':'здесь','slychu':'слышу','gotov':'готов','tok':'ток','volna':'волна',
 'vopros':'вопрос','status':'статус','kamera':'камера','pamyat':'память','signal':'сигнал','umor':'юмор',
 'pomosch':'помощь','eho':'эхо','oblako':'облако','obuchenie':'обучение','pokoy':'покой','tohna':'точно',
 'tochno':'точно','da':'да','net':'нет','ok':'хорошо'}
_DIGR=(('shch','щ'),('sch','щ'),('yo','ё'),('zh','ж'),('kh','х'),('ts','ц'),('ch','ч'),('sh','ш'),('yu','ю'),('ya','я'),('ye','е'),('ju','ю'),('ja','я'),('jo','ё'))
_SINGLE=dict(zip('abvgdezijklmnoprstufhcyw','абвгдезийклмнопрстуфхцыв'))
def to_russian(word):
    """Латинский транслит -> русское слово. Кириллицу не трогает; не распознанное — ''."""
    w=str(word or '').strip().lower()
    if not w:return ''
    if re.search(r'[а-яё]',w):return w if not re.search(r'[a-z]',w) else ''
    if w in _FIXED_RU:return _FIXED_RU[w]
    out=[];i=0
    while i<len(w):
        for lat,ru in _DIGR:
            if w.startswith(lat,i):out.append(ru);i+=len(lat);break
        else:
            ch=w[i];i+=1
            if ch in _SINGLE:out.append(_SINGLE[ch])
            elif ch.isdigit() or ch in '_-':out.append(ch)
            elif ch=='q':out.append('к')
            elif ch=='x':out.append('кс')
    return ''.join(out)

class NeuronBus:
    def __init__(self):
        self.cv=threading.Condition();self.session=uuid.uuid4().hex;self.seq=0;self.ts=0;self.history=[collections.deque() for _ in range(12)];self.nodes=[dict(id=i,online=False,spikes=0,word='',pulse=0) for i in range(12)];self.rejected=0;self.last_error='';self.last_packet=''
    def reject(self,error,packet=''):
        """Пакет on_neurons отклонён: запоминаем причину, чтобы UI показал её, а не «нет снимка»."""
        with self.cv:self.rejected+=1;self.last_error=str(error)[:120];self.last_packet=str(packet)[:160]
    def ingest(self,packet):
        parts=str(packet).split('|');rows={}
        for part in parts:
            if not part:continue
            m=re.fullmatch(r'N(\d{1,2}):([01]):(\d{1,10}):(.*)',part,re.S)
            if not m:raise ValueError('Malformed neuron telemetry')
            i=int(m[1])
            if not 0<=i<12 or i in rows:raise ValueError('Duplicate/invalid neuron ID')
            # v2.98.2: слово нейрона может содержать знаки/пробелы/мусор — вычищаем, а не бросаем весь кадр
            word=re.sub(r'[^а-яёa-zA-ZА-ЯЁ0-9_-]','',m[4])[:48]
            raw=word.lower();rows[i]=dict(id=i,online=m[2]=='1',spikes=min(1000000,int(m[3])),word=to_russian(raw),raw=raw)
        if len(rows)!=12:raise ValueError('All 12 neuron slots are required')
        now=time.monotonic()
        with self.cv:
            for i,row in rows.items():
                row['pulse']=self.nodes[i]['pulse']+(1 if row['online'] and row['spikes']>0 else 0)
                if row['online'] and row['spikes']>0:self.history[i].append((now,row['spikes'],row['word']))
            self.nodes=[rows[i] for i in range(12)];self.seq+=1;self.ts=now;self.cv.notify_all()
    def snapshot(self,after=None,timeout=.8):
        with self.cv:
            if after is not None:self.cv.wait_for(lambda:self.seq>after,timeout)
            age=time.monotonic()-self.ts if self.ts else None
            fresh=age is not None and 0<=age<4
            nodes=[]
            now=time.monotonic()
            for row,history in zip(self.nodes,self.history):
                while history and now-history[0][0]>3:history.popleft()
                nodes.append(dict(row,online=bool(row['online'] and fresh),spikes=row['spikes'] if fresh else 0,word=row['word'] if fresh else '',context_spikes=min(1000000,sum(r[1] for r in history)) if fresh and row['online'] else 0,context_word=history[-1][2] if history and fresh and row['online'] else ''))
            return dict(seq=self.seq,session=self.session,fresh=fresh,rejected=self.rejected,last_error=self.last_error,age=round(age,2) if age is not None else None,context_window_seconds=3,nodes=nodes)

BUS=NeuronBus()
_LOCK=threading.Lock();_STATE_LOCK=threading.Lock()
_STATE={'phase':'idle','round':0,'limit':6,'attempts':[],'candidate_buffer':[],'accepted':'','reason':''}
_PUNCT={}

def learn_punctuation(texts):
    counts=collections.defaultdict(collections.Counter)
    for text in texts:
        tokens=re.findall(r'[а-яёa-z]+|[,;:.!?]',str(text).lower())
        for i in range(len(tokens)-2):
            if tokens[i].isalpha() and tokens[i+1] in ',;:' and tokens[i+2].isalpha():counts[(tokens[i],tokens[i+2])][tokens[i+1]]+=1
    with _STATE_LOCK:_PUNCT.clear();_PUNCT.update({k:v.most_common(1)[0][0] for k,v in counts.items()})

def surface(text,zone=''):
    # v2.107.0: мозг говорит по-русски — латинские токены (старый мусор в памяти) не выводим
    words=[w for w in re.findall(r'[а-яёa-z]+',str(text).lower()) if re.search(r'[а-яё]',w)]
    if not words:return ''
    with _STATE_LOCK:punct=dict(_PUNCT)
    out=[]
    for i,w in enumerate(words):
        out.append(w)
        if i+1<len(words) and (w,words[i+1]) in punct and len(out)>1:out[-1]+=punct[(w,words[i+1])]
    end='?' if words[0] in {'кто','что','почему','зачем','когда','где','куда','сколько'} or zone=='Вопросы' else '.'
    phrase=' '.join(out)+end
    return phrase[0].upper()+phrase[1:]

def check(phrase,context,known,successors):
    words=re.findall(r'[а-яёa-z]+',str(phrase).lower())
    if len(words)<3:return False,'меньше трёх слов'
    if any(not known(w) for w in words):return False,'слово вне словаря'
    if any(a==b for a,b in zip(words,words[1:])) or len(set(words))/len(words)<.55:return False,'повтор слов'
    ctx={str(w).lower()[:5] for w,_ in context}
    if not ctx.intersection(w[:5] for w in words):return False,'нет связи с контекстом нейронов'
    edges=sum(b in successors(a) for a,b in zip(words,words[1:]))
    if edges/max(1,len(words)-1)<.85:return False,'пары слов не подтверждены изученными текстами'
    if not phrase.endswith(('.','!','?')) or re.search(r'[,;:]\s*[.!?]|[,;:]{2}',phrase):return False,'незавершённая пунктуация'
    return True,'структурная проверка пройдена; смысл и факты не гарантированы'

def status():
    with _STATE_LOCK:return copy.deepcopy(_STATE)

def reflect(snapshot,compose,pick,known,successors,limit=6,seconds=6,target_chars=0,max_chars=3800,fallback_words=False,pool=None):
    if not _LOCK.acquire(False):return '',{'phase':'busy'}
    deadline=time.monotonic()+max(.5,min(12,float(seconds)))
    limit=max(1,min(6,int(limit)))
    try:
        with _STATE_LOCK:_STATE.update(phase='collecting',round=0,limit=limit,attempts=[],candidate_buffer=[],accepted='',reason='')
        seq=None;buffer=[];used_contexts=[];accepted=[];last_meta={};last_context=[]
        for step in range(1,limit+1):
            if time.monotonic()>=deadline:break
            frame=snapshot(seq,min(.8,max(0,deadline-time.monotonic())))
            if not frame.get('fresh') or frame.get('seq')==seq:
                why='Нет нового полного снимка нейронов; повторный контекст не выдаётся за новый'
                if not frame.get('fresh'):
                    why+=(' (пакетов отклонено: %s, последняя причина: %s)'%(frame.get('rejected',0),frame.get('last_error'))) if frame.get('rejected') else ' (пакеты on_neurons от скетча не приходят)'
                with _STATE_LOCK:_STATE.update(reason=why)
                break
            seq=frame['seq'];context=frame.get('words') or [];last_context=context or last_context
            with _STATE_LOCK:_STATE.update(round=step,phase='assembling')
            if not context:
                used_contexts.append({'round':step,'seq':seq,'physical_online':frame.get('physical_online',0),'software_neurons':frame.get('software_neurons',0),'context':[],'valid':0,'reason':'Нейроны не дали словесного контекста'})
                with _STATE_LOCK:_STATE['attempts']=list(used_contexts)
                continue
            candidates=[];rejected=[];seen_candidates=set()
            for _ in range(6):
                phrase=surface(compose(frame),frame.get('zone',''))
                if not phrase or phrase in seen_candidates:continue
                seen_candidates.add(phrase)
                if phrase not in buffer:buffer.append(phrase)
                ok,why=check(phrase,context,known,successors)
                if ok:candidates.append(phrase)
                else:rejected.append(why)
            record={'round':step,'seq':seq,'context':[w for w,_ in context[:24]],'physical_online':frame.get('physical_online',0),'software_neurons':frame.get('software_neurons',0),'valid':len(candidates),'rejected':rejected[:6]}
            if candidates:
                phrase,meta=pick(candidates,frame.get('zone',''))
                if not meta.get('by') or meta.get('by')=='первый вариант' or not math.isfinite(float(meta.get('score',0))):record['reason']='Локальная нейросеть ещё не дала обученного выбора'
                else:
                    prior=set(' '.join(accepted).lower().split())
                    tokens=set(phrase.lower().split())
                    duplicate=phrase in accepted or (bool(prior) and len(prior&tokens)/max(1,len(tokens))>.8)
                    if not duplicate and len(' '.join(accepted+[phrase]))<=max_chars:
                        accepted.append(phrase);last_meta=meta
                    record['accepted_sentences']=len(accepted)
                    used_contexts.append(record)
                    result=' '.join(accepted)
                    with _STATE_LOCK:_STATE.update(phase='collecting_text',accepted=result,attempts=list(used_contexts),candidate_buffer=buffer[-36:],reason='Предложений: '+str(len(accepted)))
                    if result and len(result)>=target_chars:
                        with _STATE_LOCK:_STATE['phase']='accepted'
                        return result,meta
                    continue
            used_contexts.append(record)
            with _STATE_LOCK:_STATE.update(attempts=list(used_contexts),candidate_buffer=buffer[-36:])
        if accepted:
            result=' '.join(accepted)
            with _STATE_LOCK:_STATE.update(phase='accepted',accepted=result,reason='Принят проверенный текст; достигнут лимит проходов, без дополнения случайными словами')
            return result,last_meta
        if fallback_words:
            # v2.107.0: проверка фразы не прошла — лучше выдать СЛОВА мозга (русские, из словаря),
            # честно помечено как неподтверждённое, чем молчать.
            words=[]
            for cand in reversed(buffer):
                ws=[w for w in re.findall(r'[а-яё]+',cand.lower()) if known(w)]
                if len(ws)>=2:words=ws;break
            if not words:
                ctx=[w for w,_ in (last_context or []) if re.search(r'[а-яё]',w) and known(w)]
                words=ctx[:10]
            if not words and pool:
                try:words=[w for w in pool() if re.search(r'[а-яё]',w) and known(w)][:8]
                except Exception:words=[]
            if words:
                seen=set();uniq=[w for w in words if not (w in seen or seen.add(w))]
                text=surface(' '.join(uniq[:12]),'')
                if text:
                    with _STATE_LOCK:_STATE.update(phase='words',accepted=text,reason='Проверка фразы не прошла — выдаю слова мозга без подтверждения смысла')
                    return text,{'by':'слова мозга','score':0.0,'unverified':True}
        with _STATE_LOCK:_STATE['phase']='no_phrase';_STATE['reason']=_STATE['reason'] or 'Лимит попыток исчерпан; неподтверждённая фраза не опубликована'
        return '',status()
    finally:_LOCK.release()
