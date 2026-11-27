"""Small, explicit image/text import from Russian OCR Small / printed.
Published test split is repurposed as a local corpus, not a benchmark score.
Pages remain available for manual crop corrections when glyph alignment fails.
"""
import base64
import hashlib
import io
import json
import os
import re
import threading
import urllib.parse
import urllib.request
import store

DATASET='Foximaz/russian_ocr_small'
REVISION='d07bcfb935743ca9c4fed09de4cdf1546fc40099'
PAGE='https://huggingface.co/datasets/'+DATASET
LICENSE='https://creativecommons.org/licenses/by/4.0/'
ASSET_PREFIX='https://datasets-server.huggingface.co/cached-assets/'+DATASET+'/--/'+REVISION+'/--/printed/test/'
_LOCK=threading.RLock()
_STATE=dict(running=False,phase='idle',percent=0)
MANIFEST='printed_ocr_pages.json'


def _rows():return store.load_json(MANIFEST,[]) or []


def _update(**kw):
    with _LOCK:_STATE.update(kw)


def status():
    rows=_rows()
    with _LOCK:
        return dict(_STATE,name='Russian OCR Small / printed',source=PAGE,revision=REVISION,
                    license='CC BY 4.0',license_url=LICENSE,
                    downloaded_pages=len(rows),prepared_pages=sum(r.get('prepared',False) for r in rows),
                    pages=[dict({k:r.get(k) for k in ('id','width','height','prepared','reason','crops','prepared_characters','partial')},text_preview=r.get('text','')[:500]) for r in rows[:100]],
                    note='1000 синтетических страниц печатного русского текста. Опубликованный test используется как местный учебный корпус, не как независимый тест.')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Перенаправление источника отклонено')


def _get(url,limit):
    opener=urllib.request.build_opener(_NoRedirect())
    with opener.open(url,timeout=25) as response:
        if int(response.headers.get('Content-Length') or 0)>limit:raise ValueError('Ответ источника слишком большой')
        raw=response.read(limit+1)
        if len(raw)>limit:raise ValueError('Ответ источника превышает предел размера')
        return raw


def _asset(url):
    if not str(url).startswith(ASSET_PREFIX):raise ValueError('Ссылка изображения не соответствует закреплённой версии базы')
    return _get(url,8_000_000)


def start(limit=20):
    import photo_ocr
    photo_ocr._deps()
    limit=max(3,min(100,int(limit)))
    with _LOCK,photo_ocr._LOCK:
        if _STATE['running'] or photo_ocr._IMPORTING or photo_ocr._STATE['running']:
            raise ValueError('Дождитесь текущего импорта или обучения OCR')
        photo_ocr._IMPORTING=True
        _STATE.update(running=True,phase='starting',percent=0,error='',done=0,requested=limit,prepared=0,pending=0)
    threading.Thread(target=_import,args=(limit,),daemon=True).start()
    return dict(ok=True,**status())


def _prepare(raw,text,page_id):
    """Only accept exact ordered glyph alignment; never insert guessed labels."""
    import photo_ocr
    from PIL import Image
    import numpy as np
    # Crop a large light paper region when its bounds are unambiguous.
    im=Image.open(io.BytesIO(raw)).convert('RGB')
    if im.width*im.height>12_000_000:raise ValueError('Страница больше 12 Мп')
    a=np.asarray(im.convert('L'));bright=a>180
    ys=np.where(bright.mean(1)>.55)[0]
    if len(ys)>im.height*.6:
        xs=np.where(bright[ys[0]:ys[-1]+1].mean(0)>.65)[0]
        if len(xs)>im.width*.6:
            bounds=(int(xs[0]+1),int(ys[0]+1),int(xs[-1]),int(ys[-1]))
            if bounds[2]>bounds[0] and bounds[3]>bounds[1]:im=im.crop(bounds)
    buf=io.BytesIO();im.save(buf,format='PNG')
    lines=photo_ocr.segment(buf.getvalue());glyphs=[g for line in lines for g in line]
    target=[c for c in str(text) if not c.isspace()]
    if not target or len(target)>5000:raise ValueError('Для подготовки нужна расшифровка до 5000 непробельных символов')
    partial=False
    if len(glyphs)!=len(target):
        # Salvage only an unshifted prefix of complete lines ending at known
        # word boundaries. Stop on the first mismatch, never guess missing glyphs.
        boundaries=set();total=0
        for word in re.findall(r'\S+',str(text)):
            total+=len(word);boundaries.add(total)
        prefix=[]
        for line in lines:
            end=len(prefix)+len(line)
            if end not in boundaries or end>len(target):break
            prefix.extend(line)
        if len(prefix)<3:raise ValueError('Выделено %d символов, в подписи %d. Нужна обрезка строки и проверка текста' % (len(glyphs),len(target)))
        glyphs=prefix;target=target[:len(prefix)];partial=True

    # Keep the complete original page in one split; its glyphs never cross it.
    key='printed:'+REVISION+':'+str(page_id)
    with photo_ocr._LOCK:
        data=store.load_json('photo_ocr_samples.json',[]) or []
        if any(r['id']==key for r in data):return dict(ok=True,existing=True,characters=len(target),partial=partial)
        if len(data)>=500:raise ValueError('Достигнут предел 500 учебных изображений')
        if sum(len(r['chars']) for r in data)+len(target)>30000:raise ValueError('Достигнут предел 30000 символов курса')
        data.append(dict(id=key,chars=target,x=[g[2].tolist() for g in glyphs],text=''.join(target) if partial else text,source='Russian OCR Small printed CC BY 4.0',sample_id=str(page_id),split='validation' if page_id%5==0 else 'train'))
        store.save_json('photo_ocr_samples.json',data)
    return dict(ok=True,characters=len(target),partial=partial)


def _import(limit):
    try:
        from PIL import Image
        directory=store.p('datasets','printed');os.makedirs(directory,exist_ok=True)
        records={r['id']:r for r in _rows()}
        for offset in range(0,limit,5):
            count=min(5,limit-offset)
            ids=list(range(offset,offset+count))
            if all(i in records and records[i].get('revision')==REVISION and os.path.isfile(os.path.join(directory,str(i)+'.image')) for i in ids):
                batch=dict(rows=[dict(row_idx=i,row=dict(text=records[i]['text'],image=dict(src=ASSET_PREFIX+str(i)+'/image/image.jpg'))) for i in ids])
            else:
                params=urllib.parse.urlencode(dict(dataset=DATASET,config='printed',split='test',offset=offset,length=count,revision=REVISION))
                _update(phase='metadata')
                batch=json.loads(_get('https://datasets-server.huggingface.co/rows?'+params,2_000_000))
            incoming=batch.get('rows') or []
            if len(incoming)!=count:raise ValueError('Источник вернул неполную порцию страниц')
            for item in incoming:
                idx=int(item.get('row_idx',-1))
                if idx<offset or idx>=offset+count:raise ValueError('Неверный номер страницы источника')
                row=item.get('row') or {};text=str(row.get('text') or '')
                if not text or len(text)>20000:raise ValueError('Некорректная расшифровка страницы')
                image=row.get('image') or {};url=image.get('src','')
                if not url.startswith(ASSET_PREFIX):raise ValueError('Изменилась версия или источник изображений; импорт остановлен')
                path=os.path.join(directory,str(idx)+'.image')
                existing=records.get(idx)
                raw=None
                if existing and os.path.isfile(path):
                    with open(path,'rb') as f:raw=f.read(8_000_001)
                    if hashlib.sha256(raw).hexdigest()!=existing.get('sha256'):raw=None
                if raw is None:
                    _update(phase='download');raw=_asset(url)
                with Image.open(io.BytesIO(raw)) as im:
                    if im.width*im.height>12_000_000:raise ValueError('Страница больше 12 Мп')
                    width,height=im.size
                with open(path+'.tmp','wb') as f:f.write(raw)
                os.replace(path+'.tmp',path)
                entry=dict(id=idx,text=text,width=width,height=height,source=PAGE,revision=REVISION,license='CC BY 4.0',sha256=hashlib.sha256(raw).hexdigest(),prepared=False,crops=(existing or {}).get('crops',0))
                _update(phase='prepare')
                try:
                    prepared=_prepare(raw,text,idx);entry['prepared']=True;entry['prepared_characters']=prepared['characters'];entry['partial']=prepared['partial']
                    entry['reason']=('Подготовлено начало страницы: ' if prepared['partial'] else 'Подготовлено: ')+str(prepared['characters'])+' символов. Совпали счётчики символов; проверьте сегментацию'
                except ValueError as exc:entry['reason']=str(exc)[:300]
                records[idx]=entry
                store.save_json(MANIFEST,[records[k] for k in sorted(records)])
                done=idx+1;ready=sum(r.get('prepared',False) for r in records.values())
                _update(done=done,percent=round(100*done/limit,1),prepared=ready,pending=len(records)-ready)
        _update(phase='done',percent=100)
    except Exception as exc:_update(phase='error',error=str(exc)[:500])
    finally:
        import photo_ocr
        with photo_ocr._LOCK:photo_ocr._IMPORTING=False
        _update(running=False)


def image_bytes(page_id):
    page_id=int(page_id)
    if page_id<0 or page_id>=1000 or not any(r['id']==page_id for r in _rows()):raise ValueError('Страница не загружена')
    with open(store.p('datasets','printed',str(page_id)+'.image'),'rb') as f:return f.read(8_000_001)


def add_crop(page_id,box,text,single=False):
    import photo_ocr
    from PIL import Image
    with photo_ocr._LOCK:
        if photo_ocr._IMPORTING or photo_ocr._STATE['running']:raise ValueError('Дождитесь завершения импорта или обучения')
    page_id=int(page_id);raw=image_bytes(page_id)
    if not isinstance(box,list) or len(box)!=4:raise ValueError('Выделите прямоугольник на странице')
    with Image.open(io.BytesIO(raw)) as im:
        x0,y0,x1,y1=map(int,box)
        if not (0<=x0<x1<=im.width and 0<=y0<y1<=im.height):raise ValueError('Обрезка выходит за границы страницы')
        crop=im.crop((x0,y0,x1,y1));buf=io.BytesIO();crop.save(buf,format='PNG')
    # Every crop inherits the page holdout, including repeated manual corrections.
    result=photo_ocr.add(buf.getvalue(),text,source='user',sample_id='printed-crop:'+str(page_id),single=single,split='validation' if page_id%5==0 else 'train')
    with _LOCK:
        rows=_rows()
        for row in rows:
            if row['id']==page_id:row['crops']=row.get('crops',0)+(0 if result.get('corrected') else 1)
        store.save_json(MANIFEST,rows)
    return result
