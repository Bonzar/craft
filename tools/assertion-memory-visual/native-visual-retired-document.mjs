// Private native TEST/PROD router update, scoped to reviewed alias-enabled sources.
import {createHash} from 'node:crypto';
import {realpathSync} from 'node:fs';
import {readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';

const pins=Object.freeze({
  test:['23123e33554640df6c7cd1fd5c0c8b403c9817b1274b903e39632b92d35a4987',
        '489907c682205c6cbbb95ec7a32219dca2952309e4eb83d7c31395b3f05db5f5'],
  prod:['b710360f7c932bc7c7ae8efdb315b0f93d5c87cb9d994463f8f118ce4ec2c08e',
        '598bba0742892f7e0559fef1b203935dc8404806904e647b070d5ceb9c94c160'],
});
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
export function addRetiredDocumentRecovery(before,lane){
  if(typeof lane!=='string'||!Object.hasOwn(pins,lane))throw Error('Exact visual stage required');
  const [input,output]=pins[lane];
  if(!(before instanceof Uint8Array)||sha(before)!==input)throw Error('Unexpected native visual source');
  const anchor='      if(exact.status===404)return res(404);';
  const source=Buffer.from(before).toString('utf8');
  if(source.split(anchor).length!==2)throw Error('Unexpected native visual route shape');
  const after=Buffer.from(source.replace(anchor,()=>`      if(exact.status===410){
        if(name==='index.html'&&req.method==='GET')return res(302,null,{location:'/visual'+url.search});
        return res(404); // An asset is never replaced by a different release.
      }
${anchor}`));
  if(sha(after)!==output)throw Error('Unexpected native visual output');
  return after;
}

if(process.argv[1]&&import.meta.url===pathToFileURL(realpathSync(process.argv[1])).href){
  const [source,output,lane,...extra]=process.argv.slice(2);
  if(!source||!output||!lane||extra.length)throw Error('Usage: native-visual-retired-document.mjs INPUT NEW_FILE test|prod');
  await writeFile(output,addRetiredDocumentRecovery(await readFile(source),lane),{flag:'wx'});
}
