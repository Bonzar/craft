"""TEST-only service entrypoint for the common hosted relay. No provisioning or secrets."""
import hashlib, json, os, stat
from pathlib import Path
import uvicorn
from policy import digest
from vm_relay import make_app, make_forward, parse
# Supply this exact reviewed module from the already qualified TEST fixture provider,
# in the root-owned Python bundle and pinned by the release operator. No PROD module.
from test_fixture_provider import ROOT, expected_cgroups, expected_identity, live_snapshot

def fixed_read(path, limit):
 with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as f:
  if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):raise ValueError('not a regular input')
  data=f.read(limit+1)
  if len(data)>limit:raise ValueError('input too large')
  return data

def main():
 c=Path(os.environ['CREDENTIALS_DIRECTORY'])
 profile=parse(fixed_read(c/'read-profile.json',4096))
 catalog=parse(fixed_read(c/'catalog.json',100000))
 if profile.get('lane')!='test' or profile.get('canonical_mcp_port')!=8893 or \
  str(ROOT)!='/srv/assertion-release/test' or profile.get('enabled_capabilities')!=['baseline_read'] or \
  digest(catalog['catalog'])!=profile.get('catalog_sha256') or catalog.get('catalog_sha256')!=profile.get('catalog_sha256'):
  raise SystemExit('not an attested TEST relay configuration')
 tag,release=expected_identity(fixed_read(c/'test_release_identity',512))
 cgroups=expected_cgroups(fixed_read(c/'test_cgroup_identity',2048),os.getuid())
 if tag!=profile.get('base_release'):raise SystemExit('not the selected TEST release')
 inbound=fixed_read(c/'gateway-bearer',4096).decode('ascii').strip()
 backend=fixed_read(c/'test-canonical-bearer',4096).decode('ascii').strip()
 if inbound==backend:raise SystemExit('distinct credentials are required')
 def active():
  marker=ROOT/'current'; start=marker.lstat()
  evidence=live_snapshot(release,cgroups) # already verified TEST authoritative loopback and DB config
  end=marker.lstat()
  generation=lambda st:(st.st_dev,st.st_ino,st.st_ctime_ns,st.st_mtime_ns)
  if generation(start)!=generation(end):raise ValueError('activation changed')
  return tag,hashlib.sha256(json.dumps([generation(end),evidence],separators=(',',':')).encode()).hexdigest()
 active()
 app=make_app(profile,catalog,inbound,active,make_forward(profile,backend))
 uvicorn.run(app,host='127.0.0.1',port=8832,access_log=False,proxy_headers=False,log_level='warning')
if __name__=='__main__':main()
