import {createHash,randomUUID} from 'node:crypto';
import {mkdir,readFile,writeFile,rename,lstat} from 'node:fs/promises';
import {join} from 'node:path';
export const hash=b=>createHash('sha256').update(b).digest('hex');
export const DIGEST=/^[a-f0-9]{64}$/;
export const ITEM=/^(?:index\.html|assets\/(?:graph\.worker-|index-)[a-zA-Z0-9_-]+\.(?:js|css))$/;
export const LIMIT=2_400_000; // Lower than API Gateway request/response limit.
export function assetType(name){
  return name.endsWith('.html')?'text/html':name.endsWith('.css')?'text/css':'text/javascript';
}
async function verify(root,id) {
  if(!DIGEST.test(id))throw Error('bad digest');
  const dir=join(root,'releases',id);
  const data=await readFile(join(dir,'manifest.json'));
  if(hash(data)!==id)throw Error('manifest mismatch');
  const files=JSON.parse(data);
  if(Object.keys(files).length!==4||!Object.hasOwn(files,'index.html')||Object.entries(files).some(([n,h])=>!ITEM.test(n)||!DIGEST.test(h)))throw Error('bad manifest');
  return {dir,files};
}
export async function readAsset(root,id,name) {
  if(!ITEM.test(name))throw Error('bad asset path');
  const {dir,files}=await verify(root,id);
  if(!Object.hasOwn(files,name))throw Error('unknown file');
  const path=join(dir,name);
  const info=await lstat(path);
  if(!info.isFile()||info.size>LIMIT)throw Error('invalid file');
  const bytes=await readFile(path);
  if(bytes.length>LIMIT||hash(bytes)!==files[name])throw Error('asset digest mismatch');
  return bytes;
}
export async function currentRelease(root){
  const id=(await readFile(join(root,'current'),'utf8')).trim();
  await verify(root,id);
  return id;
}
export async function stageAssets(root,files){
  const assets=Object.entries(files).sort(([a],[b])=>a<b?-1:a>b?1:0);
  if(assets.length!==4||!Object.hasOwn(files,'index.html')||assets.some(([n,b])=>!ITEM.test(n)||!(b instanceof Uint8Array)||b.length>LIMIT))throw Error('invalid assets');
  const manifest=Buffer.from(JSON.stringify(Object.fromEntries(assets.map(([n,b])=>[n,hash(b)])))+'\n');
  const id=hash(manifest);
  const releases=join(root,'releases');
  const tmp=join(releases,'.upload-'+randomUUID());
  await mkdir(tmp,{recursive:true,mode:0o750});
  await mkdir(join(tmp,'assets'),{mode:0o750});
  for(const [name,body] of assets)await writeFile(join(tmp,name),body,{mode:0o640,flag:'wx'});
  await writeFile(join(tmp,'manifest.json'),manifest,{mode:0o640,flag:'wx'});
  await rename(tmp,join(releases,id));
  return id;
}
export async function selectRelease(root,id){
  const {files}=await verify(root,id);
  for(const name of Object.keys(files))await readAsset(root,id,name);
  const ptr=join(root,'.current-'+randomUUID());
  await writeFile(ptr,id+'\n',{mode:0o640,flag:'wx'});
  await rename(ptr,join(root,'current'));
}
