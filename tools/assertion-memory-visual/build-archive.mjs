import {mkdtemp,rm,readFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {execFileSync} from 'node:child_process';
import {versionableBundle} from './bundle.mjs';
import {stageAssets,hash} from './store.mjs';
const [src,out,...extra]=process.argv.slice(2);
if(!src||!out||extra.length)throw Error('Usage: build-archive EXACT_UI_ROOT NEW_ARCHIVE_TAR');
const root=await mkdtemp(join(tmpdir(),'visual-archive-'));
try{
  const files=await versionableBundle(resolve(src));
  const release=await stageAssets(root,files);
  execFileSync('python3',['-c',`
import io, pathlib, sys, tarfile
root, output, *members = sys.argv[1:]
with tarfile.open(fileobj=open(output,'xb'),mode='w',format=tarfile.USTAR_FORMAT) as tar:
    for name in sorted(members):
        data = pathlib.Path(root,name).read_bytes()
        i=tarfile.TarInfo(name); i.size=len(data); i.mode=0o640
        tar.addfile(i,io.BytesIO(data))
`,join(root,'releases',release),resolve(out),...Object.keys(files),'manifest.json']);
  const archive=await readFile(out);
  if(archive.length>5_000_000)throw Error('Archive exceeds receiver limit');
  console.log(JSON.stringify({archive_sha256:hash(archive),release,bytes:archive.length}));
}finally{await rm(root,{recursive:true,force:true});}
