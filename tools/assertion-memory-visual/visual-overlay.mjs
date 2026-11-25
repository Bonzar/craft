import {readFile} from 'node:fs/promises';
const [queueHtml, queueScript]=await Promise.all(['queue-dashboard.html','queue-dashboard.js'].map(name=>readFile(new URL(name,import.meta.url),'utf8')));
// The pinned bundle still owns the Connections renderer and worker.
const TAG = /^assertion-memory-visual-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?$/;
function once(input, from, to) {
  if (input.split(from).length !== 2) throw Error('Pinned viewer integration changed: ' + from.slice(0,60));
  return input.replace(from, () => to);
}
export function patchVisual({html,script,css,releaseTag=null}) {
  if(releaseTag!==null&&(typeof releaseTag!=='string'||!TAG.test(releaseTag)||/\s/.test(releaseTag)))throw Error('Invalid Visual release tag');
  script=once(script,'graphMode=e(new URLSearchParams(location.search));','graphMode=`connections`;');
  script=once(script,'let e=Z(this.graphMode===`connections`?`⬡ Обзоры`:`⬡ Связи`,()=>this.run(()=>this.toggleGraphMode()));e.id=`layout-toggle`,','let e=Z(`Связи`,()=>{});e.hidden=true;e.disabled=true;e.id=`layout-toggle`,');
  html=once(html,'id="topics-toggle" aria-label=','id="topics-toggle" hidden aria-label=');
  if(releaseTag!==null) {
    html=once(html,'</body>','<div class="visual-release-version">Визуал: '+releaseTag+'</div>\n</body>');
  }
  html=once(html,'</body>',queueHtml+'\n</body>');
  script+='\n'+queueScript;
  css+='\n#visual-queue{position:fixed;top:78px;right:12px;z-index:4;max-height:74vh;max-width:min(92vw,390px);overflow:auto;background:#131a2b;color:#e6eafa;border:1px solid #526086;border-radius:8px;padding:8px 12px;font:13px system-ui} #visual-queue *{overflow-wrap:anywhere} #visual-queue summary{cursor:pointer}';
  css+='\n#layout-toggle[hidden],#topics-toggle[hidden]{display:none!important}\n'+
    '.visual-release-version{position:fixed;bottom:12px;left:12px;z-index:3;background:#101728;color:#e6eafa;border:1px solid #526086;border-radius:7px;padding:5px 8px;font:12px system-ui;pointer-events:none}';
  return {html,script,css};
}
