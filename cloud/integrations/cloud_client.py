"""Server-side client for a MAX bot hosted on any other platform.
Set NEURO_CLOUD_URL and NEURO_CLOUD_API_KEY in that platform's secret storage.
This client neither polls MAX nor claims an external ticket was created.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class CloudClient:
    def __init__(self,base=None,key=None):
        self.base=(base or os.environ.get('NEURO_CLOUD_URL','')).rstrip('/')
        self.key=key or os.environ.get('NEURO_CLOUD_API_KEY','')
        parsed=urllib.parse.urlsplit(self.base)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:raise ValueError('Set NEURO_CLOUD_URL to the cloud origin without /api/v1')
        if not self.key:raise ValueError('Set NEURO_CLOUD_API_KEY on the platform server')

    def request(self,path,body=None,query=None):
        url=self.base+path+('?' + urllib.parse.urlencode(query) if query else '')
        req=urllib.request.Request(url,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers={'Content-Type':'application/json','Authorization':'Bearer '+self.key},method='POST' if body is not None else 'GET')
        try:
            with urllib.request.urlopen(req,timeout=120) as response:data=json.load(response)
        except urllib.error.HTTPError as exc:
            # Don't include authorization, submitted remote passwords or raw response bodies.
            raise RuntimeError('Cloud API returned HTTP '+str(exc.code)) from None
        if data.get('ok') is False:raise RuntimeError(str(data.get('error') or 'Cloud API error'))
        return data

    def ask(self,question,chat_id,user_id):
        result=self.request('/api/v1/support/ask',{'question':question,'user_id':str(user_id),'session':'max:'+str(chat_id)})
        for action in result.get('actions',[]):
            if str(action.get('url','')).startswith('/'):action['url']=self.base+action['url']
        return result

    def create_ticket(self,service,description,user_id,**fields):
        return self.request('/api/v1/support/tickets',dict(fields,action='create',service=service,description=description,user_id=str(user_id)))

    def tickets(self,user_id=''):
        return self.request('/api/v1/support/tickets',query={'user_id':str(user_id)})

    def acknowledge_external_ticket(self,cloud_ticket_id,external_id,status='in_progress'):
        if not external_id:raise ValueError('Use the actual identifier returned by the other platform')
        return self.request('/api/v1/support/tickets',{'action':'update','id':cloud_ticket_id,'external_id':external_id,'status':status})


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Check the cloud AI API without running a second MAX receiver')
    parser.add_argument('question');parser.add_argument('--chat',default='test');parser.add_argument('--user',default='test')
    args=parser.parse_args()
    print(CloudClient().ask(args.question,args.chat,args.user).get('answer',''))
