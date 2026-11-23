import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {hash,ITEM} from './store.mjs';
export async function versionableBundle(dir) {
  const pins=JSON.parse(await readFile(join(dir,'asset-hashes.json')));
  const files=Object.fromEntries(await Promise.all(pins.map(async p=>{
    const name=p.url.slice(1);
    if(!ITEM.test(name))throw Error('unexpected asset');
    const bytes=await readFile(join(dir,'ui',name));
    if(bytes.length!==p.bytes||hash(bytes)!==p.sha256)throw Error('pinned input mismatch');
    return [name,bytes];
  })));
  const html=files['index.html'].toString('utf8');
  const main=html.match(/src="\/(assets\/index-[A-Za-z0-9_-]+\.js)"/g);
  const css=html.match(/href="\/(assets\/index-[A-Za-z0-9_-]+\.css)"/g);
  if(main?.length!==1||css?.length!==1||/<base\b/i.test(html))throw Error('unexpected HTML dependency shape');
  const deps=[...html.matchAll(/(?:src|href)="\/(assets\/[^"]+)"/g)];
  if(deps.length!==2||deps.some(m=>!Object.hasOwn(files,m[1])))throw Error('unknown HTML dependency');
  const mainName=deps[0][1], script=files[mainName].toString('utf8');
  const matches=[...script.matchAll(/new Worker\(new URL\(`\/(assets\/graph\.worker-[A-Za-z0-9_-]+\.js)`/g)];
  if(matches.length!==1||!Object.hasOwn(files,matches[0][1])||script.split('/assets/').length!==2)throw Error('unexpected lazy Worker dependency');
  files['index.html']=Buffer.from(html.replace(/((?:src|href)=")\/assets\//g,'$1./assets/'));
  files[mainName]=Buffer.from(script.replace('/'+matches[0][1],'./'+matches[0][1].split('/').at(-1)));
  return files;
}
