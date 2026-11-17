"""Regression checks: run with python -m unittest discover -s tests -v."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import urllib.request
import urllib.error

_TMP = tempfile.TemporaryDirectory()
os.environ['NEURO_DATA'] = _TMP.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import activity
import agent
import api_ui
import codewrite
import coherence
import store
import talkmind
import tinyllm


class Repairs(unittest.TestCase):
    def test_activity_owner_and_threads(self):
        activity.begin('request_first', 'owner')
        activity.update('Generating')
        self.assertIsNone(activity.get('request_first', 'other'))
        def worker():
            activity.begin('request_second', 'owner')
            activity.update('Checking')
            activity.finish()
        t = threading.Thread(target=worker)
        t.start(); t.join()
        self.assertEqual(activity.get('request_first', 'owner')['status'], 'Generating')
        self.assertTrue(activity.get('request_second', 'owner')['done'])
        activity.finish()

    def test_own_model_precedes_templates(self):
        result = dict(ok=True, n=1, of=1, lang='python', code='print(7)', check='syntax')
        with patch.object(codewrite, 'own_write', return_value=result), patch('codegen.answer') as templates:
            reply, matched, info = agent.code_agent('напиши факториал на python')
        self.assertTrue(matched)
        self.assertEqual(info['code_source'], 'own_llm')
        self.assertIn('print(7)', reply)
        templates.assert_not_called()

    def test_unknown_code_task_stays_in_agent(self):
        with patch.object(codewrite, 'own_write', return_value=None), \
             patch('codegen.answer', return_value=('', False, {})), \
             patch.object(codewrite, 'compose', return_value='generated'):
            reply, matched, _ = agent.code_agent('уникальная задача без ключевых слов')
        self.assertTrue(matched)
        self.assertEqual(reply, 'generated')

    def test_compose_prefers_own_model(self):
        result = dict(ok=True, n=1, of=1, lang='python', code='print(1)', check='syntax')
        with patch.object(codewrite, 'own_write', return_value=result), patch.object(codewrite, 'write') as external:
            self.assertIn('своя LLM', codewrite.compose('test'))
        external.assert_not_called()

    def test_invalid_generated_code_is_rejected(self):
        with patch.object(codewrite, 'own_ready', return_value=True), \
             patch.object(tinyllm, 'write_code', return_value=['def broken(:']):
            result = codewrite.own_write('test')
        self.assertFalse(result['ok'])
        self.assertIn('draft', result)

    def test_talk_rejects_noise(self):
        with patch.object(talkmind, '_brain_thought', return_value=('капли облаках вопросы помогать', 'cloud')), \
             patch.object(talkmind, '_llm_continue', return_value='буквы шум снова ничто'), \
             patch.object(coherence, 'judge', return_value={'ok': False, 'score': 0.1}):
            parts, info = talkmind.respond_parts('привет')
        self.assertEqual(len(parts), 1)
        self.assertIn('не удалось сформулировать связный ответ', parts[0])
        self.assertEqual(info['talk_by'], [])

    def test_talk_does_not_trust_unready_judge(self):
        with patch.object(coherence, 'judge', return_value={'ok': True, 'score': None}):
            self.assertFalse(talkmind._acceptable('слова облака просто случайный порядок'))

    def test_talk_accepts_one_validated_response(self):
        with patch.object(talkmind, '_brain_thought', return_value=('', '')), \
             patch.object(talkmind, '_llm_continue', return_value='Давай поговорим о твоих планах.' ) as sample, \
             patch.object(coherence, 'judge', return_value={'ok': True, 'score': 0.9}), \
             patch.object(tinyllm, 'quality', return_value=0.9), \
             patch.object(tinyllm, 'coherence', return_value=0.9, create=True):
            parts, info = talkmind.respond_parts('привет')
        self.assertEqual(parts, ['Давай поговорим о твоих планах.'])
        self.assertEqual(info['talk_by'], ['своя LLM'])
        sample.assert_called_once()

    def test_activity_finished_on_chat_exception(self):
        store.ensure_dirs()
        req = SimpleNamespace(body={'text': 'test', 'request_id': 'request_error'}, client_ip='owner')
        with patch.object(api_ui, '_chat_process', side_effect=RuntimeError('test')), patch('traceback.print_exc'):
            _, result = api_ui.h_chat_send(req)
        self.assertFalse(result['ok'])
        self.assertTrue(activity.get('request_error', 'owner')['done'])


class HTTPChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        import auth
        from http.server import ThreadingHTTPServer
        store.ensure_dirs()
        cls.sid = auth.create_session('127.0.0.1', 'tests')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def request(self, path, body=None, authenticated=True):
        import auth
        headers = {'Cookie': auth.COOKIE + '=' + self.sid} if authenticated else {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base + path, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)

    def test_rules_and_status_endpoint(self):
        result = self.request('/api/agent/rules')
        self.assertTrue(result['ok'])
        self.assertTrue(result['rules']['coding'])
        activity.begin('request_http', '127.0.0.1')
        activity.update('Своя LLM генерирует код')
        result = self.request('/api/chat/activity?id=request_http')
        self.assertEqual(result['status'], 'Своя LLM генерирует код')
        activity.finish()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/chat/activity?id=request_http', authenticated=False)
        self.assertEqual(error.exception.code, 401)

    def test_chat_response_is_saved_and_returned(self):
        with patch.object(talkmind, 'respond_parts', return_value=(['Проверенный ответ.'], {'talk_by': ['test']})):
            result = self.request('/api/chat/send', {'route': 'talk', 'text': 'Привет', 'request_id': 'request_chat'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['reply'], 'Проверенный ответ.')
        chat = self.request('/api/chat/get?id=' + result['id'])
        self.assertEqual(chat['chat']['messages'][-1]['text'], result['reply'])
        self.assertTrue(self.request('/api/chat/activity?id=request_chat')['done'])

if __name__ == '__main__':
    unittest.main()
