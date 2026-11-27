"""Trainable pixel-to-character head for the Camera zone. No Tesseract labels.
MVP: cropped horizontal printed text, separated glyphs, supervised corrections.
The text LLM shares the resulting evidence, not the image encoder weights.
"""
import base64
import hashlib
import io
import os
import threading
import time
import store

_LOCK = threading.RLock()
_STATE = {'running': False}
_IMPORTING = False


def _deps():
    if os.environ.get("NEURO_SAFE") == "1":
        raise RuntimeError("безопасный режим: распознавание отключено после падений")
    import numpy as np
    from PIL import Image, ImageOps
    return np, Image, ImageOps


def _runs(values):
    out = []
    for i, active in enumerate(list(values) + [False]):
        if active and (i == 0 or not values[i-1]): start = i
        if not active and i and values[i-1]: out.append((start, i))
    return out


def _mask(raw):
    np, Image, ImageOps = _deps()
    if len(raw) > 8_000_000: raise ValueError('Фото больше 8 МБ')
    im = Image.open(io.BytesIO(raw))
    if im.width * im.height > 12_000_000: raise ValueError('Фото больше 12 Мп; обрежьте строку')
    im = ImageOps.exif_transpose(im)
    if im.mode in ('RGBA','LA') or 'transparency' in im.info:
        rgba=im.convert('RGBA');bg=Image.new('RGBA',rgba.size,'white');bg.alpha_composite(rgba);im=bg.convert('L')
    else:im=im.convert('L')
    im.thumbnail((2400, 1600))
    a = np.asarray(im, dtype=np.float32)
    if float(a.max()-a.min()) < 25: raise ValueError('Не найден контрастный текст')
    # Otsu threshold; supports light text on a dark background.
    hist = np.bincount(a.astype('uint8').ravel(), minlength=256)
    prob = hist / hist.sum(); omega = prob.cumsum(); mu = (prob*np.arange(256)).cumsum()
    threshold = int(np.argmax((mu[-1]*omega-mu)**2 / (omega*(1-omega)+1e-12)))
    ink = a <= threshold
    if ink.mean() > .5: ink = ~ink
    return ink


def _feature(ink):
    np,Image,_=_deps()
    ys,xs=np.where(ink)
    if len(xs)<4:raise ValueError('Слишком мало штрихов символа')
    crop=ink[ys.min():ys.max()+1,xs.min():xs.max()+1]
    glyph=Image.fromarray((crop*255).astype('uint8'))
    glyph.thumbnail((18,22))
    canvas=Image.new('L',(20,24));canvas.paste(glyph,((20-glyph.width)//2,(24-glyph.height)//2))
    return np.asarray(canvas,dtype=np.float32).ravel()/255


def segment(raw):
    np, Image, ImageOps = _deps()
    ink=_mask(raw)
    lines = []
    for y0,y1 in _runs(ink.any(axis=1)):
        if y1-y0 < 3: continue
        row = ink[y0:y1]
        boxes = _runs(row.any(axis=0))
        glyphs=[]
        for x0,x1 in boxes:
            if (x1-x0)*(y1-y0)<4: continue
            crop=row[:,x0:x1]; ys=np.where(crop.any(axis=1))[0]
            if not len(ys) or int(crop.sum())<4:continue
            crop=crop[ys[0]:ys[-1]+1]
            glyphs.append((x0,x1,_feature(crop)))
        if glyphs:lines.append(glyphs)
    if not lines:raise ValueError('Символы не выделены; обрежьте фото до одной печатной строки')
    return lines


def add(raw, text, source="user", sample_id=None, single=False, split=None):
    text=str(text).strip()
    if not text or len(text)>500:raise ValueError('Нужна правильная расшифровка до 500 знаков')
    if single:
        if len(text)!=1:raise ValueError('В режиме одного символа нужна ровно одна буква')
        lines=[[(0,1,_feature(_mask(raw)))]]
    else:lines=segment(raw)
    labels=text.splitlines()
    if len(lines)!=len(labels):raise ValueError('Число выделенных строк не совпало. Загружайте обрезанную строку без рамок')
    chars=[];features=[]
    for glyphs,label in zip(lines,labels):
        target=[c for c in label if not c.isspace()]
        if len(glyphs)!=len(target):raise ValueError('Выделено %d символов, в подписи %d. Нужен печатный текст с раздельными буквами; тесные буквы, точки и диакритика пока не поддерживаются' % (len(glyphs),len(target)))
        chars.extend(target);features.extend(g[2].tolist() for g in glyphs)
    key=hashlib.sha256(raw).hexdigest()
    with _LOCK:
        data=store.load_json('photo_ocr_samples.json',[]) or []
        for row in data:
            if row['id']==key:
                if source!='user' and row['chars']!=chars:raise ValueError('Конфликт меток одинакового изображения; пример пропущен')
                row.update(chars=chars,x=features,text=text)
                store.save_json('photo_ocr_samples.json',data)
                return dict(ok=True,corrected=True,samples=len(data))
        if len(data)>=500:raise ValueError('Достигнут предел 500 фотографий учебного набора')
        data.append(dict(id=key,chars=chars,x=features,text=text,source=source,sample_id=sample_id,split=split if split in ('validation','train') else 'validation' if len(data)%5==0 else 'train'))
        store.save_json('photo_ocr_samples.json',data)
    return dict(ok=True,samples=len(data),characters=len(chars))


def status():
    with _LOCK:
        return dict(_STATE, samples=len(store.load_json('photo_ocr_samples.json',[]) or []),
                    model=store.load_json('photo_ocr_metrics.json',{}) or {},
                    architecture='обучаемая MLP по пикселям символов; отдельная голова зоны Камера, общая текстовая LLM использует её результат')


def train(epochs=80):
    epochs=max(1,min(300,int(epochs)))
    with _LOCK:
        if _IMPORTING:raise ValueError('Дождитесь завершения импорта CoMNIST')
        if _STATE['running']:raise ValueError('Обучение уже идёт')
        data=store.load_json('photo_ocr_samples.json',[]) or []
        if len(data)<3:raise ValueError('Добавьте минимум 3 разных фотографии с расшифровками; лучше десятки примеров каждого символа')
        _STATE.update(running=True,error='',epoch=0,epochs=epochs,percent=0,phase='prepare',started=time.time(),elapsed_seconds=0,eta_seconds=None,loss=None)
    threading.Thread(target=_train,args=(data,epochs),daemon=True).start()
    return dict(ok=True,running=True)


def _train(data,epochs):
    try:
        np,_,_=_deps();rng=np.random.default_rng(12)
        # Hold out complete photos, never random characters from the same photo.
        val=[r for r in data if r.get('split')=='validation']
        learn=[r for r in data if r.get('split')!='validation']
        if not val or not learn:raise ValueError('Нужны учебные и проверочные фотографии')
        model=store.p('photo_ocr.npz')
        old_weights=None
        if os.path.exists(model):
            with np.load(model,allow_pickle=False) as old:
                old_weights={k:old[k].copy() for k in ('w','b','v','d','alphabet')}
        alphabet=sorted(set(c for r in learn for c in r['chars']) | (set(old_weights['alphabet']) if old_weights else set()))
        if len(alphabet)<2:raise ValueError('Нужны минимум два разных символа в обучающей части')
        index={c:i for i,c in enumerate(alphabet)}
        x=np.array([v for r in learn for v in r['x']],dtype=np.float32)
        y=np.array([index[c] for r in learn for c in r['chars']])
        if len(x)>30000:raise ValueError('Слишком много символов для одного курса')
        w=rng.normal(0,.05,(480,96)).astype(np.float32);b=np.zeros(96,dtype=np.float32)
        v=rng.normal(0,.05,(96,len(alphabet))).astype(np.float32);d=np.zeros(len(alphabet),dtype=np.float32)
        # Preserve learned visual features and old output columns as vocabulary grows.
        if old_weights:
            w,b=old_weights['w'],old_weights['b']
            for j,char in enumerate(old_weights['alphabet']):
                k=index[str(char)];v[:,k]=old_weights['v'][:,j];d[k]=old_weights['d'][j]
        started=time.monotonic()
        for epoch in range(epochs):
            epoch_loss=0.;seen=0
            batches=np.array_split(rng.permutation(len(x)),max(1,(len(x)+31)//32))
            for batch_i,batch in enumerate(batches):
                xx=x[batch]; yy=y[batch];h=np.tanh(xx@w+b);logits=h@v+d
                p=np.exp(logits-logits.max(axis=1,keepdims=True));p/=p.sum(axis=1,keepdims=True)
                epoch_loss+=float(-np.log(np.maximum(p[np.arange(len(batch)),yy],1e-9)).sum());seen+=len(batch)
                done=epoch+(batch_i+1)/len(batches);elapsed=time.monotonic()-started
                with _LOCK:_STATE.update(phase='training',percent=round(95*done/epochs,1),elapsed_seconds=round(elapsed,1),eta_seconds=round(elapsed*(epochs-done)/done,1),loss=round(epoch_loss/max(1,seen),5))
                p[np.arange(len(batch)),yy]-=1;p/=len(batch)
                dh=(p@v.T)*(1-h*h)
                w-=.15*(xx.T@dh);b-=.15*dh.sum(axis=0);v-=.15*(h.T@p);d-=.15*p.sum(axis=0)
            with _LOCK:_STATE.update(epoch=epoch+1,epochs=epochs)
        with _LOCK:_STATE.update(phase='validation',percent=96,eta_seconds=None)
        pred=(np.tanh(x@w+b)@v+d).argmax(axis=1)
        correct=total=unknown=0
        for row in val:
            predictions=(np.tanh(np.array(row['x'])@w+b)@v+d).argmax(axis=1)
            for char,i in zip(row['chars'],predictions):
                total+=1;correct+=alphabet[int(i)]==char;unknown+=char not in index
        metrics=dict(train_accuracy=float((pred==y).mean()),validation_accuracy=correct/max(1,total),validation_characters=total,unseen_validation_characters=unknown,train_photos=len(learn),validation_photos=len(val),alphabet=''.join(alphabet),epochs=epochs,ts=time.time())
        tmp=model+'.tmp'
        with open(tmp,'wb') as f:np.savez_compressed(f,w=w,b=b,v=v,d=d,alphabet=np.array(alphabet))
        with _LOCK:
            os.replace(tmp,model);store.save_json('photo_ocr_metrics.json',metrics)
            _STATE.update(phase='done',percent=100,eta_seconds=0,elapsed_seconds=round(time.monotonic()-started,1))
    except Exception as exc:
        with _LOCK:_STATE.update(error=str(exc)[:500],phase='error',eta_seconds=None)
    finally:
        with _LOCK:_STATE['running']=False


def read(raw, single=False):
    np,_,_=_deps();model=store.p('photo_ocr.npz')
    if not os.path.exists(model):return None
    with _LOCK:
        with np.load(model,allow_pickle=False) as m:w,b,v,d,alphabet=(m[k].copy() for k in ('w','b','v','d','alphabet'))
    results=[];confidence=[]
    for glyphs in ([[(0,1,_feature(_mask(raw)))]] if single else segment(raw)):
        x=np.array([g[2] for g in glyphs]);z=np.tanh(x@w+b)@v+d
        p=np.exp(z-z.max(axis=1,keepdims=True));p/=p.sum(axis=1,keepdims=True)
        widths=[g[1]-g[0] for g in glyphs];gap=max(3,float(np.median(widths))*.6)
        line=''
        for i,(x0,x1,_) in enumerate(glyphs):
            if i and x0-glyphs[i-1][1]>gap:line+=' '
            score=float(p[i].max());confidence.append(score)
            line+=str(alphabet[int(p[i].argmax())]) if score>=.55 else '�'
        results.append(line)
    text='\n'.join(results)
    try:
        import llm_zones
        llm_zones.route(text,zone='Камера')
    except Exception:pass
    return dict(text=text,confidence=sum(confidence)/max(1,len(confidence)),engine='own_pixel_mlp',warning='Результат собственной обучаемой OCR-головы; возможны ошибки. Уверенность не является гарантией точности')
