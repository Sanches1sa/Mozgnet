#pragma once
#include <stdint.h>
#include <stddef.h>
// Caller serializes push/take; one PCM8 stream, no silent overwrite.
struct PcmRing {
#ifndef NEURO_PCM_RING_CAPACITY
#define NEURO_PCM_RING_CAPACITY 32768
#endif
 static const uint32_t capacity=NEURO_PCM_RING_CAPACITY;
 uint8_t data[capacity];volatile uint32_t head=0,tail=0,count=0,accepted=0,played=0,expected=0,sequence=0,underruns=0;
 volatile bool eof=false,active=false,done=false,failed=false;
 void reset(uint32_t n){head=tail=count=accepted=played=sequence=underruns=0;expected=n;eof=active=done=failed=false;}
 bool push(uint32_t seq,const uint8_t* src,size_t n){
  if(failed||done||eof||seq!=sequence||!n||n>capacity-count||accepted+n>expected)return false;
  for(size_t i=0;i<n;i++){data[head]=src[i];head=(head+1)%capacity;}count+=n;accepted+=n;sequence++;return true;
 }
 bool finish(){if(accepted!=expected||failed)return false;eof=true;return true;}
 uint8_t take(){
  if(!active||failed||done)return 128;
  if(!count){if(eof){done=true;active=false;}else if(++underruns>=8000){failed=true;active=false;}return 128;}
  uint8_t v=data[tail];tail=(tail+1)%capacity;count--;played++;
  if(!count&&eof){done=true;active=false;}return v;
 }
};
