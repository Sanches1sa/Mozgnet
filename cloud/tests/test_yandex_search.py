"""Official Search API v2 contract and integration regressions; no real keys."""
import base64
import io
import json
from pathlib import Path
import sys
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import test_repairs
import yandex_search as yc
import webfetch
import store
import api_ui

XML = b'''<?xml version="1.0" encoding="UTF-8"?><yandexsearch><response><results><grouping><group><doc>
<url>https://example.com/a?a=1&amp;b=2</url><title>A &amp; <hlword>B</hlword></title>
<passages><passage>Hello <hlword>world</hlword>.</passage></passages></doc></group></grouping></results></response></yandexsearch>'''

class YandexTests(unittest.TestCase):
    def setUp(self):
        webfetch._ENGINE_STATE.clear()
        webfetch._cache.clear()
        self.settings=patch.object(store,'settings',return_value={'yandex_api_key':'secret-api-key','yandex_folder':'folder-secret','web_engines':'mailru,mojeek'})
        self.settings.start()
    def tearDown(self): self.settings.stop()
    def response(self, xml=XML):
        return io.BytesIO(json.dumps({'rawData':base64.b64encode(xml).decode()}).encode())
    def test_post_contract_and_highlight_xml(self):
        opener=Mock(); opener.open.return_value=self.response()
        with patch.object(yc.urllib.request,'build_opener',return_value=opener):
            results=webfetch.yandex_api('русский запрос',3)
        req=opener.open.call_args.args[0]
        self.assertEqual(req.full_url,yc.ENDPOINT)
        self.assertEqual(req.method,'POST')
        self.assertEqual(req.get_header('Authorization'),'Api-Key secret-api-key')
        data=json.loads(req.data)
        self.assertEqual(data['folderId'],'folder-secret')
        self.assertEqual(data['query']['queryText'],'русский запрос')
        self.assertEqual(data['query']['searchType'],'SEARCH_TYPE_RU')
        self.assertEqual(data['responseFormat'],'FORMAT_XML')
        self.assertEqual(data['groupSpec']['groupsOnPage'],'3')
        self.assertEqual(results[0]['title'],'A & B')
        self.assertEqual(results[0]['snippet'],'Hello world.')
        self.assertIn('&b=2',results[0]['url'])
        self.assertIn('yandex',webfetch.engines_state())
        self.assertNotIn('yandex-api',webfetch.engines_state())
    def test_http_permissions_are_actionable_and_redacted(self):
        error=urllib.error.HTTPError(yc.ENDPOINT,403,'Forbidden',{},io.BytesIO(json.dumps({'message':'secret-api-key folder-secret denied'}).encode()))
        opener=Mock(); opener.open.side_effect=error
        with patch.object(yc.urllib.request,'build_opener',return_value=opener):
            self.assertEqual(webfetch.yandex_api('q'),[])
        message=webfetch.engines_state()['yandex']['err']
        self.assertIn('403',message)
        self.assertIn('search-api.webSearch.user',message)
        self.assertNotIn('secret-api-key',message)
        self.assertNotIn('folder-secret',message)
    def test_valid_empty_does_not_trip_breaker(self):
        for xml in (b'<yandexsearch><response><results/></response></yandexsearch>',b'<yandexsearch><response><error code="15">No results</error></response></yandexsearch>'):
            opener=Mock();opener.open.return_value=self.response(xml)
            with patch.object(yc.urllib.request,'build_opener',return_value=opener):
                self.assertEqual(webfetch.yandex_api('empty'),[])
            self.assertEqual(webfetch.engines_state()['yandex']['fails'],0)
            self.assertEqual(webfetch.engines_state()['yandex']['err'],'')
    def test_bad_response_is_not_reported_as_success(self):
        for content in (b'<html>captcha</html>',b'{"rawData":"not-base64!"}',b'{"operation":"not-a-sync-response"}'):
            opener=Mock();opener.open.return_value=io.BytesIO(content)
            with patch.object(yc.urllib.request,'build_opener',return_value=opener):
                self.assertEqual(webfetch.yandex_api('q'),[])
            self.assertTrue(webfetch.engines_state()['yandex']['err'])
    def test_xml_errors_unsafe_urls_and_entities(self):
        with self.assertRaises(yc.SearchError):yc.parse_xml(b'<yandexsearch><response><error code="32">denied</error></response></yandexsearch>',5)
        with self.assertRaises(yc.SearchError):yc.parse_xml(b'<!DOCTYPE yandexsearch [<!ENTITY x "bad">]><yandexsearch/>',5)
        bad=XML.replace(b'https://example.com/a?a=1&amp;b=2',b'javascript:alert(1)')
        self.assertEqual(yc.parse_xml(bad,5),[])
    def test_no_redirect_with_api_key(self):
        self.assertIsNone(yc._NoRedirect().redirect_request(None,None,302,'',{},'https://other.test'))
        opener=Mock();opener.open.side_effect=urllib.error.HTTPError(yc.ENDPOINT,302,'redirect',{},io.BytesIO())
        with patch.object(yc.urllib.request,'build_opener',return_value=opener):
            with self.assertRaisesRegex(yc.SearchError,'перенаправление'):yc.search('key','folder','q')
        opener.open.assert_called_once()
    def test_missing_folder_does_not_send_request(self):
        with patch.object(yc.urllib.request,'build_opener') as build:
            self.assertEqual(webfetch.yandex_api('q',credentials=('key','')),[])
        build.assert_not_called()
        self.assertIn('Folder ID',webfetch.engines_state()['yandex']['err'])
    def test_api_prioritized_over_html(self):
        names=webfetch.engines_list()
        self.assertEqual(names[0],'yandex')
        self.assertNotIn('yandex-html',names)
    def test_probe_uses_saved_mask_and_does_not_save_form(self):
        opener=Mock();opener.open.return_value=self.response()
        req=SimpleNamespace(body={'yandex_api_key':'…-key','yandex_folder':'folder-new'})
        with patch.object(yc.urllib.request,'build_opener',return_value=opener), patch.object(store,'update_settings') as update:
            status,result=api_ui.h_yandex_probe(req)
        self.assertEqual(status,200)
        self.assertTrue(result['ok'])
        update.assert_not_called()
        self.assertEqual(opener.open.call_args.args[0].get_header('Authorization'),'Api-Key secret-api-key')
        self.assertEqual(json.loads(opener.open.call_args.args[0].data)['folderId'],'folder-new')
        self.assertNotIn('secret-api-key',json.dumps(result))
    def test_settings_changes_reset_failure_cooldown(self):
        webfetch._engine_note('yandex',False,'bad key')
        webfetch._engine_note('yandex',False,'bad key')
        webfetch._engine_note('yandex',False,'bad key')
        self.assertFalse(webfetch._engine_alive('yandex'))
        current=store.settings()
        def update(patch):return dict(current,**patch)
        with patch.object(store,'update_settings',side_effect=update):
            _,result=api_ui.h_settings_post(SimpleNamespace(body={'yandex_api_key':'…-key','yandex_folder':'new-folder'}))
        self.assertTrue(webfetch._engine_alive('yandex'))
        self.assertNotIn('secret-api-key',json.dumps(result))
    def test_empty_probe_is_success(self):
        opener=Mock();opener.open.return_value=self.response(b'<yandexsearch><response><results/></response></yandexsearch>')
        with patch.object(yc.urllib.request,'build_opener',return_value=opener):
            _,result=api_ui.h_yandex_probe(SimpleNamespace(body={}))
        self.assertTrue(result['ok'])
        self.assertEqual(result['found'],0)
    def test_general_probe_labels_empty_api_as_success(self):
        def empty(*args):
            webfetch._engine_note('yandex',True)
            return []
        with patch.object(webfetch,'yandex_api',side_effect=empty), patch.object(webfetch,'yandex_html',return_value=[]), patch.object(webfetch,'mailru_search',return_value=[]), patch.object(webfetch,'mojeek_search',return_value=[]), patch.object(webfetch,'google_api',return_value=[]), patch.object(webfetch,'_wiki_lang',return_value=[]), patch.object(webfetch,'ruwiki_search',return_value=[]), patch('sources.enabled',return_value=False):
            _,result=api_ui.h_web_engines(SimpleNamespace(body={}))
        state=result['engines']['Яндекс (ключ API)']
        self.assertTrue(state['ok'])
        self.assertEqual(state['err'],'')
        self.assertEqual(state['found'],0)

    def test_key_prefix_is_normalized(self):
        opener=Mock();opener.open.return_value=self.response()
        with patch.object(yc.urllib.request,'build_opener',return_value=opener):
            yc.search('Api-Key secret-api-key','folder-secret','q')
        self.assertEqual(opener.open.call_args.args[0].get_header('Authorization'),'Api-Key secret-api-key')

if __name__=='__main__': unittest.main()
