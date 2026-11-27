import io,sys,tempfile,unittest,hashlib,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import numpy as np
import tinyllm,loop_transformer,photo_ocr,ocr_dataset,store
from PIL import Image,ImageDraw
from PIL.PngImagePlugin import PngInfo

class LoopTests(unittest.TestCase):
 def setUp(self):
  c=dict(tinyllm.cfg(),llm_d=12,llm_layers=2,llm_heads=3,llm_ctx=8)
  self.m=tinyllm.GPT(c);self.m.init_params(20)
  self.gain=patch.object(self.m,'_gains',return_value=[1.]*4);self.gain.start()
  self.fp=patch.object(self.m,'_fp',return_value=None);self.fp.start()
  self.x=np.array([[1,2,3,4]])
  tinyllm._FP.loop=None
 def tearDown(self):
  tinyllm._FP.loop=None;self.gain.stop();self.fp.stop()
 def test_recurrence_changes_logits_without_touching_weights(self):
  before={k:v.copy() for k,v in self.m.p.items()}
  base,_=self.m.forward(self.x)
  tinyllm._FP.loop=dict(enabled=True,passes=3,mix=.25)
  recurrent,_=self.m.forward(self.x)
  self.assertFalse(np.array_equal(base,recurrent))
  for k,v in before.items():np.testing.assert_array_equal(v,self.m.p[k])
  tinyllm._FP.loop=None
  np.testing.assert_array_equal(base,self.m.forward(self.x)[0])
 def test_single_pass_is_exactly_compatible(self):
  base,_=self.m.forward(self.x)
  tinyllm._FP.loop=dict(enabled=True,passes=1,mix=.9)
  np.testing.assert_array_equal(base,self.m.forward(self.x)[0])
 def test_training_caches_and_gradients_ignore_loop(self):
  y=np.array([[2,3,4,5]])
  a,ga=self.m.grads(self.x,y)
  tinyllm._FP.loop=dict(enabled=True,passes=4,mix=1.)
  b,gb=self.m.grads(self.x,y)
  self.assertEqual(a,b)
  for k in ga:np.testing.assert_array_equal(ga[k],gb[k])
  self.assertEqual(len(self.m.forward(self.x,cache=True)[1][0]),2)
 def test_causality_survives_recurrence(self):
  tinyllm._FP.loop=dict(enabled=True,passes=3,mix=.5)
  a,_=self.m.forward(self.x);b,_=self.m.forward(np.array([[1,2,3,9]]))
  np.testing.assert_array_equal(a[:,:3],b[:,:3])
 def test_configuration_clamped(self):
  c=loop_transformer.config(dict(llm_loop_passes=100,llm_loop_mix=float('nan')))
  self.assertEqual(c['passes'],4);self.assertEqual(c['mix'],.25);self.assertFalse(c['enabled'])

class DatasetTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(store,'DATA_DIR',self.tmp.name);self.p.start()
  self.state=dict(ocr_dataset._STATE);photo_ocr._STATE.clear();photo_ocr._STATE['running']=False
 def tearDown(self):
  self.p.stop();self.tmp.cleanup();ocr_dataset._STATE.clear();ocr_dataset._STATE.update(self.state);photo_ocr._IMPORTING=False
 def archive(self):
  path=Path(self.tmp.name,'fixture.zip')
  with zipfile.ZipFile(path,'w') as z:
   for char in ocr_dataset.ALPHABET:
    for i in range(2):
     im=Image.new('RGBA',(40,40),(0,0,0,0));d=ImageDraw.Draw(im)
     # Distinct stable pixels; tests importer mapping, not language accuracy.
     d.rectangle((5,6,12+i,26),fill='black');d.line((8,15,20+ord(char)%11,22),fill='black',width=3)
     meta=PngInfo();meta.add_text('fixture',char+str(i))
     buf=io.BytesIO();im.save(buf,format='PNG',pnginfo=meta);z.writestr('Cyrillic/'+char+'/'+str(i)+'.png',buf.getvalue())
  return str(path)
 def test_import_russian_mapping_and_holdouts(self):
  with patch.object(ocr_dataset,'_download',return_value=self.archive()):ocr_dataset._import(66)
  self.assertEqual(ocr_dataset._STATE['phase'],'done',ocr_dataset._STATE)
  rows=store.load_json('photo_ocr_samples.json',[])
  self.assertEqual(set(c for r in rows for c in r['chars']),set(ocr_dataset.ALPHABET))
  self.assertTrue(any(r['chars']==['Ё'] for r in rows));self.assertTrue(any(r['chars']==['Й'] for r in rows))
  self.assertEqual(sum(r['split']=='validation' for r in rows),33)
 def test_checksum_rejects_modified_file(self):
  path=self.archive();self.assertFalse(ocr_dataset._valid(path))
 def test_transparency_and_diacritic_single_glyph(self):
  im=Image.new('RGBA',(40,40),(0,0,0,0));d=ImageDraw.Draw(im);d.rectangle((8,14,20,35),fill='black');d.rectangle((9,3,13,7),fill='black');d.rectangle((17,3,21,7),fill='black')
  buf=io.BytesIO();im.save(buf,format='PNG')
  self.assertTrue(photo_ocr.add(buf.getvalue(),'Ё',single=True)['ok'])
  rows=store.load_json('photo_ocr_samples.json',[]);self.assertEqual(rows[0]['chars'],['Ё']);self.assertGreater(sum(rows[0]['x'][0]),0)
 def test_progress_finishes_at_100(self):
  rows=[]
  for i in range(4):
   rows.append(dict(id=str(i),chars=['А','Б'],x=[[0.]*480,[1.]*480],split='validation' if i==0 else 'train'))
  photo_ocr._train(rows,3)
  self.assertEqual(photo_ocr._STATE['phase'],'done',photo_ocr._STATE);self.assertEqual(photo_ocr._STATE['percent'],100)
  self.assertIn('loss',photo_ocr._STATE)
if __name__=='__main__':unittest.main()
