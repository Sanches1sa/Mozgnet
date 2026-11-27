import io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import store,photo_ocr,printed_dataset,ocr_dataset
from PIL import Image,ImageDraw,ImageFont

class PrintedTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(store,'DATA_DIR',self.tmp.name);self.p.start()
  self.state=dict(photo_ocr._STATE);self.ds=dict(printed_dataset._STATE)
  photo_ocr._STATE.clear();photo_ocr._STATE['running']=False;photo_ocr._IMPORTING=False
  self.raw=[]
  for i in range(3):
   im=Image.new('RGB',(150+i,50),'white');d=ImageDraw.Draw(im)
   d.text((5,4),'A B A B',font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',26),fill='black')
   out=io.BytesIO();im.save(out,format='PNG');self.raw.append(out.getvalue())
 def tearDown(self):
  self.p.stop();self.tmp.cleanup();photo_ocr._STATE.clear();photo_ocr._STATE.update(self.state);printed_dataset._STATE.clear();printed_dataset._STATE.update(self.ds);photo_ocr._IMPORTING=False
 def do_import(self):
  rows=[dict(row_idx=i,row=dict(text='ABAB',image=dict(src=printed_dataset.ASSET_PREFIX+str(i)+'/image/image.jpg'))) for i in range(3)]
  with patch.object(printed_dataset,'_get',return_value=json.dumps(dict(rows=rows)).encode()),patch.object(printed_dataset,'_asset',side_effect=lambda u:self.raw[int(u[len(printed_dataset.ASSET_PREFIX):].split('/')[0])]):
   printed_dataset._import(3)
 def test_import_pairs_and_prepare(self):
  self.do_import();s=printed_dataset.status()
  self.assertEqual(s['phase'],'done',s);self.assertEqual(s['downloaded_pages'],3);self.assertEqual(s['prepared_pages'],3)
  self.assertEqual(printed_dataset.image_bytes(1),self.raw[1])
  rows=store.load_json('photo_ocr_samples.json',[])
  self.assertEqual(rows[0]['split'],'validation');self.assertEqual(rows[1]['split'],'train')
  self.assertEqual(rows[0]['chars'],list('ABAB'))
 def test_cached_import_works_without_network(self):
  self.do_import()
  with patch.object(printed_dataset,'_get',side_effect=AssertionError('unexpected network')),patch.object(printed_dataset,'_asset',side_effect=AssertionError('unexpected download')):
   printed_dataset._import(3)
  self.assertEqual(printed_dataset.status()['phase'],'done')
  self.assertEqual(len(store.load_json('photo_ocr_samples.json',[])),3)
 def test_mismatch_not_used(self):
  with self.assertRaises(ValueError):printed_dataset._prepare(self.raw[0],'ABCDEF',0)
  self.assertEqual(store.load_json('photo_ocr_samples.json',[]),[])
 def test_partial_prefix_explicit(self):
  r=printed_dataset._prepare(self.raw[0],'ABAB OTHERWORD',1)
  self.assertTrue(r['partial']);self.assertEqual(r['characters'],4)
 def test_manual_crop_preserves_page_holdout(self):
  self.do_import();r=printed_dataset.add_crop(0,[4,4,26,34],'A',single=True)
  self.assertTrue(r['ok']);rows=store.load_json('photo_ocr_samples.json',[])
  self.assertEqual(rows[-1]['split'],'validation')
  with self.assertRaises(ValueError):printed_dataset.add_crop(0,[-1,0,5,5],'A',single=True)
 def test_image_path_is_numeric_and_known(self):
  with self.assertRaises(ValueError):printed_dataset.image_bytes('../settings.json')
  with self.assertRaises(ValueError):printed_dataset.image_bytes(1000)
 def test_asset_source_pinned(self):
  with self.assertRaises(ValueError):printed_dataset._asset('http://127.0.0.1/settings')
 def test_shared_import_exclusion(self):
  photo_ocr._IMPORTING=True
  with self.assertRaises(ValueError):printed_dataset.start()
  with self.assertRaises(ValueError):ocr_dataset.start()
if __name__=='__main__':unittest.main()
