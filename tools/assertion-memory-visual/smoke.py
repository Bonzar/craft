"""Read-only verification of the private VM visual gateway, not a physical phone."""
import hashlib
import json
import os
import re
import tarfile
from pathlib import Path
import urllib.request


def main(env=os.environ):
    host = env['VISUAL_GATEWAY_HOST']
    token = env['VISUAL_GATEWAY_TOKEN']
    mode, expected = env['VISUAL_ENV'], env['VISUAL_DIGEST']
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if not re.fullmatch(label+r'\.'+label+r'\.apigw\.yandexcloud\.net', host):
        raise ValueError('Invalid fixed HTTPS gateway hostname')
    if mode not in ('test','prod') or any(not re.fullmatch('[a-f0-9]{64}', value) for value in (token,expected)):
        raise ValueError('Missing isolated TEST/PROD smoke configuration')
    url = 'https://'+host+f'/memory-visual-{mode}-read'
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    opener=urllib.request.build_opener(NoRedirect)
    def call(value):
        req=urllib.request.Request(url,json.dumps(value).encode(),headers={
            'Content-Type':'application/json','X-Memory-Visual-Auth':'Bearer '+token},method='POST')
        with opener.open(req,timeout=8) as r:
            if r.status != 200: raise ValueError('Visual gateway unavailable')
            return r.headers.get_content_type(),r.read(2_400_001)
    mime,data=call({'op':'current'})
    if mime!='application/json' or len(data)>512 or json.loads(data)!={'digest':expected}:
        raise ValueError('Gateway serves a different visual release')
    archive=Path(env['VISUAL_DOWNLOAD'])/'visual.tar'
    with tarfile.open(archive,mode='r:') as tar:
        manifest=tar.extractfile('manifest.json').read(1025)
        if len(manifest)>1024 or hashlib.sha256(manifest).hexdigest()!=expected:
            raise ValueError('Visual manifest identifies a different release')
        files=json.loads(manifest)
        if len(files)!=4:
            raise ValueError('Incomplete visual manifest')
        for name, sha in files.items():
            mime,data=call({'op':'asset','digest':expected,'name':name})
            predicted={'html':'text/html','css':'text/css','js':'text/javascript'}[name.rsplit('.',1)[1]]
            if mime!=predicted or len(data)>2_400_000 or hashlib.sha256(data).hexdigest()!=sha:
                raise ValueError('Gateway did not return exact visual asset bytes')
    print('Authenticated visual gateway serves all approved asset bytes')


if __name__=='__main__': main()
