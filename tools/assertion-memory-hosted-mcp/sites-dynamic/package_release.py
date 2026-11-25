"""Build one reproducible release bundle. No GitHub/Sites call and no credentials."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import subprocess
import tarfile

FORMAT=1
PAUSE_FORMAT=2
PAUSE_MODULES=('native-pause.mjs','v25-bind.mjs')
HEX=re.compile(r'[a-f0-9]{64}')
SHA=re.compile(r'[a-f0-9]{40}')
TAG=re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?')
LANES=('test','prod')
PIN_KEYS={'project_id_sha256','site_origin_sha256','access_identity_sha256'}
CONFIG_KEYS={'lane','owner_only','include_effects','effects_timeout_ms',*PIN_KEYS}

def digest(data):
    return hashlib.sha256(data).hexdigest()

def valid(regex,value):
    return isinstance(value,str) and regex.fullmatch(value) is not None

def wire(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()+b'\n'

def read_doc(path):
    path=Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size>65536:
        raise ValueError('invalid input file')
    def unique(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('duplicate key')
            out[k]=v
        return out
    return json.loads(path.read_bytes(),object_pairs_hook=unique)

def envelope(lane,profile):
    if type(profile) is not dict or set(profile)!=CONFIG_KEYS or profile['lane']!=lane or lane not in LANES:
        raise ValueError('wrong profile shape')
    if (profile['owner_only'] is not True or type(profile['include_effects']) is not bool
            or type(profile['effects_timeout_ms']) is not int or not 1000<=profile['effects_timeout_ms']<=250000
            or any(not valid(HEX,profile[k]) for k in PIN_KEYS)):
        raise ValueError('invalid private profile')
    return wire(profile)

def build(tag,revision,worker,profiles,*,test_pause=None):
    if not valid(TAG,tag) or not valid(SHA,revision) or type(worker) is not bytes or not 0<len(worker)<50000:
        raise ValueError('invalid release source')
    if set(profiles)!=set(LANES):
        raise ValueError('two profiles required')
    files={'worker.mjs':worker}
    for lane in LANES:
        files[f'profiles/{lane}.json']=envelope(lane,profiles[lane])
    if any(profiles['test'][k]==profiles['prod'][k] for k in ('project_id_sha256','site_origin_sha256')):
        raise ValueError('environment overlap')
    format_version=FORMAT
    if test_pause is not None:
        if set(test_pause)!=set(PAUSE_MODULES) or any(
                type(raw) is not bytes or not 0<len(raw)<50000 for raw in test_pause.values()):
            raise ValueError('invalid TEST pause source')
        files.update({'test/pause/'+name:raw for name,raw in test_pause.items()})
        format_version=PAUSE_FORMAT
    manifest={'version':format_version,'tag':tag,'source_sha':revision,
              'files':{n:digest(b) for n,b in sorted(files.items())}}
    files['manifest.json']=wire(manifest)
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w',format=tarfile.USTAR_FORMAT) as tar:
        for n,b in sorted(files.items()):
            i=tarfile.TarInfo(n);i.mode=0o644;i.size=len(b)
            tar.addfile(i,io.BytesIO(b))
    zipped=io.BytesIO()
    with gzip.GzipFile(fileobj=zipped,mode='wb',mtime=0,filename='') as gz:
        gz.write(buffer.getvalue())
    return zipped.getvalue(),manifest

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('test-profile','prod-profile','worker','output'):
        p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--test-pause-dir',type=Path);
    p.add_argument('--tag',required=True);p.add_argument('--source-sha',required=True)
    args=p.parse_args()
    revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    if revision!=args.source_sha:
        raise ValueError('checkout differs from immutable selected GitHub release')
    def tracked_bytes(path):
        rel=subprocess.check_output(['git','ls-files','--full-name','--',str(path)],text=True).strip()
        if not rel or '\n' in rel or path.is_symlink() or not path.is_file():
            raise ValueError('expected one tracked source')
        raw=path.read_bytes()
        if raw!=subprocess.check_output(['git','show',revision+':'+rel]):
            raise ValueError('modified source after checkout')
        return raw
    pause = None
    if args.test_pause_dir is not None:
        pause={name:tracked_bytes(args.test_pause_dir/name) for name in PAUSE_MODULES}
    out,manifest=build(args.tag,revision,tracked_bytes(args.worker),
        {'test':read_doc(args.test_profile),'prod':read_doc(args.prod_profile)},test_pause=pause)
    with args.output.open('xb') as f:f.write(out)
    print(wire({'archive_sha256':digest(out),'manifest':manifest}).decode(),end='')
if __name__=='__main__':main()
