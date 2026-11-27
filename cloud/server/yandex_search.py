"""Yandex Cloud Search API v2, stdlib client for a fixed trusted endpoint.
Contract: yandex-cloud/cloudapi searchapi/v2/search_service.proto.
"""
import base64
import binascii
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ENDPOINT = 'https://searchapi.api.cloud.yandex.net/v2/web/search'
MAX_RESPONSE = 8 * 1024 * 1024


class SearchError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the API key to another endpoint.


def _redact(text, secrets):
    text = str(text)
    for secret in secrets:
        if secret:
            for value in (secret, urllib.parse.quote(secret, safe='')):
                text = text.replace(value, '[скрыто]')
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', text).strip()[:350]


def _failure(status, body, secrets, retry_after=''):
    hints = {
        400: 'проверьте Folder ID и параметры запроса',
        401: 'проверьте действующий API-ключ сервисного аккаунта',
        403: 'проверьте роль search-api.webSearch.user на каталоге и область ключа yc.search-api.execute',
        404: 'проверьте доступность Search API v2 и Folder ID',
        429: 'исчерпана квота или частота запросов Search API',
    }
    hint = hints.get(status, 'ошибка Yandex Cloud Search API')
    if 300 <= status < 400:
        hint = 'API вернул перенаправление; ключ не отправлялся по другому адресу'
    detail = ''
    try:
        obj = json.loads(body)
        detail = str(obj.get('message') or (obj.get('error') or {}).get('message') or '')
    except (ValueError, AttributeError, TypeError):
        pass
    return SearchError(_redact('HTTP %d: %s%s%s' % (
        status, hint, ('; повтор через '+retry_after+' с') if status==429 and retry_after else '',
        ('; '+detail) if detail else ''), secrets))


def parse_xml(raw, limit):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise SearchError('Search API вернул XML с запрещёнными объявлениями сущностей')
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError) as e:
        raise SearchError('Некорректный XML в rawData Search API') from e
    # Ignore namespace prefixes, preserve text of <hlword> and XML entities.
    for node in root.iter():
        node.tag = node.tag.rsplit('}',1)[-1]
    error = root.find('.//error')
    if error is not None:
        if error.get('code') == '15':  # Valid empty search, not an auth failure.
            return []
        raise SearchError('Ошибка XML %s: %s' % (error.get('code','?'), ''.join(error.itertext())[:200]))
    if root.tag != 'yandexsearch' or root.find('.//response') is None:
        raise SearchError('Search API вернул XML без yandexsearch/response')
    def text(node):
        return ' '.join(''.join(node.itertext()).split()) if node is not None else ''
    results = []; seen = set()
    for doc in root.findall('.//doc'):
        url = text(doc.find('url'))
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password or url in seen:
            continue
        seen.add(url)
        snippet = ' '.join(text(p) for p in doc.findall('.//passage')).strip() or text(doc.find('headline'))
        results.append(dict(title=(text(doc.find('title')) or url)[:160], url=url[:300], snippet=snippet[:400], src=''))
        if len(results) >= limit:
            break
    return results


def search(key, folder, query, limit=10):
    key, folder, query = str(key).strip(), str(folder).strip(), str(query or '').strip()
    original_key = key
    if key.lower().startswith('api-key '):
        key = key.split(None,1)[1].strip()
    secrets = (original_key, key, folder)
    if not key or not folder:
        raise SearchError('Укажите API-ключ и Folder ID каталога Yandex Cloud')
    if '\r' in key or '\n' in key or '…' in key:
        raise SearchError('Некорректный API-ключ; нужна полная строка ключа, а не маска')
    if not query:
        return []
    if len(query) > 400:
        raise SearchError('Запрос Search API должен содержать не более 400 символов')
    count = max(1,min(100,int(limit)))
    payload = dict(folderId=folder, query=dict(searchType='SEARCH_TYPE_RU', queryText=query,
        familyMode='FAMILY_MODE_MODERATE', page='0'),
        groupSpec=dict(groupMode='GROUP_MODE_FLAT', groupsOnPage=str(count), docsInGroup='1'),
        maxPassages='2', l10n='LOCALIZATION_RU', responseFormat='FORMAT_XML')
    request = urllib.request.Request(ENDPOINT, data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
        headers={'Authorization':'Api-Key '+key,'Content-Type':'application/json','Accept':'application/json',
                 'User-Agent':'neuro12-cloud-search/3.195.0'}, method='POST')
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request,timeout=8) as response:
            body = response.read(MAX_RESPONSE+1)
            if len(body)>MAX_RESPONSE:
                raise SearchError('Ответ Search API больше допустимого размера')
        data = json.loads(body.decode('utf-8'))
        if not isinstance(data,dict) or not isinstance(data.get('rawData'),str):
            raise SearchError('В ответе Search API отсутствует rawData')
        raw = base64.b64decode(data['rawData'],validate=True)
        return parse_xml(raw,count)
    except urllib.error.HTTPError as e:
        with e:
            raise _failure(e.code,e.read(10000),secrets,e.headers.get('Retry-After','')) from None
    except (urllib.error.URLError,TimeoutError,OSError) as e:
        raise SearchError(_redact('Сеть Search API: '+str(e),secrets)) from None
    except (json.JSONDecodeError,UnicodeError,binascii.Error) as e:
        raise SearchError('Некорректный JSON/base64 в ответе Search API') from e
    except SearchError as e:
        raise SearchError(_redact(e,secrets)) from None
