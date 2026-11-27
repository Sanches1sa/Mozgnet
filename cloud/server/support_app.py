"""MAX mini-app auth, local support requests, scoped platform API.
Remote-access passwords are volatile, never persisted or sent to the AI.
"""
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.parse
import store

_LOCK=threading.RLock()
_SESS={}
_PASSWORDS={}
_IDEMP={}
FILE='support_tickets.json'
TTL=7200


def _token():return str(store.settings().get('support_max_bot_token') or '').strip()


def validate_init(raw,now=None):
    now=time.time() if now is None else now
    if not _token():raise ValueError('В облаке не настроен токен бота мини-приложения')
    if not raw or len(raw)>16000:raise ValueError('Нет корректных данных запуска MAX')
    pairs=urllib.parse.parse_qsl(str(raw),keep_blank_values=True,strict_parsing=True)
    data={}
    for key,value in pairs:
        if key in data:raise ValueError('Повторяющийся параметр запуска')
        data[key]=value
    signature=data.pop('hash','')
    secret=hmac.new(b'WebAppData',_token().encode(),hashlib.sha256).digest()
    expected=hmac.new(secret,'\n'.join(k+'='+data[k] for k in sorted(data)).encode(),hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature,expected):raise ValueError('Подпись запуска MAX не прошла проверку')
    stamp=int(data.get('auth_date','0'))
    if stamp>now+60 or now-stamp>3600:raise ValueError('Данные запуска устарели; откройте приложение заново')
    user=json.loads(data.get('user','{}'))
    uid=user.get('id')
    if not isinstance(uid,int) or isinstance(uid,bool) or uid<=0:raise ValueError('Нет идентификатора пользователя MAX')
    scope=hashlib.sha256(_token().encode()).hexdigest()[:12]
    return dict(id='max:'+scope+':'+str(uid),user_id=str(uid),name=str(user.get('first_name') or '')[:80],admin=False)


def login(raw):
    actor=validate_init(raw);token=secrets.token_urlsafe(32);now=time.time()
    with _LOCK:
        for key in list(_SESS):
            if _SESS[key]['expires']<=now:_SESS.pop(key,None)
        if len(_SESS)>=2000:raise ValueError('Слишком много сессий мини-приложения')
        _SESS[token]=dict(actor=actor,expires=now+3600)
    return dict(ok=True,session=token,expires_in=3600,user={'name':actor['name'],'id':actor['user_id']})


def session(token):
    with _LOCK:
        row=_SESS.get(str(token or ''))
        if row and row['expires']>time.time() and row['actor']['id'].startswith('max:'+hashlib.sha256(_token().encode()).hexdigest()[:12]+':'):return dict(row['actor'])
        _SESS.pop(str(token or ''),None)
    return None


def platform_actor(key,user_id=''):
    kid=str((key or {}).get('id') or '')
    if not kid:raise ValueError('Нет ключа приложения')
    connector=str(store.settings().get('support_connector_key_id') or '')
    return dict(id='api:'+kid+':'+str(user_id or 'default')[:80],name=str((key or {}).get('name') or ''),
                user_id=str(user_id or '')[:80],admin=bool(connector and connector==kid))


def _allowed(actor,ticket):return bool(actor.get('admin') or ticket.get('owner')==actor['id'])


def _clean(value,limit):return str(value or '').replace('\x00','').strip()[:limit]


def _public(ticket):
    row=dict(ticket)
    row.pop('owner',None)
    row.pop('request_digest',None)
    row.pop('request_fingerprint',None)
    with _LOCK:
        secret=_PASSWORDS.get(row['id'])
        row['password_available']=bool(secret and secret['expires']>time.time())
    return row


def tickets(actor):
    with _LOCK:
        rows=store.load_json(FILE,[]) or []
        return [_public(row) for row in reversed(rows) if _allowed(actor,row)][:200]


def create(actor,body):
    service=_clean(body.get('service'),120);description=_clean(body.get('description'),6000)
    if not service or len(description)<5:raise ValueError('Укажите сервис и опишите проблему (от 5 знаков)')
    remote_id=_clean(body.get('remote_id'),160);password=_clean(body.get('remote_password'),300)
    if password and not remote_id:raise ValueError('Укажите идентификатор удалённой поддержки')
    nonce=_clean(body.get('request_id'),100)
    fingerprint=hashlib.sha256(json.dumps({k:body.get(k) for k in ('service','description','contact','remote_id','remote_password')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    request_digest=hashlib.sha256((actor['id']+':'+nonce).encode()).hexdigest() if nonce else ''
    safe_fingerprint=hashlib.sha256(json.dumps({k:body.get(k) for k in ('service','description','contact','remote_id')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    now=time.time()
    with _LOCK:
        for key in list(_PASSWORDS):
            if _PASSWORDS[key]['expires']<=now:_PASSWORDS.pop(key,None)
        for key in list(_IDEMP):
            if _IDEMP[key]['expires']<=now:_IDEMP.pop(key,None)
        rows=store.load_json(FILE,[]) or []
        if nonce:
            old=_IDEMP.get((actor['id'],nonce))
            if old:
                if old['fingerprint']!=fingerprint:raise ValueError('Этот номер запроса уже использован для другой заявки')
                row=next((r for r in rows if r['id']==old['id']),None)
                if row:return dict(ok=True,ticket=_public(row),duplicate=True,message='Эта заявка уже сохранена в облаке.')
        if request_digest:
            row=next((r for r in rows if r.get('request_digest')==request_digest),None)
            if row:
                if row.get('request_fingerprint')!=safe_fingerprint:raise ValueError('Номер запроса уже принадлежит другой заявке')
                return dict(ok=True,ticket=_public(row),duplicate=True,message='Эта заявка уже сохранена в облаке.')
        if len(rows)>=10000:raise ValueError('Достигнут предел 10000 заявок; обратитесь к администратору')
        ticket=dict(id='REQ-'+secrets.token_hex(5).upper(),owner=actor['id'],user_id=actor.get('user_id',''),name=actor.get('name',''),service=service,description=description,contact=_clean(body.get('contact'),200),remote_id=remote_id,status='new',delivery='cloud',created=now,updated=now,request_digest=request_digest,request_fingerprint=safe_fingerprint)
        rows.append(ticket);store.save_json(FILE,rows)
        if password:_PASSWORDS[ticket['id']]=dict(value=password,expires=now+TTL)
        if nonce:_IDEMP[(actor['id'],nonce)]=dict(id=ticket['id'],fingerprint=fingerprint,expires=now+86400)
    return dict(ok=True,ticket=_public(ticket),message='Заявка сохранена в облаке. Отправка в другую платформу пока не подтверждена.')


def reveal(actor,ticket_id):
    with _LOCK:
        row=next((r for r in store.load_json(FILE,[]) or [] if r['id']==ticket_id),None)
        if not row or not _allowed(actor,row):raise PermissionError('Заявка недоступна')
        secret=_PASSWORDS.get(ticket_id)
        if not secret or secret['expires']<=time.time():
            _PASSWORDS.pop(ticket_id,None);raise ValueError('Пароль уже недоступен; попросите пользователя передать его заново')
        return dict(ok=True,remote_id=row.get('remote_id',''),remote_password=secret['value'],expires_in=max(0,int(secret['expires']-time.time())))


def update(actor,ticket_id,body):
    if not actor.get('admin'):raise PermissionError('Изменять статус может специалист или подключённая платформа')
    status=str(body.get('status') or '')
    if status not in ('new','in_progress','resolved','closed'):raise ValueError('Неизвестный статус')
    with _LOCK:
        rows=store.load_json(FILE,[]) or []
        row=next((r for r in rows if r['id']==ticket_id),None)
        if not row:raise ValueError('Заявка не найдена')
        row.update(status=status,updated=time.time())
        if body.get('external_id'):
            row.update(external_id=_clean(body['external_id'],120),delivery='external')
        if status=='closed':_PASSWORDS.pop(ticket_id,None)
        store.save_json(FILE,rows)
    return dict(ok=True,ticket=_public(row))


def ask(actor,body):
    import aiapi
    q=_clean(body.get('question'),4000)
    if not q:raise ValueError('Введите вопрос')
    # Only the explicit question enters the AI. Remote credentials are not context.
    result=aiapi.answer(q,{'id':'support:'+actor['id'],'name':'Поддержка'},session=str(body.get('session') or 'support')[:64],mode='cloud',name=actor.get('name',''),template=False)
    if result.get('unanswered'):
        result['answer']='Пока не нашёл подтверждённого решения. Уточните название сервиса и текст ошибки или подайте заявку специалисту.'
        result['parts']=[result['answer']]
    result['actions']=[dict(type='open_app',label='Подать заявку',url=(str(store.settings().get('support_public_url') or '').rstrip('/')+'/max-app'))]
    return result
