import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {verifyAndStage} from './qa-assets.mjs';

const [ui,manifestFile,snapshotFile,output]=process.argv.slice(2);
if(!ui||!manifestFile||!snapshotFile||!output) throw new Error('UI, pinned checksum manifest, oracle JSON and destination required');
const manifest=JSON.parse(await readFile(manifestFile,'utf8'));
await verifyAndStage(ui,manifest);
const assets={};
for(const {path} of manifest.files){
  const type=path.endsWith('.html')?'text/html; charset=utf-8':path.endsWith('.css')?'text/css; charset=utf-8':'text/javascript; charset=utf-8';
  assets['/'+path]={type,base64:(await readFile(join(ui,path))).toString('base64')};
}
const snapshot=JSON.parse(await readFile(snapshotFile,'utf8'));
if(!snapshot?.manifest?.revision || !snapshot.capabilities?.readiness?.some(x=>x.code==='synthetic_fixture')) {
  throw new Error('Synthetic upstream graph fixture required');
}
await writeFile(output,'export default '+JSON.stringify({assets,snapshot})+';\n',{flag:'wx'});
