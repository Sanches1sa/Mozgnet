import ast
import binascii
import io
import json
from pathlib import Path
import struct
import sys
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
# Shared isolated store created by the regression suite.
import test_repairs
import codewrite
import project_agent as agent
import project_archive as archive
import store


def zipped(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return data.getvalue()


def stored_rar(name, content):
    """Small genuine RAR4 (stored method) fixture, no external archiver needed."""
    def header(kind, flags, body=b''):
        raw = struct.pack('<BHH', kind, flags, 7 + len(body)) + body
        return struct.pack('<H', binascii.crc32(raw) & 0xffff) + raw
    filename = name.encode()
    body = struct.pack('<IIBIIBBHI', len(content), len(content), 3,
                       binascii.crc32(content) & 0xffffffff, 0, 20, 0x30,
                       len(filename), 0o100644) + filename
    return (b'Rar!\x1a\x07\x00' + header(0x73, 0, b'\0' * 6) +
            header(0x74, 0x8000, body) + content + header(0x7b, 0))


def wait_job(ident, owner='owner'):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = agent.status(ident, owner)
        if result['done']:
            return result
        time.sleep(.01)
    raise AssertionError('job timed out')


class ProjectTests(unittest.TestCase):
    def project(self, files=None):
        return agent.upload(zipped(files or {'main.py': 'def answer():\n    return 1\n',
                                             'README.md': 'Original project\n'}), 'project.zip', 'owner')

    def test_zip_all_files_and_owner(self):
        p = self.project({'main.py': 'print(1)', 'assets/icon.bin': b'\x00\x01'})
        self.assertEqual(p['files'], 2)
        with self.assertRaises(PermissionError):
            agent.start(p['id'], 'fix', 'someone else')

    def test_archive_traversal_and_links(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for path in ('../escape.py', '/etc/test', 'C:\\escape.py', 'a/../../b'):
                with self.assertRaises(ValueError):
                    archive.unpack(zipped({path: 'bad'}), 'bad.zip', Path(d))
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as z:
                info = zipfile.ZipInfo('link'); info.create_system = 3
                info.external_attr = 0o120777 << 16
                z.writestr(info, '../../target')
            with self.assertRaises(ValueError):
                archive.unpack(data.getvalue(), 'link.zip', Path(d))

    def test_archive_budget(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, patch.object(archive, 'MAX_BYTES', 3):
            with self.assertRaises(ValueError):
                archive.unpack(zipped({'x': 'longer'}), 'big.zip', Path(d))

    def test_real_rar(self):
        import tempfile
        import ctypes.util
        if not ctypes.util.find_library('archive'):
            self.skipTest('libarchive not installed')
        with tempfile.TemporaryDirectory() as d:
            files, n = archive.unpack(stored_rar('src/main.py', b'print(123)\n'), 'project.rar', Path(d))
            self.assertEqual(files, ['src/main.py'])
            self.assertEqual((Path(d) / files[0]).read_bytes(), b'print(123)\n')
            self.assertEqual(n, 11)

    def test_tools_reject_escape_secret_and_disabled_web(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            for path in ('../x', '.env', 'sub/key.pem'):
                with self.assertRaises(ValueError):
                    agent._tool({}, work, dict(tool='write', path=path, content='x'))
            with self.assertRaises(ValueError):
                agent._web({'internet': False}, 'web_read', 'https://example.com')

    def test_large_file_read_and_exact_replace(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            (work / 'large.py').write_text('# header\n' * 15000 + 'value = 1\n')
            with patch.object(agent, '_event'):
                result = agent._tool({}, work, dict(tool='read', path='large.py', offset=134995, limit=100))
                self.assertIn('value = 1', result['content'])
                agent._tool({}, work, dict(tool='replace', path='large.py', before='value = 1', after='value = 42'))
                self.assertIn('value = 42', (work / 'large.py').read_text())
                with self.assertRaises(ValueError):
                    agent._tool({}, work, dict(tool='replace', path='large.py', before='# header', after='x'))

    def test_multifile_agent_loop_and_download(self):
        p = self.project()
        actions = [
            dict(tool='read', path='main.py'),
            dict(tool='web_search', query='python return documentation'),
            dict(tool='write', path='main.py', content='def answer():\n    return 42\n'),
            dict(tool='write', path='README.md', content='Answer is now 42.\n'),
            dict(tool='check'), dict(tool='finish', summary='Изменены код и README; тесты не запускались.')]
        with patch.object(codewrite, 'own_ready', return_value=False), \
             patch.object(codewrite, 'available', return_value=True), \
             patch.object(codewrite, '_call', side_effect=[json.dumps(x) for x in actions]), \
             patch('project_web.search', return_value=[{'url': 'https://docs.python.org/3/', 'title': 'Python'}]):
            ident = agent.start(p['id'], 'Верни 42 и обнови README', 'owner')
            result = wait_job(ident)
        self.assertEqual(result['state'], 'review')
        self.assertEqual(set(result['changed']), {'main.py', 'README.md'})
        self.assertTrue(any(e['kind'] == 'web' for e in result['events']))
        with zipfile.ZipFile(agent.download(ident, 'owner')) as z:
            self.assertIn('return 42', z.read('project/main.py').decode())
            report = json.loads(z.read('agent-report.json'))
            self.assertFalse(report['tests_executed'])
            self.assertIn('agent-changes.diff', z.namelist())
        source, _ = agent._load('projects', p['id'], 'owner')
        self.assertIn('return 1', (source / 'source/main.py').read_text())
        with self.assertRaises(PermissionError):
            agent.download(ident, 'other')

    def test_followup_keeps_previous_changes(self):
        p = self.project()
        with patch.object(codewrite, 'own_ready', return_value=False), patch.object(codewrite, 'available', return_value=True):
            with patch.object(codewrite, '_call', side_effect=[json.dumps(dict(tool='write', path='main.py', content='value = 42\n')), json.dumps(dict(tool='finish', summary='first'))]):
                first = wait_job(agent.start(p['id'], 'first', 'owner'))
            with patch.object(codewrite, '_call', side_effect=[json.dumps(dict(tool='write', path='README.md', content='Second task\n')), json.dumps(dict(tool='finish', summary='second'))]):
                second = wait_job(agent.start(p['id'], 'second', 'owner', base_job=first['id']))
        with zipfile.ZipFile(agent.download(second['id'], 'owner')) as z:
            self.assertEqual(z.read('project/main.py'), b'value = 42\n')
            self.assertEqual(z.read('project/README.md'), b'Second task\n')

    def test_web_rejects_local_urls(self):
        import project_web
        for url in ('file:///etc/passwd', 'https://127.0.0.1/', 'https://169.254.169.254/', 'https://user:pass@example.com', 'https://example.com:7000/'):
            with self.assertRaises(ValueError):
                project_web._public(url)

    def test_agent_creates_new_project_and_repairs_syntax(self):
        p = agent.create('owner')
        actions = [dict(tool='write', path='main.py', content='def broken(:'),
                   dict(tool='finish', summary='ready'),
                   dict(tool='write', path='main.py', content='print(42)\n'),
                   dict(tool='finish', summary='Синтаксис исправлен.')]
        with patch.object(codewrite, 'own_ready', return_value=False), \
             patch.object(codewrite, 'available', return_value=True), \
             patch.object(codewrite, '_call', side_effect=[json.dumps(x) for x in actions]):
            result = wait_job(agent.start(p['id'], 'create', 'owner'))
        self.assertEqual(result['state'], 'review')
        self.assertTrue(any('возвращаю модели' in e['status'] for e in result['events']))
        self.assertEqual(result['changed'], ['main.py'])
        self.assertTrue(result['checks'][0]['ok'])

    def test_syntax_failure_needs_review(self):
        p = self.project()
        actions = [dict(tool='write', path='main.py', content='def broken(:'),
                   dict(tool='finish', summary='done')]
        with patch.object(codewrite, 'own_ready', return_value=False), \
             patch.object(codewrite, 'available', return_value=True), \
             patch.object(codewrite, '_call', side_effect=[json.dumps(x) for x in actions]):
            result = wait_job(agent.start(p['id'], 'change', 'owner'))
        self.assertEqual(result['state'], 'needs_review')
        self.assertTrue(any(x['ok'] is False for x in result['checks']))

    def test_untrained_own_model_reports_blocked(self):
        p = self.project()
        with patch.object(codewrite, 'own_ready', return_value=False), patch.object(codewrite, 'available', return_value=False):
            result = wait_job(agent.start(p['id'], 'change', 'owner', model='own'))
        self.assertEqual(result['state'], 'blocked')
        self.assertFalse(result['changed'])

    def test_own_preserves_module_and_changes_matching_function(self):
        p = self.project({'main.py': 'import math\n\ndef answer():\n    return 1\n\ndef other():\n    return 7\n'})
        with patch.object(codewrite, 'own_ready', return_value=True), \
             patch('tinyllm.write_code', return_value=['def answer():\n    return 42\n']):
            result = wait_job(agent.start(p['id'], 'исправь answer', 'owner', model='own'))
        self.assertEqual(result['state'], 'partial')
        with zipfile.ZipFile(agent.download(result['id'], 'owner')) as z:
            code = z.read('project/main.py').decode()
        tree = ast.parse(code)
        self.assertIn('import math', code)
        self.assertEqual([n.name for n in tree.body if isinstance(n, ast.FunctionDef)], ['answer', 'other'])
        self.assertIn('return 42', code)

    def test_tool_exception_becomes_event_and_loop_continues(self):
        p = self.project()
        actions = [dict(tool='write', path='../escape', content='bad'), dict(tool='finish', summary='Не смогла')]
        with patch.object(codewrite, 'own_ready', return_value=False), \
             patch.object(codewrite, 'available', return_value=True), \
             patch.object(codewrite, '_call', side_effect=[json.dumps(x) for x in actions]):
            result = wait_job(agent.start(p['id'], 'change', 'owner'))
        self.assertTrue(any(e['status'] == 'Действие не выполнено' for e in result['events']))
        self.assertFalse(result['changed'])

if __name__ == '__main__':
    unittest.main()
