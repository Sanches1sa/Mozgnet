"""PCM WAV -> filtered mono 8 kHz -> bounded, acknowledged D9 PWM stream."""
import io,math,struct,time,wave

def pcm8(data):
 with wave.open(io.BytesIO(data),'rb') as w:
  if w.getcomptype()!='NONE' or w.getsampwidth() not in (1,2) or w.getnchannels() not in (1,2):raise ValueError('Нужен PCM WAV 8/16 бит, моно/стерео')
  channels=w.getnchannels();width=w.getsampwidth();rate=w.getframerate();raw=w.readframes(w.getnframes())
  if rate<8000 or rate>96000:raise ValueError('Частота WAV вне диапазона 8–96 кГц')
 if width==2:values=[v/32768 for v in struct.unpack('<%dh'%(len(raw)//2),raw)]
 else:values=[(v-128)/128 for v in raw]
 mono=[sum(values[i:i+channels])/channels for i in range(0,len(values),channels)]
 # Low-pass before decimation. Two cascaded RC sections avoid aliasing high harmonics.
 alpha=1-math.exp(-2*math.pi*2800/rate);a=b=0.;filtered=[]
 for v in mono:a+=alpha*(v-a);b+=alpha*(a-b);filtered.append(b)
 count=int(len(mono)*8000/rate)
 if not count or count>480000:raise ValueError('Часть речи должна длиться 0–60 секунд')
 out=bytearray()
 for j in range(count):
  pos=j*rate/8000;i=int(pos);f=pos-i;v=filtered[i]*(1-f)+filtered[min(i+1,len(filtered)-1)]*f
  # Headroom and 10ms envelope reduce clipping and switching clicks.
  gain=min(1,j/80,(count-1-j)/80)*.85
  out.append(max(0,min(255,round(128+127*v*gain))))
 return bytes(out)

def play(data,call,enabled=lambda:True,sleep=time.sleep,clock=time.monotonic):
 samples=pcm8(data);started=False
 def invoke(method,*args):
  r=call(method,*args,timeout=4,channel='pcm')
  if isinstance(r,bytes):r=r.decode('ascii','replace')
  parts=str(r or '').split(':')
  if len(parts)!=5 or parts[0] not in ('buffering','playing','done'):raise RuntimeError('PWM D9: '+str(r or 'нет ответа Bridge'))
  return parts[0],*[int(x) for x in parts[1:]]
 try:
  state=invoke('pcm_begin',str(len(samples)));started=True;offset=sequence=0;deadline=clock()+len(samples)/8000+45
  while offset<len(samples):
   if not enabled():raise RuntimeError('Озвучка остановлена')
   if clock()>deadline:raise RuntimeError('PWM D9: истекло время передачи')
   block=samples[offset:offset+512]
   if state[2]<len(block):sleep(.03);state=invoke('pcm_status');continue
   if state[1]!=sequence:raise RuntimeError('PWM D9: нарушена последовательность частей')
   state=invoke('pcm_write',str(sequence)+':'+block.hex());sequence+=1;offset+=len(block)
  state=invoke('pcm_end')
  while state[0]!='done':
   if not enabled():raise RuntimeError('Озвучка остановлена')
   if clock()>deadline:raise RuntimeError('PWM D9: нет подтверждения окончания')
   sleep(.05);state=invoke('pcm_status')
  if state[3]!=len(samples):raise RuntimeError('PWM D9: воспроизведён не весь звук')
  return {'samples':len(samples),'underruns':state[4],'pin':9,'rate':8000}
 finally:
  if started:call('pcm_stop',timeout=4,channel='pcm')
