// Release-controlled source transformation, not a Sites deploy client.
// Only the authenticated TEST route is added; any other native checkout needs review.
import {createHash} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';

export const INPUT_SHA256='46fb107541f13643c93d84d0b4a89fe1b0a03ebc0247a982e0353bb032114c3c';
export const OUTPUT_SHA256='23123e33554640df6c7cd1fd5c0c8b403c9817b1274b903e39632b92d35a4987';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');

export function addNativeTestVisualAlias(original){
  if(!(original instanceof Uint8Array)||hash(original)!==INPUT_SHA256)throw Error('Unexpected native visual source');
  let content=Buffer.from(original).toString('utf8');
  const replace=(from,to)=>{
    if(content.split(from).length!==2)throw Error('Native visual source shape changed');
    content=content.replace(from,()=>to);
  };
  replace('export function buildPairedShell(){',
    "const latestVisualAlias=path=>path==='/visual'||path==='/visual/';\nexport function buildPairedShell(){");
  replace("url.pathname==='/'||/^","url.pathname==='/'||(env.MEMORY_GRAPH_MODE==='test'&&latestVisualAlias(url.pathname))||/^");
  replace("    const name=match&&(match[2]||'index.html');\n    if(path!=='/'&&",
    "    const name=match&&(match[2]||'index.html');\n    const isLatestAlias=stage==='test'&&latestVisualAlias(path);\n    if(path!=='/'&&!isLatestAlias&&");
  replace("const operation=path==='/'?'current':'asset';","const operation=path==='/'||isLatestAlias?'current':'asset';");
  replace("if(!shouldFallback(env,pointer))return rejected;","if(isLatestAlias||!shouldFallback(env,pointer))return rejected;");
  replace("if(path==='/' || path==='/__memory-visual-egress' || path.startsWith('/visual/')",
    "if(path==='/' || path==='/__memory-visual-egress' || (stage==='test'&&latestVisualAlias(path)) || path.startsWith('/visual/')");
  const bytes=Buffer.from(content);
  if(hash(bytes)!==OUTPUT_SHA256)throw Error('Native visual patch output changed');
  return bytes;
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  const [input,output,...extra]=process.argv.slice(2);
  if(!input||!output||extra.length)throw Error('Usage: native-test-visual-alias.mjs REVIEWED_INPUT NEW_FILE');
  await writeFile(output,addNativeTestVisualAlias(await readFile(input)),{flag:'wx'});
}
