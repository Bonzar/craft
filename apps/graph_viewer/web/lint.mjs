import {readdir,readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
const directory=new URL('./src/',import.meta.url);
let failures=0;
for(const name of await readdir(directory)){
  if(!name.endsWith('.ts'))continue;
  const path=new URL(name,directory);const source=await readFile(path,'utf8');
  const rules=[[/\b(?:innerHTML|outerHTML|insertAdjacentHTML|document\.write)\b/,'Unsafe HTML sink'],[/\bconsole\./,'Production console call'],[/\b(?:localStorage|sessionStorage)\b/,'Browser persistent storage'],[/\b(?:eval|Function)\s*\(/,'Dynamic code execution'],[/\bas\s+any\b|:\s*any\b/,'Unvalidated any type']];
  for(const [pattern,message] of rules)if(pattern.test(source)){process.stderr.write(`${name}: ${message}\n`);failures++;}
  if(source.split('\n').length>800){process.stderr.write(`${name}: module exceeds 800 lines\n`);failures++;}
  const checked=spawnSync(process.execPath,['--check',path.pathname],{encoding:'utf8'});if(checked.status!==0){process.stderr.write(checked.stderr);failures++;}
}
if(failures)process.exitCode=1;else process.stdout.write('Source syntax and security checks passed.\n');
