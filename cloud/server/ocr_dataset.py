"""Verified CoMNIST Cyrillic subset. Images by Gregory Vial, CC BY-SA 4.0.
No remote scripts, no unlabelled scraping, no archive extraction to disk.
"""
import hashlib
import os
import threading
import time
import urllib.request
import zipfile
import store

COMMIT='3716a52d7b7571e71b347039b167ea3359d14e7e'
SOURCE='https://raw.githubusercontent.com/GregVial/CoMNIST/'+COMMIT+'/images/Cyrillic.zip'
PAGE='https://github.com/GregVial/CoMNIST'
LICENSE='https://creativecommons.org/licenses/by-sa/4.0/'
SHA256='f35b8584491c3b8991b25a82450b25868f1c2b3fe4f1e24ebad393acebd41238'
ALPHABET='АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ'
MAX_BYTES=75_000_000
_LOCK=threading.RLock()
_STATE=dict(running=False,phase='idle',percent=0)


def status():
    with _LOCK:
        return dict(_STATE,source=SOURCE,page=PAGE,name='CoMNIST: 33 русские буквы',
                    license='CC BY-SA 4.0 — Gregory Vial',license_url=LICENSE,
                    kind='рукописные символы с сенсорного экрана, не фотографии страниц',
                    download_bytes=71657411,sha256_expected=SHA256,
                    last_import=store.load_json('photo_ocr_dataset.json',{}) or {})


def _update(**kwargs):
    with _LOCK:_STATE.update(kwargs)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Источник CoMNIST перенаправляет запрос; импорт остановлен')


def _valid(path):
    if not os.path.isfile(path) or os.path.getsize(path)>MAX_BYTES:return False
    digest=hashlib.sha256()
    with open(path,'rb') as f:
        while True:
            chunk=f.read(1024*1024)
            if not chunk:break
            digest.update(chunk)
    return digest.hexdigest()==SHA256


def _download(path):
    if _valid(path):
        _update(phase='cached',downloaded=os.path.getsize(path),percent=50);return path
    part=path+'.partial';started=time.monotonic()
    try:
        opener=urllib.request.build_opener(_NoRedirect())
        with opener.open(SOURCE,timeout=25) as response, open(part,'wb') as out:
            total=int(response.headers.get('Content-Length') or 0)
            if total>MAX_BYTES:raise ValueError('Архив CoMNIST превышает допустимый размер')
            count=0;digest=hashlib.sha256()
            while True:
                chunk=response.read(256*1024)
                if not chunk:break
                count+=len(chunk)
                if count>MAX_BYTES or time.monotonic()-started>900:raise ValueError('Превышен размер или время скачивания CoMNIST')
                out.write(chunk);digest.update(chunk)
                _update(phase='download',downloaded=count,total_bytes=total,percent=round(50*count/total,1) if total else None)
            if total and count!=total:raise ValueError('CoMNIST скачан не полностью')
            if digest.hexdigest()!=SHA256:raise ValueError('Контрольная сумма CoMNIST не совпала; данные не импортированы')
        os.replace(part,path);_update(sha256=digest.hexdigest())
        return path
    finally:
        if os.path.exists(part):os.remove(part)


def start(limit=330):
    import photo_ocr
    photo_ocr._deps()  # dependency error before starting a download
    limit=max(66,min(396,int(limit)))
    with _LOCK,photo_ocr._LOCK:
        if _STATE['running'] or photo_ocr._IMPORTING:raise ValueError('Импорт уже идёт')
        if photo_ocr._STATE['running']:raise ValueError('Дождитесь завершения OCR-обучения')
        photo_ocr._IMPORTING=True
        _STATE.update(running=True,phase='starting',percent=0,error='',accepted=0,skipped=0,limit=limit)
    threading.Thread(target=_import,args=(limit,),daemon=True).start()
    return dict(ok=True,**status())


def _import(limit):
    try:
        import photo_ocr
        cache=store.p('datasets');os.makedirs(cache,exist_ok=True)
        path=_download(os.path.join(cache,'comnist-cyrillic.zip'))
        _update(phase='decode',percent=50)
        accepted=skipped=existing=0;counts={char:0 for char in ALPHABET}
        per_class=limit//len(ALPHABET)
        with photo_ocr._LOCK:
            rows=store.load_json('photo_ocr_samples.json',[]) or []
            imported={r.get('sample_id') for r in rows if r.get('source')=='CoMNIST CC BY-SA 4.0'}
            available=max(0,500-len(rows))
        per_class=min(per_class,(available+len(imported))//33)
        if per_class<2:raise ValueError('Недостаточно места: нужны примеры каждой буквы в пределах 500 изображений')
        with zipfile.ZipFile(path) as archive:
            groups={char:[] for char in ALPHABET}
            for entry in archive.infolist():
                parts=entry.filename.split('/')
                if len(parts)==3 and parts[0]=='Cyrillic' and parts[1] in groups and parts[2].endswith('.png'):
                    if entry.file_size>1_000_000:raise ValueError('Неожиданно большое изображение в CoMNIST')
                    groups[parts[1]].append(entry)
            if any(not entries for entries in groups.values()):raise ValueError('В наборе отсутствуют русские буквы')
            for char in ALPHABET:
                # Stable order independent of archive ordering; each class has a holdout.
                entries=sorted(groups[char],key=lambda e:hashlib.sha256(e.filename.encode()).hexdigest())
                for j,entry in enumerate(entries[:per_class]):
                    sample_id=COMMIT+':'+entry.filename
                    if sample_id in imported:existing+=1;counts[char]+=1;continue
                    raw=archive.read(entry)
                    try:
                        result=photo_ocr.add(raw,char,source='CoMNIST CC BY-SA 4.0',sample_id=sample_id,single=True,split='validation' if j%5==0 else 'train')
                        if result.get('corrected'):existing+=1
                        else:accepted+=1
                        counts[char]+=1
                    except ValueError as exc:
                        if 'предел' in str(exc):raise
                        skipped+=1
                    _update(phase='import',accepted=accepted,existing=existing,skipped=skipped,classes=dict(counts),percent=50+round(50*(accepted+existing+skipped)/(33*per_class),1))
        manifest=dict(source=SOURCE,page=PAGE,license='CC BY-SA 4.0',license_url=LICENSE,attribution='CoMNIST by Gregory Vial',sha256=SHA256,accepted=accepted,existing=existing,skipped=skipped,classes=counts,ts=time.time())
        store.save_json('photo_ocr_dataset.json',manifest)
        _update(phase='done',percent=100,accepted=accepted,existing=existing,skipped=skipped)
    except Exception as exc:
        _update(phase='error',error=str(exc)[:500])
    finally:
        import photo_ocr
        with photo_ocr._LOCK:photo_ocr._IMPORTING=False
        _update(running=False)
