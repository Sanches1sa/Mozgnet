from pathlib import Path
import sys,io,wave,unittest,subprocess,tempfile,shutil
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
import pwm_voice

def audio(frames=24000):
 b=io.BytesIO()
 with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*frames)
 return b.getvalue()

class TransportTests(unittest.TestCase):
 def test_acknowledged_full_stream_with_backpressure(self):
  expected=24000;state={'seq':0,'accepted':0,'played':0,'eof':False};methods=[]
  def call(m,*args,**kw):
   methods.append(m)
   if m=='pcm_stop':return 'stopped'
   if m=='pcm_write':
    seq,hexdata=args[0].split(':');self.assertEqual(int(seq),state['seq']);state['accepted']+=len(bytes.fromhex(hexdata));state['seq']+=1
   if m=='pcm_end':state['eof']=True;self.assertEqual(state['accepted'],expected)
   if m=='pcm_status':state['played']=min(state['accepted'],state['played']+1024)
   queued=state['accepted']-state['played'];phase='done' if state['eof'] and queued==0 else 'playing'
   return '%s:%d:%d:%d:0'%(phase,state['seq'],2048-queued,state['played'])
  result=pwm_voice.play(audio(),call,sleep=lambda t:None);self.assertEqual(result['samples'],expected);self.assertEqual(result['pin'],9);self.assertEqual(methods[-1],'pcm_stop');self.assertGreater(methods.count('pcm_status'),20)
 def test_missing_firmware_explicit_failure(self):
  with self.assertRaisesRegex(RuntimeError,'unavailable'):pwm_voice.play(audio(100),lambda *a,**kw:'error:D9_PWM_or_TIM16_unavailable')
 def test_midstream_error_stops_without_claiming_playback(self):
  calls=[]
  def call(m,*a,**kw):calls.append(m);return 'buffering:0:32768:0:0' if m=='pcm_begin' else 'error:transport'
  with self.assertRaises(RuntimeError):pwm_voice.play(audio(100),call)
  self.assertEqual(calls[-1],'pcm_stop')
 def test_resampler_preserves_duration_and_dc_silence(self):
  data=pwm_voice.pcm8(audio(8000));self.assertEqual(len(data),8000);self.assertEqual(set(data),{128})
 @unittest.skipUnless(shutil.which('g++'),'Needs host C++ compiler')
 def test_real_ring_preserves_order_bounds_and_underrun(self):
  header=Path(__file__).resolve().parents[1]/'sketch/pcm_ring.h'
  code='#include <cassert>\n#include "'+str(header)+'"\n'+'''int main(){PcmRing r;uint8_t block[512];for(int i=0;i<512;i++)block[i]=i%256;
 r.reset(40000);for(int i=0;i<64;i++)assert(r.push(i,block,512));assert(!r.push(64,block,512));assert(!r.finish());r.active=true;
 for(int i=0;i<32768;i++)assert(r.take()==i%256);assert(r.played==32768);assert(!r.push(2,block,512));
 for(int i=64;i<78;i++)assert(r.push(i,block,512));assert(r.push(78,block,64));assert(r.finish());
 while(r.active)r.take();assert(r.done&&r.played==40000&&!r.failed);
 r.reset(1);r.active=true;for(int i=0;i<8000;i++)r.take();assert(r.failed&&!r.active);
}'''
  with tempfile.TemporaryDirectory() as d:
   source=Path(d)/'test.cpp';source.write_text(code);target=Path(d)/'test';subprocess.run(['g++','-std=c++17',str(source),'-o',str(target)],check=True,capture_output=True);subprocess.run([str(target)],check=True,capture_output=True)
if __name__=='__main__':unittest.main()
