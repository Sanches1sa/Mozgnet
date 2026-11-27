import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import test_repairs
import api_board
import tts
import neuron_context as nc
board_python=Path(__file__).resolve().parents[3]/'neuro12-app-work/neuro12-app/python'
sys.path.insert(0,str(board_python))
try:import neural_bridge
except ImportError:neural_bridge=None

class CompatibilityTests(unittest.TestCase):
 @unittest.skipIf(neural_bridge is None,'Cross-project source test requires board source alongside cloud')
 def test_board_frame_is_accepted_by_cloud(self):
  bus=neural_bridge.NeuronBus();bus.ingest(''.join('N%d:1:2:память|'%i for i in range(12)))
  with patch.object(nc,'_FRAMES',{}):
   self.assertTrue(nc.note('uno-q',bus.snapshot()));self.assertIn('память',nc.snapshot()['words'])
 def test_russian_endpoint_never_substitutes_tones(self):
  with patch.object(tts,'synth_espeak',return_value=None),patch.object(tts,'synth_builtin') as fake:
   result=api_board.h_tts_ru(SimpleNamespace(body={'text':'Проверка речи.'}));self.assertEqual(result[0],503);fake.assert_not_called()
 def test_text_bound_and_success(self):
  self.assertEqual(api_board.h_tts_ru(SimpleNamespace(body={'text':'я'*601}))[0],400)
  with patch.object(tts,'synth_espeak',return_value=b'RIFFtest'):
   self.assertEqual(api_board.h_tts_ru(SimpleNamespace(body={'text':'Проверка речи.'})),(200,'wav',b'RIFFtest'))
if __name__=='__main__':unittest.main()
