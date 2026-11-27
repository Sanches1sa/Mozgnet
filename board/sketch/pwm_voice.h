#pragma once
// UNO Q: D9/PB8 = TIM4_CH3. D6 uses TIM3; leave its period unchanged.
// TIM16/counter_servo is reserved here; do not use Arduino_HardwareServo concurrently.
//
// v2.98.1: PCM на D9 использует НАПРЯМУЮ драйверы Zephyr (DEVICE_DT_GET, pwm_*,
// counter_*, irq_lock). Скетч UNO Q грузится как llext-модуль: эти символы ядро
// может не экспортировать — сборка проходит, но при загрузке на MCU скетч молча
// не стартует, и приложение вечно пишет «мост ждёт скетч». Поэтому по умолчанию
// драйверный код НЕ компилируется (RPC pcm_* отвечают error:D9_PWM_or_TIM16_unavailable,
// кольцевой буфер не занимает ОЗУ). Включать только осознанно: -DNEURO_PCM_ENABLE.
#if defined(NEURO_PCM_ENABLE) && defined(CONFIG_PWM) && defined(CONFIG_COUNTER) && __has_include(<zephyr/drivers/counter.h>)
#include <zephyr/kernel.h>
#include <zephyr/drivers/pwm.h>
#include <zephyr/drivers/counter.h>
#if DT_NODE_HAS_STATUS(DT_NODELABEL(pwm4), okay) && DT_NODE_HAS_STATUS(DT_NODELABEL(counter_servo), okay)
#define NEURO_PCM_SUPPORTED 1
#endif
#endif
#ifndef NEURO_PCM_SUPPORTED
#define NEURO_PCM_SUPPORTED 0
#endif
#if !NEURO_PCM_SUPPORTED
#define NEURO_PCM_RING_CAPACITY 16384   // v2.111.3: программный PCM — до ~2 с звука (было 24 КБ; память llext-модуля)
#endif
#include "pcm_ring.h"
static PcmRing pcmRing;
static bool pcmReady=false;
#if NEURO_PCM_SUPPORTED
/* v2.104: device_is_ready() — НЕ inline: это вызов функции ядра (z_device_is_ready),
 * которую llext-скетч может не получить при загрузке -> "undefined" -> скетч не
 * стартует. Проверяем указатели сами (header-only): pwm4 уже поднят
 * analogWrite(9) ядром Arduino, у счётчика API задан статически. */
#define PCM_DEV_OK(d) ((d)!=nullptr&&(d)->api!=nullptr)
static const struct device* const pcmPwm=DEVICE_DT_GET(DT_NODELABEL(pwm4));
static const struct device* const pcmClock=DEVICE_DT_GET(DT_NODELABEL(counter_servo));
static uint64_t pcmPwmHz=0;
static uint32_t pcmPeriod=0;
static void pcmTimer(const struct device*,void*){
 uint8_t sample=pcmRing.take();
 int err=pwm_set_cycles(pcmPwm,3,pcmPeriod,(uint32_t)(((uint64_t)pcmPeriod*sample)/255),0);
 if(err){pcmRing.failed=true;pcmRing.active=false;}
 if(!pcmRing.active){counter_stop(pcmClock);pwm_set_cycles(pcmPwm,3,pcmPeriod,0,0);}
}
static bool pcmInit(){
 if(pcmReady)return true;
 analogWrite(9,128); // Core performs the per-channel pinctrl and deferred device init.
 if(!PCM_DEV_OK(pcmPwm)||!PCM_DEV_OK(pcmClock))return false;
 if(pwm_get_cycles_per_sec(pcmPwm,3,&pcmPwmHz))return false;
 pcmPeriod=pcmPwmHz/31250u;if(pcmPeriod<256)return false;
 if(pwm_set_cycles(pcmPwm,3,pcmPeriod,0,0))return false;
 uint32_t hz=counter_get_frequency(pcmClock);if(hz<8000||hz%8000)return false;
 struct counter_top_cfg top={};top.ticks=hz/8000-1;top.callback=pcmTimer;
 if(top.ticks<1||top.ticks>counter_get_max_top_value(pcmClock))return false;
 counter_stop(pcmClock);if(counter_set_top_value(pcmClock,&top))return false;
 pcmReady=true;return true;
}
static void pcmHalt(){if(pcmReady){counter_stop(pcmClock);pwm_set_cycles(pcmPwm,3,pcmPeriod,0,0);}pcmRing.active=false;}
static bool pcmStart(){if(pcmRing.active||pcmRing.done)return true;pcmRing.active=true;if(counter_start(pcmClock)){pcmRing.active=false;pcmRing.failed=true;return false;}return true;}
#else
/* v2.103: ПРОГРАММНЫЙ PCM на D9 — без драйверов Zephyr (llext их не грузит),
 * только pinMode/digitalWrite/micros. Звук 8 кБ/с x 8 бит целиком копится в
 * буфере (pcm_begin/pcm_write/pcm_end), по pcm_end loop() один раз проигрывает
 * его БЛОКИРУЮЩЕ: 1-битная сигма-дельта на 64 кГц (8 шагов по 15,6 мкс на
 * отсчёт) -> RC-фильтр/усилитель на D9. Пока звучит (<= ~3 с) нейроны не
 * опрашиваются — для коротких слов («Роуз») это незаметно. */
#define NEURO_PCM_SOFT 1
static volatile bool pcmSoftPending=false;
static bool pcmInit(){
 if(pcmReady)return true;
 pinMode(9,OUTPUT);digitalWrite(9,LOW);pcmReady=true;return true;
}
static void pcmHalt(){pcmSoftPending=false;pcmRing.active=false;if(pcmReady)digitalWrite(9,LOW);}
static bool pcmStart(){
 if(pcmRing.active||pcmRing.done)return true;
 pcmRing.active=true;pcmSoftPending=true;return true;   // играет pcmSoftTick() из loop()
}
static void pcmSoftTick(){
 if(!pcmSoftPending)return;
 pcmSoftPending=false;
 uint16_t acc=128;                       // накопитель сигма-дельты
 uint32_t t0=micros();
 while(pcmRing.active&&!pcmRing.failed&&!pcmRing.done){
  uint8_t smp=pcmRing.take();
  for(uint8_t k=0;k<8;k++){
   acc+=smp;
   bool bit=acc>=256;if(bit)acc-=256;
   digitalWrite(9,bit?HIGH:LOW);
   uint32_t target=t0+(uint32_t)((k+1)*125u/8u);
   while((int32_t)(micros()-target)<0){}
  }
  t0+=125;
 }
 digitalWrite(9,LOW);
}
#endif
#if NEURO_PCM_SUPPORTED
static inline void pcmSoftTick(){}
#endif
static String pcmReport(){
#if NEURO_PCM_SUPPORTED
 unsigned key=irq_lock();
#endif
 bool failed=pcmRing.failed,done=pcmRing.done,active=pcmRing.active;
 uint32_t sequence=pcmRing.sequence,free=PcmRing::capacity-pcmRing.count,played=pcmRing.played,under=pcmRing.underruns;
#if NEURO_PCM_SUPPORTED
 irq_unlock(key);
#endif
 String s=String(failed?"error":done?"done":active?"playing":"buffering")+":"+sequence+":"+free+":"+played+":"+under;
 return s;
}
static String rpcPcmBegin(String value){
 long n=value.toInt();if(n<1||n>480000)return String("error:sample_count");
 if(!pcmInit())return String("error:D9_PWM_or_TIM16_unavailable");pcmHalt();
#ifdef NEURO_PCM_SOFT
 if((uint32_t)n>PcmRing::capacity)return String("error:too_long_for_soft_pcm");
#endif
 pcmRing.reset((uint32_t)n);return pcmReport();
}
static String rpcPcmWrite(String value){
 int colon=value.indexOf(':');if(colon<1)return String("error:packet");
 String hex=value.substring(colon+1);size_t n=hex.length()/2;if(!n||n>512||hex.length()%2)return String("error:packet_size");
 static uint8_t data[512];for(size_t i=0;i<n;i++){unsigned v=0;for(int j=0;j<2;j++){char c=hex[2*i+j];int k=c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:-1;if(k<0)return String("error:hex");v=v*16+k;}data[i]=(uint8_t)v;}
#if NEURO_PCM_SUPPORTED
 unsigned key=irq_lock();
#endif
 bool ok=pcmReady&&pcmRing.push(value.substring(0,colon).toInt(),data,n);
 bool start=ok&&!pcmRing.active&&pcmRing.count>=(PcmRing::capacity<16384?PcmRing::capacity/2:8192);
#ifdef NEURO_PCM_SOFT
 start=false;                 // программный режим: играем целиком по pcm_end
#endif
#if NEURO_PCM_SUPPORTED
 irq_unlock(key);
#endif
 if(!ok)return String("error:sequence_or_buffer");if(start&&!pcmStart())return String("error:timer_start");return pcmReport();
}
static String rpcPcmEnd(){
#if NEURO_PCM_SUPPORTED
 unsigned key=irq_lock();
#endif
 bool ok=pcmReady&&pcmRing.finish();
#if NEURO_PCM_SUPPORTED
 irq_unlock(key);
#endif
 if(!ok)return String("error:incomplete_stream");if(!pcmStart())return String("error:timer_start");return pcmReport();
}
static String rpcPcmStatus(){return pcmReport();}
static String rpcPcmStop(){pcmHalt();pcmRing.failed=true;return String("stopped");}
