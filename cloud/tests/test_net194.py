# -*- coding: utf-8 -*-
"""v3.194: облако снова ходит в интернет — честно и без мёртвых движков.
1) выдача Яндекса подключена как поисковик без ключа (yandex_html);
2) Поиск Mail.ru закрыл анонимную выдачу (перенаправление на вход) —
   детектится и фиксируется честной причиной;
3) предохранитель: 3 неудачи подряд — движок на паузу 10 минут;
4) при пустой выдаче чат получает диагноз «почему молчат поисковики»,
   а не безликое «страницы выдачи не ответили»;
5) запасной DNS: проводной DoH RFC 8484 (dns.yandex.ru) после JSON-шлюзов;
6) адреса: IPv4 первыми; HTTPS_PROXY — CONNECT-туннель.
Все сетевые вызовы подменены (офлайн). Запуск:
python -m unittest tests.test_net194 -v
"""
import base64
import os
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

if 'NEURO_DATA' not in os.environ:
    os.environ['NEURO_DATA'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'server'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_repairs  # noqa: F401  (ставит NEURO_DATA и поднимает общий контекст)

import store
import webfetch


def _fresh_state():
    """Чистое состояние движков между тестами."""
    webfetch._ENGINE_STATE.clear()
    webfetch._cache.clear()
    webfetch._LAST_ERRS.clear()
    webfetch._doh_cache.clear()


class EnginesListTest(unittest.TestCase):
    """Поисковики: выдача Яндекса — в дефолтном наборе, первой."""

    def setUp(self):
        _fresh_state()

    def test_default_includes_yandex_html(self):
        with patch.object(store, "settings",
                          return_value={"web_engines": "", "web_any": True}):
            names = webfetch.engines_list()
        self.assertIn("yandex-html", names)
        self.assertEqual(names[0], "yandex-html")

    def test_legacy_list_prepends_yandex_html(self):
        # владелец раньше сохранил «mailru,mojeek» — выдача Яндекса встаёт первой
        with patch.object(store, "settings",
                          return_value={"web_engines": "mailru,mojeek"}):
            names = webfetch.engines_list()
        self.assertEqual(names, ["yandex-html", "mailru", "mojeek"])

    def test_keyed_engines_kept(self):
        with patch.object(store, "settings",
                          return_value={"web_engines": "mailru,mojeek"}), \
             patch.object(webfetch, "_skey",
                          side_effect=lambda k: {"google_api_key": "K",
                                                 "google_cx": "CX"}.get(k, "")):
            names = webfetch.engines_list()
        self.assertIn("google", names)

    def test_engine_search_skips_paused(self):
        calls = []

        def fake_mailru(q, limit=10):
            calls.append("mailru")
            return []

        def fake_mojeek(q, limit=10):
            calls.append("mojeek")
            return []

        webfetch._engine_note("mailru", False, "раз")
        webfetch._engine_note("mailru", False, "два")
        webfetch._engine_note("mailru", False, "три")
        self.assertFalse(webfetch._engine_alive("mailru"))
        with patch.object(webfetch, "_engine_alive",
                          side_effect=lambda n: n != "mailru"), \
             patch.dict(webfetch._ENGINES,
                        {"mailru": fake_mailru, "mojeek": fake_mojeek}), \
             patch.object(webfetch, "engines_list",
                          return_value=["mailru", "mojeek"]):
            out = webfetch.engine_search("тест", 5)
        self.assertEqual(out, [])
        self.assertEqual(calls, ["mojeek"])   # mailru на паузе — не вызван


class BreakerTest(unittest.TestCase):
    """Предохранитель: неудачи копятся, пауза включается, успех сбрасывает."""

    def setUp(self):
        _fresh_state()

    def test_pause_after_three_fails(self):
        webfetch._engine_note("mojeek", False, "таймаут")
        webfetch._engine_note("mojeek", False, "таймаут")
        self.assertTrue(webfetch._engine_alive("mojeek"))
        webfetch._engine_note("mojeek", False, "таймаут")
        self.assertFalse(webfetch._engine_alive("mojeek"))
        st = webfetch.engines_state()["mojeek"]
        self.assertEqual(st["fails"], 3)
        self.assertGreater(st["pause_s"], 500)

    def test_success_resets(self):
        webfetch._engine_note("mojeek", False, "таймаут")
        webfetch._engine_note("mojeek", False, "таймаут")
        webfetch._engine_note("mojeek", True)
        self.assertTrue(webfetch._engine_alive("mojeek"))
        self.assertEqual(webfetch.engines_state()["mojeek"]["fails"], 0)


class MailruLoginTest(unittest.TestCase):
    """Mail.ru закрыл поиск без аккаунта: детект перенаправления на вход."""

    def setUp(self):
        _fresh_state()

    def _fake_get(self, url, **kw):
        return (200, "https://account.mail.ru/login?act=autologin&"
                     "errorCode=11300", "<html>вход</html>")

    def test_login_redirect_detected(self):
        with patch.object(webfetch, "_search_get", self._fake_get):
            out = webfetch.mailru_search("что такое транзистор", 5)
        self.assertEqual(out, [])
        st = webfetch.engines_state()["mailru"]
        self.assertIn("входа", st["err"])
        self.assertEqual(st["fails"], 1)

    def test_diag_titles(self):
        webfetch._engine_note("mailru", False, "требует входа")
        webfetch._engine_note("yandex-html", False, "попросил капчу")
        with patch.object(store, "settings",
                          return_value={"web_engines": "mailru,mojeek"}):
            diag = webfetch.engines_diag(2)
        self.assertTrue(any("Поиск Mail.ru" in d for d in diag))
        self.assertTrue(any("Яндекс (выдача)" in d for d in diag))


class PagesAnswerDiagTest(unittest.TestCase):
    """Пустая выдача — честный диагноз в чат вместо «не ответили»."""

    def setUp(self):
        _fresh_state()
        multisource_mod = __import__("multisource")
        multisource_mod._CACHE.clear()

    def test_no_links_message_names_engines(self):
        import multisource
        multisource._CACHE.clear()
        with patch.object(webfetch, "top_pages", return_value=[]), \
             patch.object(webfetch, "engines_diag",
                          return_value=["Поиск Mail.ru — требует входа",
                                        "Mojeek — сервер не ответил вовремя"]):
            reply, meta = multisource.pages_answer("что такое транзистор")
        self.assertIsNone(reply)
        errs = meta["errors"]
        self.assertTrue(errs[0].startswith("поисковики молчат"))
        self.assertIn("Поиск Mail.ru — требует входа", errs[0])
        self.assertIn("Mojeek", errs[0])
        # фраза без «. » внутри первой части — дойдёт до чата целиком
        self.assertNotIn(". ", errs[0].split("в Настройках")[0])

    def test_no_links_no_diag(self):
        import multisource
        multisource._CACHE.clear()
        with patch.object(webfetch, "top_pages", return_value=[]), \
             patch.object(webfetch, "engines_diag", return_value=[]):
            reply, meta = multisource.pages_answer("что такое транзистор")
        self.assertIsNone(reply)
        self.assertIn("не дали ссылок", meta["errors"][0])


class DohWireTest(unittest.TestCase):
    """Проводной DoH RFC 8484: сборка запроса и разбор A-записей ответа."""

    def test_query_packet_shape(self):
        pkt = webfetch._dns_wire_q("ru.wikipedia.org")
        self.assertEqual(struct.unpack(">H", pkt[2:4])[0], 0x0100)  # флаги: рекурсия
        self.assertEqual(struct.unpack(">H", pkt[4:6])[0], 1)   # QDCOUNT=1
        self.assertEqual(pkt[12:15], b"\x02ru")                 # метка «ru»
        self.assertIn(b"wikipedia", pkt)
        self.assertEqual(pkt[-4:], struct.pack(">HH", 1, 1))    # A, IN

    def _answer_with_a_record(self, ip, name_ptr=True):
        """Собрать DNS-ответ с одной A-записью (имя — указатель 0xC00C)."""
        ip_bytes = bytes(int(x) for x in ip.split("."))
        header = struct.pack(">HHHHHH", 0x4219, 0x8180, 1, 1, 0, 0)
        question = b"\x02ru\x09wikipedia\x03org\x00" + struct.pack(">HH", 1, 1)
        aname = (b"\xc0\x0c" if name_ptr else
                 b"\x02ru\x09wikipedia\x03org\x00")
        answer = aname + struct.pack(">HHIH", 1, 1, 300, 4) + ip_bytes
        return header + question + answer

    def test_parse_pointer_answer(self):
        ips = webfetch._dns_wire_ips(self._answer_with_a_record("5.9.9.9"))
        self.assertEqual(ips, ["5.9.9.9"])

    def test_parse_full_name_answer(self):
        pkt = self._answer_with_a_record("93.184.216.34", name_ptr=False)
        self.assertEqual(webfetch._dns_wire_ips(pkt), ["93.184.216.34"])

    def test_rcode_and_garbage(self):
        self.assertEqual(webfetch._dns_wire_ips(b""), [])
        bad = bytearray(self._answer_with_a_record("5.9.9.9"))
        bad[3] = 0x82                           # rcode=SERVFAIL (низшие 4 бита)
        self.assertEqual(webfetch._dns_wire_ips(bytes(bad)), [])

    def test_wire_fallback_in_resolve(self):
        # JSON-шлюзы молчат — запрос уходит в dns.yandex.ru
        _fresh_state()
        webfetch._doh_cache.clear()

        def fake_urlopen(req, timeout=6):
            import io
            host = req.full_url
            self.assertIn("dns.yandex.ru", host)
            pkt = self._answer_with_a_record("1.2.3.4")
            return io.BytesIO(pkt)

        with patch("urllib.request.urlopen", fake_urlopen), \
             patch.object(webfetch, "_sys_resolve", return_value=[]):
            ips = webfetch._doh_resolve("ru.wikipedia.org")
        self.assertEqual(ips, ["1.2.3.4"])


class ResolveOrderTest(unittest.TestCase):
    """IPv4-адреса первыми — на машинах без IPv6 таймауты не съедаются."""

    def test_ipv4_first(self):
        with patch.object(webfetch, "_sys_resolve",
                          return_value=["2606:4700::6810:f8f9",
                                        "8.8.4.4"]), \
             patch.object(webfetch, "_doh_resolve", return_value=[]):
            ips = webfetch._resolve_public("dns.google")
        self.assertEqual(ips[0], "8.8.4.4")
        self.assertEqual(len(ips), 2)

    def test_ipv6_only_falls_back_to_doh(self):
        with patch.object(webfetch, "_sys_resolve",
                          return_value=["2606:4700::6810:f8f9"]), \
             patch.object(webfetch, "_doh_resolve",
                          return_value=["104.16.248.249"]):
            ips = webfetch._resolve_public("cloudflare-dns.com")
        self.assertEqual(ips[0], "104.16.248.249")   # A-запись из DoH — первой


class ProxyTest(unittest.TestCase):
    """HTTPS_PROXY: адрес парсится, туннель проверяет ответ прокси."""

    def setUp(self):
        self._old = {k: os.environ.get(k)
                     for k in ("HTTPS_PROXY", "https_proxy",
                               "ALL_PROXY", "all_proxy")}
        for k in self._old:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_no_proxy_by_default(self):
        self.assertIsNone(webfetch._proxy_addr())

    def test_parse_plain(self):
        os.environ["HTTPS_PROXY"] = "192.168.1.254:3128"
        host, port, auth = webfetch._proxy_addr()
        self.assertEqual((host, port, auth), ("192.168.1.254", 3128, ""))

    def test_parse_with_auth(self):
        os.environ["https_proxy"] = "http://user:pass@proxy.local:8080"
        host, port, auth = webfetch._proxy_addr()
        self.assertEqual((host, port), ("proxy.local", 8080))
        self.assertEqual(base64.b64decode(auth), b"user:pass")

    def test_connect_rejects_non200(self):
        class FakeSock:
            def __init__(self):
                self.sent = b""

            def sendall(self, b):
                self.sent = b

            def recv(self, n):
                return b"HTTP/1.1 403 Forbidden\r\n\r\n"

            def close(self):
                pass

        with patch.object(webfetch.socket, "create_connection",
                          return_value=FakeSock()):
            with self.assertRaises(OSError):
                webfetch._proxy_connect("93.184.216.34",
                                        ("proxy.local", 3128, ""), 5)

    def test_connect_ok(self):
        class FakeSock:
            def sendall(self, b):
                assert b.startswith(b"CONNECT 1.2.3.4:443 ")

            def recv(self, n):
                return b"HTTP/1.1 200 Connection established\r\n\r\n"

            def close(self):
                pass

        with patch.object(webfetch.socket, "create_connection",
                          return_value=FakeSock()):
            s = webfetch._proxy_connect("1.2.3.4",
                                        ("proxy.local", 3128, ""), 5)
            s.close()


class TopPagesPausedTest(unittest.TestCase):
    """top_pages: движок на паузе не опрашивается, Википедия — всегда."""

    def setUp(self):
        _fresh_state()

    def test_paused_engine_not_called(self):
        def boom(q, limit=10):
            raise AssertionError("mailru на паузе, но вызван")

        def fake_wiki(q, limit=3):
            return [{"title": "Транзистор",
                     "url": "https://ru.wikipedia.org/wiki/Транзистор",
                     "snippet": "", "src": ""}]

        webfetch._engine_note("mailru", False, "раз")
        webfetch._engine_note("mailru", False, "два")
        webfetch._engine_note("mailru", False, "три")
        with patch.object(webfetch, "engines_list",
                          return_value=["mailru", "mojeek"]), \
             patch.dict(webfetch._ENGINES,
                        {"mailru": boom,
                         "mojeek": lambda q, limit=10: []}), \
             patch.object(webfetch, "wiki_search", fake_wiki), \
             patch.object(store, "settings",
                          return_value={"web_engines": "", "web_any": True}):
            pages = webfetch.top_pages("транзистор", 5)
        self.assertTrue(pages)
        self.assertIn("wikipedia.org", pages[0]["url"])


class HonestTopPagesEngines(unittest.TestCase):
    """Выдача Яндекса действительно вызывается из top_pages (без ключа)."""

    def test_yandex_html_called(self):
        called = []

        def fake_ya(q, limit=10):
            called.append(q)
            return [{"title": "Транзистор",
                     "url": "https://example.com/tr", "snippet": "", "src": ""}]

        _fresh_state()
        with patch.object(webfetch, "engines_list",
                          return_value=["yandex-html"]), \
             patch.dict(webfetch._ENGINES, {"yandex-html": fake_ya}), \
             patch.object(webfetch, "wiki_search",
                          return_value=[]), \
             patch.object(store, "settings",
                          return_value={"web_engines": "", "web_any": True}):
            pages = webfetch.top_pages("транзистор", 5)
        self.assertEqual(called, ["транзистор"])
        self.assertEqual(pages[0]["url"], "https://example.com/tr")


if __name__ == "__main__":
    unittest.main()
