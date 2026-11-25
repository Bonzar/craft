// Release-controlled source transformation, not a Sites deploy client.
// TEST is retained as the backward-compatible default; PROD must be explicit.
import {createHash} from 'node:crypto';
import {realpathSync} from 'node:fs';
import {readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';

export const INPUT_SHA256='46fb107541f13643c93d84d0b4a89fe1b0a03ebc0247a982e0353bb032114c3c';
export const OUTPUT_SHA256='23123e33554640df6c7cd1fd5c0c8b403c9817b1274b903e39632b92d35a4987';
export const PROD_OUTPUT_SHA256='b710360f7c932bc7c7ae8efdb315b0f93d5c87cb9d994463f8f118ce4ec2c08e';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');

export function addNativeVisualAlias(original,lane){
  if(lane!=='test'&&lane!=='prod')throw Error('Explicit native visual stage required');
  if(!(original instanceof Uint8Array)||hash(original)!==INPUT_SHA256)throw Error('Unexpected native visual source');
  let content=Buffer.from(original).toString('utf8');
  const replace=(from,to)=>{
    if(content.split(from).length!==2)throw Error('Native visual source shape changed');
    content=content.replace(from,()=>to);
  };
  replace('export function buildPairedShell(){',
    "const latestVisualAlias=path=>path==='/visual'||path==='/visual/';\nexport function buildPairedShell(){");
  replace("url.pathname==='/'||/^", "url.pathname==='/'||(env.MEMORY_GRAPH_MODE==='" + lane + "'&&latestVisualAlias(url.pathname))||/^");
  replace("    const name=match&&(match[2]||'index.html');\n    if(path!=='/'&&",
    "    const name=match&&(match[2]||'index.html');\n    const isLatestAlias=stage==='" + lane + "'&&latestVisualAlias(path);\n    if(path!=='/'&&!isLatestAlias&&");
  replace("const operation=path==='/'?'current':'asset';","const operation=path==='/'||isLatestAlias?'current':'asset';");
  replace("if(!shouldFallback(env,pointer))return rejected;","if(isLatestAlias||!shouldFallback(env,pointer))return rejected;");
  replace("if(path==='/' || path==='/__memory-visual-egress' || path.startsWith('/visual/')",
    "if(path==='/' || path==='/__memory-visual-egress' || (stage==='" + lane + "'&&latestVisualAlias(path)) || path.startsWith('/visual/')");
  const bytes=Buffer.from(content);
  if(hash(bytes)!==(lane==='test'?OUTPUT_SHA256:PROD_OUTPUT_SHA256))throw Error('Native visual patch output changed');
  return bytes;
}

export const addNativeTestVisualAlias=original=>addNativeVisualAlias(original,'test');

if(process.argv[1]&&import.meta.url===pathToFileURL(realpathSync(process.argv[1])).href){
  const [input,output,lane='test',...extra]=process.argv.slice(2);
  if(!input||!output||extra.length)throw Error('Usage: native-test-visual-alias.mjs REVIEWED_INPUT NEW_FILE [test|prod]');
  await writeFile(output,addNativeVisualAlias(await readFile(input),lane),{flag:'wx'});
}
