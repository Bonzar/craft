"""Build a hash-locked offline runtime wheelhouse from attested Linux versions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PACKAGE=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,160}')
VERSION=re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,100}')
WHEEL=re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,240}[.]whl')
DIGEST=re.compile(r'[a-f0-9]{64}')
REQUIRED={'mcp':'1.30.0','starlette':'1.10.0','uvicorn':'0.54.0','httpx':'0.28.1','numpy':'2.5.4'}
MAX_ONE=128*1024**2
MAX_ALL=512*1024**2
DEST=Path('/etc/assertion-release/production/wheels')


def need(ok):
    if not ok: raise ValueError('Wheelhouse path, provenance or target validation failed')


class DenyRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('Unexpected registry redirect')


def normalize(name):
    return re.sub(r'[-_.]+','-',name).lower()


def endpoint(url,host):
    p=urlsplit(url)
    need(p.scheme=='https' and p.netloc==host and not p.query and not p.fragment and
         bool(p.path) and not any(x in p.path for x in ('\\','..')))
    return p


def scope(path,owner=None):
    need(path.is_absolute() and path.resolve()==path and path.is_dir())
    st=path.lstat()
    need(owner is None or st.st_uid==owner)
    need(not st.st_mode&0o022)


def parse_lock(text):
    result={}
    for line in text.splitlines():
        if not line: continue
        s=line.split('==')
        need(len(s)==2 and PACKAGE.fullmatch(s[0]) and VERSION.fullmatch(s[1]))
        name=normalize(s[0])
        need(name not in result and name!='fastembed')
        result[name]=s[1]
    need(0<len(result)<=150 and all(result.get(k)==v for k,v in REQUIRED.items()))
    return result


def sha_file(path,cap):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
        m=os.fstat(f.fileno())
        need(stat.S_ISREG(m.st_mode) and 0<m.st_size<=cap)
        digest=hashlib.file_digest(f,'sha256').hexdigest()
    return digest,m.st_size


def publish_no_replace(source,dest):
    # Linux atomic rename without replacing an existing path.
    libc=ctypes.CDLL(None,use_errno=True)
    rename=libc.renameat2
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    if rename(-100,os.fsencode(source),-100,os.fsencode(dest),1)!=0:
        raise OSError(ctypes.get_errno(),'Wheelhouse atomic no-overwrite rename refused')


def build(attested,output,expected_python,expected_machine):
    need(os.geteuid()!=0 and platform.system()=='Linux' and platform.machine()==expected_machine
         and platform.python_version()==expected_python)
    from pip._vendor.packaging.specifiers import SpecifierSet
    from pip._vendor.packaging.tags import sys_tags
    from pip._vendor.packaging.utils import parse_wheel_filename
    from pip._vendor.packaging.version import Version
    packages=parse_lock(attested.read_text(encoding='ascii'))
    scope(output.parent,os.geteuid())
    need(not output.exists() and not output.is_symlink())
    tags={tag:i for i,tag in enumerate(sys_tags())}
    def metadata(name,ver,opener):
        uri=f'https://pypi.org/pypi/{name}/{ver}/json'
        endpoint(uri,'pypi.org')
        req=Request(uri,headers={'Accept':'application/json','User-Agent':'offline-wheelhouse-builder/1'})
        with opener.open(req,timeout=12) as f:
            need(f.status==200)
            data=f.read(2*1024**2+1)
        need(len(data)<=2*1024**2)
        return json.loads(data)
    with tempfile.TemporaryDirectory(prefix='.wheel-build-',dir=output.parent) as tmp:
        staged=Path(tmp)/'package'
        staged.mkdir(mode=0o700)
        items=[]; total=0
        budget_lock=Lock()
        def fetch(pair):
            name,ver=pair
            opener=build_opener(ProxyHandler({}),DenyRedirect())
            m=metadata(name,ver,opener)
            need(normalize(m['info']['name'])==name and Version(m['info']['version'])==Version(ver))
            ranked=[]
            for item in m['urls']:
                file=item.get('filename','')
                if item.get('packagetype')!='bdist_wheel' or item.get('yanked') or not WHEEL.fullmatch(file): continue
                filename_name,filename_version,_,filetags=parse_wheel_filename(file)
                need(normalize(filename_name)==name and filename_version==Version(ver))
                compatible=[tags[tag] for tag in filetags if tag in tags]
                req=item.get('requires_python')
                if not compatible or (req and not SpecifierSet(req).contains(expected_python)): continue
                ranked.append((min(compatible),file,item))
            need(bool(ranked))
            ranked.sort(key=lambda e:(e[0],e[1])); _,file,item=ranked[0]
            url=item['url']; p=endpoint(url,'files.pythonhosted.org')
            need(p.path.rsplit('/',1)[-1]==file)
            digest=item['digests']['sha256']; size=item['size']
            need(DIGEST.fullmatch(digest) and type(size) is int and 0<size<=MAX_ONE )
            with budget_lock:
                nonlocal total
                need(total+size<=MAX_ALL)
                total+=size
            h=hashlib.sha256(); count=0
            with opener.open(Request(url,headers={'User-Agent':'offline-wheelhouse-builder/1'}),timeout=20) as src, (staged/file).open('xb') as out:
                need(src.status==200)
                while data:=src.read(1<<20):
                    count+=len(data); need(count<=size); h.update(data); out.write(data)
            need(count==size and h.hexdigest()==digest)
            return {'package':name,'version':ver,'file':file,'sha256':digest,'size':size}
        with ThreadPoolExecutor(max_workers=4) as pool:
            items=list(pool.map(fetch,sorted(packages.items())))
        locked=''.join(item['package']+'=='+item['version']+' --hash=sha256:'+item['sha256']+'\n' for item in items)
        (staged/'locked-requirements.txt').write_text(locked,encoding='ascii')
        manifest={'python':expected_python,'machine':expected_machine,'wheels':items,
                  'requirements_sha256':hashlib.sha256(locked.encode('ascii')).hexdigest()}
        (staged/'wheelhouse-manifest.json').write_text(json.dumps(manifest,sort_keys=True)+'\n',encoding='ascii')
        digest,_=sha_file(staged/'wheelhouse-manifest.json',128000)
        publish_no_replace(staged,output)
        print(json.dumps({'wheel_count':len(items),'total_bytes':total,'manifest_sha256':digest},sort_keys=True))


def publish(input_path,expected_manifest):
    # Root never imports, installs or executes package files; trusted manifest hash comes from review.
    need(os.geteuid()==0 and platform.system()=='Linux' and DIGEST.fullmatch(expected_manifest))
    scope(input_path)
    scope(DEST.parent,0)
    need(not DEST.exists() and not DEST.is_symlink())
    mpath=input_path/'wheelhouse-manifest.json'
    need(sha_file(mpath,128000)[0]==expected_manifest)
    with os.fdopen(os.open(mpath,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
        body=f.read(128001)
    need(len(body)<=128000 and hashlib.sha256(body).hexdigest()==expected_manifest)
    manifest=json.loads(body); items=manifest['wheels']
    need(0<len(items)<=150)
    lock=''.join(x['package']+'=='+x['version']+' --hash=sha256:'+x['sha256']+'\n' for x in items).encode('ascii')
    need(hashlib.sha256(lock).hexdigest()==manifest['requirements_sha256'])
    need(parse_lock('\n'.join(x['package']+'=='+x['version'] for x in items)))
    need(len(set(x['file'] for x in items))==len(items) and sum(x['size'] for x in items)<=MAX_ALL)
    with tempfile.TemporaryDirectory(prefix='.prod-wheelhouse-',dir=DEST.parent) as tmp:
        stage=Path(tmp)/'payload'; stage.mkdir(mode=0o755)
        for x in items:
            filename=x['file']; need(WHEEL.fullmatch(filename) and DIGEST.fullmatch(x['sha256']) and
                                      type(x['size']) is int and 0<x['size']<=MAX_ONE)
            with os.fdopen(os.open(input_path/filename,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as src, (stage/filename).open('xb') as dst:
                meta=os.fstat(src.fileno()); need(stat.S_ISREG(meta.st_mode) and meta.st_size==x['size'])
                remaining=x['size']
                while remaining:
                    data=src.read(min(remaining,1<<20)); need(bool(data)); dst.write(data); remaining-=len(data)
                need(src.read(1)==b'')
            need(sha_file(stage/filename,MAX_ONE)==(x['sha256'],x['size']))
            (stage/filename).chmod(0o644)
        (stage/'locked-requirements.txt').write_bytes(lock)
        (stage/'wheelhouse-manifest.json').write_bytes(body)
        for f in (stage/'locked-requirements.txt',stage/'wheelhouse-manifest.json'): f.chmod(0o644)
        stage.chmod(0o755)
        publish_no_replace(stage,DEST)
    print('Verified wheelhouse files installed dormant; no package execution, service or DB activity.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='mode',required=True)
    b=sub.add_parser('build')
    b.add_argument('--attested',type=Path,required=True); b.add_argument('--output',type=Path,required=True)
    b.add_argument('--python',required=True); b.add_argument('--machine',required=True)
    u=sub.add_parser('publish')
    u.add_argument('--input',type=Path,required=True); u.add_argument('--manifest-sha',required=True)
    a=p.parse_args()
    os.umask(0o077)
    if a.mode=='build': build(a.attested,a.output,a.python,a.machine)
    else: publish(a.input,a.manifest_sha)


if __name__=='__main__':
    try: main()
    except Exception:
        raise SystemExit('Offline wheelhouse step failed closed; no package execution or enabled service.') from None
