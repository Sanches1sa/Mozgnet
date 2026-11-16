"""Public web tools; respect configured HTTPS proxy and verified TLS."""
import ipaddress
import urllib.error
import urllib.parse
import urllib.request

import webfetch


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _public(url):
    parsed = urllib.parse.urlparse(webfetch.safe_url(url))
    if parsed.scheme == 'http':
        parsed = parsed._replace(scheme='https')
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Только публичные HTTPS-страницы без паролей в URL')
    host = webfetch._public_host_check(parsed.hostname)
    resolved = webfetch._sys_resolve(host)
    if resolved and any(not ipaddress.ip_address(ip).is_global for ip in resolved):
        raise ValueError('Приватные или смешанные DNS-адреса запрещены')
    if not (resolved or webfetch._resolve_public(host)):
        raise ValueError('Не удалось проверить публичный адрес сайта')
    return parsed._replace(fragment='').geturl()


def fetch_page(url, timeout=12, max_bytes=200000):
    try:
        url = _public(url)
        # The existing fetcher pins destination IPs when no proxy is required.
        if not urllib.request.getproxies().get('https'):
            return webfetch.fetch_page(url, timeout=timeout, max_bytes=max_bytes)
        opener = urllib.request.build_opener(_NoRedirect())
        for _ in range(4):
            url = _public(url)
            req = urllib.request.Request(url, headers={'User-Agent': 'Neuro12-Contour/1.0', 'Accept-Encoding': 'identity'})
            try:
                response = opener.open(req, timeout=timeout)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and e.headers.get('Location'):
                    url = urllib.parse.urljoin(url, e.headers['Location']); e.close(); continue
                raise
            with response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ValueError('Страница больше разрешённого размера')
                html = webfetch._decode_body(body, response.headers.get('Content-Type', ''))
            text, title = webfetch._page_parts(html, base=url)
            return True, dict(url=url, title=title, text=text, html=html)
        raise ValueError('Слишком много перенаправлений')
    except Exception as e:
        return False, str(e)[:500]


def search(query, limit=3):
    # v3.155: Google и Яндекс вместо DuckDuckGo
    if not urllib.request.getproxies().get('https'):
        return webfetch.engine_search(query, limit=limit)
    ok, page = fetch_page('https://www.google.com/search?' + urllib.parse.urlencode(
        {'q': query, 'hl': 'ru', 'gbv': '1'}), max_bytes=900000)
    res = webfetch._parse_google_html(page.get('html', ''), limit=limit) if ok else []
    if not res:
        ok2, page2 = fetch_page('https://yandex.ru/search/?' + urllib.parse.urlencode(
            {'text': query, 'lr': '213'}), max_bytes=1500000)
        res = webfetch._parse_yandex_html(page2.get('html', ''), limit=limit) if ok2 else []
    if not res and not ok:
        raise ValueError(str(page))
    return res
