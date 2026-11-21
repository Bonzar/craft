import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {verifyAndStage} from './qa-assets.mjs';
import {keywordSeed} from './qa_keyword_seed.mjs';

const [ui,manifestFile,snapshotFile,output,keywordSQL]=process.argv.slice(2);
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
if(keywordSQL){
  const seed=keywordSeed(snapshot);
  snapshot.keyword_index=seed.index;
  await writeFile(keywordSQL,seed.sql,{flag:'wx'});
}
delete snapshot.keyword_cases;
delete snapshot.keyword_projection; // Exact search text is stored in synthetic D1, not JS bundle.
await writeFile(output,'export default '+JSON.stringify({assets,snapshot})+';\n',{flag:'wx'});
