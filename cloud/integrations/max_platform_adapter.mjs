/** Run on the OTHER PLATFORM'S SERVER (Node 18+), never in a mini-app/browser.
 * Use its existing MAX event handler and its existing send-message function.
 * No second webhook, polling worker or MAX token is created here.
 */
export async function cloudAnswer(text,{chatId,userId}) {
  const origin=(process.env.NEURO_CLOUD_URL||'').replace(/\/$/,'');
  const key=process.env.NEURO_CLOUD_API_KEY;
  if(!origin||!key)throw new Error('Configure NEURO_CLOUD_URL and NEURO_CLOUD_API_KEY on the platform server');
  const address=new URL(origin);if(!['http:','https:'].includes(address.protocol)||address.username||address.password||address.pathname!=='/'||address.search||address.hash)throw new Error('NEURO_CLOUD_URL must be the origin without /api/v1');
  const response=await fetch(origin+'/api/v1/support/ask',{
    method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},
    body:JSON.stringify({question:text,user_id:String(userId),session:'max:'+String(chatId)}),
    signal:AbortSignal.timeout(120000)
  });
  if(!response.ok)throw new Error('Cloud API HTTP '+response.status);
  const result=await response.json();if(!result.ok)throw new Error('Cloud API failed');
  for(const action of result.actions||[]){if(action.url?.startsWith('/'))action.url=origin+action.url;}
  return result;
}

export async function answerInExistingMaxHandler(event,sendMessage){
  // The host platform must authenticate its MAX webhook before calling this.
  // Its local commands/ticket actions run BEFORE this AI branch.
  const result=await cloudAnswer(event.text,{chatId:event.chatId,userId:event.userId});
  for(const part of result.parts?.length?result.parts:[result.answer]){
    if(part)await sendMessage(event.chatId,part);
  }
  // result.actions carries an open_app link. Map it to the platform's MAX UI.
  return result;
}
