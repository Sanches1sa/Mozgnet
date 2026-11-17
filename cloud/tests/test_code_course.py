import ast
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import test_repairs
import codeboot
import store
import tinyllm as tl


class CodeCourseTests(unittest.TestCase):
    def test_controls_not_in_training_and_code_roundtrip(self):
        texts = codeboot._course()
        self.assertGreaterEqual(len(texts), 20)
        for _, code, _ in codeboot._EVAL:
            self.assertFalse(any(code in text for text in texts))
        if tl.HAVE_NP:
            tok = tl.Tokenizer()
            tok.build(texts, 512)
            code = 'def MyName(x):\n    return x + 1\n'
            self.assertEqual(tok.decode_code(tok.encode_code(code)), code)

    def test_arithmetic_checker_never_executes_generated_code(self):
        self.assertTrue(codeboot._simple_cases('def f(x):\n    return x*x', [([3],9)]))
        self.assertFalse(codeboot._simple_cases('def f(x):\n    return x+1', [([3],9)]))
        self.assertIsNone(codeboot._simple_cases('def f(x):\n    return __import__("os").system("echo bad")', [([3],9)]))
        self.assertIsNone(codeboot._simple_cases('@malicious\ndef f(x):\n    return x*x', [([3],9)]))

    def test_scheduler_rechecks_reservation_after_corpus_scan(self):
        if not tl.HAVE_NP:
            self.skipTest('NumPy unavailable')
        from types import SimpleNamespace
        fake=SimpleNamespace(training=False,ready=False,steps=0,meta={})
        def corpus(*args,**kwargs):
            fake.training=True
            return []
        with patch.object(tl,'_THREAD',[None]), patch.object(tl,'model',return_value=fake), patch.object(tl,'_deleted_flag',return_value=False), patch('kb.list_articles',side_effect=corpus), patch.object(tl.threading,'Thread') as thread:
            tl.ensure_training()
            thread.assert_not_called()
        fake.training=False; fake.ready=True; fake.steps=1
        def new():
            fake.training=True
            return [('key','code')], [('key','code')]
        with patch.object(tl,'_THREAD',[None]), patch.object(tl,'model',return_value=fake), patch.object(tl,'_deleted_flag',return_value=False), patch.object(tl,'new_material',side_effect=new), patch.object(tl.threading,'Thread') as thread:
            self.assertEqual(tl.learn_new(),'busy')
            thread.assert_not_called()

    def test_wait_survives_long_step_and_can_be_cancelled(self):
        from types import SimpleNamespace
        class Previous:
            left=3
            def is_alive(self): return self.left>0
            def join(self,timeout): self.left-=1
        codeboot._CANCEL.clear()
        with patch.object(codeboot.time,'monotonic',side_effect=[0,200,400,600]), patch.object(codeboot,'_update') as update:
            self.assertTrue(codeboot._wait_previous(SimpleNamespace(),Previous()))
            self.assertGreaterEqual(update.call_args.kwargs['waiting_seconds'],600)
        codeboot._CANCEL.set()
        try:
            with patch.object(codeboot,'_update') as update:
                self.assertFalse(codeboot._wait_previous(SimpleNamespace(),Previous()))
                self.assertEqual(update.call_args.kwargs['phase'],'cancelled')
        finally:
            codeboot._CANCEL.clear()

    def test_real_gradient_course_preserves_architecture_and_backup(self):
        if not tl.HAVE_NP:
            self.skipTest('NumPy unavailable')
        import numpy as np
        old_m, old_thread = tl._M, tl._THREAD[0]
        settings = store.settings()
        store.update_settings({'llm_disk_state': False})
        cfg = dict(tl.cfg(), llm_d=16, llm_layers=1, llm_heads=2, llm_ctx=32, llm_vocab=512, llm_lr=.002)
        m = tl.GPT(cfg)
        texts = codeboot._course() + [codecorpus_mark + '# task: ' + task + '\n' + code for task, code, _ in codeboot._EVAL for codecorpus_mark in ['⟦код⟧']]
        m.tok.build(texts, 512); m.init_params(m.tok.size); m.steps=1; m.ready=True
        before = {k:v.copy() for k,v in m.p.items()}
        old_ids = dict(m.tok.vocab)
        tl._M=m; tl._THREAD[0]=None
        try:
            with patch.object(tl, 'cfg', return_value=cfg):
                codeboot.start(steps=20, seconds=20)
                until = time.monotonic()+30
                while codeboot._THREAD.is_alive() and time.monotonic()<until:
                    codeboot._THREAD.join(.05)
                result = codeboot.status()
            self.assertFalse(result['running'])
            self.assertEqual(result['phase'],'done',result)
            self.assertEqual(result['done'],20)
            self.assertLess(result['after']['loss'], result['before']['loss'])
            self.assertTrue(Path(result['backup']+'.npz').is_file())
            self.assertEqual(m.c,cfg)
            self.assertTrue(all(m.tok.vocab[k]==v for k,v in old_ids.items()))
            self.assertFalse(m.training)
            self.assertFalse(m._code_course)
            self.assertTrue(any(not np.array_equal(before[k],m.p[k]) for k in before))
            print('Measured code loss:',result['before']['loss'],'->',result['after']['loss'])
            # If a course regresses on controls, restore both saved weights
            # and the old step count rather than publishing the worse model.
            prior_weights={k:v.copy() for k,v in m.p.items()}
            prior_steps=m.steps
            with patch.object(tl,'cfg',return_value=cfg), patch.object(codeboot,'_loss',side_effect=[1.0,2.0]), patch.object(codeboot,'_probe',return_value=[]):
                codeboot.start(steps=2,seconds=5)
                codeboot._THREAD.join(10)
                rolled=codeboot.status()
            self.assertTrue(rolled['rollback'],rolled)
            self.assertEqual(m.steps,prior_steps)
            self.assertTrue(all(np.array_equal(v,m.p[k]) for k,v in prior_weights.items()))
            # Actual two-source training: one updater, ten code and ten text steps.
            with patch.object(tl,'cfg',return_value=cfg), patch.object(codeboot,'_ordinary_texts',return_value=['Numbers and arithmetic help explain addition and multiplication. '*3]), patch.object(codeboot,'_probe',return_value=[]):
                codeboot.start(steps=20,seconds=20,mode='dual')
                codeboot._THREAD.join(10)
                dual=codeboot.status()
            self.assertFalse(dual['running'],dual)
            self.assertEqual(dual['phase'],'done',dual)
            self.assertEqual((dual['code_steps'],dual['text_steps']),(10,10))
            self.assertIsNotNone(dual['before']['text_loss'])
            # Yield after a microbatch: no Adam step and no parameter changes.
            before_yield={k:v.copy() for k,v in m.p.items()}
            previous_adam_time=m.adam['t'] if m.adam else None
            m._stop=False; m._code_course=True
            def gradients(*args):
                m._stop=True
                return 1.0, {k:np.zeros_like(v) for k,v in m.p.items()}
            tl._FP.stable_code=True
            try:
                with patch.object(m,'micro_size',return_value=1), patch.object(m,'grads',side_effect=gradients):
                    with self.assertRaises(tl._TrainingHandoff):
                        m.train_step(np.ones((2,4),dtype=np.int64),np.ones((2,4),dtype=np.int64),.001)
                self.assertEqual(m.adam['t'] if m.adam else None,previous_adam_time)
                self.assertTrue(all(np.array_equal(v,m.p[k]) for k,v in before_yield.items()))
            finally:
                m._stop=False; m._code_course=False; tl._FP.stable_code=False


        finally:
            if codeboot._THREAD and codeboot._THREAD.is_alive():
                codeboot.stop(); codeboot._THREAD.join(10)
            tl._M=old_m; tl._THREAD[0]=old_thread
            store.save_json('settings.json',settings)

if __name__=='__main__': unittest.main()
