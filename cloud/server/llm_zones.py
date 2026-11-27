"""Zonal routing/memory over one existing LLM, coupled to all board zones.
Not separate transformer weights: learned word prototypes condition training
and generation; live activations merge with board telemetry for scheduling.
"""
import collections
import hashlib
import math
import re
import threading
import time
import store

SEEDS={
 'Приветствие':'привет здравствуй разговор настроение общение',
 'Буквы':'буква слово язык текст пунктуация русский',
 'Знания':'знание статья наука история определение',
 'Сигнал':'сигнал частота генератор герц квант d6 d4',
 'Время':'время дата день часы когда',
 'Юмор':'шутка юмор смешно анекдот',
 'Чувства':'чувство эмоция радость грусть',
 'Помощь':'помоги помощь настрой установить исправить инструкция сервер',
 'Вопросы':'почему вопрос объясни причина как',
 'Обучение':'обучение обучить урок модель курс веса',
 'Память':'память запомни вспомни заметка',
 'Воображение':'придумай воображение рассказ фантазия',
 'Кирпичики':'код python программа функция алгоритм приложение applab javascript',
 'Любопытство':'поиск найди интернет исследуй новости',
 'Камера':'камера фото изображение зрение видео',
 'Математика':'математика вычисли посчитай формула число сумма',
}
_LOCK=threading.RLock()
_CACHE={'path':None,'data':None,'saved':0}
_LIVE={}
_RECENT=collections.deque(maxlen=24)   # v3.210: последние выбранные зоны


def _words(text):
    return [w[:6] for w in re.findall(r'[а-яёa-z0-9]{3,}',str(text).lower().replace('ё','е'))]


def _data():
    path=store.p('llm_zones.json')
    if _CACHE['path']!=path or _CACHE['data'] is None:
        data=store.load_json('llm_zones.json',{}) or {}
        _CACHE.update(path=path,data={'zones':data.get('zones',{}),'seen':data.get('seen',[])},saved=0)
        _LIVE.clear()
    return _CACHE['data']


def _scores(text,data):
    words=collections.Counter(_words(text))
    names=set(SEEDS)|set(data['zones'])
    scores={}
    for name in names:
        row=data['zones'].get(name,{})
        seed=set(_words(SEEDS.get(name,'')))
        learned=row.get('words',{})
        maximum=max(learned.values(),default=1)
        score=sum(min(n,3)*(2 if word in seed else min(1,learned.get(word,0)/maximum)) for word,n in words.items())
        scores[name]=score
    return scores


def route_multi(text,k=3,rel=.5):
    """v3.210: ГИБКАЯ маршрутизация: не одна зона, а до k зон, чьи оценки не
    ниже rel от лучшей (вопрос на стыке тем получает смешанный контекст)."""
    with _LOCK:
        scores=_scores(text,_data())
    top=max(scores.values(),default=0)
    if top<=0:
        return ['Знания']
    rows=sorted(((v,n) for n,v in scores.items() if v>=top*rel and v>0),reverse=True)
    return [n for _v,n in rows[:max(1,int(k))]]


def flexibility():
    """v3.210: ГИБКОСТЬ зонной LLM, 0…1. Складывается из ручной базы
    (настройка coupling_flex, 0…100, по умолчанию 40) и живых сигналов
    СОПРЯЖЕНИЯ: (а) застой — последние выборы зон почти одинаковы;
    (б) расхождение — зона, которую хочет плата (воля), не совпадает с зоной
    LLM; (в) слабый сигнал платы. Чем выше, тем шире зонный контекст
    (несколько зон), шире выбор слов/температура и тем охотнее LLM
    обучается на зонах платы. Границы безопасности и права не затрагивает."""
    st=store.settings()
    try:
        base=max(0.0,min(100.0,float(st.get('coupling_flex',40))))/100
    except (TypeError,ValueError):
        base=.4
    why=[]
    stagn=0.0
    if len(_RECENT)>=8:
        uniq=len(set(_RECENT))
        stagn=max(0.0,1-(uniq-1)/4)          # 1 зона на 24 выбора = 1.0
        if stagn>.5:why.append('застой зон')
    mism=0.0
    try:
        import brainwill
        bz=brainwill.state().get('zones') or {}
        if bz:
            btop=max(bz,key=bz.get)
            with _LOCK:
                lz=sorted(_LIVE.items(),key=lambda kv:-kv[1]['a'])
            if lz and lz[0][0]!=btop:
                mism=.6;why.append('плата хочет «%s», LLM — «%s»'%(btop,lz[0][0]))
        else:
            mism=.2;why.append('нет сигнала платы')
    except Exception:
        pass
    val=max(0.0,min(1.0,base+.25*stagn+.2*mism))
    return dict(flex=round(val,3),base=round(base,3),stagnation=round(stagn,3),
                mismatch=round(mism,3),why=why)


def choose(text):
    with _LOCK:
        scores=_scores(text,_data())
    return max(sorted(scores),key=lambda name:scores[name]) if max(scores.values(),default=0)>0 else 'Знания'


def observe(text,zone=None,source='training'):
    zone=zone or choose(text)
    signature=hashlib.sha256((zone+'\0'+str(text)).encode()).hexdigest()
    with _LOCK:
        data=_data()
        if signature in data['seen']:return zone
        row=data['zones'].setdefault(zone,{'examples':0,'words':{}})
        count=collections.Counter(row.get('words',{}));count.update(_words(str(text)[:6500]))
        row.update(examples=row.get('examples',0)+1,words=dict(count.most_common(256)),source=source)
        data['seen']=(data['seen']+[signature])[-12000:]
        if time.monotonic()-_CACHE['saved']>30:
            store.save_json('llm_zones.json',data);_CACHE['saved']=time.monotonic()
    try:                          # v3.210: сопряжение учит и соседние зоны
        if enabled() and flexibility()['flex']>.3:
            for z2 in route_multi(text,k=3,rel=.6)[1:]:
                if z2==zone:continue
                sig=hashlib.sha256((z2+'\0'+str(text)).encode()).hexdigest()
                with _LOCK:
                    data=_data()
                    if sig in data['seen']:continue
                    row=data['zones'].setdefault(z2,{'examples':0,'words':{}})
                    cnt=collections.Counter(row.get('words',{}))
                    cnt.update(_words(str(text)[:3000]))
                    row.update(words=dict(cnt.most_common(256)),source='flex')
                    data['seen']=(data['seen']+[sig])[-12000:]
            try:                  # и зоне, которую сейчас хочет плата
                import brainwill
                bz=brainwill.state().get('zones') or {}
                if bz:
                    bt=max(bz,key=bz.get)
                    if bt!=zone and bt in SEEDS:
                        with _LOCK:
                            row=_data()['zones'].setdefault(bt,{'examples':0,'words':{}})
                            cnt=collections.Counter(row.get('words',{}))
                            cnt.update(_words(str(text)[:1500]))
                            row['words']=dict(cnt.most_common(256))
            except Exception:
                pass
    except Exception:
        pass
    return zone


def route(text,zone=None):
    with _LOCK:
        data=_data();scores=_scores(text,data)
        if zone: scores[zone]=max(3,max(scores.values(),default=0))
        selected=zone or choose(text)
        _RECENT.append(selected)
        now=time.time()
        for name,score in scores.items():
            if score>0:_LIVE[name]={'a':score/(score+3),'ts':now}
        if not any(scores.values()):_LIVE[selected]={'a':.25,'ts':now}
    return selected


def combined(board=None):
    import brainwill
    board=board if board is not None else brainwill.state()
    settings=store.settings()
    on=bool(settings.get('llm_zones_on',True))
    mix=max(0,min(100,float(settings.get('coupling_zone_mix',50))))/100
    import llm_activity
    activity=llm_activity.snapshot()
    busy_zones={name for name,row in activity['zones'].items() if row['busy']}
    now=time.time()
    with _LOCK:
        data=_data()
        live={k:max(0,v['a'] if k in busy_zones else v['a']*(1-(now-v['ts'])/90)) for k,v in _LIVE.items() if k in busy_zones or 0<=now-v['ts']<90} if on else {}
        rows=[];values={}
        for name in sorted(set(SEEDS)|set(board.get('zones',{}))|set(data['zones'])|set(live)):
            b=board.get('zones',{}).get(name);l=live.get(name)
            unified=(1-mix)*b+mix*l if b is not None and l is not None else b if b is not None else l
            if unified is not None:values[name]=unified
            work=activity['zones'].get(name,{}) if on else {}
            rows.append(dict(zone=name,board=b,llm=l,unified=unified,
                             llm_busy=bool(work.get('busy')),llm_duty=work.get('duty'),
                             examples=data['zones'].get(name,{}).get('examples',0)))
    return dict(enabled=on,mix=mix,zones=rows,values=values,activity=activity,
                context_metric='Тематический сигнал маршрутизации (не загрузка модели)',
                architecture='одна LLM; зонные контексты и обучаемые словарные профили, общие веса')


def touch(zones):
    """An actual call finished: keep its context fresh without changing strength."""
    with _LOCK:
        for zone in zones:
            if zone in _LIVE:
                _LIVE[zone]['ts']=time.time()


def enabled():
    return bool(store.settings().get('llm_zones_on',True))


def condition(text):
    if not store.settings().get('llm_zones_on',True):return str(text),None
    zone=route(text)
    try:                          # v3.210: гибкий контекст — до 3 зон сразу
        fl=flexibility()['flex']
        if fl>.35:
            extra=[z for z in route_multi(text,k=1+round(2*fl),rel=.55) if z!=zone]
            if extra:
                for z in extra:route(text,zone=z)
                return 'Зона '+zone+' (рядом: '+', '.join(extra[:2])+'). '+str(text),zone
    except Exception:
        pass
    return 'Зона '+zone+'. '+str(text),zone
